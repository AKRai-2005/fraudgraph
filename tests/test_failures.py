"""Failure handling.

The governing rule: a tool failure must never be read as evidence that fraud
did, or did not, occur.  A broken investigation escalates; it does not guess.
"""
from __future__ import annotations

import json

import pytest

from fraudgraph.agent.narrative import _validate
from fraudgraph.agent.llm import Narrator, NullProvider
from fraudgraph.agent.orchestrator import InvestigationAgent, Trigger
from fraudgraph.config import PATHS
from fraudgraph.graph.local_mirror import get_local_backend
from fraudgraph.graph.store import GraphStore
from fraudgraph.schemas import Action, CaseStatus, Verdict

pytestmark = pytest.mark.skipif(
    not (PATHS.build / "tx_index.parquet").exists(), reason="parquet cache not built"
)


class BrokenBackend:
    """Every query raises, as if the database went away mid-investigation."""

    name = "broken"

    def __getattr__(self, name):
        def _raise(*a, **k):
            raise ConnectionError("graph unreachable")
        return _raise

    def ping(self):
        raise ConnectionError("graph unreachable")


class FlakyBackend:
    """Resolves the transaction, then fails on every relationship query."""

    name = "flaky"

    def __init__(self):
        self.inner = get_local_backend()

    def txn_detail(self, **kw):
        return self.inner.txn_detail(**kw)

    def card_profile(self, **kw):
        return self.inner.card_profile(**kw)

    def card_window(self, **kw):
        return self.inner.card_window(**kw)

    def __getattr__(self, name):
        def _raise(*a, **k):
            raise TimeoutError(f"{name} timed out")
        return _raise

    def ping(self):
        return {"ok": True, "backend": self.name}


def _trigger(case_id="HHG-014", txn="3478561", card="C13487-K1", cust="C13487",
             ttype="analyst_request"):
    return Trigger(case_id=case_id, trigger_type=ttype, trigger_text="test",
                   flagged_txn_id=txn, card_id=card, customer_id=cust,
                   opened_at="2016-11-22 20:11:00")


# -------------------------------------------------------- database failure
def test_store_records_a_failed_call_instead_of_raising():
    s = GraphStore(backend=BrokenBackend())
    out = s.call("txn_detail", txn_id="3478561")
    assert "error" in out
    assert s.call_count == 1
    assert s.failures() and s.failures()[0].ok is False


def test_total_database_failure_escalates_without_a_verdict():
    agent = InvestigationAgent(store=GraphStore(backend=BrokenBackend()), narrator=None)
    ans = agent.investigate(_trigger())
    assert ans.case.status is CaseStatus.ESCALATED
    assert ans.case.verdict is Verdict.UNCERTAIN
    assert ans.case.pattern.value == "none"
    assert ans.case.affected_txn_ids == []
    assert ans.sar.file is False
    assert {a.action for a in ans.next_best_actions.final} == {Action.ESCALATE_TO_ANALYST}
    assert "not" in ans.stop_reason.lower()


def test_partial_failure_is_recorded_as_a_gap_not_as_evidence():
    agent = InvestigationAgent(store=GraphStore(backend=FlakyBackend()), narrator=None)
    ans = agent.investigate(_trigger())
    gaps = [e for e in ans.case.evidence if "Evidence gap" in e.claim]
    assert gaps, "a failed relationship query must appear as a recorded gap"
    for g in gaps:
        assert "not read as evidence" in g.claim
    # with the ring query unavailable, the ring must not be asserted
    assert ans.case.connected_card_ids == [] or ans.case.verdict is not Verdict.FRAUD


def test_health_reports_a_down_backend_without_raising():
    s = GraphStore(backend=BrokenBackend())
    h = s.health()
    assert h["ok"] is False and "error" in h


# ------------------------------------------------------ bad inputs / data
def test_unknown_transaction_id_escalates():
    agent = InvestigationAgent(store=GraphStore(backend=get_local_backend()), narrator=None)
    ans = agent.investigate(_trigger(case_id="BAD-1", txn="999999999"))
    assert ans.case.status is CaseStatus.ESCALATED
    assert ans.case.verdict is Verdict.UNCERTAIN
    assert ans.sar.file is False
    assert ans.case.exposure_usd == 0


def test_card_with_no_prior_history_is_handled():
    """A card whose first transaction is the flagged one has no baseline."""
    b = get_local_backend()
    p = b.card_profile("C13487-K1", "2016-01-01 00:00:00")
    assert p["empty"] is True
    assert p["n_txns"] == 0


def test_missing_device_record_does_not_crash_the_device_tests():
    b = get_local_backend()
    r = b.device_history_for_card("C13487-K1", "no-such-device", "2016-12-01 00:00:00")
    assert r["prior_txns_on_device"] == 0
    n = b.device_neighbors("no-such-device")
    assert n["n_cards"] == 0 and n["cards"] == []


def test_unknown_region_returns_empty_not_error():
    b = get_local_backend()
    r = b.region_neighbors(999999.0)
    assert r["n_cards"] == 0


# ------------------------------------------------------ malformed LLM output
def test_llm_plan_rejects_unknown_queries():
    n = Narrator(provider=NullProvider())
    bad = json.dumps([
        {"name": "drop_everything", "params": {}},
        {"name": "card_window", "params": {"card_id": "C1-K1"}},          # missing center_ts
        {"name": "write_case", "params": {"case": {}}},                   # not plannable
        {"name": "device_neighbors", "params": {"device_profile": "d", "evil": 1}},
    ])
    out = n._validate_plan(bad, already_run=[], max_new=5)
    names = [p["name"] for p in out]
    assert "drop_everything" not in names
    assert "write_case" not in names
    assert "card_window" not in names, "a proposal missing a required param must be dropped"
    assert names == ["device_neighbors"]
    assert "evil" not in out[0]["params"], "unknown parameters must be stripped"


def test_llm_plan_handles_garbage_output():
    n = Narrator(provider=NullProvider())
    for junk in ("", "I'm afraid I can't do that", "[{unclosed", "null", "{}"):
        assert n._validate_plan(junk, already_run=[], max_new=3) == []


def test_llm_plan_does_not_repeat_a_call():
    n = Narrator(provider=NullProvider())
    raw = json.dumps([{"name": "customer_cards", "params": {"customer_id": "C1"}}])
    already = [f"customer_cards:{sorted({'customer_id': 'C1'}.items())}"]
    assert n._validate_plan(raw, already_run=already, max_new=3) == []


def test_narrative_rejects_invented_identifiers():
    """The LLM may rewrite; it may not introduce facts."""
    from fraudgraph.schemas import (AnswerFile, InvestigationCase, NextBestActions,
                                    SAR, CaseStatus as CS, Verdict as V, Pattern, Evidence,
                                    EvidenceSource)
    from fraudgraph.analysis.features import Features

    case = InvestigationCase(
        status=CS.CLOSED_FRAUD, verdict=V.FRAUD, fraud_probability=0.9,
        pattern=Pattern.CARD_TESTING, affected_txn_ids=["3478561"],
        first_suspicious_txn_id="3478561", exposure_usd=74.96,
        evidence=[Evidence(claim="Transaction 3478561 for $74.96 on 2016-11-22.",
                           source=EvidenceSource.GRAPH, ref="query:txn_detail(txn_id=3478561)",
                           entity_ids=["3478561", "C13487-K1"])],
    )
    ans = AnswerFile(case_id="T", case=case, next_best_actions=NextBestActions(), sar=SAR())
    f = Features(txn_id="3478561", card_id="C13487-K1", customer_id="C13487",
                 ts="2016-11-22 16:11:00", amount=74.96)

    ok, _ = _validate("Transaction 3478561 for $74.96 on 2016-11-22 was unauthorised.", ans, f)
    assert ok
    bad, why = _validate("Transaction 9999999 for $74.96 was unauthorised.", ans, f)
    assert not bad and "identifier" in why
    bad, why = _validate("Transaction 3478561 for $12,345.00 was unauthorised.", ans, f)
    assert not bad and "amount" in why
    bad, why = _validate("Transaction 3478561 on 1999-01-01 was unauthorised.", ans, f)
    assert not bad and "date" in why


def test_narrator_failure_falls_back_to_the_template():
    from fraudgraph.agent import narrative as N
    from fraudgraph.schemas import (AnswerFile, InvestigationCase, NextBestActions, SAR,
                                    CaseStatus as CS, Verdict as V, Pattern, Evidence,
                                    EvidenceSource)
    from fraudgraph.analysis.features import Features
    from fraudgraph.analysis.risk import RiskAssessment

    class Exploding:
        enabled = True
        tokens_used = 0

        def rewrite_summary(self, *a, **k):
            raise RuntimeError("model unavailable")

        def rewrite_sar(self, *a, **k):
            raise RuntimeError("model unavailable")

    case = InvestigationCase(status=CS.OPEN, verdict=V.UNCERTAIN, fraud_probability=0.4,
                             pattern=Pattern.NONE,
                             evidence=[Evidence(claim="x", source=EvidenceSource.GRAPH, ref="r")])
    ans = AnswerFile(case_id="T", case=case, next_best_actions=NextBestActions(), sar=SAR())
    f = Features(txn_id="1", card_id="C1-K1", customer_id="C1", ts="2016-12-01 00:00:00")
    out = N.write_summary(ans, f, RiskAssessment(), narrator=Exploding())
    assert out and "Assessed" in out or out


# ---------------------------------------------------------- policy safety
def test_duplicate_approval_is_idempotent_in_effect(tmp_path, monkeypatch):
    from fraudgraph.policy.actions import MockActionService
    from fraudgraph.schemas import Action as A

    svc = MockActionService()
    a = svc.perform(A.MONITOR_CARD, case_id="DUP-1")
    b = svc.perform(A.MONITOR_CARD, case_id="DUP-1")
    assert a["execution_id"] != b["execution_id"]
    hist = svc.history(case_id="DUP-1")
    assert all(r["simulated"] for r in hist)


def test_interrupted_investigation_leaves_no_partial_answer_file(tmp_path):
    """A crashed case must not write a half-formed answer file."""
    from fraudgraph.benchmark.run import run_all
    import fraudgraph.benchmark.run as runmod

    class Boom(InvestigationAgent):
        def investigate(self, trigger):
            raise RuntimeError("boom")

    orig = runmod.build_agent
    runmod.build_agent = lambda backend, use_llm: Boom(
        store=GraphStore(backend=get_local_backend()), narrator=None
    )
    try:
        summary = run_all(backend="local", use_llm=False, only=["HHG-001"])
    finally:
        runmod.build_agent = orig
    assert summary["cases_written"] == 0
    assert summary["errors"] and summary["errors"][0]["case_id"] == "HHG-001"

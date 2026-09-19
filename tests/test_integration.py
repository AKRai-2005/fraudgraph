"""Integration tests: the graph query layer and the end-to-end investigation."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from fraudgraph.config import PATHS
from fraudgraph.agent.orchestrator import InvestigationAgent, Trigger
from fraudgraph.graph.local_mirror import get_local_backend
from fraudgraph.graph.queries import CATALOGUE, spec
from fraudgraph.graph.store import GraphStore
from fraudgraph.schemas import Action, Verdict

pytestmark = pytest.mark.skipif(
    not (PATHS.build / "tx_index.parquet").exists(),
    reason="parquet cache not built; run python -m fraudgraph.ingest.prepare && .ingest.entities",
)


@pytest.fixture(scope="module")
def store() -> GraphStore:
    return GraphStore(backend=get_local_backend())


@pytest.fixture(scope="module")
def pack() -> pd.DataFrame:
    return pd.read_csv(PATHS.case_pack_csv)


# ------------------------------------------------------------ query layer
def test_backend_implements_the_whole_catalogue(store):
    missing = [n for n in CATALOGUE if not hasattr(store.backend, n)]
    assert missing == [], f"backend is missing queries: {missing}"


def test_ping_reports_the_expected_dataset_size(store):
    h = store.health()
    assert h["ok"] is True
    assert h["transactions"] == 590_742
    assert h["closed_cases"] == 5_565


def test_txn_detail_resolves_a_case_pack_transaction(store, pack):
    row = pack.iloc[0]
    t = store.call("txn_detail", txn_id=int(row.flagged_txn_id))
    assert t["found"] is True
    assert t["card_id"] == row.card_id
    assert t["customer_id"] == row.customer_id
    assert abs(float(t["TransactionAmt"])) > 0


def test_every_case_pack_transaction_resolves_to_its_declared_card(store, pack):
    for _, row in pack.iterrows():
        t = store.call("txn_detail", txn_id=int(row.flagged_txn_id))
        assert t["found"], f"{row.case_id}: transaction not found"
        assert t["card_id"] == row.card_id, f"{row.case_id}: card mismatch"


def test_card_window_is_bounded_and_ordered(store, pack):
    row = pack.iloc[0]
    t = store.call("txn_detail", txn_id=int(row.flagged_txn_id))
    w = store.call("card_window", card_id=t["card_id"], center_ts=t["ts"],
                   hours_before=24, hours_after=24)
    ts = [x["ts"] for x in w["transactions"]]
    assert ts == sorted(ts)
    assert all(x["card_id"] == t["card_id"] for x in w["transactions"])
    lo, hi = pd.Timestamp(w["window"][0]), pd.Timestamp(w["window"][1])
    assert all(lo <= pd.Timestamp(x["ts"]) <= hi for x in w["transactions"])


def test_card_profile_is_strictly_before_the_cut(store, pack):
    row = pack.iloc[1]
    t = store.call("txn_detail", txn_id=int(row.flagged_txn_id))
    p = store.call("card_profile", card_id=t["card_id"], before_ts=t["ts"])
    if not p.get("empty"):
        assert pd.Timestamp(p["last_ts"]) < pd.Timestamp(t["ts"])
        assert p["amount"]["max"] >= p["amount"]["median"] >= p["amount"]["min"]


def test_device_neighbors_traverses_to_other_cards(store):
    """The shared-device ring is reachable by graph traversal, not a table scan."""
    dev = "SM-G935F Build/NRD90M | Android 7.0 | chrome 62.0 for android | 1920x1080"
    r = store.call("device_neighbors", device_profile=dev,
                   from_ts="2016-11-01 00:00:00", to_ts="2016-12-01 00:00:00")
    assert r["n_cards"] >= 8
    assert len({c["card_id"] for c in r["cards"]}) == r["n_cards"]
    assert "IP_PROXY:ANONYMOUS" in r["proxy_flags"]


def test_closed_cases_for_device_finds_the_undocumented_ring(store):
    dev = "SM-G935F Build/NRD90M | Android 7.0 | chrome 62.0 for android | 1920x1080"
    r = store.call("closed_cases_for_device", device_profile=dev)
    assert r["n"] >= 1
    assert all("case_id" in c for c in r["cases"])


def test_as_of_filter_hides_later_cases(store):
    cc = pd.read_parquet(PATHS.closed_cases_parquet)
    row = cc.iloc[0]
    late = store.call("closed_cases_for_customer", customer_id=row.customer_id)
    early = store.call("closed_cases_for_customer", customer_id=row.customer_id,
                       as_of="2016-07-01 00:00:00")
    assert early["n"] <= late["n"]
    assert early["n"] == 0


def test_query_refs_are_wellformed():
    r = spec("card_window").ref(card_id="C00001-K1", center_ts="2016-12-01 00:00:00")
    assert r.startswith("query:card_window(") and "card_id=C00001-K1" in r


def test_unknown_query_is_recorded_not_raised(store):
    with pytest.raises(KeyError):
        store.call("no_such_query", x=1)


# ------------------------------------------------------- ledger / audit
def test_store_ledger_records_every_call(store, pack):
    s = GraphStore(backend=get_local_backend())
    s.call("txn_detail", txn_id=int(pack.iloc[0].flagged_txn_id))
    s.call("customer_cards", customer_id=pack.iloc[0].customer_id)
    assert s.call_count == 2
    assert [t.name for t in s.ledger] == ["txn_detail", "customer_cards"]
    assert all(t.backend == "local" and t.ok for t in s.ledger)
    assert all(t.ref.startswith("query:") for t in s.ledger)


# --------------------------------------------------------- end to end
@pytest.fixture(scope="module")
def agent() -> InvestigationAgent:
    return InvestigationAgent(store=GraphStore(backend=get_local_backend()), narrator=None)


def test_end_to_end_investigation_produces_a_valid_answer(agent, pack):
    row = pack[pack.case_id == "HHG-014"].iloc[0].to_dict()
    ans = agent.investigate(Trigger.from_case_pack_row(row))
    d = ans.to_answer_dict()
    assert d["case_id"] == "HHG-014"
    assert d["tool_calls"] > 0
    assert d["case"]["evidence"], "an investigation must produce evidence"
    assert d["case"]["summary"]
    assert d["stop_reason"]
    assert d["next_best_actions"]["initial"] and d["next_best_actions"]["final"]


def test_shared_device_ring_case_is_recognised(agent, pack):
    """HHG-014's analyst request points at the ring; the agent should find it."""
    row = pack[pack.case_id == "HHG-014"].iloc[0].to_dict()
    ans = agent.investigate(Trigger.from_case_pack_row(row))
    assert ans.case.verdict is Verdict.FRAUD
    assert ans.case.pattern.value == "undocumented"
    assert ans.case.pattern_description
    assert len(ans.case.connected_card_ids) >= 5
    assert ans.case.connected_device_profiles
    assert ans.sar.file is True
    acts = {a.action for a in ans.next_best_actions.final}
    assert Action.MONITOR_CONNECTED_CARDS in acts
    assert Action.FILE_REPORT in acts
    # the bank model scored this transaction near zero; the graph carried it
    assert ans.risk.bank_risk_score < 0.2


def test_structuring_case_is_recognised(agent, pack):
    """HHG-006 is four online purchases just under $500 inside 40 minutes."""
    row = pack[pack.case_id == "HHG-006"].iloc[0].to_dict()
    ans = agent.investigate(Trigger.from_case_pack_row(row))
    assert ans.case.verdict is Verdict.FRAUD
    assert len(ans.case.affected_txn_ids) == 4
    assert 1800 < ans.case.exposure_usd < 2000
    names = {f.name for f in ans.findings if f.matched}
    assert "sub_threshold_structuring" in names


def test_every_evidence_ref_names_a_real_query_or_source(agent, pack):
    row = pack[pack.case_id == "HHG-010"].iloc[0].to_dict()
    ans = agent.investigate(Trigger.from_case_pack_row(row))
    called = {t.name for t in ans.tool_log}
    for ev in ans.case.evidence:
        if ev.ref.startswith("query:"):
            name = ev.ref.split("query:")[1].split("(")[0]
            assert name in CATALOGUE, f"evidence cites unknown query {name}"
            assert name in called, f"evidence cites {name} which was never called"
        else:
            assert ev.ref.startswith(("risk:", "evidence_request:", "document:"))


def test_agent_never_claims_a_graph_write_it_did_not_make(agent, pack):
    """The local mirror is read-only; written_to_graph must stay false."""
    row = pack[pack.case_id == "HHG-001"].iloc[0].to_dict()
    ans = agent.investigate(Trigger.from_case_pack_row(row))
    assert ans.case.written_to_graph is False
    assert ans.case.graph_case_id == ""


def test_investigation_is_deterministic(agent, pack):
    row = pack[pack.case_id == "HHG-002"].iloc[0].to_dict()
    a = agent.investigate(Trigger.from_case_pack_row(row))
    b = agent.investigate(Trigger.from_case_pack_row(row))
    assert a.case.fraud_probability == b.case.fraud_probability
    assert a.case.verdict == b.case.verdict
    assert [x.action for x in a.next_best_actions.final] == \
           [x.action for x in b.next_best_actions.final]


def test_l1_and_l2_actions_are_never_marked_executed(agent, pack):
    for cid in ("HHG-006", "HHG-014"):
        row = pack[pack.case_id == cid].iloc[0].to_dict()
        ans = agent.investigate(Trigger.from_case_pack_row(row))
        for a in ans.next_best_actions.final:
            if a.route.value in ("L1", "L2"):
                assert a.status == "awaiting_approval"
                assert a.executed_at is None

"""The catalogue is a contract: two backends must produce the same answer.

These tests exist because that claim was made and was not true.  Comparing
verdict, probability, pattern and exposure across backends passed while the
*evidence* underneath silently differed -- a percentile computed two ways, and
case memory citing two different prior cases for the same alert.  Each test
below pins one of those, plus the machinery that would have caught them.
"""
from __future__ import annotations

import json
import random

import pytest

from fraudgraph.benchmark.compare import (
    EXPECTED_TO_DIFFER,
    ComparisonResult,
    compare_answers,
    compare_dirs,
)
from fraudgraph.config import PATHS, TG
from fraudgraph.graph.queries import CANDIDATE_POOL, quantile_nearest, rank_similar_cases

HAVE_CACHE = (PATHS.build / "tx_index.parquet").exists()


# --------------------------------------------------------------- quantiles
def test_quantile_returns_a_value_that_actually_occurred():
    """An interpolated percentile is a number the card never transacted.

    The evidence sentence says "at or below the card's 95th-percentile amount
    ($425.08)", so that figure has to be a real amount, not a midpoint.
    """
    values = [1.0, 2.0, 3.0, 10.0, 100.0]
    for p in (0.0, 0.25, 0.5, 0.75, 0.95, 1.0):
        assert quantile_nearest(values, p) in values


def test_quantile_matches_the_gsql_path_definition():
    values = [float(x) for x in range(1, 101)]  # 1..100
    assert quantile_nearest(values, 0.0) == 1.0
    assert quantile_nearest(values, 1.0) == 100.0
    # nearest rank over n-1 == round(0.95 * 99) == 94 -> the 95th element
    assert quantile_nearest(values, 0.95) == 95.0


def test_quantile_handles_an_empty_history():
    assert quantile_nearest([], 0.5) == 0.0


def test_quantile_of_one_transaction_is_that_transaction():
    assert quantile_nearest([42.5], 0.95) == 42.5


@pytest.mark.skipif(not HAVE_CACHE, reason="parquet cache not built")
def test_local_mirror_uses_the_shared_quantile():
    """The regression: pandas' interpolating quantile in card_profile."""
    import pandas as pd

    from fraudgraph.graph.local_mirror import get_local_backend

    be = get_local_backend()
    pack = pd.read_csv(PATHS.case_pack_csv)
    row = pack.iloc[0]
    # the profile is "strictly before the alert", so the alert's own timestamp
    # is the cut for both the profile and the timeline we check it against
    cut = str(be.txn_detail(txn_id=str(row.flagged_txn_id))["ts"])
    prof = be.card_profile(card_id=str(row.card_id), before_ts=cut, lookback_days=365)
    if prof.get("empty"):
        pytest.skip("no history before the first case's alert")
    window_start = (pd.Timestamp(cut) - pd.Timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")
    tl = be.card_timeline(card_id=str(row.card_id), from_ts=window_start, to_ts=cut,
                          limit=100000)
    amounts = sorted(float(t["TransactionAmt"]) for t in tl["transactions"]
                     if str(t["ts"]) < cut)
    assert len(amounts) == prof["n_txns"], "the two queries disagree on the window"
    for key, p in (("median", 0.5), ("p95", 0.95), ("p25", 0.25), ("p75", 0.75)):
        assert prof["amount"][key] == quantile_nearest(amounts, p)
        # the point of the shared definition: a reported percentile is always
        # an amount the card actually transacted, never an interpolated one
        assert prof["amount"][key] in amounts


# ------------------------------------------------------- case-memory ranking
def _fake_cases(n: int = 30) -> list[dict]:
    return [
        {"case_id": f"CC-{i:04d}", "exposure_usd": 100.0 + (i % 7) * 10,
         "n_txns": 1 + (i % 3), "analyst_notes": "Card-present dispute"}
        for i in range(n)
    ]


def test_ranking_is_invariant_to_the_order_rows_arrive_in():
    """The bug: ties resolved by whatever order the engine walked its vertices.

    TigerGraph and the local mirror return the same rows in different orders,
    so without a total order the top-ranked prior case -- the one that becomes
    evidence in the answer file -- changed with the backend.
    """
    cases = _fake_cases()
    baseline = rank_similar_cases(cases, channel="in_person", amount=120.0,
                                  n_txns=2, limit=6)
    rng = random.Random(7)
    for _ in range(25):
        shuffled = cases[:]
        rng.shuffle(shuffled)
        got = rank_similar_cases(shuffled, channel="in_person", amount=120.0,
                                 n_txns=2, limit=6)
        assert [c["case_id"] for c in got] == [c["case_id"] for c in baseline]


def test_ranking_breaks_exact_ties_by_case_id():
    tied = [{"case_id": cid, "exposure_usd": 100.0, "n_txns": 1, "analyst_notes": ""}
            for cid in ("CC-0009", "CC-0001", "CC-0005")]
    got = rank_similar_cases(tied, channel=None, amount=100.0, n_txns=1, limit=3)
    assert [c["case_id"] for c in got] == ["CC-0001", "CC-0005", "CC-0009"]


def test_ranking_prefers_comparable_size_not_comparable_dollars():
    """Log-space comparison: $40 against a $45 alert beats $4,000 against $4,005."""
    cases = [
        {"case_id": "CC-SMALL", "exposure_usd": 40.0, "n_txns": 1, "analyst_notes": ""},
        {"case_id": "CC-LARGE", "exposure_usd": 4000.0, "n_txns": 1, "analyst_notes": ""},
    ]
    got = rank_similar_cases(cases, channel=None, amount=45.0, n_txns=1, limit=1)
    assert got[0]["case_id"] == "CC-SMALL"


def test_ranking_respects_the_limit():
    assert len(rank_similar_cases(_fake_cases(50), channel=None, amount=100.0,
                                  n_txns=1, limit=6)) == 6


@pytest.mark.skipif(not HAVE_CACHE, reason="parquet cache not built")
def test_similar_closed_cases_is_deterministic_and_bounded():
    from fraudgraph.graph.local_mirror import get_local_backend

    be = get_local_backend()
    first = be.similar_closed_cases(pattern="account_takeover", channel="online",
                                    amount=250.0, n_txns=2, limit=8)
    second = be.similar_closed_cases(pattern="account_takeover", channel="online",
                                     amount=250.0, n_txns=2, limit=8)
    assert [c["case_id"] for c in first["cases"]] == [c["case_id"] for c in second["cases"]]
    assert first["n"] <= 8


@pytest.mark.skipif(not HAVE_CACHE, reason="parquet cache not built")
def test_similar_closed_cases_excludes_and_time_boxes():
    """``as_of`` and ``exclude_case_ids`` were declared but never applied.

    In the local mirror they were not even parameters -- the body referenced
    two undefined names, so the branch that used them would have raised
    NameError had it ever been reached.
    """
    from fraudgraph.graph.local_mirror import get_local_backend

    be = get_local_backend()
    every = be.similar_closed_cases(pattern="account_takeover", amount=250.0, limit=8)
    assert every["cases"], "need a non-empty result to exclude from"
    drop = every["cases"][0]["case_id"]
    without = be.similar_closed_cases(pattern="account_takeover", amount=250.0,
                                      limit=8, exclude_case_ids=(drop,))
    assert drop not in [c["case_id"] for c in without["cases"]]

    boxed = be.similar_closed_cases(pattern="account_takeover", amount=250.0,
                                    limit=8, as_of="2016-08-01")
    for c in boxed["cases"]:
        assert str(c["closed_at"]) < "2016-08-01", "case memory leaked a future case"


def test_candidate_pool_is_large_enough_to_not_decide_the_ranking():
    assert CANDIDATE_POOL >= 100


# ------------------------------------------------------ the comparison itself
def test_comparator_catches_a_changed_percentile():
    left = {"case": {"evidence": [{"claim": "95th percentile $424.99"}]}}
    right = {"case": {"evidence": [{"claim": "95th percentile $425.08"}]}}
    res = ComparisonResult("a", "b")
    compare_answers(left, right, "HHG-001", res)
    assert not res.agree
    assert "evidence[0].claim" in res.differences[0].path


def test_comparator_catches_a_changed_prior_case():
    left = {"case": {"similar_prior_cases": ["CC-2935", "CC-1589"]}}
    right = {"case": {"similar_prior_cases": ["CC-1589", "CC-2935"]}}
    res = ComparisonResult("a", "b")
    compare_answers(left, right, "HHG-001", res)
    assert not res.agree, "reordered case memory must not read as agreement"


def test_comparator_catches_a_dropped_list_item():
    res = ComparisonResult("a", "b")
    compare_answers({"case": {"affected_txn_ids": ["1", "2"]}},
                    {"case": {"affected_txn_ids": ["1"]}}, "HHG-001", res)
    assert not res.agree


def test_comparator_ignores_only_the_documented_fields():
    left = {"latency_s": 3.6, "case": {"written_to_graph": True,
                                       "graph_case_id": "CASE-2016-001"}}
    right = {"latency_s": 0.4, "case": {"written_to_graph": False,
                                        "graph_case_id": ""}}
    res = ComparisonResult("a", "b")
    compare_answers(left, right, "HHG-001", res)
    assert res.agree, f"unexpected: {[d.path for d in res.differences]}"
    assert len(res.ignored) == 3


def test_every_ignored_field_carries_a_reason():
    for path, reason in EXPECTED_TO_DIFFER.items():
        assert reason.strip(), f"{path} is excluded from comparison without a reason"


def test_comparator_reports_a_missing_case(tmp_path):
    (tmp_path / "l").mkdir()
    (tmp_path / "r").mkdir()
    payload = json.dumps({"case_id": "HHG-001"})
    (tmp_path / "l" / "HHG-001.json").write_text(payload)
    (tmp_path / "l" / "HHG-002.json").write_text(payload)
    (tmp_path / "r" / "HHG-001.json").write_text(payload)
    res = compare_dirs(tmp_path / "l", tmp_path / "r")
    assert res.missing == ["HHG-002"]
    assert not res.agree


# ---------------------------------------------------- end-to-end agreement
@pytest.mark.skipif(not HAVE_CACHE, reason="parquet cache not built")
def test_the_same_backend_twice_gives_byte_identical_answers():
    """Reproducibility: same inputs, same outputs, no hidden clock or ordering."""
    import pandas as pd

    from fraudgraph.agent.orchestrator import InvestigationAgent, Trigger
    from fraudgraph.graph.local_mirror import get_local_backend
    from fraudgraph.graph.store import GraphStore
    from fraudgraph.memory.case_memory import CaseMemory

    pack = pd.read_csv(PATHS.case_pack_csv).head(5)

    def run() -> list[dict]:
        store = GraphStore(backend=get_local_backend())
        agent = InvestigationAgent(store=store, memory=CaseMemory(store), narrator=None)
        return [agent.investigate(Trigger.from_case_pack_row(r.to_dict())).to_answer_dict()
                for _, r in pack.iterrows()]

    first, second = run(), run()
    res = ComparisonResult("run 1", "run 2")
    for a, b in zip(first, second):
        compare_answers(a, b, a["case_id"], res)
    assert res.agree, res.report()


@pytest.mark.skipif(not (HAVE_CACHE and TG.configured),
                    reason="TigerGraph is not configured in .env")
def test_local_and_tigergraph_agree_on_the_full_answer():
    """The real cross-backend check. Skipped -- loudly -- when TG is asleep.

    A Savanna workspace suspends itself when idle, so this cannot be a test
    that merely passes when the graph is unreachable; it skips with the reason
    attached, and `scripts/compare_backends.py local tigergraph` is the manual
    form that has to be run before a release.
    """
    import pandas as pd

    from fraudgraph.agent.orchestrator import InvestigationAgent, Trigger
    from fraudgraph.graph.local_mirror import get_local_backend
    from fraudgraph.graph.store import GraphStore
    from fraudgraph.graph.tigergraph import TigerGraphBackend
    from fraudgraph.memory.case_memory import CaseMemory

    try:
        tg = TigerGraphBackend()
        health = tg.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"TigerGraph unreachable: {type(exc).__name__}: {exc}")
    if not health.get("ok"):
        pytest.skip(f"TigerGraph unreachable: {health.get('error')}")

    pack = pd.read_csv(PATHS.case_pack_csv).head(5)

    def run(backend) -> list[dict]:
        store = GraphStore(backend=backend)
        agent = InvestigationAgent(store=store, memory=CaseMemory(store), narrator=None)
        return [agent.investigate(Trigger.from_case_pack_row(r.to_dict())).to_answer_dict()
                for _, r in pack.iterrows()]

    res = ComparisonResult("local", "tigergraph")
    for a, b in zip(run(get_local_backend()), run(tg)):
        compare_answers(a, b, a["case_id"], res)
    assert res.agree, res.report()

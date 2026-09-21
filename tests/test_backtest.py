"""The backtest, and the two ways it could lie.

A backtest that leaks is worse than no backtest: it produces a confident
number that measures hindsight. Most of these tests are about the leak
surface. The rest pin the scoring arithmetic, because a metric nobody checks
is a metric nobody should quote.
"""
from __future__ import annotations

import math

import pytest

from fraudgraph.analysis.backtest import (
    CLEARED,
    FRAUD,
    TRIGGER_MODES,
    CaseResult,
    _auc,
    _infer_trigger,
    _leakage,
    _trigger_for,
    score,
)
from fraudgraph.config import PATHS

HAVE_CACHE = (PATHS.build / "tx_index.parquet").exists()


class _Row:
    """The fields the replay reads off a closed-case row."""

    def __init__(self, **kw):
        self.case_id = kw.get("case_id", "CC-0001")
        self.customer_id = kw.get("customer_id", "C00001")
        self.card_id = kw.get("card_id", "C00001-K1")
        self.opened_at = kw.get("opened_at", "2016-08-01 12:00:00")
        self.outcome = kw.get("outcome", FRAUD)
        self.pattern = kw.get("pattern", "card_not_present_fraud")
        self.analyst_notes = kw.get("analyst_notes", "")
        self.txn_id_list = kw.get("txn_id_list", [3000120])


# ------------------------------------------------------------------- leakage
def test_the_replay_time_boxes_memory_to_the_alert():
    t = _trigger_for(_Row(opened_at="2016-08-01 12:00:00"), "neutral")
    assert t.as_of == "2016-08-01 12:00:00"
    assert t.memory_window == {"as_of": "2016-08-01 12:00:00"}


def test_the_replay_excludes_the_case_under_test():
    t = _trigger_for(_Row(case_id="CC-0042"), "neutral")
    assert t.exclude_case_ids == ("CC-0042",)


def test_leakage_detects_a_case_retrieving_itself():
    class A:
        class case:
            similar_prior_cases = ["CC-0042"]

    t = _trigger_for(_Row(case_id="CC-0042"), "neutral")
    bad = _leakage(A, t, {"CC-0042": "2016-09-01 00:00:00"})
    assert bad and "the case under test" in bad[0]


def test_leakage_detects_a_case_from_the_future():
    class A:
        class case:
            similar_prior_cases = ["CC-9999"]

    t = _trigger_for(_Row(opened_at="2016-08-01 00:00:00"), "neutral")
    bad = _leakage(A, t, {"CC-9999": "2016-10-01 00:00:00"})
    assert bad and "CC-9999" in bad[0]


def test_leakage_allows_a_genuinely_prior_case():
    class A:
        class case:
            similar_prior_cases = ["CC-0007"]

    t = _trigger_for(_Row(case_id="CC-0042", opened_at="2016-08-01 00:00:00"), "neutral")
    assert _leakage(A, t, {"CC-0007": "2016-07-15 00:00:00"}) == []


def test_a_case_closing_exactly_at_the_alert_is_not_available():
    """Closed *at* the same instant is not closed *before*."""
    class A:
        class case:
            similar_prior_cases = ["CC-0007"]

    t = _trigger_for(_Row(case_id="CC-0042", opened_at="2016-08-01 00:00:00"), "neutral")
    assert _leakage(A, t, {"CC-0007": "2016-08-01 00:00:00"}) != []


# ------------------------------------------------------------- the trigger
def test_the_starting_transaction_is_chosen_the_same_way_for_both_classes():
    """Cleared cases have no first_fraud_txn_id; using it would be asymmetric."""
    fraud = _trigger_for(_Row(outcome=FRAUD, txn_id_list=[111, 222]), "neutral")
    clear = _trigger_for(_Row(outcome=CLEARED, txn_id_list=[333]), "neutral")
    assert fraud.flagged_txn_id == "111"
    assert clear.flagged_txn_id == "333"


def test_neutral_mode_carries_a_zero_prior():
    """The headline run must put nothing on either scale."""
    from fraudgraph.analysis.risk import TRIGGER_PRIOR

    assert TRIGGER_MODES["neutral"] == "analyst_request"
    assert TRIGGER_PRIOR["analyst_request"] == 0.0


def test_every_case_gets_the_same_trigger_in_a_fixed_mode():
    a = _trigger_for(_Row(outcome=FRAUD, analyst_notes="cardholder reported"), "neutral")
    b = _trigger_for(_Row(outcome=CLEARED, analyst_notes="model scored a $9 txn at 0.9"),
                     "neutral")
    assert a.trigger_type == b.trigger_type == "analyst_request"


def test_actual_mode_recovers_the_trigger_from_the_notes():
    assert _infer_trigger("cardholder C1 reported unrecognized activity") == "customer_report"
    assert _infer_trigger("model scored a $442.92 transaction at 0.91") == "risk_score"


def test_a_case_with_no_transactions_is_skipped_not_guessed():
    assert _trigger_for(_Row(txn_id_list=[]), "neutral") is None


# -------------------------------------------------------------- the scoring
def _r(truth, verdict, p=0.5, pattern="card_not_present_fraud"):
    return CaseResult(case_id="CC", truth=truth, verdict=verdict, probability=p,
                      confidence=0.5, pattern="", n_evidence=1, tool_calls=1,
                      bank_risk_score=0.5, truth_pattern=pattern)


def test_confusion_matrix_and_rates():
    res = score([
        _r(FRAUD, "fraud"), _r(FRAUD, "fraud"), _r(FRAUD, "legitimate"),
        _r(CLEARED, "legitimate"), _r(CLEARED, "legitimate"), _r(CLEARED, "fraud"),
    ], trigger_mode="neutral", backend="local", seed=1, elapsed_s=0.0)
    d = res["decided_only"]
    assert (d["tp"], d["fn"], d["fp"], d["tn"]) == (2, 1, 1, 2)
    assert d["recall_on_fraud"] == pytest.approx(2 / 3, abs=1e-4)
    assert d["specificity_on_cleared"] == pytest.approx(2 / 3, abs=1e-4)
    assert d["precision"] == pytest.approx(2 / 3, abs=1e-4)   # tp/(tp+fp)


def test_uncertain_is_reported_both_ways_and_never_silently_dropped():
    res = score([_r(FRAUD, "fraud"), _r(FRAUD, "uncertain"), _r(CLEARED, "uncertain")],
                trigger_mode="neutral", backend="local", seed=1, elapsed_s=0.0)
    assert res["decided_only"]["excluded_uncertain"] == 2
    # conservative reading: uncertain takes no fraud action, so it is a negative
    assert res["uncertain_as_negative"]["fn"] == 1
    assert res["uncertain_as_negative"]["tn"] == 1
    assert res["uncertain_as_negative"]["excluded_uncertain"] == 0


def test_recall_is_broken_down_by_true_typology():
    res = score([
        _r(FRAUD, "fraud", pattern="undocumented"),
        _r(FRAUD, "uncertain", pattern="card_testing"),
        _r(FRAUD, "uncertain", pattern="card_testing"),
    ], trigger_mode="neutral", backend="local", seed=1, elapsed_s=0.0)
    bp = res["recall_by_true_pattern"]
    assert bp["undocumented"]["recall"] == 1.0
    assert bp["card_testing"]["recall"] == 0.0
    assert bp["card_testing"]["n"] == 2


def test_auc_is_one_for_a_perfect_ranking_and_half_for_a_coin():
    assert _auc([(0.9, 1), (0.8, 1), (0.2, 0), (0.1, 0)]) == pytest.approx(1.0)
    assert _auc([(0.5, 1), (0.5, 0)]) == pytest.approx(0.5)
    assert _auc([(0.1, 1), (0.2, 0)]) == pytest.approx(0.0)


def test_auc_handles_ties_by_average_rank():
    # two tied pairs straddling the classes -> no information
    assert _auc([(0.5, 1), (0.5, 0), (0.5, 1), (0.5, 0)]) == pytest.approx(0.5)


def test_auc_is_none_when_a_class_is_missing():
    assert _auc([(0.9, 1), (0.8, 1)]) is None


def test_auc_ignores_nan_probabilities_from_errored_cases():
    assert _auc([(float("nan"), 1), (0.9, 1), (0.1, 0)]) == pytest.approx(1.0)


def test_errors_are_counted_not_swallowed():
    res = score([_r(FRAUD, "fraud"),
                 CaseResult(case_id="CC-X", truth=FRAUD, verdict="error",
                            probability=float("nan"), confidence=0.0, pattern="",
                            n_evidence=0, tool_calls=0, bank_risk_score=None,
                            error="boom")],
                trigger_mode="neutral", backend="local", seed=1, elapsed_s=0.0)
    assert res["n_errors"] == 1
    assert res["errors"][0]["error"] == "boom"
    assert res["n_scored"] == 1


def test_the_report_always_carries_the_caveats():
    res = score([_r(FRAUD, "fraud"), _r(CLEARED, "legitimate")],
                trigger_mode="neutral", backend="local", seed=1, elapsed_s=0.0)
    from fraudgraph.analysis.backtest import report

    text = report(res)
    assert "hardest negatives" in text
    assert "says nothing about the 20 exam cases" in text
    assert len(res["caveats"]) >= 5


# ------------------------------------------------------------ the artefact
@pytest.mark.skipif(not (PATHS.build / "backtest.json").exists(),
                    reason="backtest not run; python -m fraudgraph.analysis.backtest")
def test_the_recorded_backtest_is_leak_free():
    import json

    blob = json.loads((PATHS.build / "backtest.json").read_text(encoding="utf-8"))
    runs = {k: v for k, v in blob.items() if k != "generated_at"}
    assert runs, "no runs recorded"
    for key, run in runs.items():
        assert run["leakage_violations"] == 0, f"{key} leaked: {run['leakage_examples']}"
        assert run["n_errors"] == 0, f"{key} had errors: {run['errors']}"


@pytest.mark.skipif(not (PATHS.build / "backtest.json").exists(),
                    reason="backtest not run")
def test_the_recorded_backtest_keeps_the_neutral_run():
    """The headline number must come from the zero-prior run, not 'actual'."""
    import json

    blob = json.loads((PATHS.build / "backtest.json").read_text(encoding="utf-8"))
    assert "neutral" in blob, "the zero-prior run is the one that may be quoted"


@pytest.mark.skipif(not HAVE_CACHE, reason="parquet cache not built")
def test_a_tiny_replay_runs_end_to_end_without_leaking():
    from fraudgraph.analysis.backtest import replay

    res = replay(n_per_class=3, backend="local", trigger_mode="neutral", progress=False)
    assert res["n_scored"] >= 1
    assert res["leakage_violations"] == 0
    assert res["n_errors"] == 0
    assert not math.isnan(res["auc"]["agent_probability"] or 0.0)


# ------------------------------------------------------ transaction windows
class _Call:
    def __init__(self, name, **params):
        self.name, self.params = name, params


class _Ans:
    def __init__(self, calls):
        self.tool_log = calls


def test_window_leakage_catches_a_forward_card_window():
    """The second leak: case memory was time-boxed, transaction windows were not.

    The agent looked 30 days past the flagged transaction, so a replayed alert
    could count activity that happened after its own investigation opened.
    """
    from fraudgraph.analysis.backtest import _window_leakage

    t = _trigger_for(_Row(opened_at="2016-08-01 00:00:00"), "neutral")
    bad = _window_leakage(_Ans([_Call("card_window", center_ts="2016-07-31 00:00:00",
                                      hours_after="720")]), t)
    assert bad and "card_window" in bad[0]


def test_window_leakage_catches_a_forward_device_ring():
    from fraudgraph.analysis.backtest import _window_leakage

    t = _trigger_for(_Row(opened_at="2016-08-01 00:00:00"), "neutral")
    bad = _window_leakage(_Ans([_Call("device_neighbors", to_ts="2016-08-20 00:00:00")]), t)
    assert bad and "device_neighbors" in bad[0]


def test_window_leakage_allows_a_window_ending_before_opening():
    from fraudgraph.analysis.backtest import _window_leakage

    t = _trigger_for(_Row(opened_at="2016-08-01 00:00:00"), "neutral")
    ok = _window_leakage(_Ans([
        _Call("card_window", center_ts="2016-07-30 00:00:00", hours_after="24"),
        _Call("device_neighbors", to_ts="2016-07-31 23:59:59"),
    ]), t)
    assert ok == []


def test_the_trigger_clamps_forward_windows_only_when_replaying():
    live = _trigger_for(_Row(opened_at="2016-08-01 00:00:00"), "neutral")
    live.as_of = ""                                    # the live path
    assert live.cap_hours_after("2016-07-31 00:00:00", 720) == 720
    assert live.cap_ts("2016-09-01 00:00:00") == "2016-09-01 00:00:00"

    replay = _trigger_for(_Row(opened_at="2016-08-01 00:00:00"), "neutral")
    assert replay.cap_hours_after("2016-07-31 00:00:00", 720) == pytest.approx(24.0)
    assert replay.cap_ts("2016-09-01 00:00:00").startswith("2016-08-01")


def test_a_clamp_never_goes_negative_for_an_alert_after_opening():
    t = _trigger_for(_Row(opened_at="2016-08-01 00:00:00"), "neutral")
    assert t.cap_hours_after("2016-08-02 00:00:00", 72) == 0.0

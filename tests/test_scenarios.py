"""Scenario tests.

Every scenario here is SYNTHETIC and hand-constructed.  None is a benchmark
case and none is taken from closed_cases_history.csv.  They exist to pin the
behaviours the challenge asks for, independently of what the exam happens to
contain.
"""
from __future__ import annotations

import pytest

from fraudgraph.analysis import features as F
from fraudgraph.analysis import patterns as P
from fraudgraph.analysis import risk as RISK
from fraudgraph.policy.engine import DecisionState, PolicyEngine, SharedOrigin, stopping_decision
from fraudgraph.schemas import Action, Pattern, Verdict

from tests.test_detectors import ctx_from, txn

E = PolicyEngine()


def assess(ctx, disputes=False):
    f = F.compute(ctx)
    findings, episodes = P.run_all(ctx, f)
    r = RISK.assess(findings, f, customer_disputes=disputes)
    return f, findings, episodes, r


def acts(d):
    return {a.action.value for a in d.actions}


# --------------------------------------------------- 1. strong evidence
def test_scenario_suspicious_with_strong_supporting_evidence():
    """SYNTHETIC: a card-testing run on a new, proxied device. Should be fraud."""
    dev = "Rig | Android 7.0 | chrome 62.0 for android | 1920x1080"
    rows = [
        txn(1, "2016-12-01 09:12:00", 1.10, device=dev, id_15="New", id_23="IP_PROXY:ANONYMOUS"),
        txn(2, "2016-12-01 09:31:00", 2.40, device=dev, id_15="New", id_23="IP_PROXY:ANONYMOUS"),
        txn(3, "2016-12-01 09:52:00", 0.95, device=dev, id_15="New", id_23="IP_PROXY:ANONYMOUS"),
        txn(4, "2016-12-01 10:31:00", 259.98, device=dev, id_15="New", id_23="IP_PROXY:ANONYMOUS"),
    ]
    ctx = ctx_from(rows, rows[3], device_test={
        "prior_txns_on_device": 0, "prior_online_txns": 180, "distinct_prior_devices": 4})
    f, findings, eps, r = assess(ctx)
    assert r.agent_fraud_probability >= 0.7
    assert r.independent_signal_count >= 2
    d = E.evaluate(DecisionState(
        fraud_probability=r.agent_fraud_probability, verdict=Verdict.FRAUD,
        independent_signal_count=r.independent_signal_count,
        exposure=eps["card_testing"].exposure, card_testing=True,
        cleared_purchase_over_100=True,
    ))
    assert {"DECLINE_TRANSACTION", "STEP_UP_AUTH", "BLOCK_CARD", "CREATE_CASE"} <= acts(d)


# ------------------------------- 2. high score, insufficient evidence
def test_scenario_high_risk_score_with_nothing_behind_it():
    """SYNTHETIC: the bank model screams, the graph shows an ordinary purchase."""
    flagged = txn(1, "2016-12-01 12:00:00", 38.0, addr1=100.0,
                  device="known-device", id_15="Found", risk=0.93)
    ctx = ctx_from([flagged], flagged,
                   region_test={"region_id": 100.0, "prior_txns_in_region": 150,
                                "prior_txns_total": 200, "distinct_prior_regions": 3,
                                "home_region": 100.0, "first_seen_in_region": "2016-07-01"},
                   device_test={"prior_txns_on_device": 100, "prior_online_txns": 180,
                                "distinct_prior_devices": 3})
    f, findings, eps, r = assess(ctx)
    assert f.bank_risk_score == 0.93
    assert r.agent_fraud_probability < 0.30, "a score alone must not carry a verdict"
    assert any("bank model scored" in n for n in r.uncertainty_notes)
    d = E.evaluate(DecisionState(
        fraud_probability=r.agent_fraud_probability, verdict=Verdict.LEGITIMATE,
        independent_signal_count=r.independent_signal_count, exposure=0.0,
    ))
    assert "BLOCK_CARD" not in acts(d)
    assert {"ALLOW_TRANSACTION", "CLOSE_NO_FRAUD"} <= acts(d)


def test_scenario_bank_score_is_never_an_input_to_the_probability():
    """The same evidence at score 0.05 and 0.95 must give the same probability."""
    def build(score):
        flagged = txn(1, "2016-12-01 12:00:00", 38.0, addr1=100.0,
                      device="known-device", id_15="Found", risk=score)
        return ctx_from([flagged], flagged,
                        region_test={"region_id": 100.0, "prior_txns_in_region": 150,
                                     "prior_txns_total": 200, "distinct_prior_regions": 3,
                                     "home_region": 100.0, "first_seen_in_region": "x"},
                        device_test={"prior_txns_on_device": 100, "prior_online_txns": 180,
                                     "distinct_prior_devices": 3})
    _, _, _, lo = assess(build(0.05))
    _, _, _, hi = assess(build(0.95))
    assert lo.agent_fraud_probability == hi.agent_fraud_probability
    assert lo.bank_risk_score == 0.05 and hi.bank_risk_score == 0.95


# ------------------------- 3. legitimate activity that looks suspicious
def test_scenario_travel_looks_like_out_of_region_fraud_but_is_not():
    """SYNTHETIC: four days in one new region, no home activity -- a trip."""
    rows = [
        txn(1, "2016-11-29 12:00:00", 60.0, channel="in_person", product="W", addr1=777.0),
        txn(2, "2016-11-30 13:00:00", 70.0, channel="in_person", product="W", addr1=777.0),
        txn(3, "2016-12-01 12:00:00", 90.0, channel="in_person", product="W", addr1=777.0),
        txn(4, "2016-12-02 14:00:00", 55.0, channel="in_person", product="W", addr1=777.0),
    ]
    ctx = ctx_from(rows, rows[2], region_test={
        "region_id": 777.0, "prior_txns_in_region": 0, "prior_txns_total": 200,
        "distinct_prior_regions": 4, "home_region": 100.0, "first_seen_in_region": None})
    f, findings, eps, trip_risk = assess(ctx)

    rows2 = [
        txn(1, "2016-12-01 09:00:00", 40.0, channel="in_person", product="W", addr1=100.0),
        txn(2, "2016-12-01 12:00:00", 90.0, channel="in_person", product="W", addr1=777.0),
        txn(3, "2016-12-01 18:00:00", 35.0, channel="in_person", product="W", addr1=100.0),
    ]
    ctx2 = ctx_from(rows2, rows2[1], region_test={
        "region_id": 777.0, "prior_txns_in_region": 0, "prior_txns_total": 200,
        "distinct_prior_regions": 4, "home_region": 100.0, "first_seen_in_region": None})
    _, _, _, clone_risk = assess(ctx2)

    assert clone_risk.agent_fraud_probability > trip_risk.agent_fraud_probability, (
        "a clone (home activity continues) must score above a trip"
    )


def test_scenario_new_phone_alone_is_not_fraud():
    """SYNTHETIC: 158 cleared closed cases were exactly this."""
    flagged = txn(1, "2016-12-01 12:00:00", 42.0, device="shiny-new-phone", id_15="New", risk=0.81)
    ctx = ctx_from([flagged], flagged, device_test={
        "prior_txns_on_device": 0, "prior_online_txns": 180, "distinct_prior_devices": 5})
    f, findings, eps, r = assess(ctx)
    cnp = next(x for x in findings if x.name == "cnp_new_device")
    assert not cnp.matched
    assert r.agent_fraud_probability < 0.5


# ------------------------------------- 4. multiple accounts, one device
def test_scenario_many_cards_on_one_device_is_a_ring():
    dev = "SM-X Build/Y | Android 7.0 | chrome 62.0 for android | 1920x1080"
    flagged = txn(9, "2016-12-01 12:00:00", 74.96, device=dev, id_15="New",
                  id_23="IP_PROXY:ANONYMOUS", risk=0.05)
    ring = {"n_cards": 24, "n_txns": 48, "total_amount": 5000.0,
            "proxy_flags": ["IP_PROXY:ANONYMOUS"],
            "cards": [{"card_id": f"C{i:05d}-K1", "n_txns": 2, "amount": 50.0} for i in range(24)],
            "lifetime": {"n_cards": 24, "n_txns": 100, "n_new_for_account": 100,
                         "proxy_flags": "IP_PROXY:ANONYMOUS"}}
    ctx = ctx_from([flagged], flagged, device_ring=ring, device_test={
        "prior_txns_on_device": 0, "prior_online_txns": 40, "distinct_prior_devices": 2})
    f, findings, eps, r = assess(ctx)
    ringf = next(x for x in findings if x.name == "shared_device_ring")
    assert ringf.matched and ringf.pattern is Pattern.UNDOCUMENTED
    so = SharedOrigin(kind="device_profile", value=dev,
                      card_ids=[c["card_id"] for c in ring["cards"]])
    d = E.evaluate(DecisionState(
        fraud_probability=r.agent_fraud_probability, verdict=Verdict.FRAUD,
        independent_signal_count=r.independent_signal_count, exposure=400.0,
        shared_origin=so, pattern=Pattern.UNDOCUMENTED, pattern_is_undocumented=True,
        coordinated_across_customers=True,
    ))
    assert {"MONITOR_CONNECTED_CARDS", "FILE_REPORT", "CREATE_CASE",
            "ESCALATE_TO_ANALYST"} <= acts(d)


# ------------------------------------------ 5. conflicting evidence
def test_scenario_conflicting_evidence_escalates():
    d = E.evaluate(DecisionState(
        fraud_probability=0.52, verdict=Verdict.UNCERTAIN, conflicting_evidence=True,
        independent_signal_count=2, exposure=300.0,
    ))
    assert "ESCALATE_TO_ANALYST" in acts(d)
    assert "R8" in d.rules_cited


def test_scenario_conflict_lowers_confidence():
    """Inculpatory and exculpatory findings together must reduce confidence."""
    rows = [
        txn(1, "2016-09-02 10:00:00", 49.00, addr1=100.0),
        txn(2, "2016-10-02 10:00:00", 49.00, addr1=100.0),
        txn(3, "2016-11-02 10:00:00", 49.00, addr1=100.0),
        txn(4, "2016-12-02 10:00:00", 49.00, addr1=100.0, device="new-dev", id_15="New"),
    ]
    ctx = ctx_from(rows, rows[3], device_test={
        "prior_txns_on_device": 0, "prior_online_txns": 180, "distinct_prior_devices": 5})
    f, findings, eps, r = assess(ctx, disputes=True)
    assert f.looks_recurring
    assert r.confidence <= 0.9


# -------------------------------- 6. cases needing customer validation
def test_scenario_single_weak_signal_asks_the_customer_first():
    d = E.evaluate(DecisionState(
        fraud_probability=0.46, verdict=Verdict.UNCERTAIN,
        independent_signal_count=1, exposure=250.0,
    ))
    assert {"VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH"} <= acts(d)
    assert "BLOCK_CARD" not in acts(d)
    assert "R1" in d.rules_cited


def test_scenario_recommendation_changes_when_the_customer_denies():
    """Policy 3b: the recommendation must be able to change on new evidence."""
    before = RISK.RiskAssessment(agent_fraud_probability=0.45, independent_signal_count=1,
                                 confidence=0.5)
    after = RISK.reassess_with_customer_response(before, denied=True, findings=[])
    assert after.agent_fraud_probability > 0.8
    d0 = E.evaluate(DecisionState(fraud_probability=before.agent_fraud_probability,
                                  verdict=Verdict.UNCERTAIN, independent_signal_count=1,
                                  exposure=300.0))
    d1 = E.evaluate(DecisionState(fraud_probability=after.agent_fraud_probability,
                                  verdict=Verdict.FRAUD, independent_signal_count=2,
                                  exposure=300.0, customer_response_denied=True,
                                  asked_customer=True))
    assert "BLOCK_CARD" not in acts(d0)
    assert "BLOCK_CARD" in acts(d1)


def test_scenario_recommendation_reverses_when_the_customer_confirms():
    before = RISK.RiskAssessment(agent_fraud_probability=0.45, independent_signal_count=1,
                                 confidence=0.5)
    after = RISK.reassess_with_customer_response(before, denied=False, findings=[])
    assert after.agent_fraud_probability < 0.15
    d = E.evaluate(DecisionState(fraud_probability=after.agent_fraud_probability,
                                 verdict=Verdict.LEGITIMATE, independent_signal_count=1,
                                 exposure=0.0, customer_response_denied=False,
                                 asked_customer=True))
    assert "CLOSE_NO_FRAUD" in acts(d)
    assert "BLOCK_CARD" not in acts(d)


# --------------------------------------- 7. cases needing human approval
def test_scenario_every_consequential_action_needs_a_human():
    d = E.evaluate(DecisionState(
        fraud_probability=0.95, verdict=Verdict.FRAUD, customer_response_denied=True,
        asked_customer=True, independent_signal_count=3, exposure=8000.0,
    ))
    by = {a.action: a for a in d.actions}
    assert by[Action.BLOCK_CARD].route.value == "L2"       # exposure > 2500
    assert by[Action.BLOCK_CARD].status == "awaiting_approval"
    assert by[Action.FILE_REPORT].route.value == "L2"
    assert by[Action.FILE_REPORT].status == "awaiting_approval"
    assert by[Action.CREATE_CASE].status == "recommended"  # auto


# ---------------------------------------------- 8. cases needing escalation
def test_scenario_uncertain_and_exposed_escalates():
    d = E.evaluate(DecisionState(
        fraud_probability=0.55, verdict=Verdict.UNCERTAIN,
        independent_signal_count=2, exposure=2500.0,
    ))
    assert "ESCALATE_TO_ANALYST" in acts(d)


# ------------------------------- 9. cases where more work is unnecessary
def test_scenario_stopping_rules():
    stop, why = stopping_decision(0.92, 3, False, 4, 12, True)
    assert stop and "0.85" in why

    stop, why = stopping_decision(0.05, 3, False, 4, 12, True)
    assert stop and "0.15" in why

    stop, why = stopping_decision(0.50, 1, True, 4, 12, True)
    assert stop and "verification response" in why

    stop, why = stopping_decision(0.50, 1, False, 12, 12, True)
    assert stop and "limit" in why

    stop, why = stopping_decision(0.50, 1, False, 3, 12, False)
    assert stop and "unlikely to change" in why

    stop, why = stopping_decision(0.50, 1, False, 3, 12, True)
    assert not stop and why == ""


def test_scenario_borderline_probability_does_not_stop_early():
    stop, _ = stopping_decision(0.60, 1, False, 2, 12, True)
    assert not stop


def test_scenario_high_probability_with_one_signal_does_not_stop():
    """Policy 6 needs *two* independent pieces of evidence to stop."""
    stop, _ = stopping_decision(0.90, 1, False, 2, 12, True)
    assert not stop

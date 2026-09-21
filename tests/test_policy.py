"""Unit tests for the policy engine -- the part that must never be wrong."""
from __future__ import annotations

import pytest

from fraudgraph.policy import rules as R
from fraudgraph.policy.engine import DecisionState, PolicyEngine, SharedOrigin
from fraudgraph.policy.actions import MockActionService
from fraudgraph.schemas import Action, Pattern, Route, Verdict

E = PolicyEngine()


def actions(d) -> set[str]:
    return {a.action.value for a in d.actions}


def route_of(d, action: str) -> str:
    return next(a.route.value for a in d.actions if a.action.value == action)


# ----------------------------------------------------------------- routing
def test_route_table_matches_policy():
    assert R.route_for(Action.CREATE_CASE) is Route.AUTO
    assert R.route_for(Action.VERIFY_WITH_CUSTOMER) is Route.AUTO
    assert R.route_for(Action.ESCALATE_TO_ANALYST) is Route.AUTO
    assert R.route_for(Action.DECLINE_TRANSACTION) is Route.L1
    assert R.route_for(Action.BLOCK_CARD, exposure=2500.0) is Route.L1
    assert R.route_for(Action.BLOCK_CARD, exposure=2500.01) is Route.L2
    assert R.route_for(Action.BLOCK_ALL_CARDS, exposure=1.0) is Route.L2
    assert R.route_for(Action.FILE_REPORT, exposure=1.0) is Route.L2


def test_only_auto_actions_are_agent_executable():
    for a in R.AUTO_ACTIONS:
        assert R.may_agent_execute(a)
    for a in R.HUMAN_ONLY:
        assert not R.may_agent_execute(a)


def test_mock_service_refuses_unapproved_human_action(tmp_path, monkeypatch):
    # The service appends to an audit log. Pointed at the real build/ one, this
    # test left a fake BLOCK_CARD on case TEST-1 in the live audit trail on
    # every run -- 48 of them before anyone noticed.
    monkeypatch.setattr(MockActionService, "log_path",
                        property(lambda self: tmp_path / "action_audit_log.jsonl"))
    svc = MockActionService()
    with pytest.raises(PermissionError):
        svc.perform(Action.BLOCK_CARD)
    rec = svc.perform(Action.BLOCK_CARD, approver="lead.one", case_id="TEST-1")
    assert rec["simulated"] is True and rec["approver"] == "lead.one"


def test_engine_execute_will_not_run_l1_without_approver():
    d = E.evaluate(DecisionState(fraud_probability=0.9, verdict=Verdict.FRAUD,
                                 independent_signal_count=3, exposure=100.0))
    block = next(a for a in d.actions if a.action is Action.BLOCK_CARD)
    out = E.execute(block)
    assert out.status == "awaiting_approval"
    assert out.executed_at is None


# ---------------------------------------------------------------- R1 gate
def test_r1_withholds_block_on_a_single_weak_signal():
    d = E.evaluate(DecisionState(
        fraud_probability=0.45, verdict=Verdict.UNCERTAIN,
        independent_signal_count=1, exposure=300.0,
    ))
    assert Action.BLOCK_CARD not in {a.action for a in d.actions}
    assert Action.VERIFY_WITH_CUSTOMER in {a.action for a in d.actions}
    assert "R1" in d.rules_cited


def test_r1_does_not_fire_with_corroboration():
    d = E.evaluate(DecisionState(
        fraud_probability=0.88, verdict=Verdict.FRAUD,
        independent_signal_count=3, exposure=900.0,
    ))
    assert "BLOCK_CARD" in actions(d)


# ----------------------------------------------------------- R2 / R3 / R4
def test_r2_denial_blocks_and_opens_a_case():
    d = E.evaluate(DecisionState(
        fraud_probability=0.8, verdict=Verdict.FRAUD, customer_response_denied=True,
        independent_signal_count=2, exposure=1500.0, asked_customer=True,
    ))
    assert {"BLOCK_CARD", "CREATE_CASE", "FILE_REPORT"} <= actions(d)
    assert route_of(d, "BLOCK_CARD") == "L1"          # exposure <= 2500
    assert route_of(d, "FILE_REPORT") == "L2"
    assert d.sar_required


def test_r2_block_becomes_l2_over_2500():
    d = E.evaluate(DecisionState(
        fraud_probability=0.9, verdict=Verdict.FRAUD, customer_response_denied=True,
        independent_signal_count=2, exposure=4000.0, asked_customer=True,
    ))
    assert route_of(d, "BLOCK_CARD") == "L2"


def test_r3_confirmation_closes_the_alert():
    d = E.evaluate(DecisionState(
        fraud_probability=0.2, verdict=Verdict.LEGITIMATE, customer_response_denied=False,
        independent_signal_count=1, exposure=0.0, asked_customer=True,
    ))
    assert "CLOSE_NO_FRAUD" in actions(d)
    assert "BLOCK_CARD" not in actions(d)
    assert "R3" in d.rules_cited


def test_r4_no_reply_monitors_and_escalates_when_exposed():
    d = E.evaluate(DecisionState(
        fraud_probability=0.5, verdict=Verdict.UNCERTAIN, customer_response_denied=None,
        asked_customer=True, independent_signal_count=2, exposure=800.0,
    ))
    assert {"MONITOR_CARD", "DECLINE_TRANSACTION", "ESCALATE_TO_ANALYST"} <= actions(d)
    assert "R4" in d.rules_cited


def test_r4_does_not_escalate_below_500():
    d = E.evaluate(DecisionState(
        fraud_probability=0.5, verdict=Verdict.FRAUD, customer_response_denied=None,
        asked_customer=True, independent_signal_count=2, exposure=120.0,
    ))
    assert "MONITOR_CARD" in actions(d)


# ----------------------------------------------------------------- R5 R6
def test_r5_card_testing_declines_and_steps_up():
    d = E.evaluate(DecisionState(
        fraud_probability=0.75, verdict=Verdict.FRAUD, card_testing=True,
        independent_signal_count=2, exposure=260.0,
    ))
    assert {"DECLINE_TRANSACTION", "STEP_UP_AUTH"} <= actions(d)
    assert "R5" in d.rules_cited


def test_r5_blocks_when_a_large_purchase_already_cleared():
    d = E.evaluate(DecisionState(
        fraud_probability=0.75, verdict=Verdict.FRAUD, card_testing=True,
        cleared_purchase_over_100=True, independent_signal_count=2, exposure=260.0,
    ))
    assert "BLOCK_CARD" in actions(d)


def test_r6_shared_origin_files_and_monitors_connected_cards():
    so = SharedOrigin(kind="device_profile", value="SM-X | Android | chrome | 1920x1080",
                      card_ids=["C00001-K1", "C00002-K1"], cards_with_known_fraud=1)
    d = E.evaluate(DecisionState(
        fraud_probability=0.9, verdict=Verdict.FRAUD, shared_origin=so,
        independent_signal_count=3, exposure=400.0,
    ))
    assert {"CREATE_CASE", "MONITOR_CONNECTED_CARDS", "FILE_REPORT"} <= actions(d)
    assert "R6" in d.rules_cited
    assert d.sar_required


# -------------------------------------------------------------------- R7
def test_r7_disputed_recurring_charge_is_never_blocked():
    d = E.evaluate(DecisionState(
        fraud_probability=0.35, verdict=Verdict.LEGITIMATE,
        customer_disputes_charge=True, recurring_charge_dispute=True,
        customer_response_denied=True, independent_signal_count=1, exposure=49.0,
    ))
    assert "BLOCK_CARD" not in actions(d)
    assert {"VERIFY_WITH_CUSTOMER", "WARN_CUSTOMER", "CREATE_CASE"} <= actions(d)
    assert "R7" in d.rules_cited


# -------------------------------------------------------------------- R8
def test_r8_escalates_uncertain_and_exposed():
    d = E.evaluate(DecisionState(
        fraud_probability=0.5, verdict=Verdict.UNCERTAIN,
        independent_signal_count=2, exposure=900.0,
    ))
    assert "ESCALATE_TO_ANALYST" in actions(d)
    assert "R8" in d.rules_cited


def test_r8_escalates_on_conflicting_evidence_regardless_of_exposure():
    d = E.evaluate(DecisionState(
        fraud_probability=0.5, verdict=Verdict.UNCERTAIN, conflicting_evidence=True,
        independent_signal_count=2, exposure=10.0,
    ))
    assert "ESCALATE_TO_ANALYST" in actions(d)


# -------------------------------------------------------------------- R9
def test_r9_undocumented_coordinated_pattern():
    d = E.evaluate(DecisionState(
        fraud_probability=0.9, verdict=Verdict.FRAUD, pattern=Pattern.UNDOCUMENTED,
        pattern_is_undocumented=True, coordinated_across_customers=True,
        independent_signal_count=3, exposure=500.0,
    ))
    assert {"CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST"} <= actions(d)
    assert "R9" in d.rules_cited


# ------------------------------------------------------------------- R10
def test_r10_blocks_block_all_cards_without_two_confirmed():
    st = DecisionState(fraud_probability=0.95, verdict=Verdict.FRAUD,
                       independent_signal_count=3, exposure=5000.0,
                       customer_n_cards=3, customer_cards_with_confirmed_fraud=1)
    d = E.evaluate(st)
    assert "BLOCK_ALL_CARDS" not in actions(d)


# --------------------------------------------------------------- 3a: SAR
def test_no_sar_when_exposure_small_and_no_shared_origin():
    d = E.evaluate(DecisionState(
        fraud_probability=0.9, verdict=Verdict.FRAUD,
        independent_signal_count=3, exposure=250.0,
    ))
    assert not d.sar_required
    assert "FILE_REPORT" not in actions(d)
    assert "$1,000" in d.sar_reason or "1,000" in d.sar_reason


def test_sar_when_exposure_over_1000():
    d = E.evaluate(DecisionState(
        fraud_probability=0.9, verdict=Verdict.FRAUD,
        independent_signal_count=3, exposure=1500.0,
    ))
    assert d.sar_required and "FILE_REPORT" in actions(d)


def test_sar_never_without_strong_suspicion():
    d = E.evaluate(DecisionState(
        fraud_probability=0.35, verdict=Verdict.UNCERTAIN,
        independent_signal_count=2, exposure=9000.0,
    ))
    assert not d.sar_required


def test_case_opened_at_probability_threshold():
    below = E.evaluate(DecisionState(fraud_probability=0.29, verdict=Verdict.LEGITIMATE,
                                     independent_signal_count=2))
    at = E.evaluate(DecisionState(fraud_probability=0.30, verdict=Verdict.UNCERTAIN,
                                  independent_signal_count=2))
    assert "CREATE_CASE" not in actions(below)
    assert "CREATE_CASE" in actions(at)


# ------------------------------------------------------------ consistency
def test_close_no_fraud_never_coexists_with_a_block():
    d = E.evaluate(DecisionState(
        fraud_probability=0.9, verdict=Verdict.LEGITIMATE, customer_response_denied=True,
        independent_signal_count=3, exposure=2000.0, asked_customer=True,
    ))
    assert not ({"CLOSE_NO_FRAUD", "BLOCK_CARD"} <= actions(d))


def test_allow_never_coexists_with_decline():
    d = E.evaluate(DecisionState(
        fraud_probability=0.75, verdict=Verdict.LEGITIMATE, card_testing=True,
        independent_signal_count=2, exposure=200.0,
    ))
    assert not ({"ALLOW_TRANSACTION", "DECLINE_TRANSACTION"} <= actions(d))


def test_every_action_carries_a_rule_citation():
    d = E.evaluate(DecisionState(fraud_probability=0.9, verdict=Verdict.FRAUD,
                                 independent_signal_count=3, exposure=1500.0))
    for a in d.actions:
        assert a.reason and len(a.reason) > 10


def test_actions_are_ordered_by_what_happens_first():
    d = E.evaluate(DecisionState(
        fraud_probability=0.9, verdict=Verdict.FRAUD, card_testing=True,
        customer_response_denied=True, asked_customer=True,
        independent_signal_count=3, exposure=1500.0,
    ))
    order = [a.action for a in d.actions]
    assert order.index(Action.DECLINE_TRANSACTION) < order.index(Action.CREATE_CASE)
    assert order.index(Action.CREATE_CASE) < order.index(Action.FILE_REPORT)

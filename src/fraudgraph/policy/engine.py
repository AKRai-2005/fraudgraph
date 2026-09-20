"""The policy engine: evidence state in, ordered actions with routes out.

Deterministic by design.  The LLM may describe what the engine decided; it may
not decide it, and it cannot cause an ``L1``/``L2`` action to be executed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas import Action, ActionRecommendation, Pattern, Route, Verdict
from . import rules as R


@dataclass
class SharedOrigin:
    """A concrete shared element linking this card to others (policy R6)."""

    kind: str          # "device_profile" | "billing_region" | "recipient_email"
    value: str
    card_ids: list[str] = field(default_factory=list)
    cards_with_known_fraud: int = 0

    @property
    def describes_ring(self) -> bool:
        return len(self.card_ids) >= 2


@dataclass
class DecisionState:
    """Everything the policy engine is allowed to look at."""

    phase: str = "initial"                 # "initial" | "final"
    fraud_probability: float = 0.0
    verdict: Verdict = Verdict.UNCERTAIN
    exposure: float = 0.0
    independent_signal_count: int = 0
    conflicting_evidence: bool = False
    pattern: Pattern = Pattern.NONE
    pattern_is_undocumented: bool = False
    coordinated_across_customers: bool = False

    # trigger context
    trigger_type: str = "risk_score"       # risk_score | customer_report | analyst_request
    customer_disputes_charge: bool = False

    # evidence-request outcome: True denied, False confirmed, None no reply/not asked
    customer_response_denied: bool | None = None
    asked_customer: bool = False

    # detector outputs the policy names directly
    card_testing: bool = False
    cleared_purchase_over_100: bool = False
    recurring_charge_dispute: bool = False
    shared_origin: SharedOrigin | None = None

    # blocking guards
    customer_n_cards: int = 1
    customer_cards_with_confirmed_fraud: int = 0
    credentials_confirmed_compromised: bool = False

    # pending authorisation exists on the flagged transaction
    has_pending_authorisation: bool = True


@dataclass
class PolicyDecision:
    actions: list[ActionRecommendation]
    rules_cited: list[str]
    sar_required: bool
    sar_reason: str
    case_required: bool
    notes: list[str] = field(default_factory=list)
    blocked_actions: list[str] = field(default_factory=list)


class PolicyEngine:
    """Applies Fraud Policy v1.0. One method, no hidden state."""

    def evaluate(self, s: DecisionState) -> PolicyDecision:
        chosen: dict[Action, str] = {}
        cited: list[str] = []
        notes: list[str] = []
        blocked: list[str] = []

        def add(action: Action, reason: str) -> None:
            # first reason wins so the earliest-applying rule is the citation
            chosen.setdefault(action, reason)

        def cite(rule: str) -> None:
            if rule not in cited:
                cited.append(rule)

        p = float(s.fraud_probability)
        single_signal = s.independent_signal_count <= 1

        # ---------------------------------------------------------- R7 first
        # A disputed charge that matches the cardholder's own recurring pattern
        # is explicitly *not* a block, so it must be considered before R2.
        r7_applies = s.recurring_charge_dispute and s.customer_disputes_charge
        if r7_applies:
            cite("R7")
            add(Action.VERIFY_WITH_CUSTOMER,
                "R7: disputed charge matches this card's own recurring pattern; confirm with the "
                "cardholder rather than blocking")
            add(Action.WARN_CUSTOMER,
                "R7: send a recurring-charge reminder so the cardholder can recognise the merchant")
            add(Action.CREATE_CASE, "R7: a disputed charge opens a case even when it looks legitimate")

        # ---------------------------------------------------- R5 card testing
        if s.card_testing:
            cite("R5")
            if s.has_pending_authorisation:
                add(Action.DECLINE_TRANSACTION,
                    "R5: testing sequence observed on this card; decline the flagged authorisation")
            add(Action.STEP_UP_AUTH,
                "R5: require step-up authentication before any further activity on this card")
            if s.cleared_purchase_over_100:
                add(Action.BLOCK_CARD,
                    "R5: a purchase over $100 has already cleared after the testing sequence")

        # ------------------------------------------- customer response branch
        if s.customer_response_denied is True:
            cite("R2")
            if r7_applies:
                # R7 is explicit: "Do not block." It overrides R2's block for a
                # dispute that matches the cardholder's own recurring pattern.
                notes.append(
                    "R2's block withheld under R7: the disputed charge matches this card's own "
                    "established recurring pattern"
                )
            else:
                add(Action.BLOCK_CARD,
                    f"R2: cardholder denies the transaction; exposure ${s.exposure:,.2f} "
                    f"{'exceeds' if s.exposure > R.BLOCK_CARD_L2_EXPOSURE else 'is within'} "
                    f"the ${R.BLOCK_CARD_L2_EXPOSURE:,.0f} L1 limit")
            add(Action.CREATE_CASE, "R2: confirmed unauthorised use opens a case")
        elif s.customer_response_denied is False:
            cite("R3")
            if not r7_applies:
                add(Action.CLOSE_NO_FRAUD,
                    "R3: cardholder confirms the transaction; close the alert as legitimate")
            else:
                add(Action.CLOSE_NO_FRAUD,
                    "R3: cardholder confirms the recurring charge; close as legitimate and note the "
                    "confirmation in the case file")
        elif s.asked_customer and s.customer_response_denied is None:
            cite("R4")
            add(Action.MONITOR_CARD, "R4: no cardholder reply within 24 hours")
            if s.has_pending_authorisation:
                add(Action.DECLINE_TRANSACTION,
                    "R4: decline pending authorisations while the cardholder is unreachable")
            if s.exposure > R.R4_ESCALATION_EXPOSURE:
                add(Action.ESCALATE_TO_ANALYST,
                    f"R4: no reply and exposure ${s.exposure:,.2f} exceeds "
                    f"${R.R4_ESCALATION_EXPOSURE:,.0f}")

        # --------------------------------------------------------- R1 gating
        # "If the case rests on a single signal ... and your assessed fraud
        # probability is below 0.70, recommend VERIFY_WITH_CUSTOMER or
        # STEP_UP_AUTH before any block."  A cardholder's denial with nothing
        # else behind it *is* a single signal, so R1 gates it too: R2's block
        # waits until the denial is corroborated or the probability rises.
        if single_signal and p < R.R1_PROBABILITY_THRESHOLD:
            cite("R1")
            # Ask only while the question is still open. Once the cardholder has
            # answered, repeating the request in the final recommendation would
            # contradict the answer we are acting on.
            if not r7_applies and s.customer_response_denied is None and not s.asked_customer:
                add(Action.VERIFY_WITH_CUSTOMER,
                    f"R1: assessed probability {p:.2f} rests on "
                    f"{s.independent_signal_count} signal; verify before any block")
                add(Action.STEP_UP_AUTH,
                    "R1: require step-up authentication on further activity while verification is "
                    "outstanding")
            if Action.BLOCK_CARD in chosen:
                del chosen[Action.BLOCK_CARD]
                blocked.append(
                    f"BLOCK_CARD withheld under R1: the case rests on a single signal and the "
                    f"assessed probability is {p:.2f}, below 0.70"
                )

        # ------------------------------------------------------- R6 / R9 ring
        so = s.shared_origin
        if so and so.describes_ring and p >= 0.5:
            cite("R6")
            add(Action.CREATE_CASE,
                f"R6: shared {so.kind.replace('_', ' ')} '{so.value}' links this card to "
                f"{len(so.card_ids)} others")
            add(Action.MONITOR_CONNECTED_CARDS,
                f"R6: every card sharing {so.kind.replace('_', ' ')} '{so.value}' goes under "
                "monitoring")
            add(Action.FILE_REPORT,
                f"R6: fraud across multiple cards from a shared {so.kind.replace('_', ' ')}")
        if s.pattern_is_undocumented and s.coordinated_across_customers and p >= 0.5:
            cite("R9")
            add(Action.CREATE_CASE, "R9: activity fits none of the documented typologies")
            add(Action.FILE_REPORT,
                "R9: coordinated or repeated abuse across customers outside the known typologies")
            add(Action.ESCALATE_TO_ANALYST,
                "R9: an undocumented typology needs human review before it becomes a rule")

        # ---------------------------------------------------------- R8 escalate
        if (s.verdict is Verdict.UNCERTAIN and s.exposure > R.R8_ESCALATION_EXPOSURE) or \
                s.conflicting_evidence:
            cite("R8")
            reason = (
                f"R8: verdict uncertain with exposure ${s.exposure:,.2f} above "
                f"${R.R8_ESCALATION_EXPOSURE:,.0f}"
                if s.verdict is Verdict.UNCERTAIN and s.exposure > R.R8_ESCALATION_EXPOSURE
                else "R8: the evidence conflicts"
            )
            add(Action.ESCALATE_TO_ANALYST, reason)

        # ------------------------------------------------ section 3a: a case
        if p >= R.CASE_PROBABILITY_THRESHOLD:
            cite("3a")
            add(Action.CREATE_CASE,
                f"3a: assessed fraud probability {p:.2f} reaches the {R.CASE_PROBABILITY_THRESHOLD:.2f} "
                "case threshold")
        if s.asked_customer:
            cite("3a")
            add(Action.CREATE_CASE, "3a: a case is opened whenever evidence is requested")
        if s.customer_disputes_charge:
            cite("3a")
            add(Action.CREATE_CASE, "3a: the customer disputes this charge")

        # ------------------------------------------------- strong fraud, no ask
        if p >= R.STOP_HIGH and s.customer_response_denied is None and not r7_applies:
            add(Action.BLOCK_CARD,
                f"R2 by analogy: assessed probability {p:.2f} with "
                f"{s.independent_signal_count} independent signals supports blocking the card; "
                f"exposure ${s.exposure:,.2f}")
            add(Action.MONITOR_CARD, "monitor the card while reissue is arranged")

        # ----------------------------------------------------- legitimate path
        if s.verdict is Verdict.LEGITIMATE and not chosen.get(Action.BLOCK_CARD):
            if not s.customer_disputes_charge:
                add(Action.ALLOW_TRANSACTION,
                    "no graph signal departs from this card's established behaviour")
            add(Action.CLOSE_NO_FRAUD,
                "R3/3a: the evidence supports legitimate activity; close the alert")

        # ------------------------------------------------------------- R10 guard
        if Action.BLOCK_ALL_CARDS in chosen:
            allowed = (
                s.customer_cards_with_confirmed_fraud >= 2
                or s.credentials_confirmed_compromised
            )
            if not allowed:
                del chosen[Action.BLOCK_ALL_CARDS]
                blocked.append(
                    "BLOCK_ALL_CARDS withheld under R10: fewer than two of the customer's cards "
                    "show confirmed fraud and credentials are not confirmed compromised"
                )
                cite("R10")

        # ------------------------------------------------------------ the SAR
        strongly_suspected = p >= 0.70 or s.customer_response_denied is True
        sar_grounds: list[str] = []
        if strongly_suspected:
            if s.exposure > R.SAR_EXPOSURE_THRESHOLD:
                sar_grounds.append(
                    f"exposure ${s.exposure:,.2f} exceeds ${R.SAR_EXPOSURE_THRESHOLD:,.0f}"
                )
            if so and so.describes_ring:
                sar_grounds.append(
                    f"the activity connects to a shared {so.kind.replace('_', ' ')} spanning "
                    f"{len(so.card_ids) + 1} cards"
                )
            if s.pattern_is_undocumented and s.coordinated_across_customers:
                sar_grounds.append("the pattern is coordinated and fits no documented typology (R9)")
        sar_required = bool(sar_grounds)
        if sar_required:
            add(Action.FILE_REPORT, "3a: " + "; ".join(sar_grounds))
            sar_reason = (
                "3a: fraud is "
                + ("confirmed by the cardholder" if s.customer_response_denied else
                   f"strongly suspected (probability {p:.2f})")
                + " and " + "; ".join(sar_grounds)
            )
        else:
            if Action.FILE_REPORT in chosen:
                # R6/R9 asked for a filing; keep it and state the ground
                sar_required = True
                sar_reason = chosen[Action.FILE_REPORT]
            elif not strongly_suspected:
                sar_reason = (
                    f"3a: fraud is not confirmed or strongly suspected (probability {p:.2f}), so no "
                    "regulatory filing is due. A case may still be opened."
                )
            else:
                sar_reason = (
                    f"3a: fraud is strongly suspected but exposure ${s.exposure:,.2f} is below "
                    f"${R.SAR_EXPOSURE_THRESHOLD:,.0f}, the activity does not connect to a shared "
                    "device profile, region cluster or another customer's fraud, and the pattern is "
                    "documented. Case only."
                )

        # ------------------------------------------------- consistency guards
        if Action.CLOSE_NO_FRAUD in chosen and (
            Action.BLOCK_CARD in chosen or Action.FILE_REPORT in chosen
        ):
            del chosen[Action.CLOSE_NO_FRAUD]
            notes.append("CLOSE_NO_FRAUD dropped: incompatible with a block or a filing")
        if Action.ESCALATE_TO_ANALYST in chosen:
            # Handing the case to a human and closing it in the same breath is a
            # contradiction: if the evidence conflicts, the analyst decides.
            for a in (Action.CLOSE_NO_FRAUD, Action.ALLOW_TRANSACTION):
                if a in chosen:
                    del chosen[a]
                    notes.append(
                        f"{a.value} dropped: the case is being escalated, so the disposition is "
                        "the analyst's to make"
                    )
        if Action.ALLOW_TRANSACTION in chosen and (
            Action.DECLINE_TRANSACTION in chosen or Action.BLOCK_CARD in chosen
        ):
            del chosen[Action.ALLOW_TRANSACTION]
            notes.append("ALLOW_TRANSACTION dropped: incompatible with a decline or a block")
        if not sar_required and Action.FILE_REPORT in chosen:
            del chosen[Action.FILE_REPORT]
        if not chosen:
            add(Action.MONITOR_CARD, "no action is indicated yet; keep the card under monitoring")

        ordered = [a for a in R.ACTION_ORDER if a in chosen]
        recs = [
            ActionRecommendation(
                action=a,
                route=R.route_for(a, s.exposure),
                reason=chosen[a],
                status="recommended" if R.may_agent_execute(a) else "awaiting_approval",
            )
            for a in ordered
        ]
        return PolicyDecision(
            actions=recs,
            rules_cited=cited,
            sar_required=sar_required,
            sar_reason=sar_reason,
            case_required=Action.CREATE_CASE in chosen,
            notes=notes,
            blocked_actions=blocked,
        )

    # ------------------------------------------------------------ execution
    def execute(
        self, rec: ActionRecommendation, approver: str | None = None
    ) -> ActionRecommendation:
        """Execute an action against the mock action service.

        ``auto`` actions may be executed by the agent.  ``L1``/``L2`` actions
        require an explicit approver and are otherwise refused -- the agent
        cannot talk its way past this.
        """
        from .actions import MockActionService

        out = rec.model_copy(deep=True)
        if not R.may_agent_execute(rec.action) and not approver:
            out.status = "awaiting_approval"
            return out
        result = MockActionService().perform(rec.action, approver=approver)
        out.status = "simulated"
        out.simulated = True
        out.executed_at = result["at"]
        out.executed_by = approver or "agent"
        return out


def stopping_decision(
    probability: float,
    independent_signals: int,
    customer_answered: bool,
    steps_used: int,
    max_steps: int,
    new_evidence_last_step: bool,
) -> tuple[bool, str]:
    """Policy section 6. Returns (should_stop, reason)."""
    if customer_answered:
        return True, (
            "A verification response settled the question, so further evidence gathering would "
            "not change the decision (policy 6)."
        )
    if probability >= R.STOP_HIGH and independent_signals >= 2:
        return True, (
            f"Fraud probability {probability:.2f} is at or above {R.STOP_HIGH} with "
            f"{independent_signals} independent pieces of evidence (policy 6)."
        )
    if probability <= R.STOP_LOW and independent_signals >= 2:
        return True, (
            f"Fraud probability {probability:.2f} is at or below {R.STOP_LOW} with "
            f"{independent_signals} independent findings supporting legitimate use (policy 6)."
        )
    if steps_used >= max_steps:
        return True, (
            f"Reached the configured investigation limit of {max_steps} steps; the remaining "
            "uncertainty is recorded rather than pursued further (policy 6)."
        )
    if not new_evidence_last_step:
        return True, (
            "The last step returned no evidence that changed the assessment, so further steps are "
            "unlikely to change the decision (policy 6)."
        )
    return False, ""

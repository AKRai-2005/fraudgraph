"""The bank Fraud Policy v1.0 as structured, testable data.

Transcribed from the Fraud Policy section of the dataset README.  Nothing here
is invented: action names, routes and rule numbers are the ones the answer files
must use.  The LLM never decides any of this.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..schemas import Action, Route

# ---------------------------------------------------------------- section 2
AUTO_ACTIONS: frozenset[Action] = frozenset({
    Action.ALLOW_TRANSACTION,
    Action.MONITOR_CARD,
    Action.MONITOR_CONNECTED_CARDS,
    Action.WARN_CUSTOMER,
    Action.VERIFY_WITH_CUSTOMER,
    Action.STEP_UP_AUTH,
    Action.GENERATE_REPORT,
    Action.CREATE_CASE,
    Action.ESCALATE_TO_ANALYST,
    Action.CLOSE_NO_FRAUD,
})

#: Actions the agent may never execute itself, whatever it recommends.
HUMAN_ONLY: frozenset[Action] = frozenset({
    Action.DECLINE_TRANSACTION,
    Action.BLOCK_CARD,
    Action.BLOCK_ALL_CARDS,
    Action.FILE_REPORT,
})

BLOCK_CARD_L2_EXPOSURE = 2500.0   # > $2,500 -> L2, otherwise L1
SAR_EXPOSURE_THRESHOLD = 1000.0   # exposure over $1,000 supports a filing
R4_ESCALATION_EXPOSURE = 500.0
R8_ESCALATION_EXPOSURE = 500.0
CASE_PROBABILITY_THRESHOLD = 0.30  # section 3a
R1_PROBABILITY_THRESHOLD = 0.70
R5_CLEARED_PURCHASE = 100.0
STOP_HIGH = 0.85
STOP_LOW = 0.15


def route_for(action: Action, exposure: float = 0.0) -> Route:
    """Approval route, exactly as the policy's routing table states it."""
    if action in AUTO_ACTIONS:
        return Route.AUTO
    if action is Action.DECLINE_TRANSACTION:
        return Route.L1
    if action is Action.BLOCK_CARD:
        return Route.L2 if float(exposure) > BLOCK_CARD_L2_EXPOSURE else Route.L1
    if action in (Action.BLOCK_ALL_CARDS, Action.FILE_REPORT):
        return Route.L2
    raise ValueError(f"no route defined for {action}")


def may_agent_execute(action: Action) -> bool:
    """Only ``auto`` actions may be executed by the agent (policy section 2)."""
    return action in AUTO_ACTIONS


@dataclass(frozen=True)
class RuleText:
    id: str
    summary: str


RULES: dict[str, RuleText] = {
    "R1": RuleText("R1", "Verify before blocking on a weak signal: a single signal with assessed "
                         "probability below 0.70 gets VERIFY_WITH_CUSTOMER or STEP_UP_AUTH first."),
    "R2": RuleText("R2", "Customer denies: BLOCK_CARD and CREATE_CASE; add FILE_REPORT if exposure "
                         "exceeds $1,000 or the case connects to a shared device profile or another "
                         "card's fraud."),
    "R3": RuleText("R3", "Customer confirms: CLOSE_NO_FRAUD, noting the confirmation."),
    "R4": RuleText("R4", "No reply within 24 hours: MONITOR_CARD and DECLINE_TRANSACTION for pending "
                         "authorisations; escalate if exposure exceeds $500."),
    "R5": RuleText("R5", "Card testing: DECLINE_TRANSACTION and STEP_UP_AUTH; BLOCK_CARD if a purchase "
                         "over $100 has already cleared."),
    "R6": RuleText("R6", "Shared origin: name the shared element, CREATE_CASE, FILE_REPORT and "
                         "MONITOR_CONNECTED_CARDS for every card that shares it."),
    "R7": RuleText("R7", "Disputed but legitimate: CREATE_CASE, VERIFY_WITH_CUSTOMER and WARN_CUSTOMER. "
                         "Do not block."),
    "R8": RuleText("R8", "Escalate when uncertain and exposed: verdict uncertain with exposure over $500, "
                         "or conflicting evidence, gets ESCALATE_TO_ANALYST."),
    "R9": RuleText("R9", "Undocumented patterns: CREATE_CASE, FILE_REPORT and ESCALATE_TO_ANALYST, "
                         "described in the agent's own words."),
    "R10": RuleText("R10", "Never BLOCK_ALL_CARDS unless at least two of the customer's cards show "
                           "confirmed fraud or credentials are confirmed compromised."),
    "3a": RuleText("3a", "Open a case whenever fraud probability reaches 0.30, whenever evidence is "
                         "requested, or whenever a customer disputes a charge. A report is a separate, "
                         "external filing."),
    "6": RuleText("6", "Stop when probability is at or above 0.85 or at or below 0.15 with at least two "
                       "independent pieces of evidence, when a verification response settles the "
                       "question, or when further steps would not change the decision."),
}

#: Canonical ordering -- "order them by what happens first".
ACTION_ORDER: tuple[Action, ...] = (
    Action.DECLINE_TRANSACTION,
    Action.STEP_UP_AUTH,
    Action.BLOCK_CARD,
    Action.BLOCK_ALL_CARDS,
    Action.VERIFY_WITH_CUSTOMER,
    Action.MONITOR_CARD,
    Action.MONITOR_CONNECTED_CARDS,
    Action.WARN_CUSTOMER,
    Action.CREATE_CASE,
    Action.FILE_REPORT,
    Action.ESCALATE_TO_ANALYST,
    Action.GENERATE_REPORT,
    Action.ALLOW_TRANSACTION,
    Action.CLOSE_NO_FRAUD,
)

"""Controlled evidence requests.

The dataset README is explicit: *"Customer and analyst replies are not provided.
If your agent asks the customer or requests step-up authentication, simulate the
response in your own system and record what you assumed."*

So nothing here claims a customer said anything.  Each request records why it
was made, what was asked, who it went to, and the **stated assumption** used in
place of a real reply, together with the rule that produced that assumption.
Responses are never invented ad hoc: they follow the published policy below.
"""
from __future__ import annotations

from ..schemas import EvidenceRequest

#: Documented simulation policy.  Stated in the case file and in docs/.
SIMULATION_POLICY = (
    "No live customer or analyst channel is connected. Responses are simulated by a fixed, "
    "published rule rather than invented per case: (a) when the alert itself is a customer "
    "dispute, the cardholder's denial is taken from the trigger, not assumed, except where the "
    "charge matches the card's own recurring pattern, in which case the validation asks the "
    "cardholder to check that merchant and the simulated reply is recognition; (b) otherwise the "
    "simulated reply follows the graph evidence -- denial when the assessed probability is at or "
    "above 0.65, confirmation at or below 0.40, and no reply within the policy window in between, "
    "which exercises rule R4. The bands are deliberately asymmetric: a denial is only assumed "
    "where the graph already supports it, so a simulated reply cannot manufacture a verdict the "
    "evidence does not carry."
)

DENY_THRESHOLD = 0.65
CONFIRM_THRESHOLD = 0.40


class EvidenceRequestSimulator:
    """Produces evidence requests with explicitly-simulated responses."""

    policy = SIMULATION_POLICY

    def request(self, kind: str, after_step: int, features, risk, findings, trigger) -> EvidenceRequest:
        p = risk.agent_fraud_probability
        recurring = any(f.name == "recurring_charge" and f.matched for f in findings)
        disputed = trigger.trigger_type == "customer_report"

        if disputed and recurring:
            info = (
                f"Ask the cardholder to check whether the ${features.amount:,.2f} charge is their "
                f"recurring payment to this merchant, which has been billed "
                f"{features.same_amount_before} times before at roughly "
                f"{features.recurring_cadence_days}-day intervals."
            )
            assumed = (
                "Simulated response: shown the earlier identical charges, the cardholder recognises "
                "the payment as their own recurring subscription and withdraws the dispute."
            )
            basis = "simulation policy (a): dispute against the card's own recurring pattern"
            status, reason = "simulated_response", (
                "R7: the disputed charge matches an established recurring pattern on this card, so "
                "the cardholder is asked to confirm the merchant before any block is considered."
            )
        elif disputed:
            info = (
                "Confirm with the cardholder that they did not authorise the flagged transaction "
                "and that the card is still in their possession."
            )
            assumed = (
                "Taken from the trigger, not assumed: the cardholder has already stated in writing "
                "that they did not make this purchase."
            )
            basis = "simulation policy (a): the denial is the alert itself"
            status, reason = "simulated_response", (
                "R2: the cardholder disputes the charge; the denial is confirmed before acting on it."
            )
        elif p >= DENY_THRESHOLD:
            info = (
                f"Ask the cardholder whether they made the ${features.amount:,.2f} "
                f"{features.channel.replace('_', '-')} transaction on {features.ts}."
            )
            assumed = (
                "Simulated response: the cardholder states they did not make this transaction and "
                "still holds the card."
            )
            basis = f"simulation policy (b): assessed probability {p:.2f} is at or above {DENY_THRESHOLD}"
            status, reason = "simulated_response", (
                f"R1: the assessed probability is {p:.2f} and the case needs the cardholder's "
                "account before any block."
            )
        elif p <= CONFIRM_THRESHOLD:
            info = (
                f"Ask the cardholder whether they made the ${features.amount:,.2f} "
                f"{features.channel.replace('_', '-')} transaction on {features.ts}."
            )
            assumed = (
                "Simulated response: the cardholder confirms the transaction as their own."
            )
            basis = f"simulation policy (b): assessed probability {p:.2f} is at or below {CONFIRM_THRESHOLD}"
            status, reason = "simulated_response", (
                f"R1: the assessed probability is {p:.2f} on limited signal; confirm rather than act."
            )
        else:
            info = (
                f"Ask the cardholder whether they made the ${features.amount:,.2f} "
                f"{features.channel.replace('_', '-')} transaction on {features.ts}, and require "
                "step-up authentication on further activity meanwhile."
            )
            assumed = (
                "Simulated response: no reply was received within the 24-hour policy window."
            )
            basis = (
                f"simulation policy (b): assessed probability {p:.2f} falls between "
                f"{CONFIRM_THRESHOLD} and {DENY_THRESHOLD}, the genuinely ambiguous band"
            )
            status, reason = "no_response", (
                f"R1: probability {p:.2f} rests on {risk.independent_signal_count} signal(s); "
                "verification is required before any block."
            )

        return EvidenceRequest(
            type="customer_validation" if kind == "customer_validation" else kind,  # type: ignore[arg-type]
            asked_after_step=after_step,
            reason=reason,
            information_required=info,
            recipient=f"cardholder {features.customer_id} (simulated channel)",
            status=status,  # type: ignore[arg-type]
            assumed_response=assumed,
            assumption_basis=f"{basis}. {SIMULATION_POLICY}",
        )

    @staticmethod
    def denied_flag(req: EvidenceRequest) -> bool | None:
        """True denied, False confirmed, None no reply."""
        if req.status == "no_response":
            return None
        text = req.assumed_response.lower()
        if "did not make" in text or "did not authorise" in text:
            return True
        if "recognises" in text or "confirms" in text or "withdraws" in text:
            return False
        return None

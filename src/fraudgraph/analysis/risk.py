"""Risk and uncertainty assessment.

The README asks for six quantities to be kept apart, and they are:

1. ``bank_risk_score``        -- the bank model's number, passed through untouched
2. ``graph_indicators``       -- what the graph actually showed, per signal
3. ``historical_evidence``    -- what case memory contributed
4. ``agent_fraud_probability``-- the agent's own assessment
5. ``confidence``             -- how well-supported that assessment is
6. ``uncertainty_notes``      -- what remains unresolved

The probability is a logistic combination of detector strengths.  Coefficients
are **fitted on the 5,565 closed cases** by ``fraudgraph.analysis.calibrate``
and stored in ``build/risk_model.json``.  If that file is absent the documented
fallback weights below are used and the calibration note says so.

Prior shift
-----------
The closed-case history is 84% confirmed fraud, because it is the set of alerts
the bank chose to investigate July-October.  The dataset README states that for
the exam pack "half the cases are legitimate".  A probability fitted on the
history is therefore re-based from the fitted prior to a deployment prior with a
log-odds offset, which is recorded in ``calibration_note``.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass

from ..config import PATHS
from ..schemas import Pattern, PatternFinding, RiskAssessment
from .features import Features

# Documented fallback weights (log-odds per unit strength).  Used only when no
# fitted model is present; calibrate.py replaces them with measured values.
FALLBACK_WEIGHTS: dict[str, float] = {
    "card_testing": 2.6,
    "sub_threshold_structuring": 2.8,
    "shared_device_ring": 2.9,
    "out_of_region_use": 1.5,
    "cnp_new_device": 1.3,
    "cnp_fraud": 1.2,
    "account_takeover": 1.4,
    "recurring_charge": -2.4,
    "consistent_with_history": -1.9,
}
FALLBACK_INTERCEPT = -2.20
FALLBACK_EXTRA: dict[str, float] = {
    "prior_fraud_on_card": 0.35,
    "corroboration": 0.55,
    "customer_dispute": 3.20,
    # raw discriminators that separate the *cleared* shapes from the fraud
    # shapes: travel vs a cloned card, a new phone vs a taken-over account
    "home_activity_continues": 0.80,
    "region_trip_shape": -0.90,
    "burst_excess": 0.45,
    "amt_over_max": 0.35,
    "thin_history": -0.40,
    "device_marked_new": 0.20,
    "anonymous_proxy": 0.60,
    "match_flag_anomaly": 0.45,
    "ring_scale": 0.90,
}

#: Why the bank's own risk score is **not** an input to the probability.
#:
#: Fitting it on the closed history produces a large *negative* coefficient,
#: because every one of the 900 cleared cases was a high-scoring model alert
#: while the 4,665 confirmed frauds were overwhelmingly customer reports with
#: low scores.  That coefficient would encode which alerts the bank chose to
#: investigate, not whether fraud occurred.  The score is therefore reported
#: alongside the assessment as a separate quantity -- which is what the dataset
#: README asks for -- and never folded into it.
BANK_SCORE_EXCLUSION_NOTE = (
    "The bank model's risk_score is reported but is not an input to the agent's probability. "
    "Fitted on the closed history it takes a large negative coefficient (-5.5), because cleared "
    "cases are by construction high-scoring model alerts and confirmed frauds are customer "
    "reports. That reflects alert selection, not fraud."
)

#: Why ``customer_dispute`` carries the weight it does, and why it is capped.
#:
#: In all 5,565 closed investigations every customer dispute that was
#: investigated was confirmed as fraud, and all 900 cleared cases originated
#: from a model score rather than a dispute.  A weight fitted on that history
#: would be unbounded, so it is fixed at +2.6 log-odds -- strong evidence, not
#: proof -- and the exculpatory detectors are allowed to offset it.  That is
#: precisely the situation policy R7 exists for.
DISPUTE_WEIGHT_NOTE = (
    "customer_dispute is fixed at +2.6 log-odds rather than fitted: every disputed case in the "
    "closed history was confirmed fraud, so the fitted weight would be unbounded. Capping it lets "
    "the recurring-charge detector overturn a dispute, which is what policy R7 requires."
)

# The history's base rate, measured: 4665 confirmed of 5565 closed cases.
HISTORY_PRIOR = 4665 / 5565
# The exam's stated balance ("Half the cases are legitimate").
DEPLOYMENT_PRIOR = 0.50

#: The prior belongs to the *trigger*, not to the model.
#:
#: Measured on the shipped history: all 900 cleared investigations began as a
#: model score, and every investigated customer dispute was confirmed as fraud.
#: The dataset README says the same thing from the other side -- "Above 0.7,
#: most flagged transactions turn out to be legitimate" -- and caps how far a
#: dispute can carry a case by stating that half the exam is legitimate.
#:
#: So a single global intercept is the wrong shape: an alert that exists because
#: a model scored it and an alert that exists because the cardholder complained
#: start from very different places, before any graph evidence is gathered.
#: These three numbers are that starting place, in log-odds.
TRIGGER_PRIOR: dict[str, float] = {
    # p = 0.17 with no corroborating evidence
    "risk_score": -1.60,
    # p = 0.73 with no corroborating evidence; the recurring-charge detector
    # (policy R7) can still overturn it, which is the case R7 exists for
    "customer_report": +1.00,
    # an analyst has already looked and escalated: genuinely open
    "analyst_request": 0.0,
}
DEFAULT_TRIGGER_PRIOR = -0.40

TRIGGER_PRIOR_NOTE = (
    "The intercept is a per-trigger prior rather than one global constant. Measured on the "
    "shipped history, all 900 cleared investigations began as a model score and every "
    "investigated customer dispute was confirmed fraud; the README states that most high scores "
    "are legitimate and that half the exam pack is. A model-score alert therefore starts at "
    "p=0.17, a customer dispute at p=0.73 and an analyst request at p=0.50, and graph evidence "
    "moves it from there."
)

STRONG_SIGNAL_NAMES = {
    "card_testing", "sub_threshold_structuring", "shared_device_ring",
    "out_of_region_use", "cnp_new_device", "cnp_fraud", "account_takeover",
}


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


@dataclass
class RiskModel:
    weights: dict[str, float]
    intercept: float
    extra: dict[str, float]
    source: str
    fitted_prior: float = HISTORY_PRIOR
    deployment_prior: float = DEPLOYMENT_PRIOR
    metrics: dict | None = None

    @classmethod
    def load(cls) -> "RiskModel":
        path = PATHS.build / "risk_model.json"
        if path.exists():
            try:
                blob = json.loads(path.read_text())
                return cls(
                    weights=blob["weights"],
                    intercept=float(blob["intercept"]),
                    extra=blob.get("extra", {}),
                    source=f"fitted on {blob.get('n_train', '?')} closed cases",
                    fitted_prior=float(blob.get("fitted_prior", HISTORY_PRIOR)),
                    deployment_prior=float(blob.get("deployment_prior", DEPLOYMENT_PRIOR)),
                    metrics=blob.get("metrics"),
                )
            except (KeyError, ValueError, TypeError):
                pass
        return cls(
            weights=dict(FALLBACK_WEIGHTS),
            intercept=FALLBACK_INTERCEPT,
            extra=dict(FALLBACK_EXTRA),
            source="documented fallback weights (no fitted model found)",
            # the fallback weights are already expressed for a balanced prior,
            # so no re-basing is applied on top of them
            fitted_prior=DEPLOYMENT_PRIOR,
        )

    @property
    def prior_offset(self) -> float:
        """Log-odds correction from the fitted prior to the deployment prior."""
        return _logit(self.deployment_prior) - _logit(self.fitted_prior)


def trigger_prior(trigger_type: str) -> float:
    return TRIGGER_PRIOR.get(trigger_type, DEFAULT_TRIGGER_PRIOR)


def feature_vector(
    findings: list[PatternFinding], f: Features, customer_disputes: bool = False
) -> dict[str, float]:
    """The exact inputs the model consumes. Shared by scoring and fitting.

    ``customer_disputes`` no longer enters the vector -- it is carried by the
    trigger prior instead -- but the parameter is kept so the calibration and
    the live path build identical vectors.
    """
    vec = {name: 0.0 for name in FALLBACK_WEIGHTS}
    for find in findings:
        if find.name in vec:
            vec[find.name] = float(find.strength) if find.matched else 0.0
    n_strong = sum(
        1 for find in findings
        if find.matched and find.name in STRONG_SIGNAL_NAMES and find.strength >= 0.5
    )
    vec["prior_fraud_on_card"] = 1.0 if f.prior_fraud_cases_card > 0 else 0.0
    vec["corroboration"] = float(max(0, n_strong - 1))
    # Raw discriminators. Each one is a sentence an analyst can read.
    # "home activity continues while the card is used elsewhere" separates a
    # cloned card from a trip; a multi-day run in one new region is a trip.
    vec["home_activity_continues"] = 1.0 if (f.region_novel and f.home_activity_continues) else 0.0
    vec["region_trip_shape"] = 1.0 if (f.region_novel and f.region_days_span >= 2.0) else 0.0
    vec["burst_excess"] = float(min(3.0, max(0.0, math.log1p(max(f.burst_rate_ratio - 1.0, 0.0)))))
    vec["amt_over_max"] = float(min(3.0, max(0.0, f.amt_over_max - 1.0)))
    vec["thin_history"] = 1.0 if f.hist_n_txns < 10 else 0.0
    vec["device_marked_new"] = 1.0 if f.device_marked_new else 0.0
    vec["anonymous_proxy"] = 1.0 if f.anonymous_proxy else 0.0
    vec["match_flag_anomaly"] = 1.0 if f.match_flag_anomaly else 0.0
    vec["ring_scale"] = float(min(2.0, f.ring_cards / 10.0)) if f.ring_new_fraction >= 0.9 else 0.0
    return vec


def independent_signals(
    findings: list[PatternFinding], f: Features, customer_disputes: bool = False
) -> list[str]:
    """Distinct, non-overlapping evidence strands supporting a fraud reading.

    Policy R1 turns on whether a case rests on a *single* signal, so this list
    must not double-count two detectors that fired on the same observation.
    """
    sig: list[str] = []
    if customer_disputes:
        sig.append("the cardholder states they did not make this transaction")
    if f.amount_novel:
        sig.append("amount above the card's historical maximum")
    if f.channel_novel:
        sig.append("channel unusual for this card")
    if f.product_novel:
        sig.append("product code never used on this card")
    if f.region_novel:
        sig.append("billing region never used on this card")
    if f.device_novel and f.device_marked_new:
        sig.append("device new to the account")
    if f.anonymous_proxy:
        sig.append("anonymising proxy")
    if f.ring_cards >= 8 and f.ring_new_fraction >= 0.9:
        sig.append("device profile shared across many unrelated cards")
    if f.match_flag_anomaly:
        sig.append("identity match-flag anomaly")
    for find in findings:
        if find.matched and find.name in ("card_testing", "sub_threshold_structuring"):
            sig.append(f"{find.name.replace('_', ' ')} sequence")
    if f.prior_fraud_cases_card > 0:
        sig.append("prior confirmed fraud on this card")
    return sig


def assess(
    findings: list[PatternFinding],
    f: Features,
    model: RiskModel | None = None,
    customer_disputes: bool = False,
    trigger_type: str = "",
) -> RiskAssessment:
    model = model or RiskModel.load()
    vec = feature_vector(findings, f, customer_disputes=customer_disputes)
    if not trigger_type:
        trigger_type = "customer_report" if customer_disputes else "risk_score"
    prior = trigger_prior(trigger_type)

    if customer_disputes:
        # "Nothing unusual for this card" is exculpatory against a *model score*
        # -- all 900 cleared cases in the history were model-triggered. It is not
        # an answer to a cardholder saying they did not make the purchase, so it
        # is suppressed here. The recurring-charge detector (policy R7) is the
        # exception that may still overturn a dispute.
        vec["consistent_with_history"] = 0.0

    contributions: list[dict] = []
    z = prior
    contributions.append({
        "signal": f"trigger_prior[{trigger_type}]", "value": 1.0,
        "weight": round(prior, 3), "log_odds": round(prior, 3),
    })
    for name, value in vec.items():
        w = model.weights.get(name, model.extra.get(name, 0.0))
        c = w * value
        if abs(c) > 1e-9:
            contributions.append(
                {"signal": name, "value": round(value, 3), "weight": round(w, 3),
                 "log_odds": round(c, 3)}
            )
        z += c
    prob = _sigmoid(z)

    sigs = independent_signals(findings, f, customer_disputes=customer_disputes)
    exculp = [find for find in findings if find.matched and find.name in
              ("recurring_charge", "consistent_with_history")]
    inculp = [find for find in findings if find.matched and find.name in STRONG_SIGNAL_NAMES]

    # confidence: how much independent support there is, and whether the
    # evidence points one way.  Low history means we cannot judge novelty.
    confidence = 0.30
    confidence += 0.13 * min(len(sigs), 4)
    if f.hist_n_txns >= 50:
        confidence += 0.10
    elif f.hist_n_txns < 10:
        confidence -= 0.15
    if exculp and inculp:
        confidence -= 0.18
    if not sigs and exculp:
        confidence += 0.12
    confidence = round(min(0.95, max(0.05, confidence)), 3)

    notes: list[str] = []
    conflicts: list[str] = []
    if f.hist_n_txns < 10:
        notes.append(
            f"only {f.hist_n_txns} prior transactions on this card, so 'unusual for this "
            "cardholder' rests on a thin baseline"
        )
    if exculp and inculp:
        conflicts.append(
            "evidence conflicts: "
            + "; ".join(x.name for x in inculp)
            + " point to fraud while "
            + "; ".join(x.name for x in exculp)
            + " point to legitimate use"
        )
    if f.channel == "online" and not f.device_profile:
        notes.append("no identity/device record attached to this online transaction, "
                     "so the device and proxy tests could not be run")
    if len(sigs) == 1:
        notes.append(f"the case rests on a single signal ({sigs[0]}); policy R1 applies")
    if not sigs:
        notes.append("no graph signal departs from this card's established behaviour")
    if customer_disputes and not [s for s in sigs if "cardholder states" not in s]:
        notes.append(
            "the cardholder's denial is the only evidence of fraud; nothing in the card's graph "
            "neighbourhood corroborates it"
        )
    if f.bank_risk_score >= 0.7 and not sigs:
        notes.append(
            f"the bank model scored this {f.bank_risk_score:.2f} but the graph shows nothing "
            "unusual; the README notes most high scores are legitimate"
        )
    if f.bank_risk_score <= 0.2 and len(sigs) >= 2:
        notes.append(
            f"the bank model scored this only {f.bank_risk_score:.2f} while the graph shows "
            f"{len(sigs)} independent anomalies; the score is an input, not a verdict"
        )

    return RiskAssessment(
        bank_risk_score=f.bank_risk_score,
        graph_indicators={
            find.name: round(find.strength, 3) for find in findings if find.matched
        },
        historical_evidence={
            "prior_confirmed_fraud_cases_on_card": f.prior_fraud_cases_card,
            "prior_confirmed_fraud_cases_on_customer": f.prior_fraud_cases_customer,
            "prior_cleared_cases_on_customer": f.prior_cleared_cases_customer,
            "closed_cases_touching_this_device": f.prior_cases_on_device,
        },
        agent_fraud_probability=round(prob, 3),
        confidence=confidence,
        uncertainty_notes=notes,
        conflicting_evidence=conflicts,
        independent_signal_count=len(sigs),
        contributions=sorted(contributions, key=lambda c: -abs(c["log_odds"])),
        calibration_note=(
            f"trigger prior for {trigger_type} ({prior:+.2f} log-odds) plus a logistic "
            f"combination of detector strengths; coefficients {model.source}. "
            + TRIGGER_PRIOR_NOTE
        ),
    )


def reassess_with_customer_response(
    risk: RiskAssessment,
    denied: bool | None,
    findings: list[PatternFinding],
    already_counted: bool = False,
) -> RiskAssessment:
    """Fold a customer validation response into the assessment.

    ``denied=True``  -> the cardholder says they did not make the transaction
    ``denied=False`` -> the cardholder confirms it
    ``denied=None``  -> no reply

    ``already_counted`` is set when the alert itself was a customer dispute, so
    the denial is already in the initial probability.  The follow-up validation
    then adds only what it genuinely established -- that the cardholder still
    holds the card and maintains the denial -- rather than counting the same
    statement twice.
    """
    new = risk.model_copy(deep=True)
    if denied is None:
        new.uncertainty_notes = list(new.uncertainty_notes) + [
            "no customer response within the policy window (R4)"
        ]
        return new
    z = _logit(risk.agent_fraud_probability)
    # A direct statement from the cardholder is the strongest single piece of
    # evidence available; weight chosen so a denial on a 0.45 case clears 0.85
    # and a confirmation on a 0.45 case falls below 0.15.
    if already_counted:
        # the denial is already in the prior; corroborating possession of the
        # card is worth a little more, withdrawing the dispute a great deal less
        delta = 0.9 if denied else -3.4
    else:
        delta = 2.6 if denied else -2.6
    z += delta
    new.agent_fraud_probability = round(_sigmoid(z), 3)
    new.confidence = round(min(0.95, risk.confidence + 0.20), 3)
    new.uncertainty_notes = [
        n for n in risk.uncertainty_notes if "single signal" not in n
    ]
    new.uncertainty_notes.append(
        "cardholder statement recorded as simulated evidence, not a live customer contact"
    )
    return new

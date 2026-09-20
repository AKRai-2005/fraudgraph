"""Build the risk model from measured detector likelihood ratios.

Why not just use the logistic fit
---------------------------------
``calibrate.py`` fits a joint model and reports it honestly. Its headline result
is that graph-derived detectors are a **poor global classifier** on this data:
out-of-fold ROC AUC 0.59 overall, and 0.40 against the cleared cases — worse
than chance. The reason is visible in one number:

    only 11.8% of confirmed frauds have any strong detector firing,
    against 31.1% of the *cleared* alerts.

The closed-case history contains two different populations. Confirmed frauds are
mostly single, unremarkable transactions found because a customer complained.
Cleared alerts are, by construction, the most anomalous-looking legitimate
activity the bank's model could find — travel, a new phone, a big purchase. So
anomaly features are *enriched in the negative class*, and any classifier fitted
to separate the two learns the wrong thing.

What this means for the design
------------------------------
Graph evidence in this dataset is **high-precision and low-recall**. When
``shared_device_ring`` fires it is right (4 of 4 firings were confirmed fraud,
0 false); when nothing fires, that is close to no information at all
(``consistent_with_history`` has a measured likelihood ratio of 0.94). So the
model is built the way an investigator reasons rather than the way a classifier
is trained:

    log-odds = trigger prior  +  Σ log(LR) over the detectors that fired

Each term is a measured quantity with a countable basis, and absence of a
detector contributes nothing. Ratios are Laplace-smoothed so a detector that has
never fired on legitimate activity cannot produce an infinite weight from four
examples, and each term is clipped so no single detector can carry a case alone.

Documented floors apply to three detectors whose firing counts are too small to
estimate from (the closed history holds 16 card-testing, 5 structuring and 4
ring cases). For those the challenge's own documentation — which states they are
fraud typologies — is used, and the small sample is recorded next to the number.

Run:  python -m fraudgraph.analysis.build_model
"""
from __future__ import annotations

import json
import math

from ..config import PATHS
from . import risk as RISK

#: Per-term clip: no one detector may move the odds by more than this.
MAX_ABS_WEIGHT = 2.6

#: Detectors whose measured counts are too small to estimate a weight from.
#: The floor is the challenge's own documentation; the count is recorded so the
#: reader can see exactly how thin the evidence is.
#: Detectors whose job is to support a fraud reading. Their weight is floored at
#: zero: a fraud detector may support fraud or stay silent, but it may not argue
#: for innocence. Where the measured ratio is below 1 (``cnp_new_device`` fires
#: on 7.1% of frauds and 10.9% of legitimate activity) the detector contributes
#: nothing rather than exculpating -- which is the dataset README's own point,
#: that people buy new phones.
INCULPATORY_DETECTORS = {
    "card_testing", "sub_threshold_structuring", "shared_device_ring",
    "out_of_region_use", "cnp_new_device", "cnp_fraud", "account_takeover",
}

DOCUMENTED_FLOORS: dict[str, tuple[float, str]] = {
    "card_testing": (
        1.20,
        "the closed history holds only 16 card-testing cases, too few to estimate from; the "
        "dataset README documents the sequence as a fraud typology and policy R5 acts on it",
    ),
    "sub_threshold_structuring": (
        1.60,
        "read from 5 closed cases (CC-3748, CC-3841, CC-3907, CC-4086, CC-4124) whose analyst "
        "notes describe amounts chosen to stay under a $500 authorisation threshold",
    ),
    "shared_device_ring": (
        2.20,
        "read from 4 closed cases (CC-2649, CC-2971, CC-2985, CC-3035); the detector fired on 4 "
        "confirmed frauds and 0 legitimate cases, which supports a high weight but on a small "
        "sample",
    ),
}

#: Corroboration: two independent strong signals are worth more than their sum,
#: because the failure modes are different. Measured LR 1.75 on the history.
CORROBORATION_WEIGHT = 0.55
PRIOR_FRAUD_ON_CARD_CAP = 0.40


def build(rates_path=None) -> dict:
    rates_path = rates_path or (PATHS.build / "detector_rates.json")
    if not rates_path.exists():
        raise SystemExit(
            f"{rates_path} not found. Run: python scripts/measure_detectors.py"
        )
    blob = json.loads(rates_path.read_text())
    weights: dict[str, float] = {}
    basis: list[dict] = []

    for row in blob["detectors"]:
        name = row["detector"]
        measured = float(row["log_odds"])
        kf, kl = row["fires_on_fraud"], row["fires_on_legitimate"]
        note = (
            f"fired on {kf} of {blob['n_fraud']} confirmed frauds and {kl} of "
            f"{blob['n_legitimate']} legitimate cases; smoothed likelihood ratio "
            f"{row['likelihood_ratio_smoothed']}"
        )
        source = "measured"
        w = measured
        floor = DOCUMENTED_FLOORS.get(name)
        if floor is not None and measured < floor[0]:
            w = floor[0]
            source = "documented floor"
            note += f". Floor applied: {floor[1]}"
        if name == "prior_fraud_on_card":
            w = min(w, PRIOR_FRAUD_ON_CARD_CAP)
        if name in INCULPATORY_DETECTORS and w < 0.0:
            note += (
                f". Measured ratio is below 1, so the weight is floored at zero: this detector "
                f"may support a fraud reading or stay silent, but may not argue for innocence"
            )
            w = 0.0
            source = "measured, floored at zero"
        w = max(-MAX_ABS_WEIGHT, min(MAX_ABS_WEIGHT, w))
        weights[name] = round(w, 3)
        basis.append({
            "detector": name, "weight": round(w, 3), "measured_log_odds": measured,
            "source": source, "basis": note,
            "rate_fraud_pct": row["rate_fraud_pct"], "rate_legit_pct": row["rate_legit_pct"],
        })

    extra = {
        "corroboration": CORROBORATION_WEIGHT,
        "prior_fraud_on_card": PRIOR_FRAUD_ON_CARD_CAP,
    }
    for k in RISK.FALLBACK_EXTRA:
        extra.setdefault(k, 0.0)
    # raw discriminators stay off unless measured: the fit put them all at zero
    for k in ("device_marked_new", "anonymous_proxy", "match_flag_anomaly",
              "home_activity_continues", "region_trip_shape", "burst_excess",
              "amt_over_max", "thin_history", "ring_scale"):
        extra.setdefault(k, 0.0)

    out = {
        "method": "measured likelihood ratios + trigger prior",
        "weights": {k: v for k, v in weights.items() if k in RISK.FALLBACK_WEIGHTS},
        "extra": extra,
        # the prior lives on the trigger, so the model carries no intercept
        "intercept": 0.0,
        "n_train": blob["n_fraud"] + blob["n_legitimate"],
        "fitted_prior": RISK.DEPLOYMENT_PRIOR,
        "deployment_prior": RISK.DEPLOYMENT_PRIOR,
        "trigger_prior": RISK.TRIGGER_PRIOR,
        "trigger_prior_note": RISK.TRIGGER_PRIOR_NOTE,
        "class_counts": {
            "confirmed_fraud": blob["n_fraud"],
            "cleared_hard_negative": blob["n_cleared_hard_negative"],
            "unalerted_base_rate": blob["n_unalerted_base_rate"],
        },
        "weight_basis": basis,
        "max_abs_weight": MAX_ABS_WEIGHT,
        "note": RISK.DISPUTE_WEIGHT_NOTE,
        "bank_score_note": RISK.BANK_SCORE_EXCLUSION_NOTE,
        "design_note": __doc__.strip(),
    }
    # keep whatever the logistic fit measured, for the model card
    fit_path = PATHS.build / "risk_model_logistic.json"
    cur = PATHS.build / "risk_model.json"
    if cur.exists() and not fit_path.exists():
        fit_path.write_text(cur.read_text())
    if fit_path.exists():
        try:
            out["logistic_fit_metrics"] = json.loads(fit_path.read_text()).get("metrics")
        except json.JSONDecodeError:
            pass
    cur.write_text(json.dumps(out, indent=2))
    return out


def main() -> int:
    out = build()
    print(f"Risk model built from measured likelihood ratios "
          f"({out['n_train']:,} labelled examples)\n")
    print(f"  trigger priors (log-odds): "
          + ", ".join(f"{k}={v:+.2f}" for k, v in out["trigger_prior"].items()))
    print("\n  detector weights:")
    for b in sorted(out["weight_basis"], key=lambda r: -abs(r["weight"])):
        flag = "" if b["source"] == "measured" else "  <- documented floor"
        print(f"    {b['detector']:28s} {b['weight']:+6.2f}  "
              f"(fraud {b['rate_fraud_pct']:5.2f}% / legit {b['rate_legit_pct']:5.2f}%){flag}")
    print(f"\n  written to {PATHS.build / 'risk_model.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

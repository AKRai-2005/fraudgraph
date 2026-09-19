"""Fit and evaluate the risk model against the closed investigations.

Why the obvious approach does not work
-------------------------------------
The first attempt fitted a plain logistic regression on the 5,565 closed cases
(4,665 confirmed fraud, 900 cleared).  It scored ROC AUC 0.96 and was useless,
because the closed cases are not a sample of *alerts* -- they are a sample of
*investigations the bank chose to open*:

* every one of the 900 cleared cases is a high-scoring model alert that turned
  out to be travel (716), a new phone (158) or a large but intended purchase (26);
* the 4,665 confirmed frauds are overwhelmingly customer reports.

So the negative class is deliberately enriched for exactly the anomaly signals
that indicate fraud.  Fitted freely, the coefficients invert: ``bank_risk_score``
came out at **-5.5**, ``device_marked_new`` at **-2.7** and
``consistent_with_history`` at **+1.4**.  Those numbers describe alert selection,
not fraud, and a model carrying them would recommend blocking ordinary
transactions and clearing ring activity.

What this module does instead
-----------------------------
1. **Adds a base-rate negative class.** Transactions that no closed case ever
   touched are sampled as presumed-legitimate.  They are what ordinary activity
   looks like, which the cleared cases are not.  This class carries label noise
   -- some of it is undetected fraud -- which biases coefficients toward zero.
   That is conservative, and it is stated in the model card.
2. **Constrains the signs.** Inculpatory features may only take non-negative
   weights and exculpatory features only non-positive ones, so the fit sets
   magnitudes but cannot invert the direction of a signal against the domain.
3. **Excludes two features by construction**: the bank's ``risk_score`` (see
   ``RISK.BANK_SCORE_EXCLUSION_NOTE``) and ``customer_dispute`` (see
   ``RISK.DISPUTE_WEIGHT_NOTE``).
4. **Reports out-of-fold metrics on both negative classes separately**, because
   the hard negatives are the ones the exam is built from.

Run:  python -m fraudgraph.analysis.calibrate [--sample N] [--unalerted N]
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd

from ..config import PATHS
from ..graph.local_mirror import get_local_backend
from ..graph.store import GraphStore
from . import features as F
from . import patterns as P
from . import risk as RISK

#: Features that may only push toward fraud.
INCULPATORY = [
    "card_testing", "sub_threshold_structuring", "shared_device_ring",
    "out_of_region_use", "cnp_new_device", "cnp_fraud", "account_takeover",
    "prior_fraud_on_card", "corroboration", "home_activity_continues",
    "burst_excess", "amt_over_max", "device_marked_new", "anonymous_proxy",
    "match_flag_anomaly", "ring_scale",
]
#: Features that may only push toward legitimate.
EXCULPATORY = ["recurring_charge", "consistent_with_history", "region_trip_shape", "thin_history"]

FIT_FEATURES = INCULPATORY + EXCULPATORY

#: Bounds tighter than the bare sign constraint, each one traceable to an
#: explicit statement in the dataset README rather than to taste.
#:
#: * ``prior_fraud_on_card`` -- "Do not automatically classify a new transaction
#:   as fraudulent merely because a related entity appeared in a historical
#:   fraud case. Historical relationships should inform the investigation, not
#:   replace current evidence."  Measured lift is small anyway (it fires on 67%
#:   of confirmed fraud and 48% of the legitimate class), and the history is
#:   drawn from cards that already had an incident, so an unbounded coefficient
#:   would encode that sampling.  Capped so it can never carry a case alone.
#: * ``consistent_with_history`` -- the exam is built so that "half the cases
#:   are legitimate. Many look suspicious."  A floor guarantees that an alert
#:   with nothing unusual behind it lands low rather than drifting upward on
#:   incidental features.
#: * ``device_marked_new`` / ``out_of_region_use`` -- "people buy new phones";
#:   "several days of purchases in one new region is a trip, not a clone".
#:   Neither may become a strong standalone signal; corroboration carries them.
#: * ``region_trip_shape`` / ``home_activity_continues`` -- the README states
#:   the discriminator outright: "Card-present purchases in a billing region the
#:   cardholder has no history in, **while their normal activity continues at
#:   home** ... Several days of purchases in one new region is a trip, not a
#:   clone."  Measured on the closed cases these two features are close to
#:   uninformative (they fire on 0.7% of fraud and 1.4% of the cleared class),
#:   because the cleared population is itself dominated by confirmed travel.
#:   Floors keep the stated discriminator alive: the data may strengthen it but
#:   cannot erase it.  This is the one place a prior overrides a measurement,
#:   and it is recorded here rather than buried.
BOUNDS: dict[str, tuple[float, float]] = {
    "prior_fraud_on_card": (0.0, 0.40),
    "consistent_with_history": (-2.60, -1.20),
    "device_marked_new": (0.0, 0.45),
    "out_of_region_use": (0.30, 1.60),
    "cnp_new_device": (0.0, 1.60),
    "thin_history": (-0.60, 0.0),
    "home_activity_continues": (0.35, 1.30),
    "region_trip_shape": (-1.20, -0.35),
}


def build_context(store: GraphStore, txn_id: int, as_of: str | None) -> F.CaseContext | None:
    """The same evidence the live agent would gather, for one alert."""
    txn = store.call("txn_detail", txn_id=txn_id)
    if not txn.get("found"):
        return None
    ctx = F.CaseContext(case_id="calib", txn=txn)
    ctx.card_id = txn.get("card_id") or ""
    ctx.customer_id = txn.get("customer_id") or ""
    ts = ctx.ts
    ctx.profile = store.call("card_profile", card_id=ctx.card_id, before_ts=ts)
    ctx.window = store.call("card_window", card_id=ctx.card_id, center_ts=ts,
                            hours_before=72, hours_after=72)
    ctx.wide_window = store.call("card_window", card_id=ctx.card_id, center_ts=ts,
                                 hours_before=24 * 180, hours_after=24 * 30, limit=4000)
    if txn.get("addr1") is not None:
        ctx.region_test = store.call("region_history_for_card", card_id=ctx.card_id,
                                     region_id=txn["addr1"], before_ts=ts)
    if txn.get("device_profile"):
        ctx.device_test = store.call("device_history_for_card", card_id=ctx.card_id,
                                     device_profile=txn["device_profile"], before_ts=ts)
        if int(ctx.device_test.get("prior_txns_on_device") or 0) == 0 or \
                txn.get("id_23") in ("IP_PROXY:ANONYMOUS", "IP_PROXY:HIDDEN"):
            lo = str(pd.Timestamp(ts) - pd.Timedelta(days=P.RING_WINDOW_DAYS))
            hi = str(pd.Timestamp(ts) + pd.Timedelta(days=P.RING_WINDOW_DAYS))
            ctx.device_ring = store.call("device_neighbors",
                                         device_profile=txn["device_profile"],
                                         from_ts=lo, to_ts=hi)
    ctx.prior_cases_card = store.call("closed_cases_for_card", card_id=ctx.card_id, as_of=as_of)
    ctx.prior_cases_customer = store.call("closed_cases_for_customer",
                                          customer_id=ctx.customer_id, as_of=as_of)
    ctx.customer_cards = store.call("customer_cards", customer_id=ctx.customer_id)
    return ctx


def _row(store: GraphStore, txn_id: int, as_of: str | None, label: int, group: str,
         extra: dict | None = None) -> dict | None:
    store.reset()
    ctx = build_context(store, txn_id, as_of)
    if ctx is None:
        return None
    feats = F.compute(ctx)
    findings, _ = P.run_all(ctx, feats)
    vec = RISK.feature_vector(findings, feats, customer_disputes=False)
    rec = {k: float(vec.get(k, 0.0)) for k in FIT_FEATURES}
    rec.update({"label": label, "group": group, "alert_txn": txn_id,
                "channel": feats.channel, "bank_risk_score": feats.bank_risk_score})
    rec.update(extra or {})
    return rec


def build_dataset(
    sample: int | None = None, unalerted: int = 4000, seed: int = 7, verbose: bool = True
) -> pd.DataFrame:
    cc = pd.read_parquet(PATHS.closed_cases_parquet)
    if sample:
        cc = cc.sample(n=min(sample, len(cc)), random_state=seed)
    store = GraphStore(backend=get_local_backend())
    rows: list[dict] = []
    t0 = time.perf_counter()

    # ---- alerted cases: confirmed fraud (positive) and cleared (hard negative)
    for i, (_, case) in enumerate(cc.iterrows()):
        raw = case["txn_id_list"]
        ids = list(raw) if raw is not None else []
        first = case.get("first_fraud_txn_id")
        alert_txn = int(first) if first is not None and not pd.isna(first) else (
            int(ids[0]) if ids else None
        )
        if alert_txn is None:
            continue
        label = 1 if case["outcome"] == "confirmed_fraud" else 0
        rec = _row(
            store, alert_txn, str(case["opened_at"]), label,
            "confirmed_fraud" if label else "cleared_hard_negative",
            {"case_id": case["case_id"], "pattern": case["pattern"],
             "exposure_usd": float(case["exposure_usd"]), "n_txns": int(case["n_txns"])},
        )
        if rec:
            rows.append(rec)
        if verbose and (i + 1) % 500 == 0:
            print(f"  closed cases {i + 1}/{len(cc)}  ({(i + 1) / (time.perf_counter() - t0):.1f}/s)",
                  flush=True)

    # ---- base-rate negatives: transactions no closed case ever touched
    if unalerted:
        d = get_local_backend()
        from ..graph.local_mirror import _Data

        data = _Data.get()
        touched = set(data.case_txns.TransactionID.tolist())
        pool = data.tx[~data.tx.TransactionID.isin(touched)]
        # restrict to the same July-October window as the closed cases so the
        # two classes are drawn from the same period
        pool = pool[pool.ts < pd.Timestamp("2016-11-01")]
        pick = pool.sample(n=min(unalerted, len(pool)), random_state=seed)
        for j, (_, tx) in enumerate(pick.iterrows()):
            rec = _row(store, int(tx.TransactionID), str(tx.ts), 0, "unalerted_base_rate",
                       {"case_id": "", "pattern": "none", "exposure_usd": 0.0, "n_txns": 1})
            if rec:
                rows.append(rec)
            if verbose and (j + 1) % 500 == 0:
                print(f"  unalerted {j + 1}/{len(pick)}", flush=True)

    df = pd.DataFrame(rows)
    df.to_parquet(PATHS.build / "calibration_dataset.parquet", index=False)
    return df


def _fit_constrained(X: np.ndarray, y: np.ndarray, w: np.ndarray, bounds) -> tuple[np.ndarray, float]:
    """Logistic loss with per-coefficient sign bounds (L-BFGS-B)."""
    from scipy.optimize import minimize

    n, k = X.shape

    def nll(theta: np.ndarray) -> tuple[float, np.ndarray]:
        b, coef = theta[0], theta[1:]
        z = X @ coef + b
        # stable log(1+exp(z))
        loss = np.where(z > 0, z + np.log1p(np.exp(-z)), np.log1p(np.exp(z))) - y * z
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))
        g = (p - y) * w
        grad = np.concatenate(([g.sum()], X.T @ g))
        reg = 0.5 * 0.05 * coef @ coef
        grad[1:] += 0.05 * coef
        return float((loss * w).sum() + reg), grad

    res = minimize(
        nll, np.zeros(k + 1), jac=True, method="L-BFGS-B",
        bounds=[(None, None)] + list(bounds), options={"maxiter": 800},
    )
    return res.x[1:], float(res.x[0])


def fit(df: pd.DataFrame, folds: int = 5, seed: int = 7) -> dict:
    from sklearn.metrics import brier_score_loss, confusion_matrix, roc_auc_score
    from sklearn.model_selection import StratifiedKFold

    X = df[FIT_FEATURES].to_numpy(dtype=float)
    y = df["label"].to_numpy(dtype=int)
    bounds = [
        BOUNDS.get(f, (0.0, None) if f in INCULPATORY else (None, 0.0))
        for f in FIT_FEATURES
    ]

    # Reweight to the deployment prior: the exam is ~50/50, the training mix is not.
    n_pos, n_neg = int(y.sum()), int((1 - y).sum())
    w = np.where(y == 1, 0.5 / max(n_pos, 1), 0.5 / max(n_neg, 1)) * len(y)

    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    oof = np.zeros(len(y), dtype=float)
    for tr, te in skf.split(X, y):
        coef, b = _fit_constrained(X[tr], y[tr], w[tr], bounds)
        z = X[te] @ coef + b
        oof[te] = 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))

    coef, intercept = _fit_constrained(X, y, w, bounds)
    coefs = dict(zip(FIT_FEATURES, (float(c) for c in coef)))

    def _metrics(mask: np.ndarray, label: str) -> dict:
        if mask.sum() == 0 or len(set(y[mask])) < 2:
            return {"n": int(mask.sum()), "note": f"{label}: not evaluable"}
        tn, fp, fn, tp = confusion_matrix(y[mask], (oof[mask] >= 0.5).astype(int)).ravel()
        return {
            "n": int(mask.sum()),
            "roc_auc": round(float(roc_auc_score(y[mask], oof[mask])), 4),
            "accuracy": round(float(((oof[mask] >= 0.5).astype(int) == y[mask]).mean()), 4),
            "recall_fraud": round(float(tp / (tp + fn)) if (tp + fn) else 0.0, 4),
            "specificity": round(float(tn / (tn + fp)) if (tn + fp) else 0.0, 4),
            "brier": round(float(brier_score_loss(y[mask], oof[mask])), 4),
            "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        }

    grp = df["group"].to_numpy()
    overall = _metrics(np.ones(len(y), dtype=bool), "overall")
    hard = _metrics((grp == "confirmed_fraud") | (grp == "cleared_hard_negative"), "hard")
    base = _metrics((grp == "confirmed_fraud") | (grp == "unalerted_base_rate"), "base")

    bands = pd.cut(oof, [0, 0.15, 0.3, 0.5, 0.7, 0.85, 1.0], include_lowest=True)
    rel = (
        pd.DataFrame({"p": oof, "y": y, "band": bands})
        .groupby("band", observed=True)
        .agg(n=("y", "size"), predicted=("p", "mean"), actual=("y", "mean"))
        .reset_index()
    )
    rel["band"] = rel["band"].astype(str)

    # per-detector firing rates: interpretable, and independent of the fit
    firing = []
    for f in FIT_FEATURES:
        v = df[f].to_numpy(dtype=float) > 0
        if v.sum() == 0:
            continue
        firing.append({
            "feature": f,
            "fires_on_fraud_pct": round(float(v[y == 1].mean() * 100), 2),
            "fires_on_legit_pct": round(float(v[y == 0].mean() * 100), 2),
            "n_fires": int(v.sum()),
        })

    return {
        "weights": {k: v for k, v in coefs.items() if k in RISK.FALLBACK_WEIGHTS},
        "extra": {k: v for k, v in coefs.items() if k not in RISK.FALLBACK_WEIGHTS},
        "intercept": intercept,
        "n_train": int(len(df)),
        # the fit is already reweighted to the deployment prior, so no further
        # re-basing is applied on top of it
        "fitted_prior": RISK.DEPLOYMENT_PRIOR,
        "deployment_prior": RISK.DEPLOYMENT_PRIOR,
        "class_counts": df["group"].value_counts().to_dict(),
        "metrics": {
            "cv_folds": folds,
            "overall": overall,
            "vs_cleared_hard_negatives": hard,
            "vs_unalerted_base_rate": base,
            "reliability": rel.to_dict("records"),
            "detector_firing_rates": sorted(firing, key=lambda r: -r["fires_on_fraud_pct"]),
        },
        "sign_constraints": {"inculpatory": INCULPATORY, "exculpatory": EXCULPATORY},
        "bounds": {k: list(v) for k, v in BOUNDS.items()},
        "note": RISK.DISPUTE_WEIGHT_NOTE,
        "bank_score_note": RISK.BANK_SCORE_EXCLUSION_NOTE,
        "label_noise_note": (
            "The unalerted base-rate negatives are presumed legitimate because no closed case "
            "touched them; some will be undetected fraud. That label noise biases coefficients "
            "toward zero, which is conservative."
        ),
        "features_fitted": FIT_FEATURES,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=None)
    ap.add_argument("--unalerted", type=int, default=4000)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--reuse", action="store_true")
    args = ap.parse_args(argv)

    cache = PATHS.build / "calibration_dataset.parquet"
    if args.reuse and cache.exists():
        df = pd.read_parquet(cache)
        print(f"Reusing cached calibration dataset: {len(df):,} rows")
    else:
        print("Replaying closed cases and base-rate transactions ...")
        df = build_dataset(sample=args.sample, unalerted=args.unalerted)
        print(f"Built {len(df):,} rows")

    blob = fit(df, folds=args.folds)
    (PATHS.build / "risk_model.json").write_text(json.dumps(blob, indent=2))
    m = blob["metrics"]
    print(f"\nFitted on {blob['n_train']:,} rows  {blob['class_counts']}")
    for key in ("overall", "vs_cleared_hard_negatives", "vs_unalerted_base_rate"):
        d = m[key]
        if "roc_auc" in d:
            print(f"  {key:30s} n={d['n']:>5d} AUC={d['roc_auc']:.4f} acc={d['accuracy']:.4f} "
                  f"recall={d['recall_fraud']:.4f} spec={d['specificity']:.4f} brier={d['brier']:.4f}")
    print("\nSign-constrained coefficients:")
    for k, v in sorted({**blob["weights"], **blob["extra"]}.items(), key=lambda kv: -abs(kv[1])):
        print(f"    {k:28s} {v:+.3f}")
    print(f"    {'intercept':28s} {blob['intercept']:+.3f}")
    print("\nReliability (out-of-fold):")
    for r in m["reliability"]:
        print(f"    {r['band']:>16s}  n={r['n']:>5d}  predicted={r['predicted']:.3f}  actual={r['actual']:.3f}")
    print("\nDetector firing rates (fraud% vs legit%):")
    for r in m["detector_firing_rates"][:14]:
        print(f"    {r['feature']:28s} {r['fires_on_fraud_pct']:>6.2f}%  {r['fires_on_legit_pct']:>6.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

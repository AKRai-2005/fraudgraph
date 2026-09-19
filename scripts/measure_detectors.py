"""Measure each detector's firing rate on confirmed fraud vs legitimate activity.

This is the number that matters for an evidence-combination model: when a
detector fires, how much does that move the odds?  It is measured, not asserted,
and the result is written to build/detector_rates.json for the model card.

Positives  : the confirmed-fraud closed cases (all of them).
Negatives  : the 900 cleared closed cases (hard negatives -- alerts that looked
             suspicious and were not fraud) plus a sample of transactions no
             closed case ever touched (base-rate negatives).

Run:  python scripts/measure_detectors.py [--unalerted N] [--sample N]
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from fraudgraph.analysis import features as F
from fraudgraph.analysis import patterns as P
from fraudgraph.analysis.calibrate import build_context
from fraudgraph.config import PATHS
from fraudgraph.graph.local_mirror import _Data, get_local_backend
from fraudgraph.graph.store import GraphStore

DETECTORS = [
    "card_testing", "sub_threshold_structuring", "shared_device_ring",
    "out_of_region_use", "cnp_new_device", "cnp_fraud", "account_takeover",
    "recurring_charge", "consistent_with_history",
]


def _fires(store, txn_id: int, as_of: str | None) -> dict | None:
    store.reset()
    ctx = build_context(store, txn_id, as_of)
    if ctx is None:
        return None
    f = F.compute(ctx)
    findings, _ = P.run_all(ctx, f)
    return {x.name: bool(x.matched) for x in findings}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--unalerted", type=int, default=2500)
    ap.add_argument("--sample", type=int, default=None, help="subsample closed cases")
    args = ap.parse_args()

    store = GraphStore(backend=get_local_backend())
    cc = pd.read_parquet(PATHS.closed_cases_parquet)
    if args.sample:
        cc = cc.sample(n=min(args.sample, len(cc)), random_state=7)

    rows = []
    for i, (_, case) in enumerate(cc.iterrows()):
        raw = case["txn_id_list"]
        ids = list(raw) if raw is not None else []
        first = case.get("first_fraud_txn_id")
        tid = int(first) if first is not None and not pd.isna(first) else (int(ids[0]) if ids else None)
        if tid is None:
            continue
        got = _fires(store, tid, str(case["opened_at"]))
        if got:
            got.update(label=1 if case["outcome"] == "confirmed_fraud" else 0,
                       group="confirmed_fraud" if case["outcome"] == "confirmed_fraud"
                             else "cleared_hard_negative")
            rows.append(got)
        if (i + 1) % 1000 == 0:
            print(f"  closed {i + 1}/{len(cc)}", flush=True)

    data = _Data.get()
    touched = set(data.case_txns.TransactionID.tolist())
    pool = data.tx[(~data.tx.TransactionID.isin(touched)) & (data.tx.ts < pd.Timestamp("2016-11-01"))]
    pick = pool.sample(n=min(args.unalerted, len(pool)), random_state=7)
    for j, (_, tx) in enumerate(pick.iterrows()):
        got = _fires(store, int(tx.TransactionID), str(tx.ts))
        if got:
            got.update(label=0, group="unalerted_base_rate")
            rows.append(got)
        if (j + 1) % 1000 == 0:
            print(f"  unalerted {j + 1}/{len(pick)}", flush=True)

    df = pd.DataFrame(rows)
    y = df.label.values
    n_pos, n_neg = int((y == 1).sum()), int((y == 0).sum())

    out = {"n_fraud": n_pos, "n_legitimate": n_neg,
           "n_cleared_hard_negative": int((df.group == "cleared_hard_negative").sum()),
           "n_unalerted_base_rate": int((df.group == "unalerted_base_rate").sum()),
           "detectors": []}
    print(f"\n{'detector':30s} {'fraud':>14s} {'legit':>14s} {'LR':>7s} {'log-odds':>9s}")
    for d in DETECTORS:
        if d not in df.columns:
            continue
        v = df[d].values.astype(bool)
        kf, kl = int(v[y == 1].sum()), int(v[y == 0].sum())
        # Laplace-smoothed rates: a detector that never fires on the legitimate
        # class must not produce an infinite weight off a handful of examples.
        pf = (kf + 0.5) / (n_pos + 1.0)
        pl = (kl + 0.5) / (n_neg + 1.0)
        lr = pf / pl
        out["detectors"].append({
            "detector": d, "fires_on_fraud": kf, "fires_on_legitimate": kl,
            "rate_fraud_pct": round(kf / n_pos * 100, 3),
            "rate_legit_pct": round(kl / n_neg * 100, 3),
            "likelihood_ratio_smoothed": round(lr, 3),
            "log_odds": round(float(np.log(lr)), 3),
        })
        print(f"{d:30s} {kf:6d}/{n_pos:<7d} {kl:6d}/{n_neg:<7d} {lr:7.2f} {np.log(lr):+9.2f}")

    (PATHS.build / "detector_rates.json").write_text(json.dumps(out, indent=2))
    print(f"\nWrote {PATHS.build / 'detector_rates.json'}")


if __name__ == "__main__":
    main()

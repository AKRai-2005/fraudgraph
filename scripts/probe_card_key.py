"""One-off probe: which transaction columns define a distinct Card (the -K<n> id)?

The case pack and the closed cases both reference cards as ``C01234-K1``.
transactions.csv only carries ``customer_id``.  This script recovers the rule by
testing candidate keys against the 5,565 closed cases, where the
transaction -> card_id mapping is known.

Result (see docs/DATA_NOTES.md): the key is (customer_id, card4, card6) and the
K index is the rank of the card by first-seen transaction timestamp.
"""
from __future__ import annotations

import pandas as pd

from fraudgraph.config import PATHS

KEY = ["card4", "card6"]


def main() -> None:
    tx = pd.read_parquet(
        PATHS.tx_core, columns=["TransactionID", "customer_id", "ts"] + KEY
    )
    for c in KEY:
        tx[c] = tx[c].astype("string").fillna("NA")
    tx["key"] = tx[KEY[0]] + "|" + tx[KEY[1]]

    cc = pd.read_parquet(PATHS.closed_cases_parquet)
    m = (
        cc[["case_id", "customer_id", "card_id", "txn_id_list"]]
        .explode("txn_id_list")
        .dropna(subset=["txn_id_list"])
        .rename(columns={"customer_id": "cust", "txn_id_list": "TransactionID"})
    )
    m["TransactionID"] = m["TransactionID"].astype("int64")
    print(f"closed-case txn links: {len(m):,}  distinct card_ids: {m.card_id.nunique():,}")

    j = m.merge(tx[["TransactionID", "key"]], on="TransactionID", how="left")
    print("  card_id -> exactly one key :", float((j.groupby('card_id')['key'].nunique() == 1).mean()))
    g = j.groupby("cust").agg(nc=("card_id", "nunique"), nk=("key", "nunique"))
    print("  per-customer n_cards == n_keys :", float((g.nc == g.nk).mean()))

    # --- K index ordering ---
    first = tx.groupby(["customer_id", "key"], as_index=False).agg(
        first_ts=("ts", "min"), n=("TransactionID", "size")
    )
    first["by_ts"] = first.groupby("customer_id")["first_ts"].rank(method="first").astype(int)
    first["by_key"] = first.groupby("customer_id")["key"].rank(method="first").astype(int)
    first["by_vol"] = (
        first.groupby("customer_id")["n"].rank(method="first", ascending=False).astype(int)
    )

    j2 = j.merge(
        first, left_on=["cust", "key"], right_on=["customer_id", "key"], how="left"
    )
    j2["k"] = j2.card_id.str.extract(r"-K(\d+)$")[0].astype(int)
    for col in ("by_ts", "by_key", "by_vol"):
        print(f"  K == rank {col:7s}:", float((j2.k == j2[col]).mean()))

    print("\ncards per customer (whole dataset):")
    print(first.groupby("customer_id").size().value_counts().sort_index().to_string())

    # Validate against the 20 exam cards too.
    cp = pd.read_csv(PATHS.case_pack_csv)
    first["card_id"] = first.customer_id + "-K" + first.by_ts.astype(str)
    known = set(first.card_id)
    missing = [c for c in cp.card_id if c not in known]
    print(f"\ncase-pack card_ids reproduced: {len(cp) - len(missing)}/{len(cp)}  missing={missing}")
    cc_cards = set(cc.card_id.dropna())
    print(f"closed-case card_ids reproduced: {len(cc_cards & known)}/{len(cc_cards)}")


if __name__ == "__main__":
    main()

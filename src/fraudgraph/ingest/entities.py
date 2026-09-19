"""Stage 2 of ingestion: derive the graph entities from the parquet cache.

The dataset does not ship a card table, a device table or a region table -- they
are derived here, once, so that every downstream component (graph loader, local
mirror, analysis, API) agrees on identity.

Run:  python -m fraudgraph.ingest.entities
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..config import PATHS, ensure_dirs

NA = "NA"

# Identity columns that make up a device profile, per the dataset README:
# "DeviceProfile (DeviceInfo + OS + browser + screen)".
DEVICE_COLS = ["DeviceInfo", "id_30", "id_31", "id_33"]


def _s(series: pd.Series) -> pd.Series:
    """Normalise to a string column with a literal NA for missing values."""
    return series.astype("string").fillna(NA).str.strip().replace("", NA)


def card_key(card4: object, card6: object) -> str:
    """The identity of a physical card within a customer.

    Verified against all 1,913 card ids in closed_cases_history.csv and all 20
    in case_pack.csv -- see docs/DATA_NOTES.md.
    """
    def norm(v: object) -> str:
        if v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() in ("", "nan", "None"):
            return NA
        return str(v).strip()

    return f"{norm(card4)}|{norm(card6)}"


def attach_card_ids(tx: pd.DataFrame) -> pd.DataFrame:
    """Add a ``card_id`` column (``C01234-K1``) to a transactions frame.

    The K index is the rank of the ``card4|card6`` key in ascending
    lexicographic order within the customer.
    """
    key = _s(tx["card4"]) + "|" + _s(tx["card6"])
    out = tx.assign(card_key=key)
    ranks = (
        out[["customer_id", "card_key"]]
        .drop_duplicates()
        .sort_values(["customer_id", "card_key"])
    )
    ranks["k"] = ranks.groupby("customer_id").cumcount() + 1
    ranks["card_id"] = ranks["customer_id"] + "-K" + ranks["k"].astype(str)
    return out.merge(ranks[["customer_id", "card_key", "card_id"]], on=["customer_id", "card_key"], how="left")


def device_profile_id(row: pd.Series | dict) -> str:
    """Human-readable device profile id, matching the README's example format."""
    parts = []
    for c in DEVICE_COLS:
        v = row.get(c)
        if v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() in ("", "nan", "None"):
            parts.append("unknown")
        else:
            parts.append(str(v).strip())
    return " | ".join(parts)


def build(verbose: bool = True) -> dict:
    ensure_dirs()
    quality: dict = {"errors": [], "warnings": []}

    tx = pd.read_parquet(
        PATHS.tx_core,
        columns=[
            "TransactionID", "customer_id", "ts", "TransactionAmt", "ProductCD",
            "channel", "risk_score", "addr1", "addr2", "dist1", "dist2",
            "P_emaildomain", "R_emaildomain",
            "card1", "card2", "card3", "card4", "card5", "card6",
        ],
    )
    n0 = len(tx)

    # --- validation -------------------------------------------------------
    dup = int(tx.TransactionID.duplicated().sum())
    if dup:
        quality["errors"].append(f"{dup} duplicate TransactionID rows")
        tx = tx.drop_duplicates(subset="TransactionID", keep="first")
    bad_ts = int(tx.ts.isna().sum())
    if bad_ts:
        quality["errors"].append(f"{bad_ts} rows with unparseable ts")
    bad_amt = int((~np.isfinite(tx.TransactionAmt.fillna(np.nan))).sum())
    if bad_amt:
        quality["warnings"].append(f"{bad_amt} rows with non-finite TransactionAmt")
    neg_amt = int((tx.TransactionAmt < 0).sum())
    if neg_amt:
        quality["warnings"].append(f"{neg_amt} rows with negative TransactionAmt")
    no_cust = int(tx.customer_id.isna().sum())
    if no_cust:
        quality["errors"].append(f"{no_cust} rows without customer_id")

    # channel consistency with the README's definition
    w_online = int(((tx.ProductCD == "W") & (tx.channel != "in_person")).sum())
    nonw_inperson = int(((tx.ProductCD != "W") & (tx.channel != "online")).sum())
    quality["channel_check"] = {
        "productW_not_in_person": w_online,
        "non_W_not_online": nonw_inperson,
    }

    # --- cards ------------------------------------------------------------
    tx = attach_card_ids(tx)
    if verbose:
        print(f"  cards attached: {tx.card_id.nunique():,} distinct card ids")

    cards = (
        tx.groupby("card_id")
        .agg(
            customer_id=("customer_id", "first"),
            card1=("card1", "first"),
            network=("card4", lambda s: s.dropna().iloc[0] if s.notna().any() else None),
            card_type=("card6", lambda s: s.dropna().iloc[0] if s.notna().any() else None),
            n_txns=("TransactionID", "size"),
            first_ts=("ts", "min"),
            last_ts=("ts", "max"),
            total_amt=("TransactionAmt", "sum"),
            median_amt=("TransactionAmt", "median"),
            max_amt=("TransactionAmt", "max"),
            n_online=("channel", lambda s: int((s == "online").sum())),
            n_in_person=("channel", lambda s: int((s == "in_person").sum())),
            mean_risk=("risk_score", "mean"),
        )
        .reset_index()
    )
    cards.to_parquet(PATHS.cards_parquet, compression="zstd", index=False)

    customers = (
        tx.groupby("customer_id")
        .agg(
            n_cards=("card_id", "nunique"),
            n_txns=("TransactionID", "size"),
            first_ts=("ts", "min"),
            last_ts=("ts", "max"),
            total_amt=("TransactionAmt", "sum"),
            home_region=("addr1", lambda s: s.dropna().mode().iloc[0] if s.notna().any() else None),
            home_country=("addr2", lambda s: s.dropna().mode().iloc[0] if s.notna().any() else None),
        )
        .reset_index()
    )
    customers.to_parquet(PATHS.build / "customers.parquet", compression="zstd", index=False)

    # --- transactions index (the hot table everything queries) ------------
    idn = pd.read_parquet(PATHS.identity_parquet)
    dev_key = idn[DEVICE_COLS].apply(
        lambda r: device_profile_id(r), axis=1
    )
    idn = idn.assign(device_profile=dev_key)

    orphan_identity = int((~idn.TransactionID.isin(set(tx.TransactionID))).sum())
    if orphan_identity:
        quality["errors"].append(f"{orphan_identity} identity rows with no matching transaction")

    idcols = [
        "TransactionID", "device_profile", "DeviceType", "DeviceInfo",
        "id_30", "id_31", "id_33", "id_15", "id_23", "id_12", "id_16",
        "id_28", "id_29", "id_34", "id_35", "id_36", "id_37", "id_38",
        "id_01", "id_02", "id_05", "id_06",
    ]
    idcols = [c for c in idcols if c in idn.columns]
    txi = tx.merge(idn[idcols], on="TransactionID", how="left")

    # identity coverage vs channel
    online_without_identity = int(
        ((txi.channel == "online") & (txi.device_profile.isna())).sum()
    )
    in_person_with_identity = int(
        ((txi.channel == "in_person") & (txi.device_profile.notna())).sum()
    )
    quality["identity_coverage"] = {
        "online_without_identity_record": online_without_identity,
        "in_person_with_identity_record": in_person_with_identity,
        "identity_rows": int(len(idn)),
    }

    txi = txi.sort_values(["card_id", "ts"]).reset_index(drop=True)
    txi["seq_in_card"] = txi.groupby("card_id").cumcount()
    txi["prev_txn_id"] = txi.groupby("card_id")["TransactionID"].shift(1)
    txi["next_txn_id"] = txi.groupby("card_id")["TransactionID"].shift(-1)
    txi["gap_prev_s"] = (
        txi.groupby("card_id")["ts"].diff().dt.total_seconds()
    )
    txi.to_parquet(PATHS.build / "tx_index.parquet", compression="zstd", index=False)

    # --- device profiles --------------------------------------------------
    dev = (
        txi.dropna(subset=["device_profile"])
        .groupby("device_profile")
        .agg(
            n_txns=("TransactionID", "size"),
            n_cards=("card_id", "nunique"),
            n_customers=("customer_id", "nunique"),
            first_ts=("ts", "min"),
            last_ts=("ts", "max"),
            device_type=("DeviceType", lambda s: s.dropna().iloc[0] if s.notna().any() else None),
            proxy_flags=("id_23", lambda s: "|".join(sorted(set(s.dropna())))),
            n_new_for_account=("id_15", lambda s: int((s == "New").sum())),
            total_amt=("TransactionAmt", "sum"),
        )
        .reset_index()
    )
    dev.to_parquet(PATHS.devices_parquet, compression="zstd", index=False)

    # --- billing regions --------------------------------------------------
    reg = (
        txi.dropna(subset=["addr1"])
        .groupby("addr1")
        .agg(
            n_txns=("TransactionID", "size"),
            n_cards=("card_id", "nunique"),
            n_customers=("customer_id", "nunique"),
            country=("addr2", lambda s: s.dropna().mode().iloc[0] if s.notna().any() else None),
        )
        .reset_index()
        .rename(columns={"addr1": "region_id"})
    )
    reg.to_parquet(PATHS.build / "regions.parquet", compression="zstd", index=False)

    # --- email domains ----------------------------------------------------
    em = pd.concat(
        [
            txi[["P_emaildomain"]].rename(columns={"P_emaildomain": "domain"}).assign(role="purchaser"),
            txi[["R_emaildomain"]].rename(columns={"R_emaildomain": "domain"}).assign(role="recipient"),
        ]
    ).dropna(subset=["domain"])
    em = em.groupby(["domain", "role"]).size().reset_index(name="n_txns")
    em.to_parquet(PATHS.build / "email_domains.parquet", compression="zstd", index=False)

    # --- closed cases: resolve card ids and validate ----------------------
    cc = pd.read_parquet(PATHS.closed_cases_parquet)
    known_cards = set(cards.card_id)
    known_txns = set(txi.TransactionID.astype("int64"))
    bad_case_cards = sorted(set(cc.card_id.dropna()) - known_cards)
    if bad_case_cards:
        quality["errors"].append(
            f"{len(bad_case_cards)} closed-case card_ids not reproducible from transactions"
        )
    missing_txn = sum(
        1 for lst in cc.txn_id_list for t in lst if int(t) not in known_txns
    )
    if missing_txn:
        quality["errors"].append(f"{missing_txn} closed-case txn ids not found in transactions.csv")

    cp = pd.read_csv(PATHS.case_pack_csv)
    bad_pack_cards = sorted(set(cp.card_id) - known_cards)
    bad_pack_txns = sorted(set(cp.flagged_txn_id.astype("int64")) - known_txns)
    if bad_pack_cards:
        quality["errors"].append(f"case-pack card_ids not reproducible: {bad_pack_cards}")
    if bad_pack_txns:
        quality["errors"].append(f"case-pack flagged txn ids missing: {bad_pack_txns}")

    # cross-check: the card_id we derive for each flagged txn matches the pack
    pack_check = cp.merge(
        txi[["TransactionID", "card_id", "customer_id"]],
        left_on="flagged_txn_id", right_on="TransactionID", how="left",
    )
    mism = pack_check[pack_check.card_id_x.fillna("") != pack_check.card_id_y.fillna("")] \
        if "card_id_x" in pack_check.columns else \
        pack_check[pack_check.card_id.fillna("") != pack_check.card_id.fillna("")]
    quality["case_pack_card_id_mismatches"] = int(len(mism))

    quality["counts"] = {
        "transactions_in": n0,
        "transactions_out": int(len(txi)),
        "customers": int(len(customers)),
        "cards": int(len(cards)),
        "device_profiles": int(len(dev)),
        "billing_regions": int(len(reg)),
        "email_domain_roles": int(len(em)),
        "closed_cases": int(len(cc)),
    }
    PATHS.quality_report.write_text(json.dumps(quality, indent=2, default=str))
    if verbose:
        print(json.dumps(quality["counts"], indent=2))
        if quality["errors"]:
            print("ERRORS:", *quality["errors"], sep="\n  - ")
        if quality["warnings"]:
            print("WARNINGS:", *quality["warnings"], sep="\n  - ")
        print(f"Quality report -> {PATHS.quality_report}")
    return quality


if __name__ == "__main__":
    build()

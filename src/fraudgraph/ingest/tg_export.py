"""Write load-ready CSVs for TigerGraph into ``build/tg_csv/``.

Compact by design: the 397-column raw file becomes an 18-column transaction file
so the upload to a cloud workspace is tens of megabytes rather than 700.

Run:  python -m fraudgraph.ingest.tg_export
"""
from __future__ import annotations

import json

import pandas as pd

from ..config import PATHS
from ..policy import rules as R

OUT = "tg_csv"
DT = "%Y-%m-%d %H:%M:%S"
EPOCH = "1970-01-01 00:00:00"


def _dt(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce").dt.strftime(DT).fillna(EPOCH)


def _s(s: pd.Series) -> pd.Series:
    return s.astype("string").fillna("").str.replace(r"[\r\n]+", " ", regex=True)


def _num(s: pd.Series, default: float = 0.0) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(default)


def export(verbose: bool = True) -> dict:
    out = PATHS.build / OUT
    out.mkdir(parents=True, exist_ok=True)
    stats: dict[str, int] = {}

    tx = pd.read_parquet(PATHS.build / "tx_index.parquet")

    # ---- transactions -----------------------------------------------------
    t = pd.DataFrame({
        "txn_id": tx.TransactionID.astype("int64").astype(str),
        "card_id": _s(tx.card_id),
        "customer_id": _s(tx.customer_id),
        "ts": _dt(tx.ts),
        "amount": _num(tx.TransactionAmt),
        "product_cd": _s(tx.ProductCD),
        "channel": _s(tx.channel),
        "risk_score": _num(tx.risk_score),
        "addr1": _s(tx.addr1.map(lambda v: "" if pd.isna(v) else f"{float(v):.0f}")),
        "addr2": _s(tx.addr2.map(lambda v: "" if pd.isna(v) else f"{float(v):.0f}")),
        "dist1": _num(tx.dist1, -1.0),
        "p_email": _s(tx.P_emaildomain),
        "r_email": _s(tx.R_emaildomain),
        "device_profile": _s(tx.device_profile),
        "device_new": _s(tx.id_15),
        "proxy_flag": _s(tx.id_23),
        "match_status": _s(tx.id_34),
        "seq_in_card": _num(tx.seq_in_card).astype(int),
    })
    t.to_csv(out / "transactions.csv", index=False)
    stats["transactions"] = len(t)

    # ---- NEXT edges (transaction order within a card) ---------------------
    nxt = tx[["TransactionID", "next_txn_id", "gap_prev_s"]].dropna(subset=["next_txn_id"])
    nxt = pd.DataFrame({
        "from_txn": nxt.TransactionID.astype("int64").astype(str),
        "to_txn": nxt.next_txn_id.astype("float").astype("int64").astype(str),
        "gap_s": _num(nxt.gap_prev_s),
    })
    nxt.to_csv(out / "edge_next.csv", index=False)
    stats["edge_next"] = len(nxt)

    # ---- cards / customers ------------------------------------------------
    cards = pd.read_parquet(PATHS.cards_parquet)
    c = pd.DataFrame({
        "card_id": _s(cards.card_id), "customer_id": _s(cards.customer_id),
        "card1": _num(cards.card1).astype(int), "network": _s(cards.network),
        "card_type": _s(cards.card_type), "n_txns": _num(cards.n_txns).astype(int),
        "first_ts": _dt(cards.first_ts), "last_ts": _dt(cards.last_ts),
        "total_amt": _num(cards.total_amt), "median_amt": _num(cards.median_amt),
        "max_amt": _num(cards.max_amt), "n_online": _num(cards.n_online).astype(int),
        "n_in_person": _num(cards.n_in_person).astype(int), "mean_risk": _num(cards.mean_risk),
    })
    c.to_csv(out / "cards.csv", index=False)
    stats["cards"] = len(c)

    cust = pd.read_parquet(PATHS.build / "customers.parquet")
    u = pd.DataFrame({
        "customer_id": _s(cust.customer_id), "n_cards": _num(cust.n_cards).astype(int),
        "n_txns": _num(cust.n_txns).astype(int), "first_ts": _dt(cust.first_ts),
        "last_ts": _dt(cust.last_ts), "total_amt": _num(cust.total_amt),
        "home_region": _s(cust.home_region.map(lambda v: "" if pd.isna(v) else f"{float(v):.0f}")),
        "home_country": _s(cust.home_country.map(lambda v: "" if pd.isna(v) else f"{float(v):.0f}")),
    })
    u.to_csv(out / "customers.csv", index=False)
    stats["customers"] = len(u)

    # ---- device profiles --------------------------------------------------
    dev = pd.read_parquet(PATHS.devices_parquet)
    parts = dev.device_profile.str.split(" | ", regex=False)
    d = pd.DataFrame({
        "device_profile": _s(dev.device_profile),
        "device_type": _s(dev.device_type),
        "device_info": _s(parts.str[0]), "os": _s(parts.str[1]),
        "browser": _s(parts.str[2]), "screen": _s(parts.str[3]),
        "proxy_flags": _s(dev.proxy_flags), "n_txns": _num(dev.n_txns).astype(int),
        "n_cards": _num(dev.n_cards).astype(int), "n_customers": _num(dev.n_customers).astype(int),
        "n_new_for_account": _num(dev.n_new_for_account).astype(int),
        "first_ts": _dt(dev.first_ts), "last_ts": _dt(dev.last_ts),
        "total_amt": _num(dev.total_amt),
    })
    d.to_csv(out / "device_profiles.csv", index=False)
    stats["device_profiles"] = len(d)

    # ---- regions / emails -------------------------------------------------
    reg = pd.read_parquet(PATHS.build / "regions.parquet")
    r = pd.DataFrame({
        "region_id": reg.region_id.map(lambda v: f"{float(v):.0f}"),
        "country": _s(reg.country.map(lambda v: "" if pd.isna(v) else f"{float(v):.0f}")),
        "n_txns": _num(reg.n_txns).astype(int), "n_cards": _num(reg.n_cards).astype(int),
        "n_customers": _num(reg.n_customers).astype(int),
    })
    r.to_csv(out / "regions.csv", index=False)
    stats["regions"] = len(r)

    em = pd.read_parquet(PATHS.build / "email_domains.parquet")
    e = em.groupby("domain", as_index=False).n_txns.sum()
    e.to_csv(out / "email_domains.csv", index=False)
    stats["email_domains"] = len(e)

    # ---- closed cases + their edges ---------------------------------------
    cc = pd.read_parquet(PATHS.closed_cases_parquet)
    k = pd.DataFrame({
        "case_id": _s(cc.case_id), "customer_id": _s(cc.customer_id), "card_id": _s(cc.card_id),
        "opened_at": _dt(cc.opened_at), "closed_at": _dt(cc.closed_at),
        "outcome": _s(cc.outcome), "pattern": _s(cc.pattern),
        "first_fraud_txn_id": _s(
            cc.first_fraud_txn_id.map(lambda v: "" if pd.isna(v) else f"{int(v)}")
        ),
        "n_txns": _num(cc.n_txns).astype(int), "exposure_usd": _num(cc.exposure_usd),
        "actions_taken": _s(cc.actions_taken), "report_filed": _s(cc.report_filed),
        "analyst_notes": _s(cc.analyst_notes),
    })
    k.to_csv(out / "closed_cases.csv", index=False)
    stats["closed_cases"] = len(k)

    ct = cc[["case_id", "txn_id_list"]].explode("txn_id_list").dropna(subset=["txn_id_list"])
    ct = pd.DataFrame({
        "case_id": _s(ct.case_id),
        "txn_id": ct.txn_id_list.astype("int64").astype(str),
    })
    ct.to_csv(out / "edge_case_txn.csv", index=False)
    stats["edge_case_txn"] = len(ct)

    cn = cc[["case_id", "connected_card_list"]].explode("connected_card_list") \
        .dropna(subset=["connected_card_list"])
    cn = pd.DataFrame({"case_id": _s(cn.case_id), "card_id": _s(cn.connected_card_list)})
    cn.to_csv(out / "edge_case_connected.csv", index=False)
    stats["edge_case_connected"] = len(cn)

    # ---- fraud typologies and policy rules (GraphRAG text in the graph) ----
    from ..analysis.patterns import RING_MIN_CARDS

    patterns = [
        ("card_testing", "Card testing", True,
         "A stolen card number is checked before use: three or more tiny online "
         "authorisations, often under $5, then a larger purchase. Confirmed by the sequence "
         "itself.", "R5"),
        ("card_not_present_fraud", "Card-not-present fraud", True,
         "The number is used online without the card. Amounts and products that do not fit the "
         "cardholder's history, often in a burst of two to four within 48 hours. One unusual "
         "online purchase on its own is ambiguous: verify.", "R1|R2|R3|R4"),
        ("card_not_present_new_device", "Card-not-present fraud from a new device", True,
         "As card-not-present fraud, with the identity record marking the device New for this "
         "account, sometimes behind a proxy. Stronger than pattern 2, still not proof: people "
         "buy new phones.", "R1|R2|R3|R4"),
        ("out_of_region_use", "Out-of-region use", True,
         "Card-present purchases in a billing region the cardholder has no history in, while "
         "their normal activity continues at home. Several days of purchases in one new region "
         "is a trip, not a clone.", "R2|R3"),
        ("account_takeover", "Account takeover", True,
         "Mixed-channel activity inconsistent with the cardholder, often with device and "
         "match-flag anomalies, pointing to stolen credentials rather than a stolen number.", "R2"),
        ("shared_device_ring", "Coordinated shared-device ring", False,
         "A single device fingerprint, always behind an anonymising proxy and marked New for "
         f"every account it touches, appears on {RING_MIN_CARDS}+ unrelated cardholders' cards "
         "inside a few weeks. Read from the analyst notes of closed cases CC-2649, CC-2971, "
         "CC-2985 and CC-3035, which the bank recorded as matching no documented typology.",
         "R6|R9"),
        ("sub_threshold_structuring", "Sub-threshold structuring", False,
         "Several online purchases inside a single short window, each priced just below a round "
         "authorisation threshold so no individual charge triggers review, while the combined "
         "total far exceeds it. Read from the analyst notes of closed cases CC-3748, CC-3841, "
         "CC-3907, CC-4086 and CC-4124.", "R9"),
    ]
    pd.DataFrame(
        patterns, columns=["pattern_id", "name", "documented", "description", "policy_rules"]
    )[["pattern_id", "name", "description", "documented", "policy_rules"]].to_csv(
        out / "fraud_patterns.csv", index=False
    )
    stats["fraud_patterns"] = len(patterns)

    pol = pd.DataFrame(
        [(r_.id, r_.summary, r_.summary) for r_ in R.RULES.values()],
        columns=["rule_id", "summary", "rule_text"],
    )
    pol.to_csv(out / "policy_rules.csv", index=False)
    stats["policy_rules"] = len(pol)

    (out / "export_stats.json").write_text(json.dumps(stats, indent=2))
    if verbose:
        print(json.dumps(stats, indent=2))
        total = sum(f.stat().st_size for f in out.glob("*.csv"))
        print(f"  -> {out}  ({total / 1e6:.1f} MB across {len(list(out.glob('*.csv')))} files)")
    return stats


if __name__ == "__main__":
    export()

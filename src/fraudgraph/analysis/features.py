"""Turn gathered graph evidence into a typed, explainable feature set.

Every feature here is something an analyst could state in a sentence and point
at a graph query for.  Nothing in this module calls an LLM.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

SMALL_AUTH_ABS = 5.0          # "often under $5" -- dataset README, pattern 1
ROUND_THRESHOLDS = (500.0, 1000.0, 2000.0)
STRUCTURING_BAND = 0.88       # amounts in [band*T, T) count as "just under T"


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


@dataclass
class CaseContext:
    """Raw evidence gathered from the graph for one alert."""

    case_id: str
    txn: dict = field(default_factory=dict)
    card_id: str = ""
    customer_id: str = ""
    profile: dict = field(default_factory=dict)
    window: dict = field(default_factory=dict)
    wide_window: dict = field(default_factory=dict)
    region_test: dict = field(default_factory=dict)
    device_test: dict = field(default_factory=dict)
    device_ring: dict = field(default_factory=dict)
    region_cluster: dict = field(default_factory=dict)
    customer_cards: dict = field(default_factory=dict)
    prior_cases_card: dict = field(default_factory=dict)
    prior_cases_customer: dict = field(default_factory=dict)
    prior_cases_device: dict = field(default_factory=dict)
    prior_cases_region: dict = field(default_factory=dict)
    similar_cases: dict = field(default_factory=dict)
    trigger: dict = field(default_factory=dict)

    @property
    def ts(self) -> str:
        return str(self.txn.get("ts") or "")

    @property
    def amount(self) -> float:
        return _f(self.txn.get("TransactionAmt"))


@dataclass
class Features:
    """Derived signals. Each is independently explainable."""

    # identity
    txn_id: str = ""
    card_id: str = ""
    customer_id: str = ""
    ts: str = ""
    amount: float = 0.0
    channel: str = ""
    product_cd: str = ""
    bank_risk_score: float = 0.0

    # history baseline
    hist_n_txns: int = 0
    hist_days: float = 0.0
    hist_median_amt: float = 0.0
    hist_p95_amt: float = 0.0
    hist_max_amt: float = 0.0
    amt_over_p95: float = 0.0        # amount / p95, 1.0 == at the 95th pct
    amt_over_max: float = 0.0        # amount / historical max
    amt_log_z: float = 0.0           # z-score of log(amount) against history
    amount_novel: bool = False       # above historical max by a clear margin

    # channel / product novelty
    channel_share: float = 1.0       # share of history using this channel
    channel_novel: bool = False
    product_share: float = 1.0
    product_novel: bool = False
    email_novel: bool = False

    # region
    region_id: float | None = None
    region_prior_txns: int = 0
    region_novel: bool = False
    home_region: float | None = None
    n_regions_seen: int = 0
    home_activity_continues: bool = False   # home-region activity around the alert
    region_days_span: float = 0.0           # how long activity in the new region lasts

    # device
    device_profile: str = ""
    device_prior_txns: int = 0
    device_novel: bool = False
    device_marked_new: bool = False
    proxy_flag: str = ""
    anonymous_proxy: bool = False
    match_status: str = ""
    match_flag_anomaly: bool = False
    n_devices_seen: int = 0

    # burst shape
    n_txns_1h: int = 0
    n_txns_24h: int = 0
    n_txns_48h: int = 0
    amt_24h: float = 0.0
    online_run_len: int = 0
    burst_rate_ratio: float = 1.0    # observed 48h rate / historical rate

    # shared origin
    ring_cards: int = 0
    ring_txns: int = 0
    ring_new_fraction: float = 0.0
    ring_anon_fraction: float = 0.0
    ring_lifetime_cards: int = 0
    region_cluster_cards: int = 0

    # recurrence (policy R7)
    same_amount_before: int = 0
    recurring_cadence_days: float | None = None
    looks_recurring: bool = False

    # memory
    prior_fraud_cases_card: int = 0
    prior_fraud_cases_customer: int = 0
    prior_cleared_cases_customer: int = 0
    prior_cases_on_device: int = 0
    customer_n_cards: int = 0

    # bookkeeping
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d.pop("notes", None)
        return d


def _log_stats(profile: dict) -> tuple[float, float]:
    """mean/std of log1p(amount) reconstructed from the profile summary."""
    a = profile.get("amount") or {}
    mean, std = _f(a.get("mean")), _f(a.get("std"))
    if mean <= 0:
        return 0.0, 0.0
    # log-space approximation from the arithmetic moments (Fenton-Wilkinson)
    var = std * std
    mu = math.log(mean**2 / math.sqrt(var + mean**2)) if var + mean**2 > 0 else 0.0
    sigma = math.sqrt(math.log(1 + var / mean**2)) if mean > 0 else 0.0
    return mu, sigma


def compute(ctx: CaseContext) -> Features:
    t = ctx.txn
    p = ctx.profile or {}
    f = Features(
        txn_id=str(t.get("TransactionID", "")),
        card_id=ctx.card_id,
        customer_id=ctx.customer_id,
        ts=ctx.ts,
        amount=ctx.amount,
        channel=str(t.get("channel") or ""),
        product_cd=str(t.get("ProductCD") or ""),
        bank_risk_score=_f(t.get("risk_score")),
        device_profile=str(t.get("device_profile") or ""),
        proxy_flag=str(t.get("id_23") or ""),
        match_status=str(t.get("id_34") or ""),
    )
    f.device_marked_new = str(t.get("id_15") or "") == "New"
    f.anonymous_proxy = f.proxy_flag in ("IP_PROXY:ANONYMOUS", "IP_PROXY:HIDDEN")
    f.match_flag_anomaly = f.match_status in ("match_status:0", "match_status:-1")

    # ---------------- history baseline ----------------
    if p and not p.get("empty"):
        a = p.get("amount") or {}
        f.hist_n_txns = int(p.get("n_txns") or 0)
        f.hist_median_amt = _f(a.get("median"))
        f.hist_p95_amt = _f(a.get("p95"))
        f.hist_max_amt = _f(a.get("max"))
        if p.get("first_ts") and p.get("last_ts"):
            f.hist_days = max(
                0.0, (pd.Timestamp(p["last_ts"]) - pd.Timestamp(p["first_ts"])).total_seconds() / 86400
            )
        f.amt_over_p95 = f.amount / f.hist_p95_amt if f.hist_p95_amt > 0 else 0.0
        f.amt_over_max = f.amount / f.hist_max_amt if f.hist_max_amt > 0 else 0.0
        mu, sigma = _log_stats(p)
        if sigma > 0:
            f.amt_log_z = (math.log(max(f.amount, 0.01)) - mu) / sigma
        f.amount_novel = f.amt_over_max > 1.15 and f.hist_n_txns >= 10

        chans = p.get("channels") or {}
        total = sum(chans.values()) or 1
        f.channel_share = chans.get(f.channel, 0) / total
        f.channel_novel = f.channel_share < 0.05 and f.hist_n_txns >= 20

        prods = {r["ProductCD"]: r["n"] for r in (p.get("product_codes") or [])}
        ptot = sum(prods.values()) or 1
        f.product_share = prods.get(f.product_cd, 0) / ptot
        f.product_novel = prods.get(f.product_cd, 0) == 0 and f.hist_n_txns >= 15

        f.n_regions_seen = len(p.get("regions") or [])
        f.n_devices_seen = len(p.get("device_profiles") or [])

        seen_emails = {r["domain"] for r in (p.get("purchaser_email_domains") or [])}
        pe = t.get("P_emaildomain")
        f.email_novel = bool(pe) and pe not in seen_emails and f.hist_n_txns >= 20

    # ---------------- region ----------------
    rt = ctx.region_test or {}
    if rt:
        f.region_id = rt.get("region_id")
        f.region_prior_txns = int(rt.get("prior_txns_in_region") or 0)
        f.home_region = rt.get("home_region")
        f.region_novel = f.region_prior_txns == 0 and int(rt.get("prior_txns_total") or 0) >= 20
        f.n_regions_seen = max(f.n_regions_seen, int(rt.get("distinct_prior_regions") or 0))

    # ---------------- device ----------------
    dt = ctx.device_test or {}
    if dt:
        f.device_prior_txns = int(dt.get("prior_txns_on_device") or 0)
        f.device_novel = (
            f.device_prior_txns == 0 and int(dt.get("prior_online_txns") or 0) >= 5
        )

    # ---------------- burst ----------------
    win = (ctx.window or {}).get("transactions") or []
    if win:
        centre = pd.Timestamp(ctx.ts)
        rows = [(pd.Timestamp(x["ts"]), _f(x["TransactionAmt"]), x.get("channel")) for x in win]
        for hours, attr in ((1, "n_txns_1h"), (24, "n_txns_24h"), (48, "n_txns_48h")):
            n = sum(1 for ts, _, _ in rows if abs((ts - centre).total_seconds()) <= hours * 3600)
            setattr(f, attr, n)
        f.amt_24h = sum(
            amt for ts, amt, _ in rows if abs((ts - centre).total_seconds()) <= 24 * 3600
        )
        # longest run of consecutive online transactions containing the alert
        ordered = sorted(rows)
        idx = next((i for i, (ts, _, _) in enumerate(ordered) if ts == centre), None)
        if idx is not None:
            run = 1
            i = idx - 1
            while i >= 0 and ordered[i][2] == "online":
                run += 1
                i -= 1
            i = idx + 1
            while i < len(ordered) and ordered[i][2] == "online":
                run += 1
                i += 1
            f.online_run_len = run if f.channel == "online" else 0
        if f.hist_n_txns >= 20 and f.hist_days > 0:
            hist_rate_48h = f.hist_n_txns / max(f.hist_days, 1.0) * 2.0
            f.burst_rate_ratio = f.n_txns_48h / hist_rate_48h if hist_rate_48h > 0 else 1.0

    # ---------------- shared origin ----------------
    dr = ctx.device_ring or {}
    if dr:
        f.ring_cards = int(dr.get("n_cards") or 0)
        f.ring_txns = int(dr.get("n_txns") or 0)
        life = dr.get("lifetime") or {}
        f.ring_lifetime_cards = int(life.get("n_cards") or 0)
        lt_txns = int(life.get("n_txns") or 0)
        if lt_txns:
            f.ring_new_fraction = _f(life.get("n_new_for_account")) / lt_txns
        flags = dr.get("proxy_flags") or []
        f.ring_anon_fraction = 1.0 if ("IP_PROXY:ANONYMOUS" in flags or "IP_PROXY:HIDDEN" in flags) else 0.0
        if life.get("proxy_flags"):
            pf = str(life["proxy_flags"])
            f.ring_anon_fraction = 1.0 if "ANONYMOUS" in pf or "HIDDEN" in pf else 0.0
    rc = ctx.region_cluster or {}
    if rc:
        f.region_cluster_cards = int(rc.get("n_cards") or 0)

    # ---------------- recurrence (policy R7) ----------------
    # Policy R7 says "same merchant, same amount, monthly".  The dataset has no
    # merchant column, so the merchant proxy is (ProductCD, billing region,
    # purchaser email domain) -- stated as a proxy wherever it is cited.
    hist_rows = (ctx.wide_window or {}).get("transactions") or []
    merchant_key = (f.product_cd, t.get("addr1"), t.get("P_emaildomain"))
    same = [
        pd.Timestamp(x["ts"])
        for x in hist_rows
        if abs(_f(x["TransactionAmt"]) - f.amount) < 0.01
        and str(x["ts"]) != str(ctx.ts)
        and pd.Timestamp(x["ts"]) < pd.Timestamp(ctx.ts)
        and (x.get("ProductCD"), x.get("addr1"), x.get("P_emaildomain")) == merchant_key
    ]
    f.same_amount_before = len(same)
    if len(same) >= 2:
        d = sorted(same)
        gaps = [(d[i + 1] - d[i]).days for i in range(len(d) - 1)]
        gaps = [g for g in gaps if g > 0]
        if gaps:
            mean_gap = sum(gaps) / len(gaps)
            spread = max(gaps) - min(gaps)
            f.recurring_cadence_days = round(mean_gap, 1)
            # monthly-ish and regular: the R7 shape
            f.looks_recurring = 20 <= mean_gap <= 40 and spread <= 12

    # ---------------- memory ----------------
    pcc = ctx.prior_cases_card or {}
    direct = pcc.get("direct") or []
    f.prior_fraud_cases_card = sum(1 for c in direct if c.get("outcome") == "confirmed_fraud")
    pcu = (ctx.prior_cases_customer or {}).get("cases") or []
    f.prior_fraud_cases_customer = sum(1 for c in pcu if c.get("outcome") == "confirmed_fraud")
    f.prior_cleared_cases_customer = sum(1 for c in pcu if c.get("outcome") == "cleared")
    f.prior_cases_on_device = int((ctx.prior_cases_device or {}).get("n") or 0)
    f.customer_n_cards = int((ctx.customer_cards or {}).get("n_cards") or 0)

    return f

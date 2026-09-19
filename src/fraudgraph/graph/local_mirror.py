"""Pandas-backed implementation of the graph query catalogue.

Why this exists
---------------
TigerGraph is the system of record for this project.  This mirror serves the
same named queries over the parquet cache so that

* the investigation logic can be unit-tested without a live database,
* development is not blocked when the Savanna workspace is asleep,
* every graph result can be cross-checked against an independent implementation.

It is **not** a substitute for the graph in the deliverable: every query result
carries ``backend`` provenance, the answer files record which backend served
each piece of evidence, and cases are written to TigerGraph.
"""
from __future__ import annotations

import threading
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

from ..config import PATHS

_LOAD_LOCK = threading.Lock()

TX_COLUMNS = [
    "TransactionID", "customer_id", "card_id", "ts", "TransactionAmt",
    "ProductCD", "channel", "risk_score", "addr1", "addr2", "dist1", "dist2",
    "P_emaildomain", "R_emaildomain", "device_profile", "DeviceType",
    "id_15", "id_23", "id_30", "id_31", "id_33", "id_34", "id_12", "id_16",
    "id_28", "id_29", "id_31", "id_35", "id_36", "id_37", "id_38",
    "card1", "card2", "card3", "card4", "card5", "card6",
    "seq_in_card", "prev_txn_id", "next_txn_id", "gap_prev_s",
    "C1", "C2", "C5", "C13", "C14", "D1", "D2", "D3", "D4", "D10", "D15",
    "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9",
]


class _Data:
    """Lazily loaded, process-wide in-memory mirror of the graph."""

    _instance: "_Data | None" = None

    def __init__(self) -> None:
        avail = pd.read_parquet(PATHS.build / "tx_index.parquet", columns=None).columns
        cols = [c for i, c in enumerate(dict.fromkeys(TX_COLUMNS)) if c in set(avail)]
        self.tx = pd.read_parquet(PATHS.build / "tx_index.parquet", columns=cols)
        self.tx["TransactionID"] = self.tx["TransactionID"].astype("int64")
        self.tx = self.tx.sort_values(["card_id", "ts"]).reset_index(drop=True)

        self.by_txn = pd.Series(self.tx.index.values, index=self.tx.TransactionID.values)
        self.by_card = self.tx.groupby("card_id").indices
        self.by_customer = self.tx.groupby("customer_id").indices
        # NOTE: groupby().indices are *positional* offsets into the frame they
        # were built from, so they must be built from the full `self.tx` (groupby
        # already drops null keys).  Building them from a filtered frame would
        # silently return the wrong rows.
        self.by_device = self.tx.groupby("device_profile").indices
        self.by_region = self.tx.groupby("addr1").indices

        self.cards = pd.read_parquet(PATHS.cards_parquet)
        self.customers = pd.read_parquet(PATHS.build / "customers.parquet")
        self.devices = pd.read_parquet(PATHS.devices_parquet)
        self.regions = pd.read_parquet(PATHS.build / "regions.parquet")
        self.closed = pd.read_parquet(PATHS.closed_cases_parquet)

        # closed-case reverse indices, built once
        exploded = (
            self.closed[["case_id", "txn_id_list"]]
            .explode("txn_id_list")
            .dropna(subset=["txn_id_list"])
        )
        exploded["txn_id_list"] = exploded["txn_id_list"].astype("int64")
        self.case_txns = exploded.rename(columns={"txn_id_list": "TransactionID"})
        self.txn_to_case = self.case_txns.groupby("TransactionID")["case_id"].apply(list).to_dict()
        conn = (
            self.closed[["case_id", "connected_card_list"]]
            .explode("connected_card_list")
            .dropna(subset=["connected_card_list"])
        )
        self.case_connected = conn.rename(columns={"connected_card_list": "card_id"})

    @classmethod
    def get(cls) -> "_Data":
        if cls._instance is None:
            with _LOAD_LOCK:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance


def _rows(idx) -> pd.DataFrame:
    d = _Data.get()
    if idx is None or len(idx) == 0:
        return d.tx.iloc[0:0]
    return d.tx.iloc[idx]


def _clean(v: Any) -> Any:
    """Make a value JSON-safe and null-consistent."""
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if np.isnan(f) else f
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, float) and np.isnan(v):
        return None
    if v is pd.NaT:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _records(df: pd.DataFrame, columns: list[str] | None = None) -> list[dict]:
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    return [{k: _clean(v) for k, v in rec.items()} for rec in df.to_dict("records")]


TXN_OUT = [
    "TransactionID", "card_id", "customer_id", "ts", "TransactionAmt", "ProductCD",
    "channel", "risk_score", "addr1", "addr2", "dist1", "P_emaildomain",
    "R_emaildomain", "device_profile", "DeviceType", "id_15", "id_23", "id_30",
    "id_31", "id_33", "id_34", "seq_in_card", "gap_prev_s",
]


class LocalMirrorBackend:
    """Implements the query catalogue over the parquet cache."""

    name = "local"

    # ---------------------------------------------------------------- basics
    def txn_detail(self, txn_id: int | str) -> dict:
        d = _Data.get()
        tid = int(txn_id)
        if tid not in d.by_txn.index:
            return {"found": False, "txn_id": tid}
        row = d.tx.loc[d.by_txn.loc[tid]]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        rec = {k: _clean(v) for k, v in row.items()}
        rec["found"] = True
        rec["match_flags"] = {
            f"M{i}": rec.get(f"M{i}") for i in range(1, 10) if rec.get(f"M{i}") is not None
        }
        rec["prior_closed_cases"] = d.txn_to_case.get(tid, [])
        return rec

    def card_window(
        self,
        card_id: str,
        center_ts: str,
        hours_before: float = 48.0,
        hours_after: float = 48.0,
        limit: int = 500,
    ) -> dict:
        d = _Data.get()
        df = _rows(d.by_card.get(card_id))
        center = pd.Timestamp(center_ts)
        lo = center - pd.Timedelta(hours=float(hours_before))
        hi = center + pd.Timedelta(hours=float(hours_after))
        sel = df[(df.ts >= lo) & (df.ts <= hi)].sort_values("ts").head(int(limit))
        return {
            "card_id": card_id,
            "window": [lo.strftime("%Y-%m-%d %H:%M:%S"), hi.strftime("%Y-%m-%d %H:%M:%S")],
            "n": int(len(sel)),
            "transactions": _records(sel, TXN_OUT),
        }

    def card_timeline(
        self, card_id: str, from_ts: str | None = None, to_ts: str | None = None, limit: int = 1000
    ) -> dict:
        d = _Data.get()
        df = _rows(d.by_card.get(card_id)).sort_values("ts")
        if from_ts:
            df = df[df.ts >= pd.Timestamp(from_ts)]
        if to_ts:
            df = df[df.ts <= pd.Timestamp(to_ts)]
        return {"card_id": card_id, "n": int(len(df)), "transactions": _records(df.head(int(limit)), TXN_OUT)}

    def card_profile(self, card_id: str, before_ts: str, lookback_days: int = 365) -> dict:
        """The cardholder's established behaviour strictly before ``before_ts``."""
        d = _Data.get()
        df = _rows(d.by_card.get(card_id))
        cut = pd.Timestamp(before_ts)
        lo = cut - pd.Timedelta(days=int(lookback_days))
        hist = df[(df.ts < cut) & (df.ts >= lo)]
        if hist.empty:
            return {
                "card_id": card_id, "before": str(cut), "n_txns": 0, "empty": True,
                "regions": [], "product_codes": [], "device_profiles": [], "channels": {},
            }
        amt = hist.TransactionAmt.astype(float)
        regions = (
            hist.addr1.dropna().value_counts().head(25).rename_axis("region_id")
            .reset_index(name="n")
        )
        prods = hist.ProductCD.value_counts().rename_axis("ProductCD").reset_index(name="n")
        devs = (
            hist.device_profile.dropna().value_counts().head(25)
            .rename_axis("device_profile").reset_index(name="n")
        )
        emails = (
            hist.P_emaildomain.dropna().value_counts().head(15)
            .rename_axis("domain").reset_index(name="n")
        )
        remails = (
            hist.R_emaildomain.dropna().value_counts().head(15)
            .rename_axis("domain").reset_index(name="n")
        )
        return {
            "card_id": card_id,
            "before": cut.strftime("%Y-%m-%d %H:%M:%S"),
            "n_txns": int(len(hist)),
            "first_ts": _clean(hist.ts.min()),
            "last_ts": _clean(hist.ts.max()),
            "amount": {
                "min": float(amt.min()), "p25": float(amt.quantile(0.25)),
                "median": float(amt.median()), "p75": float(amt.quantile(0.75)),
                "p95": float(amt.quantile(0.95)), "max": float(amt.max()),
                "mean": float(amt.mean()), "std": float(amt.std(ddof=0)) if len(amt) > 1 else 0.0,
                "total": float(amt.sum()),
            },
            "channels": {k: int(v) for k, v in hist.channel.value_counts().items()},
            "product_codes": _records(prods),
            "regions": _records(regions),
            "device_profiles": _records(devs),
            "purchaser_email_domains": _records(emails),
            "recipient_email_domains": _records(remails),
            "mean_risk_score": float(hist.risk_score.mean()),
            "empty": False,
        }

    def customer_cards(self, customer_id: str) -> dict:
        d = _Data.get()
        sub = d.cards[d.cards.customer_id == customer_id]
        return {"customer_id": customer_id, "n_cards": int(len(sub)), "cards": _records(sub)}

    # -------------------------------------------------------- shared origin
    def device_neighbors(
        self,
        device_profile: str,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 200,
    ) -> dict:
        d = _Data.get()
        df = _rows(d.by_device.get(device_profile))
        if from_ts:
            df = df[df.ts >= pd.Timestamp(from_ts)]
        if to_ts:
            df = df[df.ts <= pd.Timestamp(to_ts)]
        if df.empty:
            return {"device_profile": device_profile, "n_cards": 0, "cards": [], "n_txns": 0,
                    "window": [from_ts, to_ts], "total_amount": 0.0}
        grp = (
            df.groupby(["card_id", "customer_id"])
            .agg(n_txns=("TransactionID", "size"), amount=("TransactionAmt", "sum"),
                 first_ts=("ts", "min"), last_ts=("ts", "max"),
                 marked_new=("id_15", lambda s: int((s == "New").sum())))
            .reset_index()
            .sort_values("n_txns", ascending=False)
            .head(int(limit))
        )
        prof = d.devices[d.devices.device_profile == device_profile]
        return {
            "device_profile": device_profile,
            "window": [from_ts, to_ts],
            "n_cards": int(df.card_id.nunique()),
            "n_customers": int(df.customer_id.nunique()),
            "n_txns": int(len(df)),
            "total_amount": float(df.TransactionAmt.sum()),
            "proxy_flags": sorted({str(x) for x in df.id_23.dropna().unique()}),
            "lifetime": _records(prof)[0] if len(prof) else None,
            "cards": _records(grp),
        }

    def region_neighbors(
        self, region_id: float | str, from_ts: str | None = None,
        to_ts: str | None = None, limit: int = 200,
    ) -> dict:
        d = _Data.get()
        key = float(region_id)
        df = _rows(d.by_region.get(key))
        if from_ts:
            df = df[df.ts >= pd.Timestamp(from_ts)]
        if to_ts:
            df = df[df.ts <= pd.Timestamp(to_ts)]
        if df.empty:
            return {"region_id": key, "n_cards": 0, "cards": [], "n_txns": 0}
        grp = (
            df.groupby(["card_id", "customer_id"])
            .agg(n_txns=("TransactionID", "size"), amount=("TransactionAmt", "sum"),
                 first_ts=("ts", "min"), last_ts=("ts", "max"))
            .reset_index().sort_values("amount", ascending=False).head(int(limit))
        )
        lifetime = d.regions[d.regions.region_id == key]
        return {
            "region_id": key,
            "window": [from_ts, to_ts],
            "n_cards": int(df.card_id.nunique()),
            "n_customers": int(df.customer_id.nunique()),
            "n_txns": int(len(df)),
            "lifetime_n_cards": int(lifetime.n_cards.iloc[0]) if len(lifetime) else None,
            "lifetime_n_txns": int(lifetime.n_txns.iloc[0]) if len(lifetime) else None,
            "cards": _records(grp),
        }

    def email_neighbors(
        self, domain: str, role: str = "recipient", from_ts: str | None = None,
        to_ts: str | None = None, limit: int = 200,
    ) -> dict:
        d = _Data.get()
        col = "R_emaildomain" if role == "recipient" else "P_emaildomain"
        df = d.tx[d.tx[col] == domain]
        if from_ts:
            df = df[df.ts >= pd.Timestamp(from_ts)]
        if to_ts:
            df = df[df.ts <= pd.Timestamp(to_ts)]
        grp = (
            df.groupby(["card_id", "customer_id"])
            .agg(n_txns=("TransactionID", "size"), amount=("TransactionAmt", "sum"))
            .reset_index().sort_values("n_txns", ascending=False).head(int(limit))
        )
        return {
            "domain": domain, "role": role,
            "n_cards": int(df.card_id.nunique()), "n_txns": int(len(df)),
            "cards": _records(grp),
        }

    # -------------------------------------------------------- history tests
    def region_history_for_card(self, card_id: str, region_id: float | str, before_ts: str) -> dict:
        d = _Data.get()
        df = _rows(d.by_card.get(card_id))
        cut = pd.Timestamp(before_ts)
        hist = df[df.ts < cut]
        key = float(region_id)
        prior = hist[hist.addr1 == key]
        return {
            "card_id": card_id, "region_id": key, "before": str(cut),
            "prior_txns_in_region": int(len(prior)),
            "prior_txns_total": int(len(hist)),
            "distinct_prior_regions": int(hist.addr1.nunique()),
            "home_region": _clean(hist.addr1.mode().iloc[0]) if hist.addr1.notna().any() else None,
            "first_seen_in_region": _clean(prior.ts.min()) if len(prior) else None,
        }

    def device_history_for_card(self, card_id: str, device_profile: str, before_ts: str) -> dict:
        d = _Data.get()
        df = _rows(d.by_card.get(card_id))
        cut = pd.Timestamp(before_ts)
        hist = df[df.ts < cut]
        prior = hist[hist.device_profile == device_profile]
        return {
            "card_id": card_id, "device_profile": device_profile, "before": str(cut),
            "prior_txns_on_device": int(len(prior)),
            "prior_online_txns": int((hist.channel == "online").sum()),
            "distinct_prior_devices": int(hist.device_profile.nunique()),
            "first_seen_on_device": _clean(prior.ts.min()) if len(prior) else None,
        }

    # ------------------------------------------------------------- memory
    @staticmethod
    def _as_of(df: pd.DataFrame, as_of: str | None) -> pd.DataFrame:
        """Only cases already closed at ``as_of`` are available as memory.

        Without this the calibration would score a case using the very
        investigation that produced its label.
        """
        if not as_of or df.empty:
            return df
        return df[df.closed_at < pd.Timestamp(as_of)]

    def closed_cases_for_card(self, card_id: str, as_of: str | None = None) -> dict:
        d = _Data.get()
        direct = self._as_of(d.closed[d.closed.card_id == card_id], as_of)
        conn = d.case_connected[d.case_connected.card_id == card_id]
        connected = self._as_of(d.closed[d.closed.case_id.isin(set(conn.case_id))], as_of)
        return {
            "card_id": card_id,
            "direct": _records(direct.drop(columns=["txn_id_list", "connected_card_list", "action_list"], errors="ignore")),
            "as_connected_card": _records(connected.drop(columns=["txn_id_list", "connected_card_list", "action_list"], errors="ignore")),
        }

    def closed_cases_for_customer(self, customer_id: str, as_of: str | None = None) -> dict:
        d = _Data.get()
        sub = self._as_of(d.closed[d.closed.customer_id == customer_id], as_of)
        return {
            "customer_id": customer_id,
            "n": int(len(sub)),
            "cases": _records(sub.drop(columns=["txn_id_list", "connected_card_list", "action_list"], errors="ignore")),
        }

    def closed_cases_for_device(self, device_profile: str, as_of: str | None = None) -> dict:
        """Closed cases whose transactions came from this device profile."""
        d = _Data.get()
        dev_txns = set(_rows(d.by_device.get(device_profile)).TransactionID.tolist())
        if not dev_txns:
            return {"device_profile": device_profile, "n": 0, "cases": []}
        hit = d.case_txns[d.case_txns.TransactionID.isin(dev_txns)]
        sub = self._as_of(d.closed[d.closed.case_id.isin(set(hit.case_id))], as_of)
        return {
            "device_profile": device_profile,
            "n": int(len(sub)),
            "cases": _records(sub.drop(columns=["txn_id_list", "connected_card_list", "action_list"], errors="ignore")),
        }

    def closed_cases_for_region(self, region_id: float | str) -> dict:
        d = _Data.get()
        key = float(region_id)
        reg_txns = set(_rows(d.by_region.get(key)).TransactionID.tolist())
        hit = d.case_txns[d.case_txns.TransactionID.isin(reg_txns)]
        sub = d.closed[d.closed.case_id.isin(set(hit.case_id))]
        return {
            "region_id": key,
            "n": int(len(sub)),
            "cases": _records(
                sub.drop(columns=["txn_id_list", "connected_card_list", "action_list"], errors="ignore").head(50)
            ),
        }

    def similar_closed_cases(
        self, pattern: str | None = None, channel: str | None = None,
        amount: float | None = None, n_txns: int | None = None, limit: int = 8,
    ) -> dict:
        """Case-memory retrieval scored on pattern, exposure band and burst size."""
        d = _Data.get()
        cand = d.closed.copy()
        if pattern:
            cand = cand[cand.pattern == pattern]
        if cand.empty:
            cand = self._as_of(d.closed.copy(), as_of)
            if exclude_case_ids:
                cand = cand[~cand.case_id.isin(set(exclude_case_ids))]
        score = pd.Series(0.0, index=cand.index)
        if amount is not None and amount > 0:
            ratio = (cand.exposure_usd.astype(float) + 1.0) / (float(amount) + 1.0)
            score -= (np.log(ratio).abs()).fillna(5.0)
        if n_txns:
            score -= (cand.n_txns.astype(float) - float(n_txns)).abs() * 0.4
        if channel == "online":
            score += cand.analyst_notes.str.contains("nline", na=False).astype(float) * 0.5
        elif channel == "in_person":
            score += cand.analyst_notes.str.contains("Card-present|card-present", na=False, regex=True).astype(float) * 0.5
        cand = cand.assign(_score=score).sort_values("_score", ascending=False).head(int(limit))
        return {
            "query": {"pattern": pattern, "channel": channel, "amount": amount, "n_txns": n_txns},
            "n": int(len(cand)),
            "cases": _records(
                cand.drop(columns=["txn_id_list", "connected_card_list", "action_list"], errors="ignore")
            ),
        }

    # ------------------------------------------------------------- writing
    def write_case(self, case: dict) -> dict:
        """The local mirror does not persist agent cases -- TigerGraph does.

        Returning ``written=False`` keeps the answer file honest when the graph
        is unreachable; the orchestrator never reports ``written_to_graph`` true
        on the strength of this backend.
        """
        return {
            "written": False,
            "backend": self.name,
            "reason": "local mirror is read-only; cases are persisted in TigerGraph",
        }

    def read_cases(self, case_id: str | None = None, limit: int = 100) -> dict:
        return {"cases": [], "backend": self.name, "note": "local mirror stores no agent cases"}

    # ------------------------------------------------------------- health
    def ping(self) -> dict:
        d = _Data.get()
        return {
            "backend": self.name, "ok": True,
            "transactions": int(len(d.tx)), "cards": int(len(d.cards)),
            "device_profiles": int(len(d.devices)), "closed_cases": int(len(d.closed)),
        }


@lru_cache(maxsize=1)
def get_local_backend() -> LocalMirrorBackend:
    return LocalMirrorBackend()

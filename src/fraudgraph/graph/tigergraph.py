"""TigerGraph backend: the system of record.

Implements the same query catalogue as the local mirror by calling installed
GSQL queries, and normalises every result into the same shape so that nothing
downstream has to know which backend answered.

Connection settings come from the environment (see ``.env.example``); no
credential is ever written to a log, an answer file or the dashboard.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..config import TG
from .queries import CANDIDATE_POOL, quantile_nearest, rank_similar_cases

DT = "%Y-%m-%d %H:%M:%S"

#: Every edge an AgentCase owns. Cleared before a re-write so the graph always
#: reflects the case's current conclusions rather than the union of every run.
CASE_EDGE_TYPES = (
    "CASE_INVESTIGATES", "CASE_ON_CARD", "CASE_CONNECTED_TO",
    "CASE_CONTAINS_EVIDENCE", "CASE_MATCHES_PATTERN", "CASE_FROM_DEVICE",
    "CASE_CITES_PRIOR", "CASE_APPLIES_RULE",
)


def _conn():
    """Build a pyTigerGraph connection.

    Savanna authenticates tools with a **database secret**, not a password:
    the workspace URL goes in TG_HOST and the secret in TG_SECRET. The secret
    is passed as ``gsqlSecret`` so GSQL DDL (schema, loading jobs, installing
    queries) authenticates too, not just the REST endpoints. Username/password
    still works for a self-hosted Community Edition install.
    """
    import pyTigerGraph as tg

    host = TG.host.rstrip("/")
    kwargs: dict[str, Any] = {
        "host": host,
        "graphname": TG.graph,
        "username": TG.username or "tigergraph",
        "restppPort": TG.rest_port,
        "gsPort": TG.gs_port,
    }
    if TG.secret:
        kwargs["gsqlSecret"] = TG.secret
        kwargs["tgCloud"] = True
        kwargs["sslPort"] = TG.rest_port
    if TG.password:
        kwargs["password"] = TG.password
    conn = tg.TigerGraphConnection(**kwargs)
    if TG.token:
        conn.apiToken = TG.token
    elif TG.secret:
        try:
            conn.getToken(TG.secret)
        except Exception:  # noqa: BLE001 - gsqlSecret already authenticates GSQL
            pass
    return conn


def _dt(v: Any) -> str:
    if v in (None, "", 0):
        return ""
    if isinstance(v, (int, float)):
        try:
            return datetime.utcfromtimestamp(float(v)).strftime(DT)
        except (OverflowError, OSError, ValueError):
            return ""
    s = str(v).replace("T", " ")
    return s[:19]


def _num(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        return default if f != f else f
    except (TypeError, ValueError):
        return default


def _blank_to_none(v: Any) -> Any:
    return None if v in ("", None) else v


def _txn_from_attrs(a: dict) -> dict:
    """Normalise a Transaction vertex into the local mirror's row shape."""
    return {
        "TransactionID": int(a.get("txn_id") or 0),
        "card_id": a.get("card_id") or "",
        "customer_id": a.get("customer_id") or "",
        "ts": _dt(a.get("ts")),
        "TransactionAmt": _num(a.get("amount")),
        "ProductCD": a.get("product_cd") or "",
        "channel": a.get("channel") or "",
        "risk_score": _num(a.get("risk_score")),
        "addr1": float(a["addr1"]) if a.get("addr1") not in ("", None) else None,
        "addr2": float(a["addr2"]) if a.get("addr2") not in ("", None) else None,
        "dist1": (lambda d: None if d < 0 else d)(_num(a.get("dist1"), -1.0)),
        "P_emaildomain": _blank_to_none(a.get("p_email")),
        "R_emaildomain": _blank_to_none(a.get("r_email")),
        "device_profile": _blank_to_none(a.get("device_profile")),
        "id_15": _blank_to_none(a.get("device_new")),
        "id_23": _blank_to_none(a.get("proxy_flag")),
        "id_34": _blank_to_none(a.get("match_status")),
        "seq_in_card": int(_num(a.get("seq_in_card"))),
        "DeviceType": None,
        "id_30": None, "id_31": None, "id_33": None,
        "gap_prev_s": None,
    }


def _first(results: list, key: str, default=None):
    for block in results or []:
        if key in block:
            return block[key]
    return default


class TigerGraphBackend:
    """The catalogue, served by installed GSQL queries."""

    name = "tigergraph"

    def __init__(self, conn=None):
        self._conn = conn or _conn()

    # ----------------------------------------------------------------- util
    def _run(self, query: str, params: dict | None = None) -> list:
        return self._conn.runInstalledQuery(query, params or {}, timeout=120_000)

    def ping(self) -> dict:
        try:
            res = self._run("graph_health")
            return {
                "ok": True, "backend": self.name,
                "transactions": _first(res, "transactions", 0),
                "cards": _first(res, "cards", 0),
                "customers": _first(res, "customers", 0),
                "device_profiles": _first(res, "device_profiles", 0),
                "closed_cases": _first(res, "closed_cases", 0),
                "agent_cases": _first(res, "agent_cases", 0),
                "graph": TG.graph,
            }
        except Exception as exc:  # noqa: BLE001
            from .mcp_backend import _explain

            return {"ok": False, "backend": self.name, "error": _explain(str(exc))}

    # --------------------------------------------------------------- queries
    def txn_detail(self, txn_id: int | str) -> dict:
        res = self._run("txn_detail", {"txn_id": str(txn_id)})
        rows = _first(res, "txn", []) or []
        if not rows:
            return {"found": False, "txn_id": txn_id}
        rec = _txn_from_attrs(rows[0].get("attributes", {}))
        rec["found"] = True
        rec["prior_closed_cases"] = _first(res, "prior_closed_cases", []) or []
        rec["match_flags"] = {}
        return rec

    def card_window(self, card_id: str, center_ts: str, hours_before: float = 48.0,
                    hours_after: float = 48.0, limit: int = 500) -> dict:
        res = self._run("card_window", {
            "card_id": card_id, "center_ts": str(center_ts)[:19],
            # the installed query takes whole seconds -- see queries.gsql
            "sec_before": int(float(hours_before) * 3600),
            "sec_after": int(float(hours_after) * 3600),
            "lim": int(limit),
        })
        rows = _first(res, "transactions", []) or []
        txns = [_txn_from_attrs(r.get("attributes", {})) for r in rows]
        txns.sort(key=lambda r: r["ts"])
        return {"card_id": card_id, "n": len(txns), "transactions": txns,
                "window": [str(center_ts), str(center_ts)]}

    def card_timeline(self, card_id: str, from_ts: str | None = None,
                      to_ts: str | None = None, limit: int = 1000) -> dict:
        res = self._run("card_timeline", {
            "card_id": card_id,
            "from_ts": (from_ts or "2016-01-01 00:00:00")[:19],
            "to_ts": (to_ts or "2017-01-01 00:00:00")[:19],
            "lim": int(limit),
        })
        rows = _first(res, "transactions", []) or []
        txns = sorted((_txn_from_attrs(r.get("attributes", {})) for r in rows),
                      key=lambda r: r["ts"])
        return {"card_id": card_id, "n": len(txns), "transactions": txns}

    def card_profile(self, card_id: str, before_ts: str, lookback_days: int = 365) -> dict:
        res = self._run("card_profile", {
            "card_id": card_id, "before_ts": str(before_ts)[:19],
            "lookback_days": int(lookback_days),
        })
        n = int(_first(res, "n_txns", 0) or 0)
        if n == 0:
            return {"card_id": card_id, "before": str(before_ts), "n_txns": 0, "empty": True,
                    "regions": [], "product_codes": [], "device_profiles": [], "channels": {}}
        amounts = sorted(float(a) for a in (_first(res, "amounts", []) or []))
        q = lambda p: quantile_nearest(amounts, p)  # noqa: E731 - shared definition
        total = _num(_first(res, "total_amt", 0.0))
        mean = total / n if n else 0.0
        var = sum((a - mean) ** 2 for a in amounts) / n if n else 0.0
        regions = _first(res, "regions", {}) or {}
        products = _first(res, "product_codes", {}) or {}
        devices = _first(res, "device_profiles", {}) or {}
        emails = _first(res, "purchaser_email_domains", {}) or {}
        return {
            "card_id": card_id,
            "before": str(before_ts)[:19],
            "n_txns": n,
            "first_ts": _dt(_first(res, "first_ts")),
            "last_ts": _dt(_first(res, "last_ts")),
            "amount": {
                "min": amounts[0] if amounts else 0.0, "p25": q(0.25), "median": q(0.5),
                "p75": q(0.75), "p95": q(0.95), "max": amounts[-1] if amounts else 0.0,
                "mean": mean, "std": var ** 0.5, "total": total,
            },
            "channels": {
                "online": int(_first(res, "n_online", 0) or 0),
                "in_person": int(_first(res, "n_in_person", 0) or 0),
            },
            "product_codes": [{"ProductCD": k, "n": int(v)} for k, v in
                              sorted(products.items(), key=lambda kv: -int(kv[1]))],
            "regions": [{"region_id": float(k), "n": int(v)} for k, v in
                        sorted(regions.items(), key=lambda kv: -int(kv[1]))[:25]],
            "device_profiles": [{"device_profile": k, "n": int(v)} for k, v in
                                sorted(devices.items(), key=lambda kv: -int(kv[1]))[:25]],
            "purchaser_email_domains": [{"domain": k, "n": int(v)} for k, v in
                                        sorted(emails.items(), key=lambda kv: -int(kv[1]))[:15]],
            "recipient_email_domains": [],
            "mean_risk_score": _num(_first(res, "risk_total", 0.0)) / n if n else 0.0,
            "empty": False,
        }

    def customer_cards(self, customer_id: str) -> dict:
        res = self._run("customer_cards", {"customer_id": customer_id})
        rows = _first(res, "cards", []) or []
        cards = []
        for r in rows:
            a = r.get("attributes", {})
            cards.append({
                "card_id": a.get("card_id"), "customer_id": a.get("customer_id"),
                "network": a.get("network"), "card_type": a.get("card_type"),
                "n_txns": int(_num(a.get("n_txns"))), "first_ts": _dt(a.get("first_ts")),
                "last_ts": _dt(a.get("last_ts")), "total_amt": _num(a.get("total_amt")),
                "median_amt": _num(a.get("median_amt")), "max_amt": _num(a.get("max_amt")),
                "n_online": int(_num(a.get("n_online"))),
                "n_in_person": int(_num(a.get("n_in_person"))),
                "mean_risk": _num(a.get("mean_risk")),
            })
        return {"customer_id": customer_id, "n_cards": len(cards), "cards": cards}

    def device_neighbors(self, device_profile: str, from_ts: str | None = None,
                         to_ts: str | None = None, limit: int = 200) -> dict:
        res = self._run("device_neighbors", {
            "device_profile": device_profile,
            "from_ts": (from_ts or "2016-01-01 00:00:00")[:19],
            "to_ts": (to_ts or "2017-01-01 00:00:00")[:19],
            "lim": int(limit),
        })
        card_txns = _first(res, "card_txns", {}) or {}
        card_amt = _first(res, "card_amt", {}) or {}
        card_new = _first(res, "card_new", {}) or {}
        customers = _first(res, "customers", []) or []
        dev_rows = _first(res, "device", []) or []
        lifetime = None
        if dev_rows:
            a = dev_rows[0].get("attributes", {})
            lifetime = {
                "device_profile": a.get("device_profile"),
                "n_txns": int(_num(a.get("n_txns"))), "n_cards": int(_num(a.get("n_cards"))),
                "n_customers": int(_num(a.get("n_customers"))),
                "n_new_for_account": int(_num(a.get("n_new_for_account"))),
                "proxy_flags": a.get("proxy_flags") or "",
                "device_type": a.get("device_type"),
                "first_ts": _dt(a.get("first_ts")), "last_ts": _dt(a.get("last_ts")),
                "total_amt": _num(a.get("total_amt")),
            }
        cards = [
            {"card_id": k, "customer_id": "", "n_txns": int(v),
             "amount": _num(card_amt.get(k, 0.0)), "marked_new": int(card_new.get(k, 0)),
             "first_ts": "", "last_ts": ""}
            for k, v in sorted(card_txns.items(), key=lambda kv: -int(kv[1]))[:limit]
        ]
        return {
            "device_profile": device_profile, "window": [from_ts, to_ts],
            "n_cards": len(card_txns), "n_customers": len(customers),
            "n_txns": int(_num(_first(res, "n_txns", 0))),
            "total_amount": _num(_first(res, "total_amount", 0.0)),
            "proxy_flags": sorted({p for p in (_first(res, "proxy_flags", []) or []) if p}),
            "lifetime": lifetime, "cards": cards,
        }

    def region_neighbors(self, region_id: float | str, from_ts: str | None = None,
                         to_ts: str | None = None, limit: int = 200) -> dict:
        key = f"{float(region_id):.0f}"
        res = self._run("region_neighbors", {
            "region_id": key,
            "from_ts": (from_ts or "2016-01-01 00:00:00")[:19],
            "to_ts": (to_ts or "2017-01-01 00:00:00")[:19],
            "lim": int(limit),
        })
        card_txns = _first(res, "card_txns", {}) or {}
        card_amt = _first(res, "card_amt", {}) or {}
        customers = _first(res, "customers", []) or []
        reg_rows = _first(res, "region", []) or []
        life = reg_rows[0].get("attributes", {}) if reg_rows else {}
        return {
            "region_id": float(region_id), "window": [from_ts, to_ts],
            "n_cards": len(card_txns), "n_customers": len(customers),
            "n_txns": int(_num(_first(res, "n_txns", 0))),
            "lifetime_n_cards": int(_num(life.get("n_cards"))) if life else None,
            "lifetime_n_txns": int(_num(life.get("n_txns"))) if life else None,
            "cards": [
                {"card_id": k, "customer_id": "", "n_txns": int(v),
                 "amount": _num(card_amt.get(k, 0.0)), "first_ts": "", "last_ts": ""}
                for k, v in sorted(card_txns.items(), key=lambda kv: -_num(card_amt.get(kv[0], 0)))[:limit]
            ],
        }

    def email_neighbors(self, domain: str, role: str = "recipient",
                        from_ts: str | None = None, to_ts: str | None = None,
                        limit: int = 200) -> dict:
        res = self._run("email_neighbors", {
            "domain": domain, "role": role,
            "from_ts": (from_ts or "2016-01-01 00:00:00")[:19],
            "to_ts": (to_ts or "2017-01-01 00:00:00")[:19],
            "lim": int(limit),
        })
        card_txns = _first(res, "card_txns", {}) or {}
        card_amt = _first(res, "card_amt", {}) or {}
        return {
            "domain": domain, "role": role, "n_cards": len(card_txns),
            "n_txns": int(_num(_first(res, "n_txns", 0))),
            "cards": [{"card_id": k, "n_txns": int(v), "amount": _num(card_amt.get(k, 0.0))}
                      for k, v in sorted(card_txns.items(), key=lambda kv: -int(kv[1]))[:limit]],
        }

    def region_history_for_card(self, card_id: str, region_id: float | str,
                                before_ts: str) -> dict:
        key = f"{float(region_id):.0f}"
        res = self._run("region_history_for_card", {
            "card_id": card_id, "region_id": key, "before_ts": str(before_ts)[:19],
        })
        by_region = _first(res, "by_region", {}) or {}
        home = max(by_region.items(), key=lambda kv: int(kv[1]))[0] if by_region else None
        return {
            "card_id": card_id, "region_id": float(region_id), "before": str(before_ts),
            "prior_txns_in_region": int(_num(_first(res, "prior_txns_in_region", 0))),
            "prior_txns_total": int(_num(_first(res, "prior_txns_total", 0))),
            "distinct_prior_regions": len(_first(res, "distinct_prior_regions", []) or []),
            "home_region": float(home) if home else None,
            "first_seen_in_region": _dt(_first(res, "first_seen_in_region")) or None,
        }

    def device_history_for_card(self, card_id: str, device_profile: str,
                                before_ts: str) -> dict:
        res = self._run("device_history_for_card", {
            "card_id": card_id, "device_profile": device_profile,
            "before_ts": str(before_ts)[:19],
        })
        return {
            "card_id": card_id, "device_profile": device_profile, "before": str(before_ts),
            "prior_txns_on_device": int(_num(_first(res, "prior_txns_on_device", 0))),
            "prior_online_txns": int(_num(_first(res, "prior_online_txns", 0))),
            "distinct_prior_devices": len(_first(res, "distinct_prior_devices", []) or []),
            "first_seen_on_device": _dt(_first(res, "first_seen_on_device")) or None,
        }

    # ------------------------------------------------------------- memory
    @staticmethod
    def _cases(rows: list) -> list[dict]:
        out = []
        for r in rows or []:
            a = r.get("attributes", {})
            out.append({
                "case_id": a.get("case_id"), "customer_id": a.get("customer_id"),
                "card_id": a.get("card_id"), "opened_at": _dt(a.get("opened_at")),
                "closed_at": _dt(a.get("closed_at")), "outcome": a.get("outcome"),
                "pattern": a.get("pattern"),
                "first_fraud_txn_id": a.get("first_fraud_txn_id"),
                "n_txns": int(_num(a.get("n_txns"))),
                "exposure_usd": _num(a.get("exposure_usd")),
                "actions_taken": a.get("actions_taken"),
                "report_filed": a.get("report_filed"),
                "analyst_notes": a.get("analyst_notes"),
            })
        return out

    def closed_cases_for_card(self, card_id: str, as_of: str | None = None) -> dict:
        res = self._run("closed_cases_for_card", {"card_id": card_id, "as_of": as_of or ""})
        direct = self._cases(_first(res, "direct", []))
        conn = self._cases(_first(res, "as_connected_card", []))
        if as_of:
            direct = [c for c in direct if (c["closed_at"] or "") < as_of]
            conn = [c for c in conn if (c["closed_at"] or "") < as_of]
        return {"card_id": card_id, "direct": direct, "as_connected_card": conn}

    def closed_cases_for_customer(self, customer_id: str, as_of: str | None = None) -> dict:
        res = self._run("closed_cases_for_customer",
                        {"customer_id": customer_id, "as_of": as_of or ""})
        cases = self._cases(_first(res, "cases", []))
        if as_of:
            cases = [c for c in cases if (c["closed_at"] or "") < as_of]
        return {"customer_id": customer_id, "n": len(cases), "cases": cases}

    def closed_cases_for_device(self, device_profile: str, as_of: str | None = None) -> dict:
        res = self._run("closed_cases_for_device",
                        {"device_profile": device_profile, "as_of": as_of or ""})
        cases = self._cases(_first(res, "cases", []))
        if as_of:
            cases = [c for c in cases if (c["closed_at"] or "") < as_of]
        return {"device_profile": device_profile, "n": len(cases), "cases": cases}

    def closed_cases_for_region(self, region_id: float | str) -> dict:
        res = self._run("closed_cases_for_region", {"region_id": f"{float(region_id):.0f}"})
        cases = self._cases(_first(res, "cases", []))
        return {"region_id": float(region_id), "n": len(cases), "cases": cases}

    def similar_closed_cases(self, pattern: str | None = None, channel: str | None = None,
                             amount: float | None = None, n_txns: int | None = None,
                             limit: int = 8, as_of: str | None = None,
                             exclude_case_ids: tuple = ()) -> dict:
        # stage 1 happens in GSQL (nearest exposure, ties by case_id, capped at
        # CANDIDATE_POOL); stage 2 is the catalogue's shared ranking, so this
        # backend and the local mirror cannot order the same pool differently.
        res = self._run("similar_closed_cases", {
            "pattern": pattern or "", "channel": channel or "",
            "amount": float(amount or 0.0), "n_txns": int(n_txns or 1),
            "lim": CANDIDATE_POOL,
        })
        cases = self._cases(_first(res, "cases", []))
        if as_of:
            cases = [c for c in cases if str(c.get("closed_at") or "") < str(as_of)]
        if exclude_case_ids:
            cases = [c for c in cases if c["case_id"] not in set(exclude_case_ids)]
        ranked = rank_similar_cases(
            cases, channel=channel, amount=amount, n_txns=n_txns, limit=limit,
        )
        return {"query": {"pattern": pattern, "channel": channel, "amount": amount,
                          "n_txns": n_txns},
                "n": len(ranked), "cases": ranked}

    # ------------------------------------------------------------- writing
    def write_case(self, case: dict) -> dict:
        """Upsert an AgentCase with its evidence and its edges.

        Returns ``{"written": bool, ...}``.  The orchestrator sets the answer
        file's ``written_to_graph`` from this and nothing else.
        """
        gid = case["graph_case_id"]
        try:
            # Clear the previous write BEFORE recreating the vertex. Edge
            # upserts are keyed on (from, to), so without this a re-run keeps
            # both runs' citations. Dropping the vertex drops its edges too.
            # Order matters: doing this *after* the upsert deletes the vertex
            # just written, and the edges below then recreate it implicitly
            # with every attribute blank -- which still passes an edge-count
            # check, so the content is verified too.
            try:
                self._conn.delVerticesById("AgentCase", gid)
            except Exception:  # noqa: BLE001 - a first write has nothing to clear
                pass

            self._conn.upsertVertex("AgentCase", gid, {
                "case_id": case.get("case_id", ""),
                "status": case.get("status", ""), "verdict": case.get("verdict", ""),
                "fraud_probability": float(case.get("fraud_probability") or 0.0),
                "pattern": case.get("pattern", ""),
                "pattern_description": (case.get("pattern_description") or "")[:4000],
                "exposure_usd": float(case.get("exposure_usd") or 0.0),
                "summary": (case.get("summary") or "")[:4000],
                "stop_reason": (case.get("stop_reason") or "")[:2000],
                "trigger_type": case.get("trigger_type", ""),
                "opened_at": case.get("opened_at", ""), "closed_at": case.get("closed_at", ""),
                "card_id": case.get("card_id", ""), "customer_id": case.get("customer_id", ""),
                "first_suspicious_txn_id": case.get("first_suspicious_txn_id", ""),
                "actions_initial": "|".join(case.get("actions_initial", [])),
                "actions_final": "|".join(case.get("actions_final", [])),
                "report_filed": bool(case.get("report_filed")),
                "n_affected_txns": len(case.get("affected_txn_ids", [])),
                "source": case.get("source", "agent"),
            })

            edges: list[tuple] = []
            for t in case.get("affected_txn_ids", []):
                edges.append(("AgentCase", gid, "CASE_INVESTIGATES", "Transaction", str(t), {}))
            if case.get("card_id"):
                edges.append(("AgentCase", gid, "CASE_ON_CARD", "PaymentCard", case["card_id"], {}))
            for c in case.get("connected_card_ids", []):
                edges.append(("AgentCase", gid, "CASE_CONNECTED_TO", "PaymentCard", str(c), {}))
            for d in case.get("connected_device_profiles", []):
                edges.append(("AgentCase", gid, "CASE_FROM_DEVICE", "DeviceProfile", str(d), {}))
            for p in case.get("similar_prior_cases", []):
                edges.append(("AgentCase", gid, "CASE_CITES_PRIOR", "ClosedCase", str(p), {}))
            typology = case.get("pattern_detector") or case.get("pattern")
            if typology and typology != "none":
                edges.append(("AgentCase", gid, "CASE_MATCHES_PATTERN", "FraudPattern",
                              typology, {}))
            n_ev = 0
            for i, ev in enumerate(case.get("evidence", [])):
                eid = f"{gid}-E{i:02d}"
                self._conn.upsertVertex("CaseEvidence", eid, {
                    "claim": (ev.get("claim") or "")[:4000],
                    "ev_source": ev.get("source", ""), "ref": (ev.get("ref") or "")[:1000],
                    "entity_ids": "|".join(str(x) for x in ev.get("entity_ids", []))[:4000],
                })
                edges.append(("AgentCase", gid, "CASE_CONTAINS_EVIDENCE", "CaseEvidence", eid, {}))
                n_ev += 1
            for e in edges:
                try:
                    self._conn.upsertEdge(*e)
                except Exception:  # noqa: BLE001 - a missing endpoint must not fail the case
                    continue
            return {"written": True, "graph_case_id": gid, "backend": self.name,
                    "evidence_vertices": n_ev, "edges": len(edges)}
        except Exception as exc:  # noqa: BLE001
            return {"written": False, "backend": self.name, "error": str(exc)[:300]}

    def read_cases(self, case_id: str | None = None, limit: int = 100) -> dict:
        try:
            res = self._run("read_cases", {"case_id": case_id or "", "lim": int(limit)})
            rows = _first(res, "cases", []) or []
            return {"cases": [r.get("attributes", {}) for r in rows], "backend": self.name}
        except Exception as exc:  # noqa: BLE001
            return {"cases": [], "backend": self.name, "error": str(exc)[:300]}

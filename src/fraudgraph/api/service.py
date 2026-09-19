"""Service layer behind the dashboard.

Every number the dashboard shows comes from a real artefact on disk or a live
graph query.  There are no hardcoded statistics.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ..agent.orchestrator import InvestigationAgent, Trigger
from ..config import LLM, PATHS, TG
from ..graph.store import GraphStore
from ..memory.case_memory import CaseMemory
from ..policy import rules as R
from ..policy.actions import WOULD_DO, MockActionService
from ..schemas import Action

_LOCK = threading.Lock()


class CaseService:
    def __init__(self, backend: str | None = None, use_llm: bool = True):
        self.store = GraphStore(prefer=backend) if backend else GraphStore()
        self.memory = CaseMemory(self.store)
        narrator = None
        if use_llm:
            from ..agent.llm import build_narrator

            narrator = build_narrator()
        self.narrator = narrator
        self.agent = InvestigationAgent(store=self.store, memory=self.memory, narrator=narrator)
        self.actions = MockActionService()

    # ------------------------------------------------------------- storage
    @property
    def records_dir(self) -> Path:
        d = PATHS.build / "case_records"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def approvals_path(self) -> Path:
        return PATHS.build / "approvals.json"

    def _approvals(self) -> dict:
        if self.approvals_path.exists():
            try:
                return json.loads(self.approvals_path.read_text())
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_approvals(self, data: dict) -> None:
        self.approvals_path.write_text(json.dumps(data, indent=2))

    def load_record(self, case_id: str) -> dict | None:
        p = self.records_dir / f"{case_id}.json"
        if not p.exists():
            return None
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        rec["approvals"] = self._approvals().get(case_id, {})
        return rec

    def all_records(self) -> list[dict]:
        out = []
        approvals = self._approvals()
        for p in sorted(self.records_dir.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            rec["approvals"] = approvals.get(rec.get("case_id", ""), {})
            out.append(rec)
        return out

    # ------------------------------------------------------------ case pack
    def case_pack(self) -> list[dict]:
        cp = pd.read_csv(PATHS.case_pack_csv)
        cp = cp.where(pd.notna(cp), None)
        return cp.to_dict("records")

    # ------------------------------------------------------------- overview
    def overview(self) -> dict:
        recs = self.all_records()
        pack = self.case_pack()
        rows = []
        for r in recs:
            a = r.get("answer", {})
            c = a.get("case", {})
            rows.append({
                "case_id": r.get("case_id"),
                "status": c.get("status"),
                "verdict": c.get("verdict"),
                "probability": float(c.get("fraud_probability") or 0),
                "exposure": float(c.get("exposure_usd") or 0),
                "pattern": c.get("pattern"),
                "sar": bool((a.get("sar") or {}).get("file")),
                "actions": [x.get("action") for x in (a.get("next_best_actions") or {}).get("final", [])],
                "evidence_requests": len(a.get("evidence_requests") or []),
                "written_to_graph": bool(c.get("written_to_graph")),
            })
        approvals = self._approvals()
        awaiting = 0
        for r in recs:
            cid = r.get("case_id", "")
            done = set((approvals.get(cid) or {}).keys())
            for act in (r.get("actions_full", {}) or {}).get("final", []):
                if act.get("route") in ("L1", "L2") and act.get("action") not in done:
                    awaiting += 1
        bands = {"0.0-0.15": 0, "0.15-0.30": 0, "0.30-0.50": 0,
                 "0.50-0.70": 0, "0.70-0.85": 0, "0.85-1.0": 0}
        for r in rows:
            p = r["probability"]
            key = ("0.0-0.15" if p <= 0.15 else "0.15-0.30" if p <= 0.30 else
                   "0.30-0.50" if p <= 0.50 else "0.50-0.70" if p <= 0.70 else
                   "0.70-0.85" if p <= 0.85 else "0.85-1.0")
            bands[key] += 1
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        return {
            "total_alerts_in_pack": len(pack),
            "cases_investigated": len(rows),
            "by_status": counts,
            "by_verdict": {v: sum(1 for r in rows if r["verdict"] == v)
                           for v in {r["verdict"] for r in rows}},
            "by_pattern": {v: sum(1 for r in rows if r["pattern"] == v)
                           for v in {r["pattern"] for r in rows}},
            "open": sum(1 for r in rows if r["status"] == "open"),
            "escalated": sum(1 for r in rows if r["status"] == "escalated"),
            "closed_fraud": sum(1 for r in rows if r["status"] == "closed_fraud"),
            "closed_legitimate": sum(1 for r in rows if r["status"] == "closed_legitimate"),
            "awaiting_approval": awaiting,
            "awaiting_evidence": sum(1 for r in rows if r["evidence_requests"] > 0
                                     and r["status"] == "open"),
            "sar_filings_recommended": sum(1 for r in rows if r["sar"]),
            "total_exposure_usd": round(sum(r["exposure"] for r in rows), 2),
            "written_to_graph": sum(1 for r in rows if r["written_to_graph"]),
            "risk_distribution": bands,
            "recent_activity": self._recent_activity(recs)[:25],
        }

    @staticmethod
    def _recent_activity(recs: list[dict]) -> list[dict]:
        events: list[dict] = []
        for r in recs:
            for t in r.get("timeline", []):
                events.append({
                    "case_id": r.get("case_id"), "step": t.get("step"),
                    "kind": t.get("kind"), "detail": t.get("detail"), "at": t.get("at"),
                })
        return sorted(events, key=lambda e: str(e.get("at")), reverse=True)

    # ---------------------------------------------------------------- queue
    def queue(self) -> list[dict]:
        pack = {p["case_id"]: p for p in self.case_pack()}
        approvals = self._approvals()
        out = []
        for r in self.all_records():
            cid = r.get("case_id", "")
            a, c = r.get("answer", {}), r.get("answer", {}).get("case", {})
            nba = (a.get("next_best_actions") or {}).get("final", [])
            done = set((approvals.get(cid) or {}).keys())
            pending = [x for x in ((r.get("actions_full") or {}).get("final") or [])
                       if x.get("route") in ("L1", "L2") and x.get("action") not in done]
            trig = r.get("trigger") or {}
            out.append({
                "case_id": cid,
                "trigger_type": trig.get("trigger_type") or pack.get(cid, {}).get("trigger_type"),
                "trigger_text": trig.get("trigger_text") or pack.get(cid, {}).get("trigger_text"),
                "card_id": trig.get("card_id"),
                "customer_id": trig.get("customer_id"),
                "flagged_txn_id": trig.get("flagged_txn_id"),
                "bank_risk_score": (r.get("risk") or {}).get("bank_risk_score"),
                "status": c.get("status"),
                "verdict": c.get("verdict"),
                "fraud_probability": c.get("fraud_probability"),
                "confidence": (r.get("risk") or {}).get("confidence"),
                "pattern": c.get("pattern"),
                "exposure_usd": c.get("exposure_usd"),
                "next_action": nba[0]["action"] if nba else None,
                "all_actions": [x["action"] for x in nba],
                "awaiting_approval": [x["action"] for x in pending],
                "sar": bool((a.get("sar") or {}).get("file")),
                "tool_calls": a.get("tool_calls"),
                "latency_s": a.get("latency_s"),
                "assigned_analyst": (approvals.get(cid) or {}).get("_assigned"),
                "last_updated": r.get("generated_at") or self._mtime(cid),
                "written_to_graph": c.get("written_to_graph"),
            })
        return sorted(out, key=lambda r: (-(r["fraud_probability"] or 0), r["case_id"]))

    def _mtime(self, case_id: str) -> str:
        p = self.records_dir / f"{case_id}.json"
        if not p.exists():
            return ""
        return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()

    # ------------------------------------------------------------ one case
    def case_detail(self, case_id: str) -> dict | None:
        rec = self.load_record(case_id)
        if rec is None:
            return None
        rec["policy_rules"] = {k: v.summary for k, v in R.RULES.items()}
        rec["action_effects"] = {a.value: WOULD_DO.get(a, "") for a in Action}
        rec["executions"] = self.actions.history(case_id=case_id)
        return rec

    # -------------------------------------------------------------- graph
    def case_graph(self, case_id: str, max_nodes: int = 160) -> dict:
        """Nodes and edges around a case, for the analyst to explore."""
        rec = self.load_record(case_id)
        if rec is None:
            return {"nodes": [], "edges": []}
        a = rec.get("answer", {})
        c = a.get("case", {})
        trig = rec.get("trigger", {})
        card_id = trig.get("card_id", "")
        customer_id = trig.get("customer_id", "")
        flagged = str(trig.get("flagged_txn_id", ""))

        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        def node(nid: str, kind: str, label: str, **attrs) -> None:
            if nid and nid not in nodes and len(nodes) < max_nodes:
                nodes[nid] = {"id": nid, "kind": kind, "label": label, **attrs}

        def edge(src: str, dst: str, kind: str) -> None:
            if src in nodes and dst in nodes:
                edges.append({"source": src, "target": dst, "kind": kind})

        node(customer_id, "Customer", customer_id)
        node(card_id, "Card", card_id, focus=True)
        edge(customer_id, card_id, "OWNS")

        affected = set(c.get("affected_txn_ids") or [])
        win = self.store.call("card_window", card_id=card_id,
                              center_ts=self._case_ts(rec), hours_before=72, hours_after=72)
        for t in (win.get("transactions") or [])[:60]:
            tid = str(t["TransactionID"])
            node(tid, "Transaction", f"${float(t['TransactionAmt']):,.2f}",
                 amount=float(t["TransactionAmt"]), ts=t["ts"], channel=t.get("channel"),
                 risk=t.get("risk_score"), flagged=(tid == flagged),
                 affected=(tid in affected), product=t.get("ProductCD"))
            edge(card_id, tid, "MADE")
            dp = t.get("device_profile")
            if dp:
                node(dp, "DeviceProfile", dp[:46] + ("..." if len(dp) > 46 else ""),
                     proxy=t.get("id_23"), device_new=t.get("id_15"))
                edge(tid, dp, "FROM_DEVICE")
            if t.get("addr1") is not None:
                rid = f"region:{float(t['addr1']):.0f}"
                node(rid, "BillingRegion", f"region {float(t['addr1']):.0f}")
                edge(tid, rid, "BILLED_IN")

        for dp in (c.get("connected_device_profiles") or []):
            node(dp, "DeviceProfile", dp[:46] + ("..." if len(dp) > 46 else ""), shared=True)
        for other in (c.get("connected_card_ids") or [])[:40]:
            node(other, "Card", other, connected=True)
            for dp in (c.get("connected_device_profiles") or []):
                edge(other, dp, "USED_DEVICE")
        for prior in (c.get("similar_prior_cases") or []):
            node(prior, "ClosedCase", prior)
            edge(card_id, prior, "PRIOR_CASE")
        gid = c.get("graph_case_id") or f"CASE:{case_id}"
        node(gid, "AgentCase", gid, verdict=c.get("verdict"),
             probability=c.get("fraud_probability"),
             written=bool(c.get("written_to_graph")))
        for tid in list(affected)[:40]:
            if tid in nodes:
                edge(gid, tid, "CASE_INVESTIGATES")
        edge(gid, card_id, "CASE_ON_CARD")

        return {"nodes": list(nodes.values()), "edges": edges,
                "backend": self.store.backend_name, "truncated": len(nodes) >= max_nodes}

    @staticmethod
    def _case_ts(rec: dict) -> str:
        for e in (rec.get("answer", {}).get("case", {}).get("evidence") or []):
            pass
        tl = rec.get("timeline") or []
        for t in tl:
            if t.get("kind") == "trigger":
                break
        return rec.get("flagged_ts") or rec.get("trigger", {}).get("flagged_ts") or \
            rec.get("answer", {}).get("case", {}).get("_ts", "") or \
            rec.get("trigger", {}).get("opened_at", "2016-12-01 00:00:00")

    # --------------------------------------------------------- investigate
    def investigate(self, case_id: str) -> dict:
        pack = {p["case_id"]: p for p in self.case_pack()}
        if case_id not in pack:
            raise KeyError(case_id)
        with _LOCK:
            answer = self.agent.investigate(Trigger.from_case_pack_row(pack[case_id]))
            from ..benchmark.run import _write_internal

            (PATHS.cases_out / f"{case_id}.json").write_text(
                json.dumps(answer.to_answer_dict(), indent=2), encoding="utf-8"
            )
            _write_internal(answer)
        return self.load_record(case_id) or {}

    def investigate_adhoc(self, txn_id: str, note: str = "") -> dict:
        """Analyst-initiated investigation of any transaction in the graph."""
        trig = Trigger(
            case_id=f"ADHOC-{txn_id}",
            trigger_type="analyst_request",
            trigger_text=note or f"Analyst opened an investigation on transaction {txn_id}.",
            flagged_txn_id=str(txn_id), card_id="", customer_id="",
            opened_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )
        with _LOCK:
            answer = self.agent.investigate(trig)
            from ..benchmark.run import _write_internal

            _write_internal(answer)
        return self.load_record(trig.case_id) or {}

    # ------------------------------------------------------------ approvals
    def approve(self, case_id: str, action: str, approver: str, decision: str,
                note: str = "") -> dict:
        rec = self.load_record(case_id)
        if rec is None:
            raise KeyError(case_id)
        finals = (rec.get("actions_full") or {}).get("final") or []
        match = next((a for a in finals if a.get("action") == action), None)
        if match is None:
            raise ValueError(f"{action} is not a recommended action on {case_id}")
        route = match.get("route")
        if route == "auto":
            raise ValueError(f"{action} is an auto action and needs no approval")
        data = self._approvals()
        entry = data.setdefault(case_id, {})
        record: dict[str, Any] = {
            "decision": decision, "approver": approver, "route": route,
            "at": datetime.now(timezone.utc).isoformat(), "note": note,
        }
        if decision == "approved":
            exec_rec = self.actions.perform(
                Action(action), approver=approver, case_id=case_id,
                detail={"route": route, "note": note},
            )
            record["execution"] = exec_rec
        entry[action] = record
        self._save_approvals(data)
        return record

    def assign(self, case_id: str, analyst: str) -> dict:
        data = self._approvals()
        data.setdefault(case_id, {})["_assigned"] = analyst
        self._save_approvals(data)
        return {"case_id": case_id, "assigned_analyst": analyst}

    # --------------------------------------------------------------- memory
    def memory_view(self, limit: int = 40) -> dict:
        cc = pd.read_parquet(PATHS.closed_cases_parquet)
        agent_cases = self.memory.read_agent_cases(limit=200)
        return {
            "historical": {
                "total": int(len(cc)),
                "confirmed_fraud": int((cc.outcome == "confirmed_fraud").sum()),
                "cleared": int((cc.outcome == "cleared").sum()),
                "by_pattern": cc.pattern.value_counts().to_dict(),
                "date_range": [str(cc.opened_at.min())[:10], str(cc.closed_at.max())[:10]],
            },
            "agent_written": {
                "total": len(agent_cases),
                "written_to_graph": sum(
                    1 for c in agent_cases if c.get("written_to_graph") or c.get("source") == "agent"
                ),
                "cases": agent_cases[-limit:],
            },
        }

    # ---------------------------------------------------------------- meta
    def health(self) -> dict:
        h = self.store.health()
        return {
            "graph": h,
            "tigergraph_configured": TG.configured,
            "tigergraph_host": (TG.host.split("//")[-1][:40] + "...") if TG.host else "",
            "llm": {
                "provider": LLM.provider,
                "model": LLM.model,
                "configured": LLM.configured,
                "enabled": bool(self.narrator and self.narrator.enabled),
                "tokens_used": getattr(self.narrator, "tokens_used", 0) if self.narrator else 0,
            },
            "actions": {"mode": "simulated", "integration": "mock action service"},
            "cases_on_disk": len(list(self.records_dir.glob("*.json"))),
        }

    def model_card(self) -> dict:
        p = PATHS.build / "risk_model.json"
        blob = json.loads(p.read_text()) if p.exists() else {}
        from ..analysis.risk import RiskModel

        m = RiskModel.load()
        from ..analysis.risk import TRIGGER_PRIOR, TRIGGER_PRIOR_NOTE

        return {
            "method": blob.get("method", "logistic fit"),
            "source": m.source,
            "intercept": m.intercept,
            "weights": {k: v for k, v in {**m.weights, **m.extra}.items() if v},
            "trigger_prior": blob.get("trigger_prior", TRIGGER_PRIOR),
            "trigger_prior_note": blob.get("trigger_prior_note", TRIGGER_PRIOR_NOTE),
            "weight_basis": blob.get("weight_basis", []),
            "max_abs_weight": blob.get("max_abs_weight"),
            "fitted_prior": m.fitted_prior,
            "deployment_prior": m.deployment_prior,
            "metrics": blob.get("metrics") or blob.get("logistic_fit_metrics"),
            "logistic_fit_metrics": blob.get("logistic_fit_metrics"),
            "class_counts": blob.get("class_counts"),
            "notes": {
                "dispute": blob.get("note"),
                "bank_score": blob.get("bank_score_note"),
                "label_noise": blob.get("label_noise_note"),
                "design": blob.get("design_note"),
            },
            "sign_constraints": blob.get("sign_constraints"),
        }

    def policy_view(self) -> dict:
        from ..agent.evidence_requests import SIMULATION_POLICY

        return {
            "rules": {k: v.summary for k, v in R.RULES.items()},
            "routes": {
                "auto": sorted(a.value for a in R.AUTO_ACTIONS),
                "L1": ["DECLINE_TRANSACTION", "BLOCK_CARD (exposure <= $2,500)"],
                "L2": ["BLOCK_CARD (exposure > $2,500)", "BLOCK_ALL_CARDS", "FILE_REPORT"],
            },
            "human_only": sorted(a.value for a in R.HUMAN_ONLY),
            "thresholds": {
                "case_probability": R.CASE_PROBABILITY_THRESHOLD,
                "R1_probability": R.R1_PROBABILITY_THRESHOLD,
                "sar_exposure": R.SAR_EXPOSURE_THRESHOLD,
                "block_card_L2_exposure": R.BLOCK_CARD_L2_EXPOSURE,
                "stop_high": R.STOP_HIGH, "stop_low": R.STOP_LOW,
            },
            "evidence_request_simulation": SIMULATION_POLICY,
        }

    def ingest_quality(self) -> dict:
        p = PATHS.quality_report
        return json.loads(p.read_text()) if p.exists() else {}

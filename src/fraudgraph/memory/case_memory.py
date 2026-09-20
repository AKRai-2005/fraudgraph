"""Case memory: retrieve prior investigations, and persist new ones.

Two sources, deliberately kept apart in the case record:

* **historical** -- the 5,565 closed investigations shipped with the dataset
  (July-October). Read-only ground truth.
* **agent-written** -- cases this system closed, written into TigerGraph so the
  next investigation can retrieve them.

Retrieval is explainable: every returned case carries ``why_retrieved``.
A prior case never sets a verdict on its own; it is evidence alongside the
current graph evidence, and the two are labelled separately in the answer file.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import PATHS
from ..graph.store import GraphStore
from ..schemas import AnswerFile


class CaseMemory:
    def __init__(self, store: GraphStore):
        self.store = store

    # ---------------------------------------------------------- retrieval
    def retrieve_similar(self, ctx, feats, findings, limit: int = 6) -> list[dict]:
        """Closed cases worth putting in front of the analyst, with reasons."""
        out: dict[str, dict] = {}

        def take(case: dict, why: str, score: float) -> None:
            cid = case.get("case_id")
            if not cid:
                return
            if cid in out:
                if why not in out[cid]["why_retrieved"]:
                    out[cid]["why_retrieved"] += f"; {why}"
                out[cid]["_score"] = max(out[cid]["_score"], score)
                return
            rec = dict(case)
            rec["why_retrieved"] = why
            rec["_score"] = score
            out[cid] = rec

        # 1. same card -- the strongest link
        for c in (ctx.prior_cases_card or {}).get("direct", []) or []:
            take(c, "a prior investigation on this exact card", 1.0)
        for c in (ctx.prior_cases_card or {}).get("as_connected_card", []) or []:
            take(c, "this card was named as a connected card in that investigation", 0.9)

        # 2. same device profile
        for c in (ctx.prior_cases_device or {}).get("cases", []) or []:
            take(c, f"transactions in that case came from the same device profile "
                    f"'{feats.device_profile}'", 0.85)

        # 3. same customer, different card
        for c in (ctx.prior_cases_customer or {}).get("cases", []) or []:
            if c.get("card_id") != feats.card_id:
                take(c, "another card belonging to the same customer", 0.6)

        # 4. same pattern and comparable shape
        matched = [f for f in findings if f.matched and f.pattern.value not in ("none",)]
        if matched:
            best = max(matched, key=lambda f: f.strength)
            res = self.store.call(
                "similar_closed_cases",
                pattern=best.pattern.value, channel=feats.channel,
                amount=feats.amount, n_txns=max(1, feats.n_txns_48h), limit=limit,
            )
            for c in (res or {}).get("cases", []) or []:
                take(c, f"same typology ({best.pattern.value}) and comparable exposure", 0.5)

        # The sort key ends in case_id because everything above assigns scores
        # from a small fixed set -- every "prior case on this exact card" gets
        # 1.0 -- so ties are the rule, not the exception.  A stable sort then
        # preserves insertion order, which is the order the *backend* returned
        # its rows in, and the top-ranked prior case (the one that reaches the
        # answer file as evidence) changes with the backend.  It did: the same
        # alert cited CC-2935 on TigerGraph and CC-1589 on the local mirror.
        ranked = sorted(out.values(), key=lambda c: (-c["_score"], str(c.get("case_id") or "")))
        for c in ranked:
            c.pop("_score", None)
        return ranked[:limit]

    # ---------------------------------------------------------- persistence
    def write_case(self, answer: AnswerFile) -> dict:
        """Persist a closed investigation. TigerGraph is the system of record.

        Returns ``{"written": bool, "graph_case_id": str, "backend": str}``.
        ``written`` is only true when the graph actually accepted the write --
        the answer file's ``written_to_graph`` is set from this, never assumed.
        """
        payload = self._case_payload(answer)
        result = self.store.call("write_case", case=payload)
        written = bool(result.get("written"))
        self._write_local_journal(payload, written, result)
        return {
            "written": written,
            "graph_case_id": payload["graph_case_id"] if written else "",
            "backend": self.store.backend_name,
            "detail": result,
        }

    @staticmethod
    def _case_payload(answer: AnswerFile) -> dict:
        c = answer.case
        # The answer file's pattern enum buckets both undocumented typologies
        # under "undocumented", so the graph edge points at the specific one
        # instead. Only a case that actually concluded a typology gets the edge:
        # a detector may fire on a case the agent went on to call legitimate,
        # and linking that to a fraud typology in the graph would mislead the
        # next investigation that retrieves it.
        from ..analysis.patterns import DETECTOR_TO_PATTERN_ID

        detector = ""
        if c.pattern.value != "none":
            matched = [
                f for f in answer.findings
                if f.matched and f.name in DETECTOR_TO_PATTERN_ID
            ]
            if matched:
                best = max(matched, key=lambda f: f.strength).name
                detector = DETECTOR_TO_PATTERN_ID[best]
        return {
            "pattern_detector": detector,
            "graph_case_id": f"CASE-2016-{answer.case_id.replace('HHG-', '')}",
            "case_id": answer.case_id,
            "status": c.status.value,
            "verdict": c.verdict.value,
            "fraud_probability": c.fraud_probability,
            "pattern": c.pattern.value,
            "pattern_description": c.pattern_description,
            "exposure_usd": c.exposure_usd,
            "summary": c.summary,
            "opened_at": answer.trigger.get("opened_at", ""),
            "closed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "trigger_type": answer.trigger.get("trigger_type", ""),
            "card_id": answer.trigger.get("card_id", ""),
            "customer_id": answer.trigger.get("customer_id", ""),
            "first_suspicious_txn_id": c.first_suspicious_txn_id,
            "affected_txn_ids": list(c.affected_txn_ids),
            "connected_card_ids": list(c.connected_card_ids),
            "connected_device_profiles": list(c.connected_device_profiles),
            "similar_prior_cases": list(c.similar_prior_cases),
            "evidence": [e.to_answer_dict() for e in c.evidence],
            "actions_final": [a.action.value for a in answer.next_best_actions.final],
            "actions_initial": [a.action.value for a in answer.next_best_actions.initial],
            "report_filed": answer.sar.file,
            "stop_reason": answer.stop_reason,
            "source": "agent",
        }

    @staticmethod
    def _write_local_journal(payload: dict, written: bool, detail: dict) -> None:
        """Always keep a local, append-only copy of what we tried to persist."""
        path: Path = PATHS.build / "agent_cases.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "at": datetime.now(timezone.utc).isoformat(),
                "written_to_graph": written,
                "graph_detail": detail,
                "case": payload,
            }) + "\n")

    def read_agent_cases(self, limit: int = 200) -> list[dict]:
        res = self.store.call("read_cases", limit=limit)
        cases = (res or {}).get("cases") or []
        if cases:
            return cases
        path = PATHS.build / "agent_cases.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out[-limit:]

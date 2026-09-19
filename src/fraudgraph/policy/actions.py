"""Mock action service.

No real financial system is connected.  Every call here is simulated and says
so, in the return value, in the case record and in the dashboard.  The class
exists so that the boundary between "recommended" and "actually happened" is a
real boundary in the code, not a narrative claim.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone

from ..config import PATHS
from ..schemas import Action
from .rules import may_agent_execute

_LOCK = threading.Lock()

#: Short description of what a real integration would do, shown in the UI.
WOULD_DO: dict[Action, str] = {
    Action.ALLOW_TRANSACTION: "release the authorisation hold",
    Action.DECLINE_TRANSACTION: "decline the pending authorisation at the switch",
    Action.MONITOR_CARD: "raise the card's monitoring sensitivity for 72 hours",
    Action.MONITOR_CONNECTED_CARDS: "raise monitoring on every linked card",
    Action.WARN_CUSTOMER: "send an informational message to the cardholder",
    Action.VERIFY_WITH_CUSTOMER: "ask the cardholder to confirm the transaction",
    Action.STEP_UP_AUTH: "require a one-time passcode before further activity",
    Action.BLOCK_CARD: "block the card and queue a reissue",
    Action.BLOCK_ALL_CARDS: "block every card the customer holds",
    Action.GENERATE_REPORT: "write the investigation to the internal record",
    Action.CREATE_CASE: "open an internal fraud case",
    Action.FILE_REPORT: "submit a suspicious activity report to the regulator",
    Action.ESCALATE_TO_ANALYST: "hand the case to a human analyst queue",
    Action.CLOSE_NO_FRAUD: "close the alert as legitimate",
}


class MockActionService:
    """Records simulated executions to an append-only audit log."""

    SIMULATED = True

    @property
    def log_path(self):
        return PATHS.build / "action_audit_log.jsonl"

    def perform(
        self,
        action: Action,
        approver: str | None = None,
        case_id: str = "",
        detail: dict | None = None,
    ) -> dict:
        if not may_agent_execute(action) and not approver:
            raise PermissionError(
                f"{action.value} requires human approval and no approver was supplied"
            )
        record = {
            "execution_id": f"EX-{uuid.uuid4().hex[:12]}",
            "action": action.value,
            "case_id": case_id,
            "approver": approver or "agent",
            "simulated": True,
            "integration": "none -- mock action service",
            "would_have": WOULD_DO.get(action, "no real-world effect configured"),
            "at": datetime.now(timezone.utc).isoformat(),
            "detail": detail or {},
        }
        PATHS.build.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        return record

    def history(self, case_id: str | None = None, limit: int = 200) -> list[dict]:
        if not self.log_path.exists():
            return []
        out = []
        with self.log_path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if case_id and rec.get("case_id") != case_id:
                    continue
                out.append(rec)
        return out[-limit:]

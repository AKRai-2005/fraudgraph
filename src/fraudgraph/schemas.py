"""Structured objects for the whole system.

The answer-file shapes here follow the dataset README's Answer Format section
exactly; ``AnswerFile.to_answer_dict`` is the only place that serialises them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------
# Policy vocabulary -- the exact identifiers the answer files must use
# --------------------------------------------------------------------------


class Action(str, Enum):
    ALLOW_TRANSACTION = "ALLOW_TRANSACTION"
    DECLINE_TRANSACTION = "DECLINE_TRANSACTION"
    MONITOR_CARD = "MONITOR_CARD"
    MONITOR_CONNECTED_CARDS = "MONITOR_CONNECTED_CARDS"
    WARN_CUSTOMER = "WARN_CUSTOMER"
    VERIFY_WITH_CUSTOMER = "VERIFY_WITH_CUSTOMER"
    STEP_UP_AUTH = "STEP_UP_AUTH"
    BLOCK_CARD = "BLOCK_CARD"
    BLOCK_ALL_CARDS = "BLOCK_ALL_CARDS"
    GENERATE_REPORT = "GENERATE_REPORT"
    CREATE_CASE = "CREATE_CASE"
    FILE_REPORT = "FILE_REPORT"
    ESCALATE_TO_ANALYST = "ESCALATE_TO_ANALYST"
    CLOSE_NO_FRAUD = "CLOSE_NO_FRAUD"


class Route(str, Enum):
    AUTO = "auto"
    L1 = "L1"
    L2 = "L2"


class Pattern(str, Enum):
    CARD_TESTING = "card_testing"
    CARD_NOT_PRESENT_FRAUD = "card_not_present_fraud"
    CARD_NOT_PRESENT_NEW_DEVICE = "card_not_present_new_device"
    OUT_OF_REGION_USE = "out_of_region_use"
    ACCOUNT_TAKEOVER = "account_takeover"
    UNDOCUMENTED = "undocumented"
    NONE = "none"


class Verdict(str, Enum):
    FRAUD = "fraud"
    LEGITIMATE = "legitimate"
    UNCERTAIN = "uncertain"


class CaseStatus(str, Enum):
    OPEN = "open"
    CLOSED_FRAUD = "closed_fraud"
    CLOSED_LEGITIMATE = "closed_legitimate"
    ESCALATED = "escalated"


class EvidenceSource(str, Enum):
    GRAPH = "graph"
    DOCUMENT = "document"
    CUSTOMER = "customer"
    EXTERNAL = "external"


# --------------------------------------------------------------------------
# Evidence and audit
# --------------------------------------------------------------------------


class Evidence(BaseModel):
    """One claim, and what it rests on. Shape fixed by the README."""

    claim: str
    source: EvidenceSource
    ref: str
    entity_ids: list[str] = Field(default_factory=list)
    # internal-only, stripped from the answer file
    confidence: float | None = None
    backend: str | None = None
    weight_tag: str | None = None

    def to_answer_dict(self) -> dict:
        return {
            "claim": self.claim,
            "source": self.source.value,
            "ref": self.ref,
            "entity_ids": [str(e) for e in self.entity_ids],
        }


class ToolCall(BaseModel):
    """One graph/retrieval call, for the audit trail."""

    step: int
    name: str
    params: dict[str, Any]
    backend: str
    ref: str
    ok: bool = True
    error: str | None = None
    duration_ms: float = 0.0
    result_summary: str = ""
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TimelineEntry(BaseModel):
    step: int
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    kind: str  # trigger | tool | finding | decision | evidence_request | policy | stop
    detail: str
    data: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Findings and risk
# --------------------------------------------------------------------------


class PatternFinding(BaseModel):
    """A deterministic detector's output. Never produced by the LLM."""

    pattern: Pattern
    name: str
    matched: bool
    strength: float = Field(ge=0.0, le=1.0)
    why: str
    limitations: str = ""
    entity_ids: list[str] = Field(default_factory=list)
    txn_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    description: str = ""  # populated for undocumented patterns


class RiskAssessment(BaseModel):
    """The six quantities the README asks to be kept apart."""

    bank_risk_score: float | None = None          # 1. the model's score, untouched
    graph_indicators: dict[str, float] = Field(default_factory=dict)  # 2.
    historical_evidence: dict[str, Any] = Field(default_factory=dict)  # 3.
    agent_fraud_probability: float = 0.0          # 4. the agent's own assessment
    confidence: float = 0.0                       # 5. confidence in the finding
    uncertainty_notes: list[str] = Field(default_factory=list)  # 6.
    conflicting_evidence: list[str] = Field(default_factory=list)
    independent_signal_count: int = 0
    contributions: list[dict[str, Any]] = Field(default_factory=list)
    calibration_note: str = ""


# --------------------------------------------------------------------------
# Actions and evidence requests
# --------------------------------------------------------------------------


class ActionRecommendation(BaseModel):
    action: Action
    route: Route
    reason: str
    # lifecycle: recommended -> awaiting_approval -> authorized -> executed
    status: Literal[
        "recommended", "awaiting_approval", "authorized", "executed", "simulated", "rejected"
    ] = "recommended"
    executed_at: datetime | None = None
    executed_by: str | None = None
    simulated: bool = False

    def to_answer_dict(self) -> dict:
        return {"action": self.action.value, "route": self.route.value, "reason": self.reason}


class EvidenceRequest(BaseModel):
    type: Literal["customer_validation", "step_up_auth", "analyst_info"]
    asked_after_step: int
    reason: str = ""
    information_required: str = ""
    recipient: str = ""
    status: Literal["requested", "simulated_response", "no_response"] = "requested"
    assumed_response: str = ""
    assumption_basis: str = ""
    effect_on_investigation: str = ""

    def to_answer_dict(self) -> dict:
        return {
            "type": self.type,
            "asked_after_step": self.asked_after_step,
            "assumed_response": self.assumed_response,
        }


# --------------------------------------------------------------------------
# The case
# --------------------------------------------------------------------------


class InvestigationCase(BaseModel):
    status: CaseStatus
    verdict: Verdict
    fraud_probability: float = Field(ge=0.0, le=1.0)
    pattern: Pattern
    pattern_description: str = ""
    affected_txn_ids: list[str] = Field(default_factory=list)
    first_suspicious_txn_id: str = ""
    connected_card_ids: list[str] = Field(default_factory=list)
    connected_device_profiles: list[str] = Field(default_factory=list)
    exposure_usd: float = 0.0
    evidence: list[Evidence] = Field(default_factory=list)
    similar_prior_cases: list[str] = Field(default_factory=list)
    summary: str = ""
    written_to_graph: bool = False
    graph_case_id: str = ""

    @field_validator("fraud_probability")
    @classmethod
    def _round(cls, v: float) -> float:
        return round(float(v), 3)

    def to_answer_dict(self) -> dict:
        return {
            "status": self.status.value,
            "verdict": self.verdict.value,
            "fraud_probability": round(self.fraud_probability, 3),
            "pattern": self.pattern.value,
            "pattern_description": self.pattern_description,
            "affected_txn_ids": [str(t) for t in self.affected_txn_ids],
            "first_suspicious_txn_id": str(self.first_suspicious_txn_id or ""),
            "connected_card_ids": list(self.connected_card_ids),
            "connected_device_profiles": list(self.connected_device_profiles),
            "exposure_usd": round(float(self.exposure_usd), 2),
            "evidence": [e.to_answer_dict() for e in self.evidence],
            "similar_prior_cases": list(self.similar_prior_cases),
            "summary": self.summary,
            "written_to_graph": bool(self.written_to_graph),
            "graph_case_id": self.graph_case_id,
        }


class SAR(BaseModel):
    file: bool = False
    reason: str = ""
    narrative: str = ""
    subjects: list[str] = Field(default_factory=list)
    total_amount_usd: float = 0.0
    activity_dates: list[str] = Field(default_factory=list)

    def to_answer_dict(self) -> dict:
        if not self.file:
            return {
                "file": False, "reason": self.reason, "narrative": "",
                "subjects": [], "total_amount_usd": 0, "activity_dates": [],
            }
        return {
            "file": True,
            "reason": self.reason,
            "narrative": self.narrative,
            "subjects": list(self.subjects),
            "total_amount_usd": round(float(self.total_amount_usd), 2),
            "activity_dates": list(self.activity_dates),
        }


class NextBestActions(BaseModel):
    initial: list[ActionRecommendation] = Field(default_factory=list)
    final: list[ActionRecommendation] = Field(default_factory=list)
    what_changed: str = "nothing"

    def to_answer_dict(self) -> dict:
        return {
            "initial": [a.to_answer_dict() for a in self.initial],
            "final": [a.to_answer_dict() for a in self.final],
            "what_changed": self.what_changed,
        }


class AnswerFile(BaseModel):
    """The complete deliverable for one case."""

    case_id: str
    case: InvestigationCase
    evidence_requests: list[EvidenceRequest] = Field(default_factory=list)
    next_best_actions: NextBestActions
    sar: SAR
    stop_reason: str = ""
    tool_calls: int = 0
    tokens: int = 0
    latency_s: float = 0.0

    # internal, not part of the graded answer
    findings: list[PatternFinding] = Field(default_factory=list, exclude=True)
    risk: RiskAssessment | None = Field(default=None, exclude=True)
    timeline: list[TimelineEntry] = Field(default_factory=list, exclude=True)
    tool_log: list[ToolCall] = Field(default_factory=list, exclude=True)
    trigger: dict[str, Any] = Field(default_factory=dict, exclude=True)

    def to_answer_dict(self) -> dict:
        """Exactly the fields the README specifies, in its order."""
        return {
            "case_id": self.case_id,
            "case": self.case.to_answer_dict(),
            "evidence_requests": [e.to_answer_dict() for e in self.evidence_requests],
            "next_best_actions": self.next_best_actions.to_answer_dict(),
            "sar": self.sar.to_answer_dict(),
            "stop_reason": self.stop_reason,
            "tool_calls": int(self.tool_calls),
            "tokens": int(self.tokens),
            "latency_s": round(float(self.latency_s), 2),
        }

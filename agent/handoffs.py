"""Structured hand-offs between agents (assignment C3).

Every decision a later node depends on is one of these Pydantic objects, never free text.
Nodes produce them via LLM structured output; validation failure triggers a retry, then the
node's fallback model, then the dead-letter state.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- job analysis
class JobSpec(BaseModel):
    job_id: str
    title: str
    category: str = Field(description="e.g. ml_engineering, web_dev, data_analysis")
    deliverables: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list, description="explicitly named")
    implied_skills: list[str] = Field(default_factory=list, description="needed but not named")
    budget_type: Literal["fixed", "hourly", "unknown"] = "unknown"
    budget_min: float | None = None
    budget_max: float | None = None
    timeline_days: int | None = None
    screening_questions: list[str] = Field(default_factory=list)
    hidden_needs: list[str] = Field(default_factory=list, description="what the client really wants")
    requirements: list[str] = Field(
        default_factory=list, description="atomic requirements; one retrieval query each (RAG V1)"
    )


class RiskFlag(str, Enum):
    FREE_WORK = "free_work_request"
    OFF_PLATFORM = "off_platform_contact_or_payment"
    PROMPT_INJECTION = "prompt_injection"
    LOW_CLIENT_QUALITY = "low_client_quality"
    UNREALISTIC_BUDGET = "unrealistic_budget"
    VAGUE_SCOPE = "vague_scope"
    DUPLICATE = "duplicate_post"


class RiskReport(BaseModel):
    job_id: str
    risk_score: float = Field(ge=0, le=1, description="0 safe, 1 certain scam")
    is_scam: bool = False
    flags: list[RiskFlag] = Field(default_factory=list)
    injection_spans: list[str] = Field(default_factory=list, description="quoted suspicious text")
    client_quality: float = Field(ge=0, le=1, default=0.5)
    explanation: str


class EvidenceRef(BaseModel):
    evidence_id: str
    type: str
    title: str
    snippet: str
    score: float = 0.0
    outcome: str | None = None


class RequirementMatch(BaseModel):
    requirement: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    strength: Literal["strong", "partial", "none"] = "none"


class FitReport(BaseModel):
    job_id: str
    fit_score: float = Field(ge=0, le=1)
    matrix: list[RequirementMatch] = Field(default_factory=list)
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- decision & price
class BidDecision(BaseModel):
    job_id: str
    action: Literal["bid", "watch", "skip"]
    confidence: float = Field(ge=0, le=1)
    reasons: list[str]


class PriceQuote(BaseModel):
    job_id: str
    amount: float
    budget_type: Literal["fixed", "hourly"]
    fair_band: tuple[float, float]
    connects_cost: int
    connects_worth_it: bool
    expected_value: float = Field(description="P(hire) * amount - connects USD cost")
    rationale: str


# ---------------------------------------------------------------- writing
class Claim(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal["supported", "partial", "unsupported", "unchecked"] = "unchecked"
    note: str | None = None


class ProposalDraft(BaseModel):
    job_id: str
    cover_letter: str
    screening_answers: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    revision: int = 0


class Critique(BaseModel):
    job_id: str
    passed: bool
    grounding: float = Field(ge=0, le=1)
    personalisation: float = Field(ge=0, le=1)
    length_ok: bool
    issues: list[str] = Field(default_factory=list)
    instructions_for_writer: str = ""


class ComplianceVerdict(BaseModel):
    allowed: bool
    violations: list[str] = Field(default_factory=list)
    redactions: list[str] = Field(default_factory=list, description="text removed (contacts, guarantees)")
    cleaned_text: str
    blocked_reason: str | None = None


# ---------------------------------------------------------------- conversations
class MessageIntent(BaseModel):
    intent: Literal["question", "negotiation", "interview_request", "scam_signal", "other"]
    confidence: float = Field(ge=0, le=1)
    summary: str


class ReplyDraft(BaseModel):
    thread_id: str
    body: str
    proposed_slots: list[str] = Field(default_factory=list)
    intent: MessageIntent


# ---------------------------------------------------------------- approval
class ApprovalDecision(BaseModel):
    action: Literal["approve", "edit", "reject"]
    edited_text: str | None = None
    reason: str | None = None
    user_id: str

"""Typed graph state (assignment C2).

Three things are kept apart:
  * State           -> the fields below: what this job/conversation needs right now.
  * Execution trace -> `trace`, append-only list of tool calls/observations (audit + debugging).
  * Long-term memory-> NOT here. Lives in the LangGraph store (agent/memory/store.py) and is only
                       written by agent/memory/promotion_rules.py.

Fields written by the parallel branches (Job Analyst, Risk Assessor, Fit Matcher) are either
owned by exactly one branch or use a reducer, so concurrent writes never clobber each other.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from agent.handoffs import (
    BidDecision,
    ComplianceVerdict,
    Critique,
    EvidenceRef,
    FitReport,
    JobSpec,
    PriceQuote,
    ProposalDraft,
    ReplyDraft,
    RiskReport,
)


def merge_usage(left: dict[str, float] | None, right: dict[str, float] | None) -> dict[str, float]:
    """Sum token/cost counters from parallel branches."""
    out = dict(left or {})
    for k, v in (right or {}).items():
        out[k] = out.get(k, 0) + v
    return out


def dedupe_evidence(left: list[EvidenceRef] | None, right: list[EvidenceRef] | None) -> list[EvidenceRef]:
    seen: dict[str, EvidenceRef] = {e.evidence_id: e for e in (left or [])}
    for e in right or []:
        if e.evidence_id not in seen or e.score > seen[e.evidence_id].score:
            seen[e.evidence_id] = e
    return sorted(seen.values(), key=lambda e: -e.score)


class TraceEvent(TypedDict):
    node: str
    kind: Literal["tool_call", "llm_call", "observation", "error", "decision"]
    name: str
    args: dict[str, Any]
    result_summary: str
    tokens: int
    latency_ms: int
    ts: str


Status = Literal[
    "running", "skipped", "awaiting_approval", "approved", "submitted",
    "rejected", "blocked", "needs_attention", "dead_letter",
]


class BidState(TypedDict, total=False):
    # identity
    thread_id: str
    trace_id: str
    settings: dict[str, Any]                 # user settings snapshot: floor rate, Connects budget, autonomy
    user_id: str
    persona_id: str
    job_id: str
    job: dict[str, Any]                      # raw job, treated as UNTRUSTED data
    # parallel analyses (each owned by one branch)
    job_spec: JobSpec
    risk_report: RiskReport
    fit_report: FitReport
    evidence: Annotated[list[EvidenceRef], dedupe_evidence]
    # decision path
    decision: BidDecision
    price: PriceQuote
    draft: ProposalDraft
    critiques: Annotated[list[Critique], operator.add]
    verification: dict[str, Any]             # claim verification metrics (unsupported before/after)
    experiment_arm: str | None               # X2 A/B arm for this proposal
    compliance: ComplianceVerdict
    dry_run_preview: dict[str, Any]
    approval: dict[str, Any]
    submission: dict[str, Any]
    # control
    status: Status
    halts: Annotated[list[str], operator.add]   # failures reported by parallel branches
    revisions: int
    steps: Annotated[int, operator.add]
    usage: Annotated[dict[str, float], merge_usage]   # tokens_in, tokens_out, usd
    errors: Annotated[list[str], operator.add]
    trace: Annotated[list[TraceEvent], operator.add]


class ConversationState(TypedDict, total=False):
    trace_id: str
    settings: dict[str, Any]
    intent: Any
    slots: list[dict[str, Any]]
    category: str
    user_id: str
    persona_id: str
    thread_id: str
    job_id: str
    messages: list[dict[str, Any]]           # UNTRUSTED client text
    reply: ReplyDraft
    compliance: ComplianceVerdict
    approval: dict[str, Any]
    sent: dict[str, Any]
    status: Status
    steps: Annotated[int, operator.add]
    usage: Annotated[dict[str, float], merge_usage]
    errors: Annotated[list[str], operator.add]
    trace: Annotated[list[TraceEvent], operator.add]

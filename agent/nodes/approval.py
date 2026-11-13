"""Human approval (R4, C5) and exactly-once submission (C6).

preview  -> dry_run through the Marketplace MCP: exactly what would be sent and what it costs
approval -> interrupt(): payload carries draft, evidence behind every claim, risk report, price
            rationale and the dry_run preview. Resumed with {"action": approve|edit|reject, ...}
submit   -> create draft + submit with idempotency keys derived from the thread id, so a crash or
            retry after approval never submits twice or spends Connects twice
"""
from __future__ import annotations

from langgraph.types import interrupt

from agent.mcp_client import call
from agent.memory.promotion_rules import record_edit, record_outcome
from agent.nodes.common import node


def _letter(state: dict) -> str:
    a = state.get("approval") or {}
    if a.get("action") == "edit" and state.get("compliance") and state["compliance"].allowed:
        return state["compliance"].cleaned_text
    return state["compliance"].cleaned_text if state.get("compliance") else state["draft"].cover_letter


def _payload(state: dict) -> dict:
    p = state["price"]
    return {"job_id": state["job_id"], "cover_letter": _letter(state),
            "screening_answers": state["draft"].screening_answers, "amount": p.amount, "budget_type": p.budget_type}


@node("preview")
async def preview(state: dict) -> dict:
    res = await call("submitter", "marketplace", "create_proposal_draft", {
        "draft": _payload(state), "idempotency_key": f"{state['thread_id']}:draft", "dry_run": True,
        "user_id": state["persona_id"]})
    return {"dry_run_preview": res, "status": "awaiting_approval"}


async def approval(state: dict) -> dict:
    """Not wrapped by @node: interrupt() must run on every resume and is not a retryable step."""
    d = state["draft"]
    decision = interrupt({
        "kind": "proposal",
        "job": {k: state["job"].get(k) for k in ("job_id", "title", "budget_type", "budget_min", "budget_max")},
        "cover_letter": _letter(state),
        "screening_answers": d.screening_answers,
        "claims": [c.model_dump() for c in d.claims],
        "evidence": [e.model_dump() for e in state.get("evidence", [])],
        "risk": state["risk_report"].model_dump(),
        "fit": state["fit_report"].model_dump(),
        "price": state["price"].model_dump(),
        "critiques": [c.model_dump() for c in state.get("critiques", [])],
        "compliance": state["compliance"].model_dump(),
        "verification": state.get("verification"),
        "dry_run": state.get("dry_run_preview"),
    })
    action = decision.get("action")
    if action == "reject":
        return {"approval": decision, "status": "rejected"}
    if action == "edit":
        learned = record_edit(state.get("user_id", state["persona_id"]), _letter(state),
                              decision.get("edited_text", ""), state["job_id"])
        return {"approval": decision | {"style_learning": learned}, "status": "running"}
    return {"approval": decision, "status": "approved"}


def after_approval(state: dict) -> str:
    a = state.get("approval") or {}
    if a.get("action") == "reject":
        return "end"
    if a.get("action") == "edit":
        return "compliance"  # edited text is re-checked; if allowed it goes straight to submit
    return "submit"


def after_compliance(state: dict) -> str:
    if state.get("status") == "blocked":
        return "end" if not (state.get("approval") or {}).get("action") == "edit" else "approval"
    if (state.get("approval") or {}).get("action") == "edit":
        return "submit"
    return "preview"


@node("submit", retries=3)
async def submit(state: dict) -> dict:
    tid, user = state["thread_id"], state["persona_id"]
    draft = await call("submitter", "marketplace", "create_proposal_draft", {
        "draft": _payload(state), "idempotency_key": f"{tid}:draft", "user_id": user})
    sub = await call("submitter", "marketplace", "submit_proposal", {
        "proposal_id": draft["proposal_id"], "idempotency_key": f"{tid}:submit", "user_id": user})
    if not sub.get("idempotent_replay"):
        record_outcome(state.get("user_id", user), state["job"].get("category", "other"), _letter(state), "sent")
    return {"submission": sub | {"proposal_id": draft["proposal_id"]}, "status": "submitted"}

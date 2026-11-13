"""Approval queue: pending interrupts are read from the LangGraph checkpoint (so a refresh or an API
restart never loses one) and resumed with approve / edit / reject."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from apps.api.auth.security import current_user
from apps.api.db import rows
from apps.api.schemas.models import Accepted, ApprovalIn
from apps.api.tasks import enqueue

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("")
def pending(user: dict = Depends(current_user)):
    from worker.runner import pending_interrupt
    out = []
    for r in rows("SELECT * FROM runs WHERE user_id=? AND status='awaiting_approval'", user["id"]):
        p = pending_interrupt(r["thread_id"])
        if p:
            out.append({"thread_id": r["thread_id"], "kind": p.get("kind"), "payload": p, "updated": r["updated"]})
    return sorted(out, key=lambda x: -(x["updated"] or 0))


@router.get("/{thread_id:path}/payload")
def payload(thread_id: str, user: dict = Depends(current_user)):
    from worker.runner import pending_interrupt
    if f":{user['id']}:" not in thread_id:
        raise HTTPException(404, "not found")
    p = pending_interrupt(thread_id)
    if not p:
        raise HTTPException(404, "nothing awaiting approval on this thread")
    return p


@router.post("/{thread_id:path}", response_model=Accepted)
async def decide(thread_id: str, body: ApprovalIn, request: Request, user: dict = Depends(current_user)):
    from worker.runner import pending_interrupt
    if f":{user['id']}:" not in thread_id:
        raise HTTPException(404, "not found")
    if not pending_interrupt(thread_id):
        raise HTTPException(409, "this thread is not awaiting approval (already decided?)")
    if body.action == "edit" and not (body.edited_text or "").strip():
        raise HTTPException(422, "edited_text required for edit")
    if body.action == "reject" and not (body.reason or "").strip():
        raise HTTPException(422, "a reason is required to reject")
    d = await enqueue("resume_thread", user, thread_id, body.model_dump(exclude_none=True), request.state.trace_id,
                      job_key=f"resume:{thread_id}:{body.action}")
    return Accepted(thread_id=thread_id, dispatch=d)

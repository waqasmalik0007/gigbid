"""Graph execution shared by the Arq worker and the API's local fallback.

start_bid / start_conversation / resume run a graph thread to its next interrupt or end, writing
every node update to run_events (the SSE stream reads that table) and keeping jobs/runs current.
Re-running a thread is safe: LangGraph resumes from the checkpoint and marketplace writes use
idempotency keys derived from the thread id, so a worker restart never double-analyses or
double-submits.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from langgraph.types import Command

from agent.memory.store import get_checkpointer, get_store
from apps.api.db import add_event, upsert
from mcp_servers.common.audit import current_trace, current_user

_graphs: dict[str, Any] = {}


def graph(kind: str):
    if kind not in _graphs:
        if kind == "bid":
            from agent.graphs.bid_graph import build
        else:
            from agent.graphs.conversation_graph import build
        _graphs[kind] = build(get_checkpointer(), get_store())
    return _graphs[kind]


def _cfg(thread_id: str, trace_id: str, user: str, checkpoint_id: str | None = None) -> dict:
    """The request's trace id doubles as the LangSmith root run id (E3): the same id is in the API
    response header, the UI, the checkpoint metadata, every MCP audit-log line and the LangSmith trace."""
    conf = {"thread_id": thread_id} | ({"checkpoint_id": checkpoint_id} if checkpoint_id else {})
    cfg = {"configurable": conf, "recursion_limit": 60,
           "metadata": {"trace_id": trace_id, "user": user}, "run_name": thread_id.split(":")[0] + "_graph",
           "tags": [thread_id.split(":")[0]]}
    try:
        cfg["run_id"] = uuid.UUID(trace_id)
    except (ValueError, TypeError):
        pass
    return cfg


def _jsonable(v):
    if hasattr(v, "model_dump"):
        return v.model_dump()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


async def _execute(kind: str, thread_id: str, user: dict, inp, trace_id: str, checkpoint_id: str | None = None) -> dict:
    app = graph(kind)
    cfg = _cfg(thread_id, trace_id, user["id"], checkpoint_id)
    tu, tt = current_user.set(user["persona_id"]), current_trace.set(trace_id)
    upsert("runs", {"thread_id": thread_id}, {"user_id": user["id"], "kind": kind, "status": "running",
                                            "trace_id": trace_id, "updated": time.time(),
                                            **({"created": time.time()} if not isinstance(inp, Command) else {})})
    add_event(thread_id, user["id"], "_run", {"status": "running", "trace_id": trace_id})
    try:
        async for upd in app.astream(inp, cfg, stream_mode="updates"):
            for node, val in upd.items():
                if node == "__interrupt__":
                    add_event(thread_id, user["id"], "approval", {"status": "awaiting_approval"})
                    continue
                v = _jsonable(val or {})
                add_event(thread_id, user["id"], node, {k: v[k] for k in v if k not in ("job", "messages")})
    except Exception as e:  # noqa: BLE001 - surface to UI, keep checkpoint for replay
        add_event(thread_id, user["id"], "_error", {"error": str(e)[:500]})
        upsert("runs", {"thread_id": thread_id}, {"status": "dead_letter", "updated": time.time()})
        raise
    finally:
        current_user.reset(tu)
        current_trace.reset(tt)
    st = app.get_state({"configurable": {"thread_id": thread_id}})  # latest, not the checkpoint we resumed from
    vals = st.values
    status = "awaiting_approval" if st.next else vals.get("status", "done")
    upsert("runs", {"thread_id": thread_id}, {"status": status, "usage": vals.get("usage", {}), "updated": time.time(),
                                            "job_id": vals.get("job_id")})
    if kind == "bid":
        _sync_job(user, thread_id, vals, status)
    add_event(thread_id, user["id"], "_run", {"status": status})
    return {"thread_id": thread_id, "status": status}


def _sync_job(user: dict, thread_id: str, v: dict, status: str) -> None:
    dec, fit, risk, price = v.get("decision"), v.get("fit_report"), v.get("risk_report"), v.get("price")
    job = v.get("job") or {}
    upsert("jobs", {"user_id": user["id"], "job_id": v.get("job_id")}, {
        "job": job, "thread_id": thread_id, "status": status, "action": dec.action if dec else None,
        "fit": fit.fit_score if fit else None, "risk": risk.risk_score if risk else None,
        "price": price.amount if price else None, "connects": job.get("connects_cost"),
        "summary": {"reasons": dec.reasons if dec else [], "flags": [f.value for f in risk.flags] if risk else [],
                    "is_scam": risk.is_scam if risk else False, "errors": v.get("errors", [])[-3:]},
        "updated": time.time()})
    sub = v.get("submission")
    if sub and sub.get("proposal_id"):
        upsert("pipeline", {"user_id": user["id"], "proposal_id": sub["proposal_id"]}, {
            "job_id": v.get("job_id"), "title": job.get("title"), "stage": "submitted", "category": job.get("category"),
            "connects": sub.get("connects_spent"), "amount": price.amount if price else None,
            "thread_id": thread_id, "letter": v["compliance"].cleaned_text if v.get("compliance") else "",
            "updated": time.time()})


async def start_bid(user: dict, job_id: str, job: dict | None = None, trace_id: str | None = None) -> dict:
    tid = f"bid:{user['id']}:{job_id}"
    inp = {"job_id": job_id, "persona_id": user["persona_id"], "user_id": user["id"], "thread_id": tid,
           "settings": user.get("settings") or {}, "trace_id": trace_id or uuid.uuid4().hex}
    if job:
        inp["job"] = job
    st = graph("bid").get_state(_cfg(tid, "", user["id"]))
    if st.values:  # idempotent: already analysed (or in flight) -> do not re-run
        return {"thread_id": tid, "status": st.values.get("status"), "duplicate": True}
    return await _execute("bid", tid, user, inp, inp["trace_id"])


async def start_conversation(user: dict, thread: str, category: str = "other", trace_id: str | None = None) -> dict:
    tid = f"conv:{user['id']}:{thread}:{int(time.time())}"
    inp = {"thread_id": tid, "persona_id": user["persona_id"], "user_id": user["id"], "category": category,
           "settings": user.get("settings") or {}}
    return await _execute("conversation", tid, user, inp, trace_id or uuid.uuid4().hex)


async def resume(user: dict, thread_id: str, decision: dict, trace_id: str | None = None) -> dict:
    kind = "bid" if thread_id.startswith("bid:") else "conversation"
    return await _execute(kind, thread_id, user, Command(resume=decision | {"user_id": user["id"]}),
                          trace_id or uuid.uuid4().hex)


def pending_interrupt(thread_id: str) -> dict | None:
    kind = "bid" if thread_id.startswith("bid:") else "conversation"
    st = graph(kind).get_state({"configurable": {"thread_id": thread_id}})
    for t in st.tasks:
        for it in t.interrupts:
            return it.value
    return None


MARKET_DOWN = "marketplace unavailable"


def _degraded(vals: dict) -> bool:
    return vals.get("status") == "needs_attention" and any(MARKET_DOWN in str(e) for e in vals.get("errors") or [])


def degraded_threads(user_id: str) -> list[str]:
    """Threads parked because the marketplace was down (A6 degraded mode), oldest first."""
    from apps.api.db import rows
    out = []
    for r in rows("SELECT thread_id FROM runs WHERE user_id=? AND status='needs_attention' ORDER BY updated", user_id):
        kind = "bid" if r["thread_id"].startswith("bid:") else "conversation"
        if _degraded(graph(kind).get_state({"configurable": {"thread_id": r["thread_id"]}}).values):
            out.append(r["thread_id"])
    return out


async def recover(user: dict, thread_id: str, trace_id: str | None = None) -> dict:
    """Resume a thread that stopped because the marketplace was unreachable, once it is back.

    Errors are append-only in the state, so the newest checkpoint with no "marketplace unavailable"
    error and a pending node is the point just before the failed step; the run continues from there
    (a fork of the same thread). An approved proposal whose submit failed therefore re-runs only
    `submit`, whose idempotency keys ({thread}:draft, {thread}:submit) make it exactly-once."""
    if thread_id in _recovering:  # health polls can ask twice; one recovery per thread at a time
        return {"thread_id": thread_id, "status": "running", "recovered": False}
    kind = "bid" if thread_id.startswith("bid:") else "conversation"
    app = graph(kind)
    latest = app.get_state({"configurable": {"thread_id": thread_id}})
    if not _degraded(latest.values):
        return {"thread_id": thread_id, "status": latest.values.get("status"), "recovered": False}
    _recovering.add(thread_id)
    try:
        for snap in app.get_state_history({"configurable": {"thread_id": thread_id}}):
            if snap.next and not any(MARKET_DOWN in str(e) for e in snap.values.get("errors") or []):
                add_event(thread_id, user["id"], "_run", {"status": "recovering", "from": list(snap.next)})
                out = await _execute(kind, thread_id, user, None, trace_id or uuid.uuid4().hex,
                                     snap.config["configurable"]["checkpoint_id"])
                return out | {"recovered": True}
        return {"thread_id": thread_id, "status": "needs_attention", "recovered": False}
    finally:
        _recovering.discard(thread_id)


_recovering: set[str] = set()

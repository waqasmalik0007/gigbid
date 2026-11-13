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


def _cfg(thread_id: str, trace_id: str, user: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 60,
            "metadata": {"trace_id": trace_id, "user": user}, "run_name": thread_id}


def _jsonable(v):
    if hasattr(v, "model_dump"):
        return v.model_dump()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


async def _execute(kind: str, thread_id: str, user: dict, inp, trace_id: str) -> dict:
    app = graph(kind)
    cfg = _cfg(thread_id, trace_id, user["id"])
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
    st = app.get_state(cfg)
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

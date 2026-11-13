"""Runs, live streaming (SSE), pipeline board, messages, analytics, memory, marketplace health."""
from __future__ import annotations

import asyncio
import json
import statistics
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from apps.api.auth.security import current_user
from apps.api.db import rows
from apps.api.schemas.models import Accepted, RunOut
from apps.api.tasks import enqueue

router = APIRouter(tags=["runs"])


@router.get("/runs", response_model=list[RunOut])
def list_runs(user: dict = Depends(current_user)):
    found = rows("SELECT * FROM runs WHERE user_id=? ORDER BY updated DESC LIMIT 200", user["id"])
    return [RunOut(**{k: r[k] for k in RunOut.model_fields if k in r} | {"usage": r["usage"] or {}}) for r in found]


@router.get("/runs/{thread_id:path}/events")
def run_events_list(thread_id: str, after: int = 0, user: dict = Depends(current_user)):
    return rows("SELECT * FROM run_events WHERE thread_id=? AND user_id=? AND id>? ORDER BY id",
                thread_id, user["id"], after)


@router.get("/runs/{thread_id:path}/stream")
async def stream(thread_id: str, request: Request, user: dict = Depends(current_user)):
    """Server-Sent Events: one event per graph node update, as the worker writes them."""
    async def gen():
        last, idle = 0, 0
        yield "retry: 2000\n\n"
        while not await request.is_disconnected():
            new = rows("SELECT * FROM run_events WHERE thread_id=? AND user_id=? AND id>? ORDER BY id",
                       thread_id, user["id"], last)
            for r in new:
                last = r["id"]
                yield f"id: {r['id']}\nevent: node\ndata: {json.dumps({'node': r['node'], 'data': r['data'], 'ts': r['ts']}, default=str)}\n\n"
            idle = 0 if new else idle + 1
            if idle and idle % 15 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.7)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/pipeline")
def board(user: dict = Depends(current_user)):
    cards = rows("SELECT * FROM pipeline WHERE user_id=?", user["id"])
    drafted = rows("SELECT * FROM runs WHERE user_id=? AND kind='bid' AND status='awaiting_approval'", user["id"])
    cols = {k: [] for k in ("drafted", "submitted", "viewed", "replied", "interview", "hired", "lost")}
    for r in drafted:
        cols["drafted"].append({"thread_id": r["thread_id"], "job_id": r["job_id"], "title": r["job_id"]})
    for c_ in cards:
        cols.get(c_["stage"], cols["submitted"]).append({k: c_[k] for k in c_ if k != "letter"})
    return cols


@router.post("/pipeline/sync")
async def sync(user: dict = Depends(current_user)):
    from worker.scout import sync_events
    n = await sync_events(user, enqueue=lambda fn, *a, **k: enqueue(fn, *a))
    return {"events": n}


@router.get("/messages")
async def threads(user: dict = Depends(current_user)):
    from agent.mcp_client import call
    return (await call("liaison", "marketplace", "list_messages", {"user_id": user["persona_id"]}))["items"]


@router.get("/messages/{thread}")
async def thread_messages(thread: str, user: dict = Depends(current_user)):
    from agent.mcp_client import call
    return (await call("liaison", "marketplace", "list_messages", {"thread_id": thread,
                                                                    "user_id": user["persona_id"]}))["items"]


@router.post("/messages/{thread}/draft", response_model=Accepted)
async def draft_reply(thread: str, request: Request, user: dict = Depends(current_user)):
    d = await enqueue("run_conversation", user, thread, "other", request.state.trace_id)
    return Accepted(thread_id=thread, dispatch=d)


@router.get("/analytics")
def analytics(user: dict = Depends(current_user)):
    cards = rows("SELECT * FROM pipeline WHERE user_id=?", user["id"])
    rs = rows("SELECT * FROM runs WHERE user_id=? AND kind='bid'", user["id"])
    js = rows("SELECT * FROM jobs WHERE user_id=?", user["id"])
    by_cat: dict = defaultdict(lambda: {"sent": 0, "replied": 0, "hired": 0, "connects": 0})
    for p in cards:
        d = by_cat[p["category"] or "other"]
        d["sent"] += 1
        d["connects"] += p["connects"] or 0
        d["replied"] += p["stage"] in ("replied", "interview", "hired")
        d["hired"] += p["stage"] == "hired"
    for d in by_cat.values():
        d["reply_rate"] = round(d["replied"] / d["sent"], 3) if d["sent"] else None
        d["hire_rate"] = round(d["hired"] / d["sent"], 3) if d["sent"] else None
    usd = [r["usage"].get("usd", 0) for r in rs if r["usage"]]
    lat = [sum(v for k, v in r["usage"].items() if k.startswith("latency_ms:")) / 1000 for r in rs if r["usage"]]
    rejects = defaultdict(int)
    for j in js:
        for reason in (j["summary"] or {}).get("reasons", []):
            if j["action"] == "skip":
                rejects[reason.split(";")[0][:60]] += 1
    q = lambda xs, p: round(sorted(xs)[min(len(xs) - 1, int(p * len(xs)))], 4) if xs else None  # noqa: E731
    return {"by_category": dict(by_cat),
            "connects_spent": sum(d["connects"] for d in by_cat.values()),
            "jobs_analysed": len(rs), "cost_usd_per_job": {"mean": round(statistics.mean(usd), 5) if usd else None,
                                                           "p95": q(usd, 0.95)},
            "latency_s_per_job": {"p50": q(lat, 0.5), "p95": q(lat, 0.95)},
            "top_skip_reasons": sorted(rejects.items(), key=lambda x: -x[1])[:8]}


@router.get("/memory")
def memory(user: dict = Depends(current_user)):
    from agent.memory.promotion_rules import outcome_memory, style_memory
    return {"style": style_memory(user["id"]), "outcome": outcome_memory(user["id"])}


@router.delete("/memory/{kind}/{key:path}")
def forget(kind: str, key: str, user: dict = Depends(current_user)):
    from agent.memory.promotion_rules import delete_item
    if kind not in ("style", "outcome"):
        raise HTTPException(400, "kind must be style or outcome")
    delete_item(user["id"], kind, key)
    return {"deleted": key}


@router.get("/health/marketplace")
async def marketplace_health(user: dict = Depends(current_user)):
    from agent.mcp_client import call
    try:
        bal = await asyncio.wait_for(call("pricing", "marketplace", "get_connects_balance",
                                          {"user_id": user["persona_id"]}, timeout=6, retries=0), 10)
        return {"ok": True, "connects": bal["balance"]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200], "message": "Marketplace unavailable: new work is queued and "
                "will resume automatically. Approvals you make are saved."}

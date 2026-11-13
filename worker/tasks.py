"""Arq worker (E1): scheduled scouting and long agent runs, outside web requests.

    arq worker.tasks.WorkerSettings

* scout_all (cron): for every user and saved search, pull jobs posted since the last run through the
  Marketplace MCP (Scout allowlist), de-duplicate, queue one bid run per new job. GigSim every
  `scheduling.gigsim_scout_interval_min`; live Upwork no more often than `upwork_scout_interval_min`
  (docs/RUNBOOK.md justifies the interval). Polite: a 1.5 s pause between searches.
* run_bid / resume_thread / run_conversation: graph executions (idempotent, see worker/runner.py).
* poll_events (cron): marketplace events -> pipeline board + outcome memory + conversation runs.
"""
from __future__ import annotations

import asyncio
import os
import time

from arq import cron
from arq.connections import RedisSettings

from agent.llm import config
from apps.api.db import rows
from worker import runner
from worker.scout import scout_user


async def run_bid(ctx, user: dict, job_id: str, job: dict | None = None, trace_id: str | None = None):
    return await runner.start_bid(user, job_id, job, trace_id)


async def resume_thread(ctx, user: dict, thread_id: str, decision: dict, trace_id: str | None = None):
    return await runner.resume(user, thread_id, decision, trace_id)


async def run_conversation(ctx, user: dict, thread: str, category: str = "other", trace_id: str | None = None):
    return await runner.start_conversation(user, thread, category, trace_id)


def _users() -> list[dict]:
    return rows("SELECT * FROM users")


async def scout_all(ctx):
    interval = config()["scheduling"]["upwork_scout_interval_min" if os.getenv("MARKETPLACE_MODE") == "upwork_live"
                                      else "gigsim_scout_interval_min"]
    last = ctx.get("last_scout", 0)
    if time.time() - last < interval * 60 - 5:
        return {"skipped": "interval not reached"}
    ctx["last_scout"] = time.time()
    queued = 0
    for u in _users():
        new = await scout_user(u)
        for j in new:
            await ctx["redis"].enqueue_job("run_bid", u, j["job_id"], j, _job_id=f"bid:{u['id']}:{j['job_id']}")
            queued += 1
        await asyncio.sleep(1.5)
    return {"queued": queued}


async def poll_events(ctx):
    from worker.scout import sync_events
    for u in _users():
        await sync_events(u, enqueue=lambda *a, **k: ctx["redis"].enqueue_job(*a, **k))


class WorkerSettings:
    functions = [run_bid, resume_thread, run_conversation, scout_all, poll_events]
    cron_jobs = [cron(scout_all, minute=set(range(0, 60, 5))), cron(poll_events, minute=set(range(0, 60, 2)))]
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    max_jobs = 4
    job_timeout = 900
    keep_result = 3600


__all__ = ["WorkerSettings"]

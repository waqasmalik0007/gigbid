"""Dispatch long runs to the Arq worker (Redis). Without Redis (laptop / single free container) the
same runner executes as a background asyncio task in the API process."""
from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger("gigbid.tasks")
_pool = None
_local: set[asyncio.Task] = set()


async def _redis():
    global _pool
    if _pool is None and os.getenv("REDIS_URL") and os.getenv("GIGBID_INPROCESS_WORKER") != "1":
        try:
            from arq import create_pool
            from arq.connections import RedisSettings
            _pool = await asyncio.wait_for(create_pool(RedisSettings.from_dsn(os.environ["REDIS_URL"])), 3)
        except Exception as e:  # noqa: BLE001
            log.warning("Redis unavailable (%s); running agent jobs in-process", e)
            os.environ["GIGBID_INPROCESS_WORKER"] = "1"
    return _pool


async def enqueue(fn: str, *args, job_key: str | None = None, **kw) -> str:
    pool = await _redis()
    if pool:
        await pool.enqueue_job(fn, *args, _job_id=job_key, **kw)
        return "queued"
    from worker import runner
    target = {"run_bid": runner.start_bid, "resume_thread": runner.resume,
              "run_conversation": runner.start_conversation}[fn]

    async def _wrap():
        try:
            await target(*args, **kw)
        except Exception as e:  # noqa: BLE001 - recorded in run_events by the runner
            log.error("background %s failed: %s", fn, e)

    t = asyncio.create_task(_wrap())
    _local.add(t)
    t.add_done_callback(_local.discard)
    return "running-inprocess"

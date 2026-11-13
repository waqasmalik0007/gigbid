"""Shared node wrapper: budgets (C4), retries/timeouts/dead-letter (C7), execution trace (C2), tracing (E3).

Every node is `@node("name")` over an async fn(state) -> dict. The wrapper:
  * stops the thread in a visible `needs_attention` state when the step or token budget is exceeded
  * retries the node (bounded, with backoff) and sends it to `dead_letter` after the last failure
  * parks the job as `needs_attention` with reason "marketplace unavailable" when GigSim/Upwork is down
  * appends a TraceEvent (node, latency, tokens, summary) and sums usage into state["usage"]
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from agent.llm import config
from agent.mcp_client import MarketplaceDown
from mcp_servers.common.audit import mask_obj

TERMINAL = {"skipped", "rejected", "blocked", "needs_attention", "dead_letter", "submitted"}


def budgets() -> dict:
    return config()["budgets"]


def halted(state: dict) -> bool:
    return state.get("status") in TERMINAL


def _summary(out: dict) -> str:
    keep = {k: v for k, v in out.items() if k not in ("trace", "usage", "steps")}
    try:
        return json.dumps(mask_obj(json.loads(json.dumps(keep, default=lambda o: getattr(o, "model_dump",
                                                                                       lambda: str(o))()))))[:600]
    except Exception:  # noqa: BLE001
        return str(keep)[:600]


def event(node: str, kind: str, name: str, args: dict | None = None, result: str = "", tokens: int = 0,
          latency_ms: int = 0) -> dict:
    return dict(node=node, kind=kind, name=name, args=mask_obj(args or {}), result_summary=result[:600],
                tokens=tokens, latency_ms=latency_ms, ts=datetime.now(timezone.utc).isoformat())


def node(name: str, retries: int = 2, timeout_s: float = 120, parallel: bool = False) -> Callable:
    """parallel=True for the Send branches: they report failures in `halts` (append-only) instead of
    writing `status`, because two branches writing the same key in one step would conflict."""
    def deco(fn: Callable[[dict], Awaitable[dict]]):
        @functools.wraps(fn)
        async def wrapper(state: dict) -> dict:
            out = await _run(state)
            if parallel and "status" in out:
                out["halts"] = [out.pop("status")]
            return out

        async def _run(state: dict) -> dict:
            b = budgets()
            used = state.get("usage", {})
            if state.get("steps", 0) >= b["max_steps_per_thread"]:
                return {"status": "needs_attention", "errors": [f"step budget exceeded before {name}"],
                        "trace": [event(name, "error", "budget", result="step budget exceeded")]}
            if used.get("tokens_in", 0) + used.get("tokens_out", 0) >= b["max_tokens_per_thread"]:
                return {"status": "needs_attention", "errors": [f"token budget exceeded before {name}"],
                        "trace": [event(name, "error", "budget", result="token budget exceeded")]}
            delay, last = 1.0, None
            for attempt in range(retries + 1):
                t0 = time.perf_counter()
                try:
                    # evals run many jobs through one rate budget: time queued in the throttle must not count as a hang
                    out = await asyncio.wait_for(fn(state), timeout_s * float(os.getenv("NODE_TIMEOUT_SCALE", "1")))
                    ms = int((time.perf_counter() - t0) * 1000)
                    u = out.pop("_usage", {}) or {}
                    tok = int(u.get("tokens_in", 0) + u.get("tokens_out", 0))
                    out.setdefault("trace", [])
                    out["trace"] = list(out["trace"]) + [event(name, "decision", name, result=_summary(out),
                                                               tokens=tok, latency_ms=ms)]
                    out["steps"] = 1
                    out["usage"] = {"tokens_in": u.get("tokens_in", 0), "tokens_out": u.get("tokens_out", 0),
                                    "usd": u.get("usd", 0.0), f"latency_ms:{name}": ms, f"tokens:{name}": tok}
                    return out
                except MarketplaceDown as e:
                    return {"status": "needs_attention", "errors": [f"marketplace unavailable: {e}"],
                            "trace": [event(name, "error", "marketplace_down", result=str(e))], "steps": 1}
                except Exception as e:  # noqa: BLE001
                    from agent.llm import QuotaExhausted
                    if isinstance(e, QuotaExhausted) or isinstance(e.__cause__, QuotaExhausted):
                        last = e
                        break  # retrying cannot help until the daily window resets
                    last = e
                    if attempt < retries:
                        await asyncio.sleep(delay)
                        delay *= 2
            return {"status": "dead_letter", "errors": [f"{name} failed after {retries + 1} attempts: {last}"],
                    "trace": [event(name, "error", "dead_letter", result=repr(last))], "steps": 1}
        wrapper.node_name = name
        return wrapper
    return deco


def add_usage(*usages: dict) -> dict:
    out: dict[str, Any] = {"tokens_in": 0, "tokens_out": 0, "usd": 0.0}
    for u in usages:
        for k in out:
            out[k] += (u or {}).get(k, 0)
    return out

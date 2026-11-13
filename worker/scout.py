"""Scout agent: saved searches -> new, de-duplicated jobs; and marketplace event sync.

De-duplication: exact job id seen before, OR a near-duplicate post (same client and normalised
title, or token Jaccard >= 0.8 on title+first 300 chars of description) already queued.
"""
from __future__ import annotations

import re
import time

from agent.mcp_client import MarketplaceDown, call
from apps.api.db import add_event, row, rows, run, upsert
from mcp_servers.common.audit import current_user

STAGE = {"viewed": "viewed", "replied": "replied", "interview": "interview", "hired": "hired", "lost": "lost"}


def _norm(t: str) -> str:
    return re.sub(r"\W+", " ", re.sub(r"\(reposted\)", "", t.lower())).strip()


def _tokens(j: dict) -> set[str]:
    return set(_norm(j.get("title", "") + " " + j.get("description", "")[:300]).split())


def is_duplicate(job: dict, seen: list[dict]) -> str | None:
    tk = _tokens(job)
    for s in seen:
        if s["job_id"] == job["job_id"]:
            return s["job_id"]
        if s.get("client_id") == job.get("client_id") and _norm(s.get("title", "")) == _norm(job.get("title", "")):
            return s["job_id"]
        other = _tokens(s)
        if tk and other and len(tk & other) / len(tk | other) >= 0.8:
            return s["job_id"]
    return None


async def scout_user(user: dict) -> list[dict]:
    tok = current_user.set(user["persona_id"])
    try:
        seen = [r["job"] for r in rows("SELECT job FROM jobs WHERE user_id=?", user["id"]) if r["job"]]
        since = max((s.get("posted_at", "") for s in seen), default=None)
        new: list[dict] = []
        for search in (user.get("settings") or {}).get("saved_searches", [{"query": ""}]):
            res = await call("scout", "marketplace", "search_jobs", {
                "query": search.get("query", ""), "category": search.get("category"), "since": since, "limit": 50,
                "user_id": user["persona_id"]})
            for j in res["jobs"]:
                dup = is_duplicate(j, seen + new)
                if dup:
                    if dup != j["job_id"]:
                        upsert("jobs", {"user_id": user["id"], "job_id": j["job_id"]},
                               {"job": j, "status": "duplicate", "action": "skip",
                                "summary": {"reasons": [f"duplicate of {dup}"]}, "updated": time.time()})
                    continue
                new.append(j)
                upsert("jobs", {"user_id": user["id"], "job_id": j["job_id"]},
                       {"job": j, "status": "queued", "updated": time.time()})
        return new
    except MarketplaceDown as e:
        add_event("_system", user["id"], "scout", {"degraded": True, "error": str(e)[:200]})
        return []
    finally:
        current_user.reset(tok)


async def sync_events(user: dict, enqueue=None) -> int:
    """Move pipeline cards on marketplace events; a client reply starts a conversation run."""
    from agent.memory.promotion_rules import record_outcome
    tok = current_user.set(user["persona_id"])
    try:
        fresh = row("SELECT settings FROM users WHERE id=?", user["id"]) or {}
        evs = await call("liaison", "marketplace", "list_events", {
            "user_id": user["persona_id"], "after_id": (fresh.get("settings") or {}).get("last_event", 0)})
    except Exception:  # noqa: BLE001 - degraded mode handled by the UI health check
        return 0
    finally:
        current_user.reset(tok)
    n, last = 0, (fresh.get("settings") or {}).get("last_event", 0)
    cards = {r["proposal_id"]: r for r in rows("SELECT * FROM pipeline WHERE user_id=?", user["id"])}
    for e in evs.get("items", []):
        last = max(last, e["id"])
        card = cards.get(e["proposal_id"])
        if not card or e["type"] not in STAGE:
            continue
        upsert("pipeline", {"user_id": user["id"], "proposal_id": e["proposal_id"]},
               {"stage": STAGE[e["type"]], "updated": time.time()})
        if e["type"] in ("replied", "hired"):
            record_outcome(user["id"], card.get("category") or "other", card.get("letter") or "", e["type"])
        if e["type"] == "replied" and enqueue:
            await enqueue("run_conversation", user, f"thr_{e['proposal_id']}", card.get("category") or "other")
        n += 1
    run("UPDATE users SET settings=? WHERE id=?",  # remember the last event so nothing is counted twice
        (fresh.get("settings") or {}) | {"last_event": last}, user["id"])
    return n

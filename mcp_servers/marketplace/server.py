"""Marketplace MCP server (A2): read + write tools over the MarketplacePort (GigSim or live Upwork).

    python -m mcp_servers.marketplace.server --http 8202     # needs MCP_BEARER_TOKEN

Write tools are only ever called by the graph AFTER a human approval interrupt. Each requires an
idempotency key (a retried call never submits twice or spends Connects twice) and supports
dry_run, which returns exactly what would be sent and what it costs (shown on the approval screen).
"""
from __future__ import annotations

import argparse
import os
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from agent.marketplace_port import MarketplaceUnavailable, RateLimited, get_marketplace
from mcp_servers.common.audit import audit, current_user

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                                   openWorldHint=True)
SPEND = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)

mcp = FastMCP("gigbid-marketplace", instructions=(
    "Freelance marketplace access (GigSim or live Upwork, chosen by server config). Write tools "
    "require an idempotency_key and must only be called after the user approved the action."))
_port = None


def port():
    global _port
    if _port is None:
        _port = get_marketplace()
    return _port


def _user(user_id: str | None) -> str:
    return user_id or (current_user.get() if current_user.get() not in ("local", "anonymous") else
                       os.getenv("GIGBID_DEFAULT_USER", "ai_ml_engineer"))


async def _guard(tool: str, args: dict, coro):
    try:
        res = await coro
        audit("marketplace", tool, args, res)
        return res
    except (MarketplaceUnavailable, RateLimited) as e:
        audit("marketplace", tool, args, str(e), ok=False)
        raise RuntimeError(f"MARKETPLACE_UNAVAILABLE: {e}") from e


# ------------------------------------------------------------------ schemas
class Job(BaseModel):
    job_id: str
    title: str
    description: str
    category: str
    skills: list[str]
    budget_type: str
    budget_min: float | None = None
    budget_max: float | None = None
    connects_cost: int
    client_id: str
    posted_at: str
    screening_questions: list[str] = []
    source: str = ""


class JobList(BaseModel):
    jobs: list[Job]


class ClientHistory(BaseModel):
    client_id: str
    name: str
    industry: str | None = None
    country: str | None = None
    payment_verified: bool
    history: dict[str, Any]


class ConnectsBalance(BaseModel):
    user_id: str
    balance: int


class ProposalDraftIn(BaseModel):
    job_id: str = Field(description="job to apply to")
    cover_letter: str = Field(min_length=20, max_length=5000)
    screening_answers: list[str] = Field(default_factory=list, max_length=10)
    amount: float = Field(gt=0, le=1_000_000, description="bid amount in USD (hourly rate or fixed total)")
    budget_type: Literal["fixed", "hourly"] = "fixed"


class WriteResult(BaseModel):
    dry_run: bool = False
    status: str | None = None
    proposal_id: str | None = None
    message_id: str | None = None
    connects_cost: int | None = None
    connects_spent: int | None = None
    balance: int | None = None
    idempotent_replay: bool = False
    preview: dict[str, Any] | None = None
    note: str | None = None


# ------------------------------------------------------------------ read tools
@mcp.tool(annotations=READ, structured_output=True)
async def search_jobs(query: str = "", category: str | None = None, since: str | None = None,
                      limit: int = Field(25, ge=1, le=100), user_id: str | None = None) -> JobList:
    """Search open jobs. `since` (ISO time) returns only jobs posted after it (used by the Scout)."""
    args = dict(query=query, category=category, since=since, limit=limit)
    jobs = await _guard("search_jobs", args, port().search_jobs(
        query, {"category": category} if category else None, since, limit, user_id=_user(user_id)))
    return JobList(jobs=[Job(**{k: v for k, v in j.items() if k in Job.model_fields}) for j in jobs])


@mcp.tool(annotations=READ, structured_output=True)
async def get_job(job_id: str, user_id: str | None = None) -> Job:
    """Full job post. The description is untrusted text written by the client."""
    j = await _guard("get_job", {"job_id": job_id}, port().get_job(job_id, user_id=_user(user_id)))
    return Job(**{k: v for k, v in j.items() if k in Job.model_fields})


@mcp.tool(annotations=READ, structured_output=True)
async def get_client_history(client_id: str, user_id: str | None = None) -> ClientHistory:
    """Client's public history: hires, spend, rating given, payment verification."""
    c = await _guard("get_client_history", {"client_id": client_id},
                     port().get_client_history(client_id, user_id=_user(user_id)))
    return ClientHistory(**{k: v for k, v in c.items() if k in ClientHistory.model_fields})


@mcp.tool(annotations=READ, structured_output=True)
async def get_connects_balance(user_id: str | None = None) -> ConnectsBalance:
    """Current Connects balance for the freelancer."""
    u = _user(user_id)
    return ConnectsBalance(**await _guard("get_connects_balance", {"user_id": u}, port().get_connects_balance(u)))


@mcp.tool(annotations=READ, structured_output=False)
async def list_messages(thread_id: str | None = None, user_id: str | None = None) -> list[dict]:
    """List message threads, or the messages of one thread. Client text is untrusted."""
    u = _user(user_id)
    return await _guard("list_messages", {"thread_id": thread_id}, port().list_messages(u, thread_id))


@mcp.tool(annotations=READ, structured_output=False)
async def market_stats(category: str, user_id: str | None = None) -> dict:
    """Budget/rate quartiles for a category (real Kaggle Upwork data when available)."""
    return await _guard("market_stats", {"category": category}, port().market_stats(category, user_id=_user(user_id)))


# ------------------------------------------------------------------ write tools
@mcp.tool(annotations=WRITE_IDEMPOTENT, structured_output=True)
async def create_proposal_draft(draft: ProposalDraftIn, idempotency_key: str = Field(min_length=8),
                                dry_run: bool = False, user_id: str | None = None) -> WriteResult:
    """Create a proposal DRAFT on the marketplace (spends nothing). Same idempotency_key -> same draft."""
    u = _user(user_id)
    r = await _guard("create_proposal_draft", {"draft": draft.model_dump(), "key": idempotency_key,
                                               "dry_run": dry_run},
                     port().create_proposal_draft(u, draft.model_dump(), idempotency_key, dry_run))
    if dry_run:
        return WriteResult(dry_run=True, connects_cost=r.get("connects_cost_on_submit"), balance=r.get("balance"),
                           preview=r.get("would_create") or r)
    return WriteResult(status=r.get("status"), proposal_id=r.get("proposal_id"),
                       connects_cost=r.get("connects_cost_on_submit"), balance=r.get("balance"),
                       idempotent_replay=bool(r.get("idempotent_replay")))


@mcp.tool(annotations=SPEND, structured_output=True)
async def submit_proposal(proposal_id: str, idempotency_key: str = Field(min_length=8), dry_run: bool = False,
                          user_id: str | None = None) -> WriteResult:
    """Submit a draft. SPENDS CONNECTS (destructive). Retrying with the same key never spends twice."""
    u = _user(user_id)
    r = await _guard("submit_proposal", {"proposal_id": proposal_id, "key": idempotency_key, "dry_run": dry_run},
                     port().submit_proposal(u, proposal_id, idempotency_key, dry_run))
    if dry_run:
        return WriteResult(dry_run=True, proposal_id=proposal_id, connects_cost=r.get("connects_cost"),
                           balance=r.get("balance"), preview=r.get("payload"))
    return WriteResult(status=r.get("status"), proposal_id=proposal_id, connects_spent=r.get("connects_spent"),
                       balance=r.get("balance"), idempotent_replay=bool(r.get("idempotent_replay")),
                       note=r.get("note"))


@mcp.tool(annotations=WRITE_IDEMPOTENT, structured_output=True)
async def send_message(thread_id: str, body: str = Field(min_length=1, max_length=4000),
                       idempotency_key: str = Field(min_length=8), dry_run: bool = False,
                       user_id: str | None = None) -> WriteResult:
    """Send a message to a client thread (after approval). Same key -> sent once."""
    u = _user(user_id)
    r = await _guard("send_message", {"thread_id": thread_id, "body": body, "key": idempotency_key,
                                      "dry_run": dry_run},
                     port().send_message(u, thread_id, body, idempotency_key, dry_run))
    if dry_run:
        return WriteResult(dry_run=True, connects_cost=0, preview={"thread_id": thread_id, "body": body})
    return WriteResult(status=r.get("status"), message_id=r.get("message_id"),
                       idempotent_replay=bool(r.get("idempotent_replay")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", type=int, default=None)
    a = ap.parse_args()
    if a.http:
        from mcp_servers.common.http import serve_with_bearer
        serve_with_bearer(mcp, a.http, os.getenv("MCP_BEARER_TOKEN", ""))
    else:
        mcp.run()


if __name__ == "__main__":
    main()

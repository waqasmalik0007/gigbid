"""GigSim adapter: MarketplacePort over GigSim's REST API.

Bounded retries with exponential backoff on 5xx / timeouts, Retry-After honoured on 429, and a
MarketplaceUnavailable error once retries are exhausted (the UI turns that into degraded mode).
"""
from __future__ import annotations

import asyncio
import os
import random
from typing import Any

import httpx

from agent.marketplace_port import MarketplaceUnavailable, RateLimited

TOKENS = {
    "ai_ml_engineer": os.getenv("GIGSIM_TOKEN_AI", "sim-ai_ml_engineer"),
    "fullstack_web": os.getenv("GIGSIM_TOKEN_WEB", "sim-fullstack_web"),
    "data_analyst": os.getenv("GIGSIM_TOKEN_DATA", "sim-data_analyst"),
}


class GigSimAdapter:
    name = "gigsim"

    def __init__(self, base_url: str | None = None, max_retries: int = 4, transport=None):
        self.base = (base_url or os.getenv("GIGSIM_BASE_URL", "http://localhost:8100")).rstrip("/")
        self.max_retries = max_retries
        self._transport = transport  # tests inject ASGITransport(app=gigsim.app)

    def _headers(self, user_id: str, idem: str | None = None) -> dict:
        h = {"Authorization": f"Bearer {TOKENS.get(user_id, TOKENS['ai_ml_engineer'])}"}
        if idem:
            h["Idempotency-Key"] = idem
        return h

    async def _req(self, method: str, path: str, user_id: str, idem: str | None = None, **kw) -> Any:
        delay = 0.5
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(base_url=self.base, timeout=10, transport=self._transport) as c:
                    r = await c.request(method, path, headers=self._headers(user_id, idem), **kw)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
                if attempt == self.max_retries:
                    raise MarketplaceUnavailable(f"GigSim unreachable: {e}") from e
                await asyncio.sleep(delay + random.random() * 0.2)
                delay *= 2
                continue
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", "5"))
                if attempt == self.max_retries:
                    raise RateLimited(wait)
                await asyncio.sleep(min(wait, 10))
                continue
            if r.status_code >= 500:
                if attempt == self.max_retries:
                    raise MarketplaceUnavailable(f"GigSim error {r.status_code}")
                await asyncio.sleep(delay + random.random() * 0.2)
                delay *= 2
                continue
            if r.status_code >= 400:
                raise ValueError(f"GigSim {r.status_code}: {r.json().get('error')}")
            return r.json()
        raise MarketplaceUnavailable("unreachable")

    # ---- read
    async def search_jobs(self, query: str, filters: dict | None = None, since: str | None = None,
                          limit: int = 50, user_id: str = "ai_ml_engineer") -> list[dict]:
        params = {"q": query, "limit": limit} | ({"since": since} if since else {}) | (filters or {})
        return (await self._req("GET", "/jobs", user_id, params=params))["jobs"]

    async def get_job(self, job_id: str, user_id: str = "ai_ml_engineer") -> dict:
        return await self._req("GET", f"/jobs/{job_id}", user_id)

    async def get_client_history(self, client_id: str, user_id: str = "ai_ml_engineer") -> dict:
        return await self._req("GET", f"/clients/{client_id}", user_id)

    async def get_connects_balance(self, user_id: str) -> dict:
        return await self._req("GET", "/connects", user_id)

    async def list_messages(self, user_id: str, thread_id: str | None = None) -> list[dict]:
        if thread_id:
            return (await self._req("GET", f"/threads/{thread_id}/messages", user_id))["messages"]
        return (await self._req("GET", "/threads", user_id))["threads"]

    async def market_stats(self, category: str, user_id: str = "ai_ml_engineer") -> dict:
        return await self._req("GET", "/market/stats", user_id, params={"category": category})

    async def list_events(self, user_id: str, after_id: int = 0) -> list[dict]:
        return (await self._req("GET", "/events", user_id, params={"after_id": after_id}))["events"]

    # ---- write (only reached after human approval in the graph)
    async def create_proposal_draft(self, user_id: str, payload: dict, idempotency_key: str,
                                    dry_run: bool = False) -> dict:
        return await self._req("POST", "/proposals/drafts", user_id, idem=None if dry_run else idempotency_key,
                               params={"dry_run": str(dry_run).lower()}, json=payload)

    async def submit_proposal(self, user_id: str, draft_id: str, idempotency_key: str,
                              dry_run: bool = False) -> dict:
        return await self._req("POST", f"/proposals/{draft_id}/submit", user_id,
                               idem=None if dry_run else idempotency_key, params={"dry_run": str(dry_run).lower()})

    async def send_message(self, user_id: str, thread_id: str, body: str, idempotency_key: str,
                           dry_run: bool = False) -> dict:
        return await self._req("POST", f"/threads/{thread_id}/messages", user_id,
                               idem=None if dry_run else idempotency_key,
                               params={"dry_run": str(dry_run).lower()}, json={"body": body})

"""Live Upwork adapter (A4): the official Upwork MCP server behind the same MarketplacePort.

* Transport: MultiServerMCPClient over Streamable HTTP to UPWORK_MCP_URL (https://mcp.upwork.com/mcp).
* Auth: OAuth 2.1 (authorization code + PKCE) completed in the web app (apps/api/auth/upwork_oauth.py);
  the access token is stored encrypted per user (apps/api/auth/crypto.py) and decrypted only here.
* Rules: read tools only on a slow schedule (config scheduling.upwork_scout_interval_min, see
  docs/RUNBOOK.md); writes create DRAFTS only, which the user then confirms on Upwork itself.
  No browser automation, no session cookies, no scraping.

Upwork's tool names are mapped onto our port in TOOL_MAP (discovered with `list_tools()` at connect
time; unmapped capabilities raise NotImplementedError instead of guessing).
"""
from __future__ import annotations

import json
import os
from typing import Any

from agent.marketplace_port import MarketplaceUnavailable

# our port method -> candidate Upwork MCP tool names (first one the server exposes wins)
TOOL_MAP = {
    "search_jobs": ["search_jobs", "job_search", "searchJobs"],
    "get_job": ["get_job", "get_job_details", "getJobDetails"],
    "get_client_history": ["get_client", "get_client_info", "getClientInfo"],
    "get_connects_balance": ["get_connects_balance", "connects_balance"],
    "list_messages": ["list_messages", "get_messages", "list_rooms"],
    "create_proposal_draft": ["create_proposal_draft", "draft_proposal", "createProposalDraft"],
    "send_message": ["create_message_draft", "draft_message", "send_message"],
}


class UpworkLiveAdapter:
    name = "upwork_live"

    def __init__(self, token_provider=None):
        self.url = os.getenv("UPWORK_MCP_URL", "https://mcp.upwork.com/mcp")
        self._token_provider = token_provider  # callable(user_id) -> decrypted access token
        self._tools: dict[str, Any] = {}

    async def _tool(self, user_id: str, method: str):
        if user_id in self._tools:
            tools = self._tools[user_id]
        else:
            from langchain_mcp_adapters.client import MultiServerMCPClient
            if not self._token_provider:
                from apps.api.auth.upwork_oauth import get_access_token
                self._token_provider = get_access_token
            token = self._token_provider(user_id)
            if not token:
                raise MarketplaceUnavailable("Upwork account not connected; complete OAuth in Settings")
            client = MultiServerMCPClient({"upwork": {"transport": "streamable_http", "url": self.url,
                                                      "headers": {"Authorization": f"Bearer {token}"}}})
            try:
                tools = {t.name: t for t in await client.get_tools()}
            except Exception as e:
                raise MarketplaceUnavailable(f"Upwork MCP unreachable: {e}") from e
            self._tools[user_id] = tools
        for name in TOOL_MAP[method]:
            if name in tools:
                return tools[name]
        raise NotImplementedError(f"Upwork MCP exposes no tool for {method}; available: {sorted(tools)}")

    async def _call(self, user_id: str, method: str, **args) -> Any:
        tool = await self._tool(user_id, method)
        out = await tool.ainvoke(args)
        if isinstance(out, str):
            try:
                return json.loads(out)
            except json.JSONDecodeError:
                return {"text": out}
        return out

    async def search_jobs(self, query, filters=None, since=None, limit=20, user_id="me"):
        return await self._call(user_id, "search_jobs", query=query, limit=limit, **(filters or {}))

    async def get_job(self, job_id, user_id="me"):
        return await self._call(user_id, "get_job", job_id=job_id)

    async def get_client_history(self, client_id, user_id="me"):
        return await self._call(user_id, "get_client_history", client_id=client_id)

    async def get_connects_balance(self, user_id):
        return await self._call(user_id, "get_connects_balance")

    async def list_messages(self, user_id, thread_id=None):
        return await self._call(user_id, "list_messages", **({"room_id": thread_id} if thread_id else {}))

    async def market_stats(self, category, user_id="me"):
        raise NotImplementedError("Upwork exposes no market statistics; Pricing falls back to GigSim/Kaggle stats")

    async def list_events(self, user_id, after_id=0):
        return []  # Upwork changes surface through messages/offers; polled via list_messages

    async def create_proposal_draft(self, user_id, payload, idempotency_key, dry_run=False):
        if dry_run:
            return {"dry_run": True, "would_create_draft_on_upwork": payload,
                    "note": "Upwork drafts must be confirmed by you on upwork.com"}
        return await self._call(user_id, "create_proposal_draft", **payload)

    async def submit_proposal(self, user_id, draft_id, idempotency_key, dry_run=False):
        # Upwork requires the freelancer to confirm submission on the platform itself.
        return {"status": "awaiting_confirmation_on_upwork", "draft_id": draft_id,
                "note": "Open Upwork to confirm; GigBid never submits on your behalf in live mode."}

    async def send_message(self, user_id, thread_id, body, idempotency_key, dry_run=False):
        if dry_run:
            return {"dry_run": True, "would_draft_message": body, "thread_id": thread_id}
        return await self._call(user_id, "send_message", room_id=thread_id, message=body)

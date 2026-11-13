"""R1: the single marketplace interface.

The graph only ever talks to `MarketplacePort`. Which adapter backs it (GigSim or live Upwork)
is chosen by MARKETPLACE_MODE / config, so switching marketplaces changes no graph code.
Adapters live in mcp_servers/marketplace/adapters/.
"""
from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable


class MarketplaceUnavailable(RuntimeError):
    """Raised when the marketplace (or its MCP server) is down; the UI shows degraded mode."""


class RateLimited(RuntimeError):
    def __init__(self, retry_after: float):
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


@runtime_checkable
class MarketplacePort(Protocol):
    name: str

    # read
    async def search_jobs(self, query: str, filters: dict[str, Any] | None = None,
                          since: str | None = None, limit: int = 50) -> list[dict[str, Any]]: ...
    async def get_job(self, job_id: str) -> dict[str, Any]: ...
    async def get_client_history(self, client_id: str) -> dict[str, Any]: ...
    async def get_connects_balance(self, user_id: str) -> dict[str, Any]: ...
    async def list_messages(self, user_id: str, thread_id: str | None = None) -> list[dict[str, Any]]: ...
    async def market_stats(self, category: str) -> dict[str, Any]: ...
    async def list_events(self, user_id: str, after_id: int = 0) -> list[dict[str, Any]]: ...

    # write (always called only after human approval; all idempotent)
    async def create_proposal_draft(self, user_id: str, payload: dict[str, Any],
                                    idempotency_key: str, dry_run: bool = False) -> dict[str, Any]: ...
    async def submit_proposal(self, user_id: str, draft_id: str,
                              idempotency_key: str, dry_run: bool = False) -> dict[str, Any]: ...
    async def send_message(self, user_id: str, thread_id: str, body: str,
                           idempotency_key: str, dry_run: bool = False) -> dict[str, Any]: ...


def get_marketplace(mode: str | None = None) -> MarketplacePort:
    mode = mode or os.getenv("MARKETPLACE_MODE", "gigsim")
    if mode == "gigsim":
        from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
        return GigSimAdapter()
    if mode == "upwork_live":
        from mcp_servers.marketplace.adapters.upwork_live import UpworkLiveAdapter
        return UpworkLiveAdapter()
    raise ValueError(f"unknown MARKETPLACE_MODE {mode!r}")

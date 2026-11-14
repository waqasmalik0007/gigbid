"""Degraded mode (A6) and exactly-once (viva Q1/Q2): an approved proposal whose submit hits a dead
marketplace is parked as needs_attention, then resumed from the checkpoint before `submit` once the
marketplace answers, and the marketplace sees exactly one submission.

The graph here is a minimal stand-in with the real node wrapper, interrupt and file checkpointer, so the
recovery logic in worker/runner.py is exercised without LLM calls."""
from __future__ import annotations

import asyncio
from typing import Annotated

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from agent.mcp_client import MarketplaceDown
from agent.nodes.common import node


class S(TypedDict, total=False):
    thread_id: str
    status: str
    errors: Annotated[list, lambda a, b: (a or []) + (b or [])]
    approval: dict
    submission: dict


MARKET = {"up": False, "submits": {}}


def build(cp):
    def approve(state: S) -> dict:
        return {"approval": interrupt({"kind": "proposal"})}

    @node("submit")
    async def submit(state: S) -> dict:
        if not MARKET["up"]:
            raise MarketplaceDown("marketplace MCP server unreachable: connection refused")
        key = f"{state['thread_id']}:submit"  # idempotency key, as in agent/nodes/approval.py
        MARKET["submits"].setdefault(key, len(MARKET["submits"]) + 1)
        return {"submission": {"proposal_id": f"p{MARKET['submits'][key]}"}, "status": "submitted"}

    g = StateGraph(S)
    g.add_node("approve", approve)
    g.add_node("submit", submit)
    g.add_edge(START, "approve")
    g.add_edge("approve", "submit")
    g.add_edge("submit", END)
    return g.compile(checkpointer=cp)


@pytest.fixture()
def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGBID_APP_DB", str(tmp_path / "app.sqlite3"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import apps.api.db as db
    db._conn.cache_clear()
    from agent.memory.store import FileSaver
    import worker.runner as r
    r._graphs.clear()
    r._graphs["bid"] = build(FileSaver(tmp_path / "cp.pkl"))
    MARKET.update(up=False, submits={})
    yield r
    r._graphs.clear()
    db._conn.cache_clear()


def test_submit_during_outage_is_parked_then_submitted_exactly_once(runner):
    user = {"id": "u1", "persona_id": "data_analyst"}
    tid = "bid:u1:job_x"

    async def flow():
        out = await runner._execute("bid", tid, user, {"thread_id": tid}, "t1")
        assert out["status"] == "awaiting_approval"
        out = await runner.resume(user, tid, {"action": "approve"})   # marketplace is down
        assert out["status"] == "needs_attention"
        assert runner.degraded_threads("u1") == [tid]
        assert (await runner.recover(user, tid))["status"] == "needs_attention"   # still down: stays parked
        MARKET["up"] = True                                              # marketplace is back
        out = await runner.recover(user, tid)
        assert out["recovered"] and out["status"] == "submitted"
        again = await runner.recover(user, tid)                          # a second recovery is a no-op
        assert again["recovered"] is False
        return out

    asyncio.run(flow())
    assert len(MARKET["submits"]) == 1
    assert runner.degraded_threads("u1") == []

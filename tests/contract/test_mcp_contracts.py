"""A7 contract tests: in-memory MCP client against all three servers + tool-schema snapshots.

Snapshots live in tests/contract/snapshots/*.json. An intentional signature change is accepted with
    UPDATE_SNAPSHOTS=1 pytest tests/contract
and the diff is reviewed in the PR; an unintentional one fails CI.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

SNAP = Path(__file__).parent / "snapshots"


def _schema(tools) -> list[dict]:
    return sorted(({"name": t.name, "input": t.inputSchema, "output": t.outputSchema,
                    "annotations": t.annotations.model_dump(exclude_none=True) if t.annotations else None}
                   for t in tools), key=lambda d: d["name"])


async def _check_snapshot(server, name: str):
    async with connect(server) as s:
        tools = (await s.list_tools()).tools
    got = _schema(tools)
    f = SNAP / f"{name}.json"
    if os.getenv("UPDATE_SNAPSHOTS") or not f.exists():
        SNAP.mkdir(exist_ok=True)
        f.write_text(json.dumps(got, indent=2, sort_keys=True))
    assert got == json.loads(f.read_text()), f"{name} tool signatures changed; review and UPDATE_SNAPSHOTS=1"
    return tools


@pytest.mark.parametrize("module,name", [("mcp_servers.portfolio.server", "portfolio"),
                                         ("mcp_servers.marketplace.server", "marketplace"),
                                         ("mcp_servers.workspace.server", "workspace")])
async def test_schema_snapshot(module, name):
    import importlib
    await _check_snapshot(importlib.import_module(module).mcp, name)


async def test_annotations_are_correct():
    from mcp_servers.marketplace.server import mcp
    async with connect(mcp) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
    for n in ("search_jobs", "get_job", "get_client_history", "get_connects_balance"):
        assert tools[n].annotations.readOnlyHint is True
    assert tools["submit_proposal"].annotations.destructiveHint is True
    for n in ("create_proposal_draft", "submit_proposal", "send_message"):
        assert tools[n].annotations.readOnlyHint is False and tools[n].annotations.idempotentHint is True
        assert "idempotency_key" in tools[n].inputSchema["required"]

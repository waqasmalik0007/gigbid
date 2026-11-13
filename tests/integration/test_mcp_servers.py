"""MCP servers end to end through an in-memory MCP client (no network), GigSim in-process."""
from __future__ import annotations

import importlib

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect


@pytest.fixture()
def market(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGSIM_DB", str(tmp_path / "sim.sqlite3"))
    monkeypatch.setenv("GIGSIM_ERROR_RATE", "0")
    monkeypatch.setenv("GIGSIM_RATE_LIMIT_PER_MIN", "1000")
    monkeypatch.setenv("GIGBID_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    import gigsim.app as sim
    sim = importlib.reload(sim)
    sim.world.sim_offset = 10 * 24 * 3600
    import mcp_servers.common.audit as audit
    importlib.reload(audit)
    import mcp_servers.marketplace.server as srv
    srv = importlib.reload(srv)
    from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
    srv._port = GigSimAdapter(base_url="http://sim", transport=httpx.ASGITransport(app=sim.app))
    return srv, tmp_path


async def test_marketplace_read_and_idempotent_write(market):
    srv, tmp = market
    async with connect(srv.mcp) as s:
        jobs = (await s.call_tool("search_jobs", {"limit": 3})).structuredContent["jobs"]
        assert len(jobs) == 3
        draft = {"job_id": jobs[0]["job_id"], "cover_letter": "I built a RAG assistant that answered 300 questions.",
                 "amount": 400, "budget_type": "fixed"}
        preview = (await s.call_tool("create_proposal_draft", {"draft": draft, "idempotency_key": "k-draft-1",
                                                                "dry_run": True})).structuredContent
        assert preview["dry_run"] and preview["connects_cost"] > 0
        a = (await s.call_tool("create_proposal_draft", {"draft": draft, "idempotency_key": "k-draft-1"})).structuredContent
        b = (await s.call_tool("create_proposal_draft", {"draft": draft, "idempotency_key": "k-draft-1"})).structuredContent
        assert a["proposal_id"] == b["proposal_id"] and b["idempotent_replay"]
        bal0 = (await s.call_tool("get_connects_balance", {})).structuredContent["balance"]
        for _ in range(3):
            await s.call_tool("submit_proposal", {"proposal_id": a["proposal_id"], "idempotency_key": "k-submit-1"})
        bal1 = (await s.call_tool("get_connects_balance", {})).structuredContent["balance"]
        assert bal0 - bal1 == preview["connects_cost"]
    log = (tmp / "audit.jsonl").read_text()
    assert "submit_proposal" in log and "k-submit-1" in log


async def test_marketplace_down_is_reported(market, monkeypatch):
    srv, _ = market
    from mcp_servers.marketplace.adapters.gigsim import GigSimAdapter
    srv._port = GigSimAdapter(base_url="http://127.0.0.1:9", max_retries=1)  # nothing listens here
    async with connect(srv.mcp) as s:
        r = await s.call_tool("search_jobs", {"limit": 1})
    assert r.isError and "MARKETPLACE_UNAVAILABLE" in r.content[0].text


async def test_portfolio_search_returns_evidence():
    from mcp_servers.portfolio.server import mcp
    async with connect(mcp) as s:
        r = (await s.call_tool("search_evidence", {"requirement": "retrieval augmented generation over documents",
                                                    "k": 5, "persona": "ai_ml_engineer"})).structuredContent
        assert len(r["items"]) == 5 and all("evidence_id" in i and "source" in i for i in r["items"])
        cs = (await s.call_tool("get_case_study", {"evidence_id": r["items"][0]["evidence_id"],
                                                   "persona": "ai_ml_engineer"})).structuredContent
        assert cs["text"]
        filt = (await s.call_tool("search_evidence", {"requirement": "proposal", "k": 3, "persona": "ai_ml_engineer",
                                                       "filters": {"type": "past_proposal"}})).structuredContent
        assert all(i["type"] == "past_proposal" for i in filt["items"])
        prompts = (await s.list_prompts()).prompts
        assert any(p.name == "answer_screening_question" for p in prompts)
        templates = (await s.list_resource_templates()).resourceTemplates
        assert {t.uriTemplate for t in templates} >= {"portfolio://case/{evidence_id}", "portfolio://proposal/{evidence_id}"}


async def test_workspace_holds_are_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DB", str(tmp_path / "ws.sqlite3"))
    import mcp_servers.workspace.server as ws
    ws = importlib.reload(ws)
    async with connect(ws.mcp) as s:
        slots = (await s.call_tool("free_slots", {"user_id": "u1", "start_from": "2026-11-16T08:00:00"})).structuredContent
        first = slots["result"][0]
        args = {"user_id": "u1", "start": first["start"], "end": first["end"], "title": "Interview",
                "idempotency_key": "hold-0001"}
        h1 = (await s.call_tool("create_interview_hold", args)).structuredContent
        h2 = (await s.call_tool("create_interview_hold", args)).structuredContent
        assert h1["event_id"] == h2["event_id"] and h2["idempotent_replay"]
        again = (await s.call_tool("free_slots", {"user_id": "u1", "start_from": "2026-11-16T08:00:00"})).structuredContent
        assert first["start"] not in [x["start"] for x in again["result"]]

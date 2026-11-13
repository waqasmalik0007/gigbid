"""MCP client used by every agent (A6 client discipline).

* Per-agent tool allowlists (ALLOW): an agent can only call tools it needs, by name.
* Timeouts and bounded retries with backoff; MARKETPLACE_UNAVAILABLE surfaces as MarketplaceDown so
  the graph parks the job and the UI shows degraded mode.
* Transport via MCP_TRANSPORT:
    inproc -> in-memory MCP sessions to the server objects (still the full MCP protocol)
    http   -> Streamable HTTP to MCP_<SERVER>_URL with bearer token, x-trace-id and x-gigbid-user
* get_langchain_tools(agent) returns allowlisted tools as LangChain tools (ReAct / supervisor baselines).
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
from datetime import timedelta
from typing import Any

from mcp_servers.common.audit import current_trace, current_user

ALLOW: dict[str, dict[str, set[str]]] = {
    "scout": {"marketplace": {"search_jobs", "get_job"}},
    "job_analyst": {},
    "risk_assessor": {"marketplace": {"get_client_history", "get_job"}},
    "fit_matcher": {"portfolio": {"search_evidence", "get_case_study", "list_skills"}},
    "pricing": {"marketplace": {"market_stats", "get_client_history", "get_connects_balance"}},
    "writer": {"portfolio": {"search_evidence", "get_case_study"}},
    "critic": {"portfolio": {"search_evidence", "get_case_study"}},
    "compliance": {},
    "submitter": {"marketplace": {"create_proposal_draft", "submit_proposal"}},  # only after approval
    "liaison": {"marketplace": {"list_messages", "send_message"},
                "workspace": {"free_slots", "create_interview_hold", "create_email_draft"}},
    "react_baseline": {"marketplace": {"get_job", "get_client_history", "market_stats"},
                       "portfolio": {"search_evidence", "get_case_study"}},
}
MODULES = {"portfolio": "mcp_servers.portfolio.server", "marketplace": "mcp_servers.marketplace.server",
           "workspace": "mcp_servers.workspace.server"}


class ToolNotAllowed(PermissionError):
    pass


class MarketplaceDown(RuntimeError):
    pass


class ToolError(RuntimeError):
    pass


def _transport() -> str:
    return os.getenv("MCP_TRANSPORT", "inproc")


async def _call_inproc(server: str, tool: str, args: dict, timeout: float):
    from mcp.shared.memory import create_connected_server_and_client_session as connect
    mcp = importlib.import_module(MODULES[server]).mcp
    async with connect(mcp, read_timeout_seconds=timedelta(seconds=timeout)) as s:
        return await s.call_tool(tool, args)


async def _call_http(server: str, tool: str, args: dict, timeout: float):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    url = os.environ[f"MCP_{server.upper()}_URL"]
    headers = {"Authorization": f"Bearer {os.getenv('MCP_BEARER_TOKEN', '')}",
               "x-trace-id": current_trace.get(), "x-gigbid-user": current_user.get()}
    async with streamablehttp_client(url, headers=headers, timeout=timeout) as (r, w, _):
        async with ClientSession(r, w, read_timeout_seconds=timedelta(seconds=timeout)) as s:
            await s.initialize()
            return await s.call_tool(tool, args)


def _decode(res) -> Any:
    if res.structuredContent is not None:
        sc = res.structuredContent
        return sc["result"] if set(sc) == {"result"} else sc
    texts = [c.text for c in res.content if getattr(c, "type", "") == "text"]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return texts[0]
    return [json.loads(t) if t.startswith(("{", "[")) else t for t in texts]


async def call(agent: str, server: str, tool: str, args: dict | None = None, timeout: float = 30,
               retries: int = 2) -> Any:
    if tool not in ALLOW.get(agent, {}).get(server, set()):
        raise ToolNotAllowed(f"agent {agent!r} may not call {server}.{tool}")
    args = {k: v for k, v in (args or {}).items() if v is not None}
    delay, last = 0.5, None
    for attempt in range(retries + 1):
        try:
            fn = _call_inproc if _transport() == "inproc" else _call_http
            res = await asyncio.wait_for(fn(server, tool, args, timeout), timeout + 5)
        except (asyncio.TimeoutError, OSError, ConnectionError) as e:
            last = e
            if server == "marketplace" and attempt == retries:
                raise MarketplaceDown(f"marketplace MCP server unreachable: {e}") from e
        except Exception as e:  # noqa: BLE001 - e.g. httpx errors when the server process is killed
            last = e
            if "connect" in str(e).lower() or "unreachable" in str(e).lower():
                if server == "marketplace" and attempt == retries:
                    raise MarketplaceDown(f"marketplace MCP server unreachable: {e}") from e
            else:
                raise
        else:
            if res.isError:
                msg = " ".join(getattr(c, "text", "") for c in res.content)
                if "MARKETPLACE_UNAVAILABLE" in msg:
                    last = MarketplaceDown(msg)
                    if attempt == retries:
                        raise last
                else:
                    raise ToolError(f"{server}.{tool}: {msg[:300]}")
            else:
                return _decode(res)
        await asyncio.sleep(delay)
        delay *= 2
    raise ToolError(f"{server}.{tool} failed: {last}")


async def get_langchain_tools(agent: str) -> list:
    """Allowlisted tools as LangChain tools (used by the single-agent and supervisor baselines)."""
    from langchain_mcp_adapters.tools import load_mcp_tools
    from mcp.shared.memory import create_connected_server_and_client_session as connect
    from langchain_core.tools import StructuredTool

    out = []
    for server, names in ALLOW.get(agent, {}).items():
        mcp = importlib.import_module(MODULES[server]).mcp
        async with connect(mcp) as s:
            listed = await load_mcp_tools(s)
        for t in listed:
            if t.name not in names:
                continue

            async def _run(_server=server, _tool=t.name, **kw):
                r = await call(agent, _server, _tool, kw)
                return json.dumps(r, default=str)[:6000]

            out.append(StructuredTool.from_function(coroutine=_run, name=t.name, description=t.description,
                                                    args_schema=t.args_schema))
    return out

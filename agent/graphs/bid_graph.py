"""Bid graph: one thread per job (C1).

intake -> [Send] job_analyst | risk_assessor | fit_matcher (parallel) -> decide
       -> pricing -> writer -> verifier -> critic -(<=2 loops)-> writer
       -> compliance -> preview (dry_run) -> approval (interrupt) -> submit
Any node can stop the thread in skipped / blocked / needs_attention / dead_letter.

    python -m agent.graphs.bid_graph --demo [--persona ai_ml_engineer] [--job <job_id>]
    python -m agent.graphs.bid_graph --mermaid      # render the graph from code
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agent.mcp_client import call
from agent.nodes.analyst import job_analyst
from agent.nodes.approval import after_approval, after_compliance, approval, preview, submit
from agent.nodes.common import halted, node
from agent.nodes.compliance import compliance
from agent.nodes.fit_matcher import fit_matcher
from agent.nodes.pricing import decide, pricing
from agent.nodes.risk_assessor import risk_assessor
from agent.nodes.writer import after_critic, critic, verifier, writer
from agent.state import BidState


@node("intake")
async def intake(state: dict) -> dict:
    job = state.get("job") or await call("scout", "marketplace", "get_job",
                                         {"job_id": state["job_id"], "user_id": state["persona_id"]})
    return {"job": job, "job_id": job["job_id"], "status": "running", "revisions": 0}


def fan_out(state: dict):
    if halted(state):
        return END
    return [Send("job_analyst", state), Send("risk_assessor", state), Send("fit_matcher", state)]


def route(next_node: str):
    def _r(state: dict) -> str:
        return END if halted(state) else next_node
    _r.__name__ = f"to_{next_node}"
    return _r


def _end_if(fn):
    def _r(state: dict) -> str:
        nxt = fn(state)
        return END if nxt == "end" else nxt
    _r.__name__ = fn.__name__
    return _r


def build(checkpointer=None, store=None):
    g = StateGraph(BidState)
    for name, fn in [("intake", intake), ("job_analyst", job_analyst), ("risk_assessor", risk_assessor),
                     ("fit_matcher", fit_matcher), ("decide", decide), ("pricing", pricing), ("writer", writer),
                     ("verifier", verifier), ("critic", critic), ("compliance", compliance), ("preview", preview),
                     ("approval", approval), ("submit", submit)]:
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", fan_out, ["job_analyst", "risk_assessor", "fit_matcher", END])
    for n in ("job_analyst", "risk_assessor", "fit_matcher"):
        g.add_edge(n, "decide")
    g.add_conditional_edges("decide", route("pricing"), ["pricing", END])
    g.add_conditional_edges("pricing", route("writer"), ["writer", END])
    g.add_conditional_edges("writer", route("verifier"), ["verifier", END])
    g.add_conditional_edges("verifier", route("critic"), ["critic", END])
    g.add_conditional_edges("critic", _end_if(after_critic), ["writer", "compliance", END])
    g.add_conditional_edges("compliance", _end_if(after_compliance), ["preview", "approval", "submit", END])
    g.add_conditional_edges("preview", route("approval"), ["approval", END])
    g.add_conditional_edges("approval", _end_if(after_approval), ["submit", "compliance", END])
    g.add_edge("submit", END)
    return g.compile(checkpointer=checkpointer, store=store)


def thread_config(thread_id: str, trace_id: str | None = None, user: str | None = None) -> dict:
    return {"configurable": {"thread_id": thread_id},
            "metadata": {"trace_id": trace_id or uuid.uuid4().hex, "user": user},
            "run_name": "bid_graph", "recursion_limit": 60}


async def _demo(persona: str, job_id: str | None) -> None:
    from agent.memory.store import get_checkpointer, get_store
    from langgraph.types import Command
    app = build(get_checkpointer(), get_store())
    if not job_id:
        jobs = await call("scout", "marketplace", "search_jobs", {"limit": 30, "user_id": persona})
        job_id = jobs["jobs"][0]["job_id"]
    tid = f"{persona}:{job_id}"
    cfg = thread_config(tid, user=persona)
    async for upd in app.astream({"job_id": job_id, "persona_id": persona, "user_id": persona, "thread_id": tid},
                                 cfg, stream_mode="updates"):
        for k, v in upd.items():
            print(f"-- {k}: {str(v)[:300]}")
    st = app.get_state(cfg)
    if st.next:
        print("\nPAUSED for approval. Approving in demo mode...")
        async for upd in app.astream(Command(resume={"action": "approve", "user_id": persona}), cfg,
                                     stream_mode="updates"):
            print(f"-- {list(upd)}: {str(upd)[:300]}")
    print(json.dumps({k: str(v)[:200] for k, v in app.get_state(cfg).values.items()
                      if k in ("status", "decision", "submission", "verification", "usage")}, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--mermaid", action="store_true")
    ap.add_argument("--persona", default="ai_ml_engineer")
    ap.add_argument("--job")
    a = ap.parse_args()
    if a.mermaid:
        print(build().get_graph().draw_mermaid())
    else:
        asyncio.run(_demo(a.persona, a.job))

"""Conversation graph: one thread per client conversation (C1).

read_thread -> classify_intent -> find_slots -> draft_reply -> approve_reply (interrupt) -> send_reply

    python -m agent.graphs.conversation_graph --mermaid
"""
from __future__ import annotations

import argparse

from langgraph.graph import END, START, StateGraph

from agent.nodes.client_liaison import (approve_reply, classify_intent, draft_reply, find_slots, read_thread,
                                        send_reply)
from agent.nodes.common import halted
from agent.state import ConversationState


def _next(n: str):
    def r(state: dict) -> str:
        return END if halted(state) else n
    r.__name__ = f"to_{n}"
    return r


def after_approval(state: dict) -> str:
    return "send_reply" if state.get("status") == "approved" else END


def build(checkpointer=None, store=None):
    g = StateGraph(ConversationState)
    for name, fn in [("read_thread", read_thread), ("classify_intent", classify_intent), ("find_slots", find_slots),
                     ("draft_reply", draft_reply), ("approve_reply", approve_reply), ("send_reply", send_reply)]:
        g.add_node(name, fn)
    g.add_edge(START, "read_thread")
    g.add_conditional_edges("read_thread", _next("classify_intent"), ["classify_intent", END])
    g.add_conditional_edges("classify_intent", _next("find_slots"), ["find_slots", END])
    g.add_conditional_edges("find_slots", _next("draft_reply"), ["draft_reply", END])
    g.add_conditional_edges("draft_reply", _next("approve_reply"), ["approve_reply", END])
    g.add_conditional_edges("approve_reply", after_approval, ["send_reply", END])
    g.add_edge("send_reply", END)
    return g.compile(checkpointer=checkpointer, store=store)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mermaid", action="store_true")
    if ap.parse_args().mermaid:
        print(build().get_graph().draw_mermaid())

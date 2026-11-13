"""Replay any job from any node using LangGraph checkpoint history (C6).

    python tools/replay.py list   bid:<user>:<job_id>
    python tools/replay.py show   bid:<user>:<job_id> <checkpoint_id>
    python tools/replay.py replay bid:<user>:<job_id> <checkpoint_id>      # re-run from that point (fork)

`list` prints every checkpoint with the node that is about to run, so you can find e.g. the state just
before `decide` for a wrongly skipped job, inspect it with `show`, change config/prompts, and `replay`.
A replay forks the thread from that checkpoint; it never re-submits, because writes use idempotency keys
and the graph still stops at the approval interrupt.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def graph_for(thread: str):
    from agent.memory.store import get_checkpointer, get_store
    if thread.startswith("conv:"):
        from agent.graphs.conversation_graph import build
    else:
        from agent.graphs.bid_graph import build
    return build(get_checkpointer(), get_store())


def main() -> None:
    cmd, thread = sys.argv[1], sys.argv[2]
    app = graph_for(thread)
    cfg = {"configurable": {"thread_id": thread}}
    if cmd == "list":
        for snap in app.get_state_history(cfg):
            v = snap.values
            print(f"{snap.config['configurable']['checkpoint_id']}  next={list(snap.next)}  "
                  f"status={v.get('status')}  decision={getattr(v.get('decision'), 'action', None)}  "
                  f"steps={v.get('steps')}")
    elif cmd == "show":
        snap = app.get_state({"configurable": {"thread_id": thread, "checkpoint_id": sys.argv[3]}})
        print(json.dumps({k: (v.model_dump() if hasattr(v, "model_dump") else v) for k, v in snap.values.items()
                          if k not in ("trace",)}, indent=1, default=str)[:8000])
    elif cmd == "replay":
        ck = {"configurable": {"thread_id": thread, "checkpoint_id": sys.argv[3]}}

        async def run():
            async for upd in app.astream(None, ck, stream_mode="updates"):
                for node, val in upd.items():
                    print(f"-- {node}: {str(val)[:300]}")
        asyncio.run(run())
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()

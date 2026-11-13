"""Part A deliverable: a single agent drafting one grounded proposal for a GigSim job via MCP tools.

    python -m evals.runners.single_agent_proposal [--persona ai_ml_engineer] [--job job_...]

One ReAct agent with the allowlisted Marketplace + Portfolio MCP tools reads the job, searches the
portfolio, and writes a proposal citing evidence ids. The claim verifier then reports which claims the
portfolio supports. Output: evals/results/single_agent_proposal.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


async def main() -> None:
    from evals.runners.multiagent_compare import run_react
    from mcp_servers.common.audit import current_user
    from rag.retriever import get_pipeline
    from rag.verify import split_claims, verify_claims
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", default="ai_ml_engineer")
    ap.add_argument("--job", default=None)
    a = ap.parse_args()
    rows = [json.loads(x) for x in (ROOT / f"evals/datasets/dev_{a.persona}.jsonl").open(encoding="utf-8")]
    row = next((r for r in rows if r["job"]["job_id"] == a.job), None) or \
        next(r for r in rows if r["gold"]["action"] == "bid" and (r["job"].get("budget_max") or 0) > 500)
    current_user.set(a.persona)
    res = await run_react(a.persona, row["job"])
    letter = re.split(r"DECISION:", res["text"], flags=re.I)[0]
    claims = verify_claims(split_claims(re.sub(r"\[[a-z]+_[\w-]+\]", "", letter)), get_pipeline(a.persona, "v2_hybrid"))
    out = {"job_id": row["job"]["job_id"], "title": row["job"]["title"], "persona": a.persona, "output": res["text"],
           "tokens": res["tokens"], "cited_ids": sorted(set(re.findall(r"\[([a-z]+_[\w-]+)\]", letter))),
           "claims": [c.model_dump() for c in claims]}
    (ROOT / "evals/results/single_agent_proposal.json").write_text(json.dumps(out, indent=2))
    print(res["text"])
    print({c.status: sum(x.status == c.status for x in claims) for c in claims})


if __name__ == "__main__":
    asyncio.run(main())

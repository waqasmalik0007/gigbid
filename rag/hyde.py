"""HyDE (V7): retrieve with a hypothetical ideal proposal for the job. Cached per job id."""
from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from agent.prompts.loader import fill, untrusted

CACHE = Path(__file__).resolve().parents[1] / "data/processed/hyde_cache.json"


def hypothetical_proposal(job: dict) -> tuple[str, dict]:
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    jid = job.get("job_id", "")
    if jid in cache:
        return cache[jid], {}
    from agent.llm import text
    out, usage = text("scout", [
        SystemMessage(fill("hyde")),
        HumanMessage(untrusted("untrusted_job_post", f"{job.get('title')}\n{job.get('description', '')[:1500]}")),
    ], temperature=0.3, max_tokens=400)
    cache[jid] = out
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache))
    return out, dict(usage)

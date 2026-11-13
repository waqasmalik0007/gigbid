"""B4 proposal quality + outcome: full bid graph on gold-'bid' dev jobs, auto-approved (eval only).

    python -m evals.runners.proposal_eval --per-persona 4 --variant best --tag best
    python -m evals.runners.proposal_eval --per-persona 4 --variant v0_naive --tag v0

Per proposal: unsupported-claim rate before/after verification (B3), critic rounds, words,
compliance redactions, LLM-judge rubric (1-5, calibrated by evals/runners/judge_calibration.py),
GigSim reply probability from the simulated client (hidden preferences; expected reply rate is the
mean of these probabilities: reproducible and lower-variance than one random draw per proposal),
latency, tokens and USD per proposal. Proposals are exported for human labelling (judge calibration)
and the blind pairwise panel. Writes evals/results/proposals_<tag>.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
PERSONAS = ["ai_ml_engineer", "fullstack_web", "data_analyst"]


class JudgeScore(BaseModel):
    relevance: int = Field(ge=1, le=5)
    specificity: int = Field(ge=1, le=5)
    credibility: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    overall: int = Field(ge=1, le=5)


def pick(persona: str, n: int, seed: int = 5) -> list[dict]:
    rows = [json.loads(line) for line in (ROOT / f"evals/datasets/dev_{persona}.jsonl").open(encoding="utf-8")]
    cand = [r for r in rows[120:] if r["gold"]["action"] == "bid" and not r["gold"]["has_injection"]
            and (r["job"].get("budget_max") or 0) >= 300]
    random.Random(seed).shuffle(cand)
    return cand[:n]


async def judge(job: dict, letter: str) -> tuple[JudgeScore | None, dict]:
    from agent.llm import astructured
    from agent.prompts.loader import fill, untrusted
    try:
        return await astructured("scout", JudgeScore, [
            SystemMessage(fill("judge")),
            HumanMessage(untrusted("untrusted_job_post", f"{job['title']}\n{job['description'][:1500]}")
                         + "\n\nPROPOSAL:\n" + letter)], temperature=0.0, max_tokens=400)
    except Exception:  # noqa: BLE001
        return None, {}


async def run_one(app, persona: str, row: dict, tag: str) -> dict:
    from langgraph.types import Command

    from gigsim.client_sim import score_proposal
    from mcp_servers.common.audit import current_user
    job = row["job"]
    tid = f"peval-{tag}:{persona}:{job['job_id']}"
    cfg = {"configurable": {"thread_id": tid}, "recursion_limit": 60}
    tok = current_user.set(persona)
    t0 = time.perf_counter()
    try:
        await app.ainvoke({"job_id": job["job_id"], "job": job, "persona_id": persona, "user_id": f"eval-{persona}",
                           "thread_id": tid, "settings": {}}, cfg)
        st = app.get_state(cfg)
        if st.next:  # reached approval: auto-approve in evaluation only
            await app.ainvoke(Command(resume={"action": "approve", "user_id": "eval"}), cfg)
    except Exception as e:  # noqa: BLE001
        return {"job_id": job["job_id"], "persona": persona, "error": str(e)[:300]}
    finally:
        current_user.reset(tok)
    v = app.get_state(cfg).values
    lat = time.perf_counter() - t0
    draft, comp, price = v.get("draft"), v.get("compliance"), v.get("price")
    out = {"job_id": job["job_id"], "persona": persona, "status": v.get("status"), "latency_s": lat,
           "tokens": v.get("usage", {}).get("tokens_in", 0) + v.get("usage", {}).get("tokens_out", 0),
           "usd": v.get("usage", {}).get("usd", 0.0), "decision": v.get("decision").action if v.get("decision") else None}
    if not draft or not comp:
        return out | {"no_proposal": True, "errors": v.get("errors")}
    letter = comp.cleaned_text
    clients = {c["client_id"]: c for c in json.loads((ROOT / "data/gigsim/clients.json").read_text())}
    sim = score_proposal(job, clients[job["client_id"]], {"cover_letter": letter, "amount": price.amount if price else 0})
    j, ju = await judge(job, letter)
    ver = v.get("verification") or {}
    return out | {"letter": letter, "words": len(letter.split()), "claims": ver.get("claims", 0),
                  "unsupported_before": ver.get("unsupported_before"), "unsupported_after": ver.get("unsupported_after"),
                  "critic_rounds": len(v.get("critiques") or []), "critic_passed": bool(v["critiques"][-1].passed)
                  if v.get("critiques") else None, "redactions": len(comp.redactions), "compliance_allowed": comp.allowed,
                  "p_reply": sim["p_reply"], "generic_opening": sim["generic"],
                  "judge": j.model_dump() if j else None, "judge_usd": ju.get("usd", 0.0),
                  "price": price.amount if price else None, "fair_band": row["gold"]["fair_band"]}


def summarise(rows: list[dict]) -> dict:
    ok = [r for r in rows if r.get("letter")]
    if not ok:
        return {"n": 0, "attempted": len(rows)}
    lat = sorted(r["latency_s"] for r in ok)
    q = lambda xs, p: round(xs[min(len(xs) - 1, int(p * len(xs)))], 2)  # noqa: E731
    js = [r["judge"]["overall"] for r in ok if r.get("judge")]
    return {"n": len(ok), "attempted": len(rows),
            "unsupported_before": round(statistics.mean(r["unsupported_before"] or 0 for r in ok), 3),
            "unsupported_after": round(statistics.mean(r["unsupported_after"] or 0 for r in ok), 3),
            "unsupported_claims_per_proposal_before": round(statistics.mean((r["unsupported_before"] or 0) * r["claims"] for r in ok), 2),
            "critic_rounds_mean": round(statistics.mean(r["critic_rounds"] for r in ok), 2),
            "judge_overall_mean": round(statistics.mean(js), 2) if js else None,
            "expected_reply_rate": round(statistics.mean(r["p_reply"] for r in ok), 3),
            "generic_opening_rate": round(sum(r["generic_opening"] for r in ok) / len(ok), 3),
            "words_mean": round(statistics.mean(r["words"] for r in ok)),
            "latency_s_per_proposal": {"p50": q(lat, 0.5), "p95": q(lat, 0.95)},
            "tokens_per_proposal": round(statistics.mean(r["tokens"] for r in ok)),
            "usd_per_proposal": round(statistics.mean(r["usd"] for r in ok), 5)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-persona", type=int, default=4)
    ap.add_argument("--variant", default="best")
    ap.add_argument("--profile", default="open")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--concurrency", type=int, default=2)
    a = ap.parse_args()
    tag = a.tag or f"{a.variant}_{a.profile}"
    os.environ["GIGBID_PROFILE"] = a.profile
    os.environ["GIGBID_RAG_VARIANT"] = a.variant
    from langgraph.checkpoint.memory import InMemorySaver

    from agent.graphs.bid_graph import build
    from agent.memory.store import serde
    app = build(InMemorySaver(serde=serde()))
    work = [(p, r) for p in PERSONAS for r in pick(p, a.per_persona)]
    sem, rows = asyncio.Semaphore(a.concurrency), []

    async def one(p, r):
        async with sem:
            res = await run_one(app, p, r, tag)
            rows.append(res)
            print(f"[{len(rows)}/{len(work)}] {p} {r['job']['job_id']} status={res.get('status')} "
                  f"unsupported {res.get('unsupported_before')}->{res.get('unsupported_after')} "
                  f"p_reply={res.get('p_reply')} judge={(res.get('judge') or {}).get('overall')} {res.get('error', '')}",
                  flush=True)

    await asyncio.gather(*(one(p, r) for p, r in work))
    out = {"meta": {"variant": a.variant, "profile": a.profile, "per_persona": a.per_persona,
                    "ts": time.strftime("%Y-%m-%d %H:%M")},
           "overall": summarise(rows), "by_persona": {p: summarise([r for r in rows if r["persona"] == p]) for p in PERSONAS},
           "rows": rows}
    (ROOT / f"evals/results/proposals_{tag}.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out["overall"], indent=1))


if __name__ == "__main__":
    asyncio.run(main())

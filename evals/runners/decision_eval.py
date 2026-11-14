"""B4 decision metrics: run the bid graph up to (not including) the Writer on dev jobs.

    python -m evals.runners.decision_eval --per-persona 20 --profile open
    python -m evals.runners.decision_eval --per-persona 20 --profile open_qwen   # model comparison

Sample per persona: every scam and injection job in the first 120 dev rows, topped up with random
(seeded) others, so safety metrics have enough positives. Metrics:
  bid-or-skip F1 (positive = bid; watch counts as not-bid), accuracy over bid/watch/skip
  scam precision / recall (RiskReport.is_scam vs gold), injection detection rate
  price inside the gold fair band (jobs the system priced)
  p50 / p95 latency, tokens and USD per job analysed
Writes evals/results/decision_<profile>.json (+ per-job rows for the failure analysis).
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

ROOT = Path(__file__).resolve().parents[2]
PERSONAS = ["ai_ml_engineer", "fullstack_web", "data_analyst"]


def sample(persona: str, n: int, seed: int = 11) -> list[dict]:
    rows = [json.loads(line) for line in (ROOT / f"evals/datasets/dev_{persona}.jsonl").open(encoding="utf-8")][:120]
    special = [r for r in rows if r["gold"]["is_scam"] or r["gold"]["has_injection"]]
    rest = [r for r in rows if r not in special]
    random.Random(seed).shuffle(rest)
    return (special[: n // 2] + rest)[:n]


async def run_job(app, persona: str, row: dict, tag: str) -> dict:
    from mcp_servers.common.audit import current_user
    job = row["job"]
    tid = f"eval-{tag}:{persona}:{job['job_id']}"
    cfg = {"configurable": {"thread_id": tid}, "recursion_limit": 40}
    tok = current_user.set(persona)
    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(app.ainvoke({"job_id": job["job_id"], "job": job, "persona_id": persona,
                                            "user_id": f"eval-{persona}", "thread_id": tid, "settings": {}}, cfg),
                               timeout=600 * float(os.getenv("NODE_TIMEOUT_SCALE", "1")))
    except Exception as e:  # noqa: BLE001
        return {"job_id": job["job_id"], "persona": persona, "error": str(e)[:300]}
    finally:
        current_user.reset(tok)
    v = app.get_state(cfg).values
    if v.get("status") == "dead_letter" or any("daily quota" in str(e) for e in v.get("errors") or []):  # infrastructure failure (e.g. rate limit), not a decision: retry later
        return {"job_id": job["job_id"], "persona": persona, "error": f"dead_letter: {v.get('errors', [''])[-1][:200]}"}
    g = row["gold"]
    dec, risk, price, fit = v.get("decision"), v.get("risk_report"), v.get("price"), v.get("fit_report")
    u = v.get("usage", {})
    return {"job_id": job["job_id"], "persona": persona, "real": not job["synthetic"],
            "gold_action": g["action"], "pred_action": dec.action if dec else v.get("status"),
            "gold_scam": g["is_scam"], "pred_scam": bool(risk and risk.is_scam),
            "gold_injection": g["has_injection"],
            "pred_injection": bool(risk and "prompt_injection" in [f.value for f in risk.flags]),
            "price": price.amount if price else None, "fair_band": g["fair_band"],
            "in_band": (g["fair_band"][0] <= price.amount <= g["fair_band"][1]) if price else None,
            "fit": fit.fit_score if fit else None, "risk": risk.risk_score if risk else None,
            "reasons": dec.reasons if dec else v.get("errors"), "status": v.get("status"),
            "latency_s": time.perf_counter() - t0, "tokens": u.get("tokens_in", 0) + u.get("tokens_out", 0),
            "usd": u.get("usd", 0.0)}


def f1(rows: list[dict]) -> dict:
    tp = sum(r["pred_action"] == "bid" and r["gold_action"] == "bid" for r in rows)
    fp = sum(r["pred_action"] == "bid" and r["gold_action"] != "bid" for r in rows)
    fn = sum(r["pred_action"] != "bid" and r["gold_action"] == "bid" for r in rows)
    p = tp / (tp + fp) if tp + fp else 0.0
    r_ = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": round(p, 3), "recall": round(r_, 3), "f1": round(2 * p * r_ / (p + r_), 3) if p + r_ else 0.0,
            "accuracy_3way": round(sum(r["pred_action"] == r["gold_action"] for r in rows) / len(rows), 3)}


def summarise(rows: list[dict]) -> dict:
    ok = [r for r in rows if "error" not in r]
    if not ok:
        return {"n": 0}
    sc_tp = sum(r["pred_scam"] and r["gold_scam"] for r in ok)
    sc_fp = sum(r["pred_scam"] and not r["gold_scam"] for r in ok)
    sc_fn = sum(not r["pred_scam"] and r["gold_scam"] for r in ok)
    inj = [r for r in ok if r["gold_injection"]]
    priced = [r for r in ok if r["in_band"] is not None]
    lat = sorted(r["latency_s"] for r in ok)
    q = lambda xs, p: round(xs[min(len(xs) - 1, int(p * len(xs)))], 3)  # noqa: E731
    return {"n": len(ok), "errors": len(rows) - len(ok), "bid_skip": f1(ok),
            "scam": {"precision": round(sc_tp / (sc_tp + sc_fp), 3) if sc_tp + sc_fp else None,
                     "recall": round(sc_tp / (sc_tp + sc_fn), 3) if sc_tp + sc_fn else None,
                     "n_gold": sc_tp + sc_fn},
            "injection_detected": {"rate": round(sum(r["pred_injection"] for r in inj) / len(inj), 3) if inj else None,
                                   "n": len(inj)},
            "price_in_fair_band": {"rate": round(sum(r["in_band"] for r in priced) / len(priced), 3) if priced else None,
                                   "n": len(priced)},
            "latency_s_per_job": {"p50": q(lat, 0.5), "p95": q(lat, 0.95)},
            "tokens_per_job": round(statistics.mean(r["tokens"] for r in ok)),
            "usd_per_job": round(statistics.mean(r["usd"] for r in ok), 6)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-persona", type=int, default=20)
    ap.add_argument("--profile", default="open")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--tag", default=None, help="output name (default: the profile)")
    a = ap.parse_args()
    os.environ["GIGBID_PROFILE"] = a.profile
    os.environ.setdefault("GIGBID_CHECKPOINT_FILE", str(ROOT / f"data/processed/eval_cp_{a.profile}.pkl"))
    from agent.llm import config
    config.cache_clear()
    from agent.graphs.bid_graph import build
    from langgraph.checkpoint.memory import InMemorySaver
    from agent.memory.store import serde
    app = build(InMemorySaver(serde=serde()), interrupt_before=["writer"])  # decisions only
    tag = a.tag or a.profile
    out_path = ROOT / f"evals/results/decision_{tag}.json"
    partial = ROOT / f"evals/results/decision_{tag}.partial.jsonl"
    rows: list[dict] = [json.loads(x) for x in partial.read_text().splitlines()] if partial.exists() else []
    done = {(r["persona"], r["job_id"]) for r in rows if "error" not in r}
    rows = list({(r["persona"], r["job_id"]): r for r in rows if "error" not in r}.values())  # dedupe
    work = [(p, r) for p in PERSONAS for r in sample(p, a.per_persona) if (p, r["job"]["job_id"]) not in done]
    sem = asyncio.Semaphore(a.concurrency)

    async def one(p, r):
        async with sem:
            res = await run_job(app, p, r, a.profile)
            rows.append(res)
            with partial.open("a") as fh:  # resumable: a crash or reboot never loses finished jobs
                fh.write(json.dumps(res, default=str) + "\n")
            print(f"[{len(rows)}/{len(work) + len(done)}] {p} {r['job']['job_id']} gold={r['gold']['action']} "
                  f"pred={res.get('pred_action')} scam={res.get('pred_scam')}/{r['gold']['is_scam']} "
                  f"{res.get('error', '')}", flush=True)

    await asyncio.gather(*(one(p, r) for p, r in work))
    out = {"meta": {"profile": a.profile, "per_persona": a.per_persona, "ts": time.strftime("%Y-%m-%d %H:%M")},
           "overall": summarise(rows), "by_persona": {p: summarise([r for r in rows if r["persona"] == p]) for p in PERSONAS},
           "by_source": {"real_kaggle": summarise([r for r in rows if r.get("real")]),
                         "synthetic": summarise([r for r in rows if r.get("real") is False])},
           "rows": rows}
    (ROOT / "evals/results").mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out["overall"], indent=1))


if __name__ == "__main__":
    asyncio.run(main())

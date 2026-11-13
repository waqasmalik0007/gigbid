"""B4 retrieval evaluation: every variant x persona on the dev set, plus ablation of the shipped pipeline.

    python -m evals.runners.rag_eval                      # all non-LLM variants, 200 jobs/persona
    python -m evals.runners.rag_eval --variants v0_naive v7_hyde --limit 50 --tag hyde
    python -m evals.runners.rag_eval --ablation           # remove one switch at a time from `best`

Metrics (jobs with at least one gold evidence item):
  Recall@5  = |relevant in top 5| / min(5, |relevant|)   (normalised: gold lists can exceed 5 items)
  MRR       = 1 / rank of first relevant item
  nDCG@10   = binary-relevance nDCG over the top 10
Breakdowns: persona, job source (real Kaggle post vs synthetic), requirements >= 4.
Writes evals/results/rag_<tag>.json and .csv (every number in the report comes from these files).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from pathlib import Path

from rag.index.embed import QUERY_PREFIX, embed_docs
from rag.pipeline import Pipeline
from rag.requirements import extract_requirements
from rag.retriever import VARIANTS, variant_config

ROOT = Path(__file__).resolve().parents[2]
PERSONAS = ["ai_ml_engineer", "fullstack_web", "data_analyst"]
OUT = ROOT / "evals/results"


def metrics(ranked: list[str], relevant: set[str]) -> dict:
    top5 = ranked[:5]
    rec5 = len(set(top5) & relevant) / min(5, len(relevant))
    rr = next((1 / (i + 1) for i, e in enumerate(ranked) if e in relevant), 0.0)
    dcg = sum(1 / math.log2(i + 2) for i, e in enumerate(ranked[:10]) if e in relevant)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(10, len(relevant))))
    return {"recall@5": rec5, "mrr": rr, "ndcg@10": dcg / idcg if idcg else 0.0}


def load_dev(persona: str, limit: int | None) -> list[dict]:
    rows = [json.loads(l) for l in (ROOT / f"evals/datasets/dev_{persona}.jsonl").open(encoding="utf-8")]
    rows = [r for r in rows if r["gold"]["relevant_evidence"]]
    return rows[:limit] if limit else rows


def run_variant(cfg, persona: str, rows: list[dict]) -> list[dict]:
    pipe = Pipeline(persona, cfg)
    reqs = {r["job"]["job_id"]: extract_requirements(r["job"]) for r in rows}
    if cfg.query in ("whole", "decompose"):  # pre-embed all queries in one batch
        qs = [q for r in rows for q in pipe.queries_for(r["job"], reqs[r["job"]["job_id"]])]
        embed_docs([QUERY_PREFIX + q for q in qs])
    out = []
    for r in rows:
        t0 = time.perf_counter()
        res = pipe.retrieve(r["job"], reqs[r["job"]["job_id"]], k=10)
        m = metrics([e.evidence_id for e in res.evidence], set(r["gold"]["relevant_evidence"]))
        out.append(m | {"latency_s": time.perf_counter() - t0, "job_id": r["job"]["job_id"],
                        "real": not r["job"]["synthetic"], "n_req": len(r["gold"]["requirements"]),
                        "unsupported_reqs": len(res.unsupported_requirements)})
    return out


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {}
    keys = ["recall@5", "mrr", "ndcg@10"]
    s = {k: round(statistics.mean(r[k] for r in rows), 4) for k in keys}
    lat = sorted(r["latency_s"] for r in rows)
    s["p50_latency_s"] = round(lat[len(lat) // 2], 4)
    s["p95_latency_s"] = round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 4)
    s["n"] = len(rows)
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="*", default=[v for v in VARIANTS if v != "v7_hyde"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--ablation", action="store_true")
    ap.add_argument("--tag", default="main")
    a = ap.parse_args()
    configs = [variant_config(v) for v in a.variants]
    if a.ablation:
        best = variant_config("best")
        configs = [best,  # remove one switch at a time ...
                   best.with_(name="best-contextual", chunking="item"),
                   best.with_(name="best-hybrid", retrieval="dense"),
                   # ... and add back each switch that lost on its own
                   best.with_(name="best+rerank", rerank=True),
                   best.with_(name="best+decompose", query="decompose"),
                   best.with_(name="best+outcome", outcome=True)]
        a.tag = "ablation"
    OUT.mkdir(parents=True, exist_ok=True)
    out_json = OUT / f"rag_{a.tag}.json"
    # merge into an existing results file so variants can run in separate processes (memory) and a
    # crash never loses finished variants
    prev = json.loads(out_json.read_text()) if out_json.exists() else {}
    results: dict = {"meta": {"limit": a.limit, "ts": time.strftime("%Y-%m-%d %H:%M"),
                              "embed_model": __import__("rag.index.embed", fromlist=["x"]).EMBED_MODEL,
                              "rerank_model": __import__("rag.index.embed", fromlist=["x"]).RERANK_MODEL},
                     "variants": prev.get("variants", {})}
    for cfg in configs:
        allrows, per = [], {}
        for persona in PERSONAS:
            rows = run_variant(cfg, persona, load_dev(persona, a.limit))
            per[persona] = summarise(rows)
            allrows += [r | {"persona": persona} for r in rows]
        results["variants"][cfg.name] = {
            "overall": summarise(allrows), "by_persona": per,
            "by_source": {"real_kaggle": summarise([r for r in allrows if r["real"]]),
                          "synthetic": summarise([r for r in allrows if not r["real"]])},
            "by_requirements": {">=4": summarise([r for r in allrows if r["n_req"] >= 4]),
                                "<4": summarise([r for r in allrows if r["n_req"] < 4])},
            "config": {k: v for k, v in cfg.__dict__.items() if k != "filters"}}
        print(f"{cfg.name:22s} {results['variants'][cfg.name]['overall']}", flush=True)
        out_json.write_text(json.dumps(results, indent=2))
    with (OUT / f"rag_{a.tag}.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["variant", "recall@5", "mrr", "ndcg@10", "p95_latency_s", "n"])
        for name, v in results["variants"].items():
            o = v["overall"]
            w.writerow([name, o["recall@5"], o["mrr"], o["ndcg@10"], o["p95_latency_s"], o["n"]])


if __name__ == "__main__":
    main()

"""Offline decision replay (failure analysis): test fixes to the deterministic part of the decision.

    python -m evals.runners.decision_replay [--tag open]

Fit Matcher (rule-based requirement extraction + local retrieval + local cross-encoder) and the decision
rule are deterministic; only the Risk Assessor calls an LLM. So every job in decision_<tag>.json can be
replayed without any LLM call: fit is recomputed through the real fit_matcher node, risk is taken from the
logged run, and the real decide node makes the call. The shipped extractor must reproduce the logged fit
scores before a variant is trusted. Writes evals/results/decision_replay_<tag>.json.

Variants (each one switch, all deterministic):
  shipped      rag/requirements.extract_requirements as run
  no_boiler    drop template sentences ("Deliverables: 3 milestones over 2 weeks") and >25-word
               sentences from the free-text fallback; they are not requirements and never match evidence
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERSONAS = ["ai_ml_engineer", "fullstack_web", "data_analyst"]
BOILER = re.compile(r"(?i)^\s*deliverables:\s*\d+\s+milestones?\b")


def no_boiler(job: dict, max_n: int = 6) -> list[str]:
    from rag import requirements as R
    text = f"{job.get('title', '')}. {job.get('description', '')}"
    low = text.lower()
    reqs = [f"experience with {s}" for s in R.ALL_SKILLS if R._PATS[s].search(low)]
    reqs += [p for _, p in R.IMPLIED.items() if p.lower() in low]
    if len(reqs) < 2:
        sents = [m.group(0).strip() for m in R._NEED.finditer(text)]
        reqs += [s[:160] for s in sents if not BOILER.search(s) and len(s.split()) <= 25][:3]
    seen, out = set(), []
    for r in reqs:
        if r.lower() not in seen:
            seen.add(r.lower())
            out.append(r)
    return out[:max_n] or [job.get("title", "")]


async def replay(tag: str, variants: list[str]) -> dict:
    os.environ.setdefault("MCP_TRANSPORT", "inproc")
    from agent.handoffs import RiskReport
    from agent.nodes import fit_matcher as FM
    from agent.nodes.pricing import decide
    from evals.runners.decision_eval import f1
    from mcp_servers.common.audit import current_user
    from rag.requirements import extract_requirements
    res = json.loads((ROOT / f"evals/results/decision_{tag}.json").read_text())
    jobs = {}
    for p in PERSONAS:
        for line in (ROOT / f"evals/datasets/dev_{p}.jsonl").open(encoding="utf-8"):
            r = json.loads(line)
            jobs[(p, r["job"]["job_id"])] = r["job"]
    extractors = {"shipped": extract_requirements, "no_boiler": no_boiler}
    out: dict = {"tag": tag, "variants": {}}
    for v in variants:
        FM.extract_requirements = extractors[v]
        rows, mism = [], 0
        for r in res["rows"]:
            job = jobs[(r["persona"], r["job_id"])]
            tok = current_user.set(r["persona"])
            try:
                fo = await FM.fit_matcher({"job": job, "persona_id": r["persona"], "job_id": job["job_id"]})
            finally:
                current_user.reset(tok)
            fit = fo["fit_report"]
            if v == "shipped" and r.get("fit") is not None and abs(fit.fit_score - r["fit"]) > 1e-3:
                mism += 1
            risk = RiskReport(job_id=job["job_id"], risk_score=r["risk"] or 0.0, is_scam=r["pred_scam"],
                              explanation="replayed from logged run")
            d = await decide({"job_id": job["job_id"], "risk_report": risk, "fit_report": fit, "job_spec": None})
            act = d["decision"].action
            rows.append({"job_id": r["job_id"], "persona": r["persona"], "gold_action": r["gold_action"],
                         "pred_action": act, "logged_action": r["pred_action"], "fit": fit.fit_score,
                         "requirements": [m.requirement for m in fit.matrix]})
        changed = [x for x in rows if x["pred_action"] != x["logged_action"]]
        out["variants"][v] = {"bid_skip": f1(rows), "fit_replay_mismatches": mism if v == "shipped" else None,
                              "changed_vs_logged": len(changed),
                              "fixed": sum(x["pred_action"] == x["gold_action"] for x in changed),
                              "broken": sum(x["logged_action"] == x["gold_action"] for x in changed),
                              "changes": changed}
        print(v, json.dumps({k: val for k, val in out["variants"][v].items() if k != "changes"}), flush=True)
    return out


def threshold_study(tag: str, grid=(0.5, 0.55, 0.6, 0.65, 0.7)) -> dict:
    """Bid-threshold sensitivity on the logged fit/risk scores, plus leave-one-persona-out selection
    (choose the threshold on two personas, test on the third) so the choice is not judged on the jobs that
    picked it. max_risk stays 0.7: raising it would bid on posts carrying injected instructions."""
    from evals.runners.decision_eval import f1
    rows = json.loads((ROOT / f"evals/results/decision_{tag}.json").read_text())["rows"]
    rows = [r for r in rows if r.get("fit") is not None]

    def act(r: dict, bt: float) -> str:
        if r["pred_scam"] or (r["risk"] or 0) >= 0.7:
            return "skip"
        return "bid" if r["fit"] >= bt else "watch" if r["fit"] >= 0.45 else "skip"

    score = lambda rs, bt: f1([dict(r, pred_action=act(r, bt)) for r in rs])["f1"]  # noqa: E731
    lopo = {}
    for held in PERSONAS:
        tr = [r for r in rows if r["persona"] != held]
        te = [r for r in rows if r["persona"] == held]
        best = max(grid, key=lambda bt: (score(tr, bt), -abs(bt - 0.65)))
        lopo[held] = {"chosen": best, "test_f1": score(te, best), "test_f1_at_0.65": score(te, 0.65)}
    return {"sweep": {str(bt): f1([dict(r, pred_action=act(r, bt)) for r in rows]) for bt in grid},
            "lopo": lopo,
            "lopo_mean_f1_tuned": round(sum(v["test_f1"] for v in lopo.values()) / 3, 3),
            "lopo_mean_f1_0.65": round(sum(v["test_f1_at_0.65"] for v in lopo.values()) / 3, 3)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="open")
    ap.add_argument("--variants", nargs="*", default=["shipped", "no_boiler"])
    a = ap.parse_args()
    out = asyncio.run(replay(a.tag, a.variants)) if a.variants else {"tag": a.tag}
    path = ROOT / f"evals/results/decision_replay_{a.tag}.json"
    if not a.variants and path.exists():
        out = json.loads(path.read_text())
    out["thresholds"] = threshold_study(a.tag)
    print(json.dumps(out["thresholds"], indent=1))
    path.write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

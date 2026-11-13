"""Pricing audit (failure analysis, "mispriced"): why price-in-fair-band is low, and what a fix buys.

    python -m evals.runners.pricing_audit [--tag open]

Pricing is deterministic, so every priced job in decision_<tag>.json is replayed offline (no LLM calls)
from the job, the measured fit score, the Kaggle market quartiles and the floor rate. The replay must
reproduce the logged prices exactly before any variant is trusted. Reports, per priced job:
  * in the gold fair band (the headline metric);
  * inside the client's own posted range;
  * whether the gold band is even reachable (gold band low <= client max).
and the in-band rate of two candidate fixes. Writes evals/results/pricing_audit_<tag>.json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERSONAS = ["ai_ml_engineer", "fullstack_web", "data_analyst"]


def price(job: dict, fit: float, market: dict, floor: float, variant: str) -> float:
    """Mirror of agent/nodes/pricing.py (variant 'shipped'), plus two candidate fixes."""
    bt = job["budget_type"] if job.get("budget_type") in ("fixed", "hourly") else "fixed"
    q = market[job["category"]][bt]
    lo, hi = job.get("budget_min") or 0, job.get("budget_max") or 0
    if bt == "hourly":
        if variant == "client_range" and hi:  # place the bid inside the client's range, like fixed jobs
            amount = lo + (hi - lo) * (0.55 + 0.4 * fit) if lo else hi * (0.8 + 0.15 * fit)
            return max(amount, floor)
        target = q["median"] + (q["p75"] - q["median"]) * fit
        if hi:
            target = min(max(target, lo or target), hi)
        return max(target, floor if variant != "shipped" else 30.0)  # shipped eval had no settings: floor 30
    if hi:
        return lo + (hi - lo) * (0.55 + 0.4 * fit) if lo else hi * (0.8 + 0.15 * fit)
    return q.get("median", 500)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="open")
    a = ap.parse_args()
    res = json.loads((ROOT / f"evals/results/decision_{a.tag}.json").read_text())
    market = json.loads((ROOT / "data/processed/market_stats.json").read_text())
    jobs, floors = {}, {}
    for p in PERSONAS:
        floors[p] = json.loads((ROOT / f"data/personas/{p}/profile.json").read_text()).get("floor_rate", 30)
        for line in (ROOT / f"evals/datasets/dev_{p}.jsonl").open(encoding="utf-8"):
            r = json.loads(line)
            jobs[(p, r["job"]["job_id"])] = r["job"]
    rows, mismatches = [], 0
    for r in res["rows"]:
        if r.get("price") is None:
            continue
        job = jobs[(r["persona"], r["job_id"])]
        band = r["fair_band"]
        replay = price(job, r["fit"], market, floors[r["persona"]], "shipped")
        mismatches += abs(replay - r["price"]) > 0.5
        lo, hi = job.get("budget_min") or 0, job.get("budget_max") or 0
        row = {"job_id": r["job_id"], "persona": r["persona"], "budget_type": job["budget_type"],
               "real_post": not job["synthetic"], "client_range": [lo, hi], "gold_band": band, "price": r["price"],
               "in_gold_band": band[0] <= r["price"] <= band[1],
               "in_client_range": (lo <= r["price"] <= hi) if hi else None,
               "gold_band_reachable": (band[0] <= hi) if hi else None}
        for v in ("persona_floor", "client_range"):
            row[f"in_band_{v}"] = band[0] <= price(job, r["fit"], market, floors[r["persona"]], v) <= band[1]
        rows.append(row)
    n = len(rows)
    rate = lambda k: round(sum(bool(x[k]) for x in rows) / n, 3) if n else None  # noqa: E731
    miss = [x for x in rows if not x["in_gold_band"]]
    out = {"tag": a.tag, "n_priced": n, "replay_mismatches": mismatches,
           "in_gold_band": rate("in_gold_band"), "in_client_range": rate("in_client_range"),
           "misses": len(miss), "misses_real_hourly": sum(x["real_post"] and x["budget_type"] == "hourly" for x in miss),
           "misses_gold_unreachable": sum(x["gold_band_reachable"] is False for x in miss),
           "fix_persona_floor_in_band": rate("in_band_persona_floor"),
           "fix_client_range_in_band": rate("in_band_client_range"), "rows": rows}
    (ROOT / f"evals/results/pricing_audit_{a.tag}.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()

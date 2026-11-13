"""X2 significance test: proposal_opening arm A vs B.

    python -m evals.runners.ab_test

Reads every evals/results/proposals_*.json row that has an `arm`. Tests:
  * GigSim reply probability per proposal: Welch's t-test (normal approximation for the p-value)
  * realised replies (Bernoulli draw from each p_reply with a fixed seed, i.e. one simulated client
    decision per proposal): Fisher's exact test, two-sided, computed exactly
Also reports the minimum detectable difference at alpha 0.05 / power 0.8 for the observed n.
Writes evals/results/ab_test.json.
"""
from __future__ import annotations

import json
import math
import random
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def welch(a: list[float], b: list[float]) -> dict:
    ma, mb = statistics.mean(a), statistics.mean(b)
    va, vb = statistics.variance(a) if len(a) > 1 else 0.0, statistics.variance(b) if len(b) > 1 else 0.0
    se = math.sqrt(va / len(a) + vb / len(b)) or 1e-9
    t = (ma - mb) / se
    p = math.erfc(abs(t) / math.sqrt(2))  # two-sided, normal approximation
    return {"mean_a": round(ma, 4), "mean_b": round(mb, 4), "t": round(t, 3), "p_value": round(p, 4)}


def fisher(a_yes: int, a_n: int, b_yes: int, b_n: int) -> float:
    """Two-sided Fisher exact test on the 2x2 table [[a_yes, a_no], [b_yes, b_no]]."""
    k, n = a_yes + b_yes, a_n + b_n

    def prob(x: int) -> float:
        return math.comb(a_n, x) * math.comb(b_n, k - x) / math.comb(n, k)
    p_obs = prob(a_yes)
    lo, hi = max(0, k - b_n), min(k, a_n)
    return min(1.0, sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= p_obs * (1 + 1e-9)))


def main() -> None:
    rows = []
    for f in sorted((ROOT / "evals/results").glob("proposals_*.json")):
        rows += [r for r in json.loads(f.read_text())["rows"] if r.get("arm") and r.get("p_reply") is not None]
    a = [r for r in rows if r["arm"] == "A"]
    b = [r for r in rows if r["arm"] == "B"]
    out = {"n_a": len(a), "n_b": len(b)}
    if len(a) >= 2 and len(b) >= 2:
        rng = random.Random(2026)
        ya = sum(rng.random() < r["p_reply"] for r in a)
        yb = sum(rng.random() < r["p_reply"] for r in b)
        pooled = statistics.pstdev([r["p_reply"] for r in a + b]) or 0.1
        mdd = (1.96 + 0.84) * pooled * math.sqrt(1 / len(a) + 1 / len(b))
        out |= {"p_reply": welch([r["p_reply"] for r in a], [r["p_reply"] for r in b]),
                "realised_replies": {"a": f"{ya}/{len(a)}", "b": f"{yb}/{len(b)}",
                                     "fisher_p": round(fisher(ya, len(a), yb, len(b)), 4)},
                "min_detectable_diff_p_reply": round(mdd, 3)}
    (ROOT / "evals/results/ab_test.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

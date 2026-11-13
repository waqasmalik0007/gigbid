"""CI smoke eval (E2): 30 dev jobs through the decision path + retrieval check, with failure thresholds.

    python -m evals.runners.smoke_eval --jobs 30

Fails (exit 1) if bid-or-skip F1 drops below `eval_thresholds.min_bid_f1`, if scam recall drops below
`min_scam_recall`, or if the retrieval Recall@5 of the shipped pipeline drops below
`min_recall_at_5` (agent/config.yaml). Writes evals/results/smoke_ci.json.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    from agent.llm import config
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=30)
    a = ap.parse_args()
    th = config().get("eval_thresholds", {})
    per = max(1, a.jobs // 3)
    subprocess.run([sys.executable, "-m", "evals.runners.decision_eval", "--per-persona", str(per),
                    "--profile", "open", "--tag", "ci"], check=True, cwd=ROOT, env={**__import__("os").environ,
                                                                     "GIGBID_CHECKPOINT_FILE": str(ROOT / "data/processed/ci_cp.pkl")})
    dec = json.loads((ROOT / "evals/results/decision_ci.json").read_text())["overall"]
    rag = json.loads((ROOT / "evals/results/rag_ci.json").read_text())["variants"]["best"]["overall"]
    checks = {"bid_f1": (dec["bid_skip"]["f1"], th.get("min_bid_f1", 0.5)),
              "scam_recall": (dec["scam"]["recall"] or 0, th.get("min_scam_recall", 0.8)),
              "recall@5": (rag["recall@5"], th.get("min_recall_at_5", 0.4))}
    failed = {k: v for k, v in checks.items() if v[0] < v[1]}
    (ROOT / "evals/results/smoke_ci.json").write_text(json.dumps({"checks": checks, "failed": failed}, indent=2))
    print(json.dumps({"checks": checks, "failed": failed}, indent=1))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

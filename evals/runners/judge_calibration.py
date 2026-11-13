"""Calibrate the LLM judge against a human (B4: label 30 items yourself, report Cohen's kappa).

    python -m evals.runners.judge_calibration export   # writes evals/datasets/judge_labels.csv (30 rows)
    # open the CSV, fill `human_overall` (1-5) for every row WITHOUT looking at judge_overall
    python -m evals.runners.judge_calibration score    # kappa -> evals/results/judge_calibration.json

The export hides the judge's score in a separate file so the labeller is blind to it.
Reports Cohen's kappa (unweighted and quadratic-weighted) and the rows with the largest disagreement.
"""
from __future__ import annotations

import csv
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHEET = ROOT / "evals/datasets/judge_labels.csv"
KEY = ROOT / "evals/datasets/judge_labels_key.json"


def export(n: int = 30) -> None:
    rows = []
    for f in sorted((ROOT / "evals/results").glob("proposals_*.json")):
        rows += [r | {"src": f.name} for r in json.loads(f.read_text())["rows"] if r.get("letter") and r.get("judge")]
    random.Random(1).shuffle(rows)
    rows = rows[:n]
    jobs = {}
    for p in ("ai_ml_engineer", "fullstack_web", "data_analyst"):
        for line in (ROOT / f"evals/datasets/dev_{p}.jsonl").open(encoding="utf-8"):
            j = json.loads(line)["job"]
            jobs[j["job_id"]] = j
    with SHEET.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["item", "job_title", "job_post", "proposal", "human_overall (1-5)", "notes"])
        for i, r in enumerate(rows):
            j = jobs.get(r["job_id"], {})
            w.writerow([i, j.get("title", ""), j.get("description", "")[:1200], r["letter"], "", ""])
    KEY.write_text(json.dumps([{"item": i, "job_id": r["job_id"], "judge_overall": r["judge"]["overall"], "src": r["src"]}
                               for i, r in enumerate(rows)], indent=1))
    print(f"wrote {len(rows)} items to {SHEET}")


def kappa(a: list[int], b: list[int], weighted: bool) -> float:
    cats = [1, 2, 3, 4, 5]
    n = len(a)
    w = (lambda i, j: ((i - j) ** 2) / 16) if weighted else (lambda i, j: 0.0 if i == j else 1.0)
    obs = sum(w(x, y) for x, y in zip(a, b)) / n
    pa = {c: a.count(c) / n for c in cats}
    pb = {c: b.count(c) / n for c in cats}
    exp = sum(pa[i] * pb[j] * w(i, j) for i in cats for j in cats)
    return 1 - obs / exp if exp else 1.0


def score() -> None:
    key = {k["item"]: k for k in json.loads(KEY.read_text())}
    human, judge, rows = [], [], []
    with SHEET.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            h = (r.get("human_overall (1-5)") or "").strip()
            if h:
                k = key[int(r["item"])]
                human.append(int(h))
                judge.append(int(k["judge_overall"]))
                rows.append({"item": int(r["item"]), "human": int(h), "judge": k["judge_overall"], "notes": r.get("notes")})
    if len(human) < 2:
        sys.exit("fill human_overall in the CSV first")
    out = {"n": len(human), "cohen_kappa": round(kappa(human, judge, False), 3),
           "cohen_kappa_quadratic": round(kappa(human, judge, True), 3),
           "exact_agreement": round(sum(h == j for h, j in zip(human, judge)) / len(human), 3),
           "largest_disagreements": sorted(rows, key=lambda r: -abs(r["human"] - r["judge"]))[:5]}
    (ROOT / "evals/results/judge_calibration.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    {"export": export, "score": score}[sys.argv[1] if len(sys.argv) > 1 else "export"]()

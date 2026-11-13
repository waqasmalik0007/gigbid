"""Turn the Kaggle Upwork datasets into GigSim job material and market statistics.

    python -m gigsim.seed.kaggle_prep

Inputs (git-ignored, downloaded with the Kaggle CLI; licences/attribution in docs/DATA_CARD.md):
  data/raw/kaggle/upwork-job-postings-dataset-2024-50k-records/upwork-jobs.csv   (title, description, budget)
  data/raw/kaggle/all-jobs-on-upwork-200k-plus/*.csv                              (title, hourly/budget)
Outputs:
  data/processed/kaggle_jobs.json     real posts mapped to our 3 categories, with skills found in text
  data/processed/market_stats.json    real budget/rate quartiles per category
"""
from __future__ import annotations

import csv
import json
import re
import statistics
import sys
from pathlib import Path

from gigsim.seed.vocab import CATEGORIES

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw/kaggle"
OUT = ROOT / "data/processed"
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

PATTERNS = {cat: {s: re.compile(r"(?<![\w.])" + re.escape(s.lower()) + r"(?![\w])") for s in spec["skills"]}
            for cat, spec in CATEGORIES.items()}


def classify(text: str) -> tuple[str | None, list[str]]:
    t = text.lower()
    best, best_skills = None, []
    for cat, pats in PATTERNS.items():
        found = [s for s, p in pats.items() if p.search(t)]
        if len(found) > len(best_skills):
            best, best_skills = cat, found
    return (best, best_skills) if len(best_skills) >= 2 else (None, [])


def _f(x) -> float | None:
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = []
    src = RAW / "upwork-job-postings-dataset-2024-50k-records/upwork-jobs.csv"
    with src.open(encoding="utf-8", errors="ignore") as fh:
        for r in csv.DictReader(fh):
            desc = (r.get("description") or "").strip()
            if len(desc) < 200:
                continue
            cat, skills = classify(r["title"] + " " + desc)
            if not cat:
                continue
            hourly = (r.get("is_hourly") or "").lower() == "true"
            jobs.append(dict(title=r["title"].strip(), description=desc[:2500], category=cat, skills=skills,
                             budget_type="hourly" if hourly else "fixed",
                             hourly_low=_f(r.get("hourly_low")), hourly_high=_f(r.get("hourly_high")),
                             budget=_f(r.get("budget")), country=r.get("country"),
                             published_date=r.get("published_date"),
                             source="kaggle:asaniczka/upwork-job-postings-dataset-2024-50k-records"))
    (OUT / "kaggle_jobs.json").write_text(json.dumps(jobs), encoding="utf-8")

    rates: dict[str, dict[str, list[float]]] = {c: {"hourly": [], "fixed": []} for c in CATEGORIES}
    for f in sorted((RAW / "all-jobs-on-upwork-200k-plus").glob("*.csv")):
        with f.open(encoding="utf-8", errors="ignore") as fh:
            for r in csv.DictReader(fh):
                cat, _ = classify(r["title"] + " " + r["title"])  # titles are short; require 2 hits
                if not cat:
                    t = r["title"].lower()
                    cat = next((c for c, pats in PATTERNS.items() if any(p.search(t) for p in pats.values())), None)
                if not cat:
                    continue
                if (r.get("is_hourly") or "").lower() == "true":
                    lo, hi = _f(r.get("hourly_low")), _f(r.get("hourly_high"))
                    if lo and hi and hi < 500:
                        rates[cat]["hourly"].append((lo + hi) / 2)
                elif (b := _f(r.get("budget"))) and b < 100_000:
                    rates[cat]["fixed"].append(b)
    stats = {}
    for cat, d in rates.items():
        stats[cat] = {}
        for bt, vals in d.items():
            if len(vals) >= 20:
                q = statistics.quantiles(vals, n=4)
                stats[cat][bt] = dict(p25=round(q[0], 2), median=round(q[1], 2), p75=round(q[2], 2), n=len(vals))
    stats["_source"] = "kaggle:asaniczka/all-jobs-on-upwork-200k-plus (Feb-Jul 2024)"
    (OUT / "market_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    by_cat = {c: sum(j["category"] == c for j in jobs) for c in CATEGORIES}
    print(f"kaggle jobs kept={len(jobs)} by_category={by_cat}")
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()

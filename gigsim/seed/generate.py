"""Generate the synthetic GigSim world: personas, clients, jobs and the gold-labelled dev set.

    python -m gigsim.seed.generate --seed 7 [--kaggle-dir data/raw/kaggle]

Outputs (all records carry "synthetic": true unless they came from Kaggle):
  data/personas/<persona>/{profile.json, cv.md, cv.pdf, case_studies/*.md, repos/*/README.md,
                           repos/*/main.py, reviews.json, past_proposals.json}
  data/gigsim/{clients.json, jobs_history.json, jobs_stream.json}
  evals/datasets/dev_<persona>.jsonl   (gold: action, is_scam, fair_band, relevant_evidence)

Gold labels are produced by the same deterministic rules that build the world (documented in
docs/DATA_CARD.md). They are NOT human labels; that limitation is reported.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from gigsim.seed.vocab import (
    CATEGORIES, COUNTRIES, GENERIC_OPENINGS, IMPLIED, INDUSTRIES, INJECTION_TEMPLATES, PERSONAS,
    PROJECT_TEMPLATES, SCAM_TEMPLATES, SCREENING_QUESTIONS,
)

ROOT = Path(__file__).resolve().parents[2]
START = datetime(2024, 1, 1)


def _id(prefix: str, *parts) -> str:
    return f"{prefix}_{hashlib.sha1('|'.join(map(str, parts)).encode()).hexdigest()[:10]}"


# ------------------------------------------------------------------ minimal PDF writer (CV loader test)
def write_simple_pdf(path: Path, text: str) -> None:
    lines = [ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for ln in text.splitlines()]
    body = "BT /F1 10 Tf 50 800 Td 12 TL " + " ".join(f"({ln}) '" for ln in lines[:60]) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(body)} >>\nstream\n{body}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out.encode("latin-1")))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    path.write_bytes(out.encode("latin-1", "replace"))


# ------------------------------------------------------------------ personas
def build_persona(pid: str, spec: dict, rng: random.Random) -> dict:
    cat, sec = spec["category"], spec["secondary"]
    base = Path(ROOT, "data/personas", pid)
    for sub in ("case_studies", "repos"):
        (base / sub).mkdir(parents=True, exist_ok=True)

    projects = []
    templates = [(cat, t) for t in PROJECT_TEMPLATES[cat]] * 2 + [(sec, t) for t in PROJECT_TEMPLATES[sec][:4]]
    rng.shuffle(templates)
    n_projects = rng.randint(15, 20)
    for i, (pcat, (title, skills, outcome)) in enumerate(templates[:n_projects]):
        industry = rng.choice(INDUSTRIES)
        date = START - timedelta(days=rng.randint(30, 1100))
        result = outcome.format(p=rng.randint(12, 60), n=rng.randint(3, 400))
        pid_ = _id("proj", pid, i)
        projects.append(dict(id=pid_, title=f"{title} for a {industry} client", category=pcat,
                             skills=skills, industry=industry, date=date.date().isoformat(),
                             result=result, synthetic=True))

    evidence: list[dict] = []
    for p in projects:
        # 1) case study: prose, names the skills
        cs_id = f"case_{p['id']}"
        implied_line = " ".join(IMPLIED[s] for s in p["skills"] if s in IMPLIED)
        md = (f"# {p['title']}\n\n_Synthetic case study. Date: {p['date']}. Industry: {p['industry']}._\n\n"
              f"## Problem\nThe client needed to {implied_line or 'improve a core workflow'}.\n\n"
              f"## What I built\nUsing {', '.join(p['skills'])}, I delivered a production system.\n\n"
              f"## Result\nThe work {p['result']}.\n")
        (base / "case_studies" / f"{cs_id}.md").write_text(md, encoding="utf-8")
        evidence.append(dict(evidence_id=cs_id, type="case_study", project_id=p["id"], title=p["title"],
                             skills=p["skills"], industry=p["industry"], date=p["date"], text=md, synthetic=True))
        # 2) repo README + code (README often under-describes; code reveals the tools)
        if rng.random() < 0.7:
            repo = p["id"].replace("proj_", "repo-")
            (base / "repos" / repo).mkdir(exist_ok=True)
            readme = f"# {repo}\n\nSmall internal tool. See main.py.\n"
            imports = "\n".join(f"# uses: {s}" for s in p["skills"])
            code = f'"""{p["title"]} (synthetic)."""\n{imports}\n\ndef run():\n    ...\n'
            (base / "repos" / repo / "README.md").write_text(readme, encoding="utf-8")
            (base / "repos" / repo / "main.py").write_text(code, encoding="utf-8")
            evidence.append(dict(evidence_id=f"readme_{p['id']}", type="repo_readme", project_id=p["id"],
                                 title=repo, skills=[], industry=p["industry"], date=p["date"],
                                 text=readme, synthetic=True))
            evidence.append(dict(evidence_id=f"code_{p['id']}", type="repo_code", project_id=p["id"],
                                 title=f"{repo}/main.py", skills=p["skills"], industry=p["industry"],
                                 date=p["date"], text=code, synthetic=True))
    # 3) client reviews: third way of describing a project, vague on tools
    reviews = []
    for p in rng.sample(projects, k=min(10, len(projects))):
        r = dict(evidence_id=f"review_{p['id']}", type="review", project_id=p["id"], title=f"Review: {p['title']}",
                 skills=[], industry=p["industry"], date=p["date"], rating=rng.choice([4.5, 4.8, 5.0, 5.0]),
                 text=f"Great work on our {p['industry']} project. It {p['result']}. Would hire again.",
                 synthetic=True)
        reviews.append(r)
        evidence.append(r)

    all_skills = sorted({s for p in projects for s in p["skills"]})
    cv = (f"# {spec['name']}\n\nSynthetic persona for GigBid evaluation.\n\nPrimary: {cat}. Secondary: {sec}.\n\n"
          f"## Skills\n{', '.join(all_skills)}\n\n## Selected projects\n" +
          "\n".join(f"- {p['title']} ({p['date'][:4]}): {p['result']}" for p in projects[:8]))
    (base / "cv.md").write_text(cv, encoding="utf-8")
    write_simple_pdf(base / "cv.pdf", cv)
    evidence.append(dict(evidence_id=f"cv_{pid}", type="cv", project_id=None, title="CV", skills=all_skills,
                         industry=None, date=START.date().isoformat(), text=cv, synthetic=True))

    profile = dict(persona_id=pid, name=spec["name"], primary_category=cat, secondary_category=sec,
                   skills=all_skills, floor_rate=spec["floor_rate"], weekly_connects_budget=80,
                   projects=projects, synthetic=True)
    (base / "profile.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")
    (base / "reviews.json").write_text(json.dumps(reviews, indent=2), encoding="utf-8")
    return dict(profile=profile, evidence=evidence)


# ------------------------------------------------------------------ clients (with hidden preferences)
def build_clients(n: int, rng: random.Random) -> list[dict]:
    clients = []
    for i in range(n):
        cat = rng.choice(list(CATEGORIES))
        quality = rng.random()
        hires = int(quality * rng.randint(0, 60))
        clients.append(dict(
            client_id=_id("cli", i), name=f"Client {i:03d} (synthetic)", industry=rng.choice(INDUSTRIES),
            country=rng.choice(COUNTRIES), payment_verified=quality > 0.2,
            history=dict(jobs_posted=hires + rng.randint(0, 20), hires=hires,
                         total_spend=round(hires * rng.uniform(200, 3000), 2),
                         avg_rating_given=round(3.2 + quality * 1.8, 2), member_since_days=rng.randint(10, 3000)),
            hidden=dict(  # never exposed via the API; drives the simulated client's decisions
                must_have=rng.sample(CATEGORIES[cat]["skills"], k=2),
                budget_ceiling_factor=round(rng.uniform(0.85, 1.25), 2),
                dislikes_generic_opening=rng.random() < 0.7,
                prefers_short=rng.random() < 0.5,
                responsiveness=round(rng.uniform(0.3, 0.95), 2),
            ),
            synthetic=True,
        ))
    return clients


# ------------------------------------------------------------------ jobs
def load_kaggle_titles(kaggle_dir: Path | None) -> list[dict]:
    """Optional: real job titles/budgets from the Kaggle Upwork datasets (attributed in DATA_CARD)."""
    rows: list[dict] = []
    if not kaggle_dir or not kaggle_dir.exists():
        return rows
    for f in kaggle_dir.glob("**/*.csv"):
        with f.open(encoding="utf-8", errors="ignore") as fh:
            for r in csv.DictReader(fh):
                t = r.get("title") or r.get("Title") or r.get("job_title")
                if t:
                    rows.append(dict(title=t.strip(), source=f.name,
                                     budget=r.get("budget") or r.get("Budget"),
                                     hourly_low=r.get("hourly_low"), hourly_high=r.get("hourly_high")))
                if len(rows) > 200_000:
                    return rows
    return rows


def fair_band(cat: str, n_req: int, budget_type: str) -> tuple[float, float]:
    lo, hi = CATEGORIES[cat]["rate"]
    if budget_type == "hourly":
        return (round(lo + (hi - lo) * 0.25), round(lo + (hi - lo) * 0.7))
    hours = 10 + 8 * n_req
    return (round(hours * lo * 1.1, -1), round(hours * (lo + hi) / 2, -1))


def make_job(i: int, rng: random.Random, clients: list[dict], when: datetime, kaggle: list[dict]) -> dict:
    cat = rng.choice(list(CATEGORIES))
    skills_pool = CATEGORIES[cat]["skills"]
    n_req = rng.randint(1, 6)
    req = rng.sample(skills_pool, k=n_req)
    named = [s for s in req if not (s in IMPLIED and rng.random() < 0.5)]
    implied = [s for s in req if s not in named]
    client = rng.choice(clients)
    budget_type = rng.choice(["fixed", "hourly"])
    band = fair_band(cat, n_req, budget_type)
    factor = rng.uniform(0.5, 1.4)
    kg = rng.choice(kaggle) if kaggle and rng.random() < 0.6 else None
    title = kg["title"] if kg else f"{rng.choice(['Need', 'Looking for', 'Hiring'])} {cat.replace('_', ' ')} help: {req[0]}"
    desc = (f"We are a {client['industry']} company. "
            + (f"Must be strong in {', '.join(named)}. " if named else "")
            + " ".join(f"We need to {IMPLIED[s]}." for s in implied)
            + f" Deliverables: {n_req} milestones over {rng.choice([1, 2, 4, 6])} weeks.")
    job = dict(
        job_id=_id("job", i), title=title, description=desc, category=cat, skills=named,
        budget_type=budget_type,
        budget_min=round(band[0] * factor * 0.8), budget_max=round(band[1] * factor),
        connects_cost=rng.choice([4, 6, 8, 12, 16]), client_id=client["client_id"],
        posted_at=when.isoformat(), screening_questions=rng.sample(SCREENING_QUESTIONS, k=rng.randint(0, 2)),
        source=("kaggle:" + kg["source"]) if kg else "synthetic", synthetic=kg is None,
    )
    gold = dict(requirements=req, fair_band=band, is_scam=False, has_injection=False, duplicate_of=None)

    roll = rng.random()
    if roll < 0.08:  # scam
        t, d = rng.choice(SCAM_TEMPLATES)
        job.update(title=t, description=d, budget_min=0, budget_max=rng.choice([5, 500, 5000]))
        gold["is_scam"] = True
    elif roll < 0.14:  # planted prompt injection inside an otherwise normal job
        job["description"] += "\n\n" + rng.choice(INJECTION_TEMPLATES)
        gold["has_injection"] = True
    return job | {"_gold": gold}


def gold_for_persona(job: dict, persona: dict, evidence: list[dict]) -> dict:
    g = job["_gold"]
    req = set(g["requirements"])
    relevant = [e["evidence_id"] for e in evidence
                if e["type"] != "cv" and (len(req & set(e["skills"])) >= min(2, len(req)))]
    covered = req & set(persona["profile"]["skills"])
    coverage = len(covered) / max(1, len(req))
    floor_ok = job["budget_type"] == "fixed" or job["budget_max"] >= persona["profile"]["floor_rate"]
    if g["is_scam"]:
        action = "skip"
    elif coverage >= 0.6 and floor_ok and relevant:
        action = "bid"
    elif coverage >= 0.4:
        action = "watch"
    else:
        action = "skip"
    return dict(action=action, is_scam=g["is_scam"], has_injection=g["has_injection"],
                fair_band=g["fair_band"], relevant_evidence=relevant[:20], requirements=sorted(req),
                coverage=round(coverage, 2))


# ------------------------------------------------------------------ past proposals with outcomes
def past_proposals(pid: str, persona: dict, jobs: list[dict], rng: random.Random, n: int = 150) -> list[dict]:
    out = []
    evid = [e for e in persona["evidence"] if e["type"] == "case_study"]
    for i, job in enumerate(rng.sample(jobs, k=n)):
        generic = rng.random() < 0.35
        cited = [] if generic else rng.sample(evid, k=rng.randint(1, 2))
        opening = rng.choice(GENERIC_OPENINGS) if generic else \
            f"You need {', '.join(job['_gold']['requirements'][:2])}; I did exactly that in '{cited[0]['title']}'."
        body = opening + ("" if generic else f" Result: {cited[0]['text'].split('## Result')[-1].strip()}")
        p_reply = 0.08 if generic else 0.35 + 0.1 * len(cited)
        r = rng.random()
        outcome = "won" if r < p_reply * 0.35 else "replied" if r < p_reply else "ignored"
        out.append(dict(evidence_id=_id("prop", pid, i), type="past_proposal", job_title=job["title"],
                        category=job["category"], text=body, cited=[c["evidence_id"] for c in cited],
                        outcome=outcome, skills=job["_gold"]["requirements"],
                        date=job["posted_at"][:10], synthetic=True))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--kaggle-dir", type=Path, default=ROOT / "data/raw/kaggle")
    ap.add_argument("--history", type=int, default=1500)
    ap.add_argument("--stream", type=int, default=300)
    ap.add_argument("--dev", type=int, default=200)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    kaggle = load_kaggle_titles(a.kaggle_dir)

    personas = {pid: build_persona(pid, spec, rng) for pid, spec in PERSONAS.items()}
    clients = build_clients(250, rng)
    history = [make_job(i, rng, clients, START + timedelta(hours=i * 5), kaggle) for i in range(a.history)]
    stream = [make_job(10_000 + i, rng, clients, datetime(2026, 11, 1) + timedelta(minutes=i * 20), kaggle)
              for i in range(a.stream)]
    for j in rng.sample(range(len(stream)), k=a.stream // 15):  # planted near-duplicates
        dup = dict(stream[j], job_id=_id("job", "dup", j), title=stream[j]["title"] + " (reposted)")
        dup["_gold"] = dict(stream[j]["_gold"], duplicate_of=stream[j]["job_id"])
        stream.append(dup)

    for pid, p in personas.items():
        props = past_proposals(pid, p, history[:600], rng)  # disjoint from dev pool (last 900)
        p["evidence"].extend(props)
        base = Path(ROOT, "data/personas", pid)
        (base / "past_proposals.json").write_text(json.dumps(props, indent=2), encoding="utf-8")
        (base / "evidence_index.json").write_text(json.dumps(p["evidence"], indent=2), encoding="utf-8")

    out = Path(ROOT, "data/gigsim")
    out.mkdir(parents=True, exist_ok=True)
    (out / "clients.json").write_text(json.dumps(clients, indent=2), encoding="utf-8")
    strip = lambda js: [{k: v for k, v in j.items() if k != "_gold"} for j in js]  # noqa: E731
    (out / "jobs_history.json").write_text(json.dumps(strip(history), indent=2), encoding="utf-8")
    (out / "jobs_stream.json").write_text(json.dumps(strip(stream), indent=2), encoding="utf-8")
    (out / "gold_internal.json").write_text(json.dumps({j["job_id"]: j["_gold"] for j in history + stream}),
                                            encoding="utf-8")

    ds = Path(ROOT, "evals/datasets")
    ds.mkdir(parents=True, exist_ok=True)
    dev_pool = [j for j in history[-900:]]
    for pid, p in personas.items():
        sample = rng.sample(dev_pool, k=a.dev)
        with (ds / f"dev_{pid}.jsonl").open("w", encoding="utf-8") as fh:
            for j in sample:
                row = {k: v for k, v in j.items() if k != "_gold"}
                fh.write(json.dumps(dict(job=row, gold=gold_for_persona(j, p, p["evidence"]))) + "\n")
    print(f"personas={len(personas)} clients={len(clients)} history={len(history)} "
          f"stream={len(stream)} kaggle_titles={len(kaggle)}")


if __name__ == "__main__":
    main()

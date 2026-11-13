"""Fit Matcher: requirement -> portfolio evidence matrix via the Portfolio MCP server.

Runs in parallel with the Job Analyst, so it extracts requirements itself (deterministic, no LLM).
Strength per requirement (proposals are excluded: a past proposal is phrasing, not proof):
  * tool/skill requirements ("experience with Docker"): exact match against the evidence items'
    skills and text -> strong if >= 2 items show it, partial if 1. Exact names matter (Part B).
  * free-text requirements ("schedule nightly loads"): cross-encoder score of the best item, with
    thresholds calibrated on observed ms-marco-MiniLM scores for this corpus (strong > -3,
    partial > -7; unrelated items score around -11).
"""
from __future__ import annotations

import asyncio
import re

from agent.handoffs import EvidenceRef, FitReport, RequirementMatch
from agent.mcp_client import call
from agent.nodes.common import node
from rag.index.embed import rerank_scores
from rag.requirements import ALL_SKILLS, extract_requirements

PROOF_TYPES = ["case_study", "repo_code", "repo_readme", "review", "cv", "upload"]
STRONG, PARTIAL = -3.0, -7.0
SKILL_IN = {s: re.compile(r"(?<![\w.])" + re.escape(s.lower()) + r"(?![\w])") for s in ALL_SKILLS}


@node("fit_matcher", parallel=True)
async def fit_matcher(state: dict) -> dict:
    job, persona = state["job"], state["persona_id"]
    reqs = extract_requirements(job)

    async def one(r: str) -> RequirementMatch:
        res = await call("fit_matcher", "portfolio", "search_evidence",
                         {"requirement": r, "k": 6, "persona": persona, "filters": {"type": PROOF_TYPES}})
        items = res["items"]
        skill = next((s for s in ALL_SKILLS if r.lower() == f"experience with {s.lower()}"), None)
        if skill:  # exact tool-name evidence
            hits = [i for i in items if SKILL_IN[skill].search(i["snippet"].lower())]
            ev = [EvidenceRef(evidence_id=i["evidence_id"], type=i["type"], title=i["title"], snippet=i["snippet"],
                              score=1.0) for i in hits]
            strength = "strong" if len(hits) >= 2 else "partial" if hits else "none"
            return RequirementMatch(requirement=r, evidence=ev[:3], strength=strength)
        scores = rerank_scores(r, [i["snippet"] for i in items]) if items else []
        ev = sorted((EvidenceRef(evidence_id=i["evidence_id"], type=i["type"], title=i["title"],
                                 snippet=i["snippet"], score=round(s, 3), outcome=i.get("outcome"))
                     for i, s in zip(items, scores)), key=lambda e: -e.score)
        top = ev[0].score if ev else -99
        strength = "strong" if top >= STRONG else "partial" if top >= PARTIAL else "none"
        return RequirementMatch(requirement=r, evidence=[e for e in ev if e.score >= PARTIAL][:3], strength=strength)

    matrix = await asyncio.gather(*(one(r) for r in reqs))
    w = {"strong": 1.0, "partial": 0.5, "none": 0.0}
    fit = sum(w[m.strength] for m in matrix) / max(1, len(matrix))
    skills = [m.requirement.removeprefix("experience with ") for m in matrix]
    report = FitReport(
        job_id=job["job_id"], fit_score=round(fit, 3), matrix=list(matrix),
        matched_skills=[s for s, m in zip(skills, matrix) if m.strength != "none"],
        missing_skills=[s for s, m in zip(skills, matrix) if m.strength == "none"],
        reasons=[f"{m.requirement}: {m.strength}" + (f" ({m.evidence[0].title})" if m.evidence else "")
                 for m in matrix])
    evidence = [e for m in matrix for e in m.evidence]
    return {"fit_report": report, "evidence": evidence}

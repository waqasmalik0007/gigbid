"""Cheap, deterministic requirement extraction used by V1 when no JobSpec is available
(e.g. in retrieval evaluation, so retrieval is measured independently of the Job Analyst LLM).
In the live graph the Job Analyst's JobSpec.requirements are used instead.
"""
from __future__ import annotations

import re

from gigsim.seed.vocab import CATEGORIES, IMPLIED

ALL_SKILLS = sorted({s for c in CATEGORIES.values() for s in c["skills"]}, key=len, reverse=True)
_PATS = {s: re.compile(r"(?<![\w.])" + re.escape(s.lower()) + r"(?![\w])") for s in ALL_SKILLS}
_NEED = re.compile(r"[^.\n]*\b(need|must|require|looking for|deliverable|build|create|develop)[^.\n]*", re.I)


def extract_requirements(job: dict, max_n: int = 6) -> list[str]:
    text = f"{job.get('title', '')}. {job.get('description', '')}"
    low = text.lower()
    reqs: list[str] = []
    for s in ALL_SKILLS:
        if _PATS[s].search(low):
            reqs.append(f"experience with {s}")
    for skill, phrase in IMPLIED.items():
        if phrase.lower() in low:
            reqs.append(phrase)
    if len(reqs) < 2:
        reqs += [m.group(0).strip()[:160] for m in _NEED.finditer(text)][:3]
    seen, out = set(), []
    for r in reqs:
        if r.lower() not in seen:
            seen.add(r.lower())
            out.append(r)
    return out[:max_n] or [job.get("title", "")]

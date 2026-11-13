"""Portfolio tool implementations (pure functions; server.py registers them with MCP)."""
from __future__ import annotations

import os
from collections import Counter
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from rag.ingest.loaders import Item, read_pdf


class EvidenceItem(BaseModel):
    evidence_id: str
    type: str = Field(description="case_study | repo_readme | repo_code | review | past_proposal | cv | upload")
    title: str
    snippet: str
    source: str
    date: str | None = None
    outcome: str | None = Field(None, description="for past proposals: won | replied | ignored")
    score: float


class SearchResult(BaseModel):
    requirement: str
    persona: str
    items: list[EvidenceItem]


class CaseStudy(BaseModel):
    evidence_id: str
    title: str
    text: str
    skills: list[str]
    industry: str | None
    date: str | None
    related: list[str] = Field(description="other evidence ids about the same project (README, code, review)")


class SkillCount(BaseModel):
    skill: str
    evidence_count: int


def default_persona() -> str:
    return os.getenv("PORTFOLIO_PERSONA", "me" if os.getenv("PORTFOLIO_DIR") else "ai_ml_engineer")


def load_dir_portfolio(path: Path) -> list[Item]:
    """Standalone mode for other freelancers: any folder of .md / .txt / .pdf files."""
    items = []
    for f in sorted(path.rglob("*")):
        if f.suffix.lower() in {".md", ".txt"}:
            text = f.read_text(encoding="utf-8", errors="ignore")
        elif f.suffix.lower() == ".pdf":
            text = read_pdf(f)
        else:
            continue
        kind = "past_proposal" if "proposal" in f.parts or "proposals" in f.parts else \
            "repo_readme" if f.name.lower() == "readme.md" else "case_study"
        items.append(Item(evidence_id=f"{kind}_{f.stem}", persona="me", type=kind, title=f.stem, text=text,
                          source=str(f), synthetic=False))
    return items


@lru_cache(maxsize=4)
def _dir_pipeline(path: str, variant: str):
    from rag.pipeline import Pipeline
    from rag.retriever import variant_config
    return Pipeline("me", variant_config(variant), items=load_dir_portfolio(Path(path)))


def pipeline(persona: str):
    """GigBid personas use the configured variant; 'me' indexes PORTFOLIO_DIR (standalone use)."""
    if persona == "me" and os.getenv("PORTFOLIO_DIR"):
        return _dir_pipeline(os.environ["PORTFOLIO_DIR"], os.getenv("PORTFOLIO_VARIANT", "v2_hybrid"))
    from rag.retriever import get_pipeline
    return get_pipeline(persona)


def search_evidence(requirement: str, k: int = 5, filters: dict | None = None,
                    persona: str | None = None) -> SearchResult:
    persona = persona or default_persona()
    pipe = pipeline(persona)
    res = pipe.retrieve({"title": requirement, "description": requirement}, [requirement], k=k, filters=filters)
    items = [EvidenceItem(evidence_id=r.evidence_id, type=r.type, title=r.title, snippet=r.snippet,
                          source=pipe.items[r.evidence_id].source, date=pipe.items[r.evidence_id].date,
                          outcome=r.outcome, score=round(r.score, 4)) for r in res.evidence]
    return SearchResult(requirement=requirement, persona=persona, items=items)


def get_case_study(evidence_id: str, persona: str | None = None) -> CaseStudy:
    pipe = pipeline(persona or default_persona())
    it = pipe.items.get(evidence_id)
    if it is None:
        raise ValueError(f"unknown evidence id {evidence_id!r}")
    related = [e for e, o in pipe.items.items() if it.project_id and o.project_id == it.project_id and e != evidence_id]
    return CaseStudy(evidence_id=evidence_id, title=it.title, text=it.text, skills=it.skills,
                     industry=it.industry, date=it.date, related=related)


def list_skills(persona: str | None = None) -> list[SkillCount]:
    pipe = pipeline(persona or default_persona())
    c = Counter(s for it in pipe.items.values() if it.type != "past_proposal" for s in it.skills)
    return [SkillCount(skill=s, evidence_count=n) for s, n in c.most_common()]

"""Composable retrieval pipeline. Each rag/variants/vN_*.py is a config of this class, so a variant
is one switch away from V0, the shipped pipeline is a combination, and ablation removes one switch.

Switches
  chunking : fixed | item | contextual | multivector
  query    : whole | decompose | hyde
  retrieval: dense | hybrid            (hybrid = BM25 + dense, reciprocal rank fusion)
  rerank   : cross-encoder over top candidates
  outcome  : boost evidence from proposals that won / got replies (and the case studies they cited)
  corrective: LangGraph CRAG loop (grade -> re-query -> refuse when nothing supports a requirement)
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from agent.handoffs import EvidenceRef
from rag.index.bm25 import BM25Index
from rag.index.embed import embed_docs, embed_query, rerank_scores
from rag.index.vector_store import make_index
from rag.ingest.loaders import Chunk, Item, fixed_chunks, item_chunks, load_persona

ROOT = Path(__file__).resolve().parents[1]
OUTCOME_WEIGHT = {"won": 1.35, "replied": 1.15, "ignored": 0.75}
RERANK_CHARS = 1000  # cross-encoder input per candidate (MiniLM reads ~256 tokens anyway)


@dataclass(frozen=True)
class RagConfig:
    name: str
    chunking: str = "fixed"
    query: str = "whole"
    retrieval: str = "dense"
    rerank: bool = False
    outcome: bool = False
    corrective: bool = False
    candidates: int = 40
    filters: dict = field(default_factory=dict)

    def with_(self, **kw) -> "RagConfig":
        return replace(self, **kw)


@dataclass
class RetrievalResult:
    evidence: list[EvidenceRef]
    per_requirement: dict[str, list[EvidenceRef]]
    unsupported_requirements: list[str]
    queries: list[str]
    llm_usage: dict


def rrf(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    s: dict[str, float] = defaultdict(float)
    for r in rankings:
        for rank, key in enumerate(r):
            s[key] += 1.0 / (k + rank + 1)
    return s


# ------------------------------------------------------------------ context lines (V6)
def _context_line(it: Item) -> str:
    """Contextual-retrieval prefix. Generated once per item and cached (template fallback, no LLM
    needed): situates a fragment (e.g. a terse README) within its project, type and skills."""
    cache_f = ROOT / "data/processed/context_lines.json"
    cache = json.loads(cache_f.read_text(encoding="utf-8")) if cache_f.exists() else {}
    if it.evidence_id in cache:
        return cache[it.evidence_id]
    proj = it.title if it.type == "case_study" else (it.project_id or "")
    return (f"[{it.type.replace('_', ' ')} from {it.persona}'s portfolio; project {proj}; "
            f"industry {it.industry or 'n/a'}; skills {', '.join(it.skills) or 'not stated'}"
            + (f"; proposal outcome {it.outcome}" if it.outcome else "") + "]")


class Pipeline:
    def __init__(self, persona: str, cfg: RagConfig, items: list[Item] | None = None):
        self.persona, self.cfg = persona, cfg
        self.items = {i.evidence_id: i for i in (items if items is not None else load_persona(persona))}
        self._filters: dict = dict(cfg.filters)
        self.chunks = self._chunk(list(self.items.values()))
        self.mat = embed_docs([c.text for c in self.chunks])
        self.index = make_index(f"gigbid_{persona}_{cfg.chunking}", self.mat)
        self.bm25 = BM25Index([c.text for c in self.chunks]) if cfg.retrieval == "hybrid" else None
        self.usage: dict = {"tokens_in": 0, "tokens_out": 0, "usd": 0.0, "calls": 0}
        # case studies cited by successful past proposals (outcome-aware)
        self.cited_weight: dict[str, float] = defaultdict(lambda: 1.0)
        for it in self.items.values():
            if it.type == "past_proposal" and it.outcome in ("won", "replied"):
                for m in re.findall(r"'([^']+)'", it.text):
                    for other in self.items.values():
                        if other.type == "case_study" and other.title == m:
                            self.cited_weight[other.evidence_id] = max(self.cited_weight[other.evidence_id],
                                                                       OUTCOME_WEIGHT[it.outcome])

    def _chunk(self, items: list[Item]) -> list[Chunk]:
        c = self.cfg.chunking
        if c == "fixed":
            return fixed_chunks(items)
        if c == "contextual":
            return item_chunks(items, prefix_fn=_context_line)
        if c == "multivector":
            # summary vector per project + README + code chunks indexed separately (item level)
            chunks = item_chunks(items)
            by_proj: dict[str, list[Item]] = defaultdict(list)
            for it in items:
                if it.project_id:
                    by_proj[it.project_id].append(it)
            for pid, its in by_proj.items():
                skills = sorted({s for i in its for s in i.skills})
                title = next((i.title for i in its if i.type == "case_study"), pid)
                chunks.append(Chunk(f"{pid}#summary", its[0].evidence_id,
                                    f"Project summary: {title}. Skills: {', '.join(skills)}.",
                                    {"type": "summary", "project_id": pid, "outcome": None}))
            return chunks
        return item_chunks(items)

    # -------------------------------------------------------------- scoring
    def _allowed(self, ch: Chunk) -> bool:
        f = self._filters
        return not f or all(ch.meta.get(k) in (v if isinstance(v, list) else [v]) for k, v in f.items())

    def _rank_chunks(self, query: str) -> list[tuple[int, float]]:
        hits = self.index.search(embed_query(query), min(len(self.chunks), self.cfg.candidates * 4))
        dense = dict(hits)
        order = [i for i, _ in hits if self._allowed(self.chunks[i])][: self.cfg.candidates]
        if self.cfg.retrieval == "hybrid":
            lex = self.bm25.scores(query)
            lorder = [i for i in np.argsort(-lex) if self._allowed(self.chunks[i])][: self.cfg.candidates]
            fused = rrf([[str(i) for i in order], [str(i) for i in lorder]])
            return sorted(((int(k), v) for k, v in fused.items()), key=lambda x: -x[1])
        return [(int(i), float(dense[i])) for i in order]

    def _to_items(self, ranked: list[tuple[int, float]], query: str) -> list[tuple[str, float]]:
        best: dict[str, float] = {}
        for i, s in ranked:
            eid = self.chunks[i].evidence_id
            best[eid] = max(best.get(eid, -1e9), s)
        pairs = sorted(best.items(), key=lambda x: -x[1])[: self.cfg.candidates]
        if self.cfg.rerank:
            rs = rerank_scores(query, [self.items[e].text[:RERANK_CHARS] for e, _ in pairs])
            pairs = sorted(zip([e for e, _ in pairs], rs), key=lambda x: -x[1])
        if self.cfg.outcome:
            def w(e: str) -> float:
                it = self.items[e]
                return OUTCOME_WEIGHT.get(it.outcome, 1.0) if it.type == "past_proposal" else self.cited_weight[e]
            # scale positive scores up and negative scores toward zero consistently
            pairs = sorted(((e, s * w(e) if s >= 0 else s / w(e)) for e, s in pairs), key=lambda x: -x[1])
        if self.cfg.chunking == "multivector":  # return the project's items together
            out, seen = [], set()
            for e, s in pairs:
                pid = self.items[e].project_id
                group = [e] + [o for o in self.items if pid and self.items[o].project_id == pid and o != e]
                for g in group:
                    if g not in seen:
                        seen.add(g)
                        out.append((g, s if g == e else s - 1e-3))
            pairs = out
        return pairs

    # -------------------------------------------------------------- public
    def queries_for(self, job: dict, requirements: list[str]) -> list[str]:
        whole = f"{job.get('title', '')}\n{job.get('description', '')}"[:1500]
        if self.cfg.query == "decompose" and requirements:
            return requirements
        if self.cfg.query == "hyde":
            from rag.hyde import hypothetical_proposal
            text, usage = hypothetical_proposal(job)
            for k in self.usage:
                self.usage[k] += usage.get(k, 0)
            return [text]
        return [whole]

    def retrieve(self, job: dict, requirements: list[str] | None = None, k: int = 10,
                 filters: dict | None = None) -> RetrievalResult:
        requirements = requirements or []
        self._filters = {**self.cfg.filters, **(filters or {})}
        if self.cfg.corrective:
            from rag.variants.v8_corrective import corrective_retrieve
            return corrective_retrieve(self, job, requirements, k)
        queries = self.queries_for(job, requirements)
        per_q = [self._to_items(self._rank_chunks(q), q) for q in queries]
        if len(per_q) == 1:
            merged = per_q[0]
        else:  # requirement decomposition: fuse so every requirement gets representation
            fused = rrf([[e for e, _ in r] for r in per_q])
            merged = sorted(fused.items(), key=lambda x: -x[1])
        refs = [self.ref(e, s) for e, s in merged[:k]]
        per_req = {q: [self.ref(e, s) for e, s in r[:3]] for q, r in zip(queries, per_q)} \
            if self.cfg.query == "decompose" else {}
        return RetrievalResult(refs, per_req, [], queries, dict(self.usage))

    def ref(self, eid: str, score: float) -> EvidenceRef:
        it = self.items[eid]
        skills = f"Skills: {', '.join(it.skills)}. " if it.skills else ""
        return EvidenceRef(evidence_id=eid, type=it.type, title=it.title, snippet=(skills + it.text)[:600],
                           score=float(score), outcome=it.outcome)

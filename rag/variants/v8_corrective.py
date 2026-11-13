"""V8 (agentic): corrective RAG loop in LangGraph.

    retrieve(requirement) -> grade -> [weak] rewrite+retry (once) -> [still weak] refuse

Grading uses the cross-encoder score (no LLM call per requirement, so it is cheap enough for every
job). A requirement with no evidence above threshold is reported as unsupported, which the
writer must not claim and the Fit Matcher counts against the bid.
"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from rag.index.embed import rerank_scores
from rag.pipeline import RagConfig, RetrievalResult, rrf

CONFIG = RagConfig(name="v8_corrective", chunking="item", query="decompose", retrieval="hybrid",
                   rerank=True, corrective=True)
THRESHOLD = 0.0      # cross-encoder logit; >0 means "more relevant than not"
SYNONYMS = {"experience with ": "built a project using ", "ml": "machine learning", "etl": "data pipeline"}


class CragState(TypedDict, total=False):
    requirement: str
    query: str
    attempts: int
    hits: list[tuple[str, float]]
    verdict: str


def _rewrite(q: str) -> str:
    for a, b in SYNONYMS.items():
        q = q.replace(a, b)
    return q + " project case study result"


def build_graph(pipe) -> Any:
    def retrieve(s: CragState) -> CragState:
        q = s.get("query") or s["requirement"]
        base = pipe.cfg.with_(corrective=False, rerank=False)
        old = pipe.cfg
        pipe.cfg = base
        try:
            pairs = pipe._to_items(pipe._rank_chunks(q), q)[:15]
        finally:
            pipe.cfg = old
        scores = rerank_scores(s["requirement"], [pipe.items[e].text[:1500] for e, _ in pairs])
        hits = sorted(zip([e for e, _ in pairs], scores), key=lambda x: -x[1])
        return {"hits": hits, "attempts": s.get("attempts", 0) + 1, "query": q}

    def grade(s: CragState) -> CragState:
        good = [h for h in s["hits"] if h[1] > THRESHOLD]
        if good:
            return {"verdict": "supported", "hits": good}
        return {"verdict": "retry" if s["attempts"] < 2 else "unsupported"}

    def rewrite(s: CragState) -> CragState:
        return {"query": _rewrite(s["requirement"])}

    g = StateGraph(CragState)
    g.add_node("retrieve", retrieve)
    g.add_node("grade", grade)
    g.add_node("rewrite", rewrite)
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "grade")
    g.add_conditional_edges("grade", lambda s: s["verdict"], {"supported": END, "unsupported": END,
                                                                "retry": "rewrite"})
    g.add_edge("rewrite", "retrieve")
    return g.compile()


def corrective_retrieve(pipe, job: dict, requirements: list[str], k: int) -> RetrievalResult:
    if not requirements:
        requirements = [f"{job.get('title', '')}"]
    graph = build_graph(pipe)
    per_req, unsupported, rankings = {}, [], []
    for r in requirements:
        out = graph.invoke({"requirement": r})
        if out["verdict"] == "unsupported":
            unsupported.append(r)
            per_req[r] = []
            continue
        per_req[r] = [pipe.ref(e, s) for e, s in out["hits"][:3]]
        rankings.append([e for e, _ in out["hits"]])
    fused = sorted(rrf(rankings).items(), key=lambda x: -x[1]) if rankings else []
    return RetrievalResult([pipe.ref(e, s) for e, s in fused[:k]], per_req, unsupported, requirements, {})

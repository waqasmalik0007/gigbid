"""Shipped pipeline. Chosen by evaluation (evals/results/rag_*.json); see docs/DATA_CARD.md.
Updated after each evaluation run; never chosen by default (R6)."""
from rag.pipeline import RagConfig

CONFIG = RagConfig(name="best", chunking="contextual", query="decompose", retrieval="hybrid", rerank=True,
                   outcome=True)

"""Shipped pipeline, chosen by evaluation (evals/results/rag_main.json, 544 dev jobs, 3 personas):
V6 contextual chunks had the best single-switch Recall@5 (0.391) and V2 hybrid BM25+dense the second
best (0.358); reranking (V3), decomposition (V1), multi-vector (V5) and outcome boosting (V4) each
scored below V0. The combination below is validated by the ablation in evals/results/rag_ablation.json.
"""
from rag.pipeline import RagConfig

CONFIG = RagConfig(name="best", chunking="contextual", query="whole", retrieval="hybrid")

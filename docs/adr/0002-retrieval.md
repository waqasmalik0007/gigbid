# 0002 Shipped retrieval: contextual chunks + hybrid BM25/dense

**Decision.** Ship `rag/variants/best.py` = contextual chunks (V6) + hybrid retrieval (V2).
**Evidence.** `evals/results/rag_main.json` (544 dev jobs, 3 personas): best Recall@5 0.502 vs V6 0.391,
V2 0.358, V0 0.267. Reranking (V3, 0.263, p95 3.4 s), decomposition (V1, 0.225), multi-vector (V5, 0.233)
and outcome boosting (V4, 0.142) each scored below V0 and are left out; `rag_ablation.json` checks
removing each shipped switch and adding each losing one: adding rerank (0.292), decomposition (0.375) or
outcome boosting (0.359) to the shipped pipeline each lowers Recall@5.
**Likely reasons (hypotheses, not yet tested separately).** Gold relevance is skill overlap; whole-post queries already contain the
skill names, so decomposition splits signal; the MiniLM cross-encoder was trained on web questions and
scores "experience with X" vs case studies poorly; outcome boosting promotes past proposals regardless
of topic.

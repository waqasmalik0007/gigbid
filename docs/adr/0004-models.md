# 0004 Models routed by node, open models only

**Decision.** Open profile on Groq: gpt-oss-20b for analyst/risk/cheap nodes, gpt-oss-120b for writer,
critic and liaison; fallback Qwen 3.8 27B; second profile `open_qwen` for comparison. Embeddings
BGE-small-en-v1.5 and the MiniLM ms-marco cross-encoder, both open and CPU-friendly.
**Why.** The deployed link must work on free resources (no GPU); the free tier is 8k tokens/min per model,
so spreading nodes across models also raises throughput. Choice between the two open profiles is by the
decision evaluation (`evals/results/decision_open*.json`).

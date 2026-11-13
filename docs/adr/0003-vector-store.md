# 0003 Vector store: Qdrant in deployment, in-memory locally

**Decision.** Dense search goes through `rag/index/vector_store.py`: Qdrant (REST) when `QDRANT_URL` is
set, an in-memory index over the same normalised vectors otherwise.
**Why.** Corpora are ~400 items per persona: exact search is trivial in memory and lets evals run anywhere;
Qdrant gives persistence and sharing between API, worker and Portfolio MCP in compose. Both return
cosine top-k over identical vectors, so results do not depend on the backend.

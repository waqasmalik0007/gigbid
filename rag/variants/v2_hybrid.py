"""V2 (retrieval, required): BM25 + dense with reciprocal rank fusion (exact tool names)."""
from rag.pipeline import RagConfig

CONFIG = RagConfig(name="v2_hybrid", chunking="fixed", query="whole", retrieval="hybrid")

"""V3 (retrieval, required): cross-encoder reranking of the dense top-40."""
from rag.pipeline import RagConfig

CONFIG = RagConfig(name="v3_rerank", chunking="fixed", query="whole", retrieval="dense", rerank=True)

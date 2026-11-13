"""V1 (query, required): one retrieval per extracted requirement, fused with RRF."""
from rag.pipeline import RagConfig

CONFIG = RagConfig(name="v1_decompose", chunking="fixed", query="decompose", retrieval="dense")

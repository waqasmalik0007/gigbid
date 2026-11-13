"""V0 (baseline, required): fixed chunks, top-k cosine on the whole job post."""
from rag.pipeline import RagConfig

CONFIG = RagConfig(name="v0_naive", chunking="fixed", query="whole", retrieval="dense")

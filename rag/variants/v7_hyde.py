"""V7 (query): HyDE, retrieve with a hypothetical ideal proposal for the job (LLM, cached)."""
from rag.pipeline import RagConfig

CONFIG = RagConfig(name="v7_hyde", chunking="fixed", query="hyde", retrieval="dense")

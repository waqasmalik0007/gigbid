"""V6 (indexing): contextual retrieval, a context line prepended to each chunk before embedding."""
from rag.pipeline import RagConfig

CONFIG = RagConfig(name="v6_contextual", chunking="contextual", query="whole", retrieval="dense")

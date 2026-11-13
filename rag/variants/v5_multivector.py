"""V5 (indexing): project summary, README and code indexed separately, returned as one project."""
from rag.pipeline import RagConfig

CONFIG = RagConfig(name="v5_multivector", chunking="multivector", query="whole", retrieval="dense")

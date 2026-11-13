"""V4 (ranking): boost evidence from proposals that won or got replies, and case studies they cited."""
from rag.pipeline import RagConfig

CONFIG = RagConfig(name="v4_outcome", chunking="fixed", query="whole", retrieval="dense", outcome=True)

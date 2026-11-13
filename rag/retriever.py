"""Factory: the deployed variant is chosen in agent/config.yaml (rag.variant), set from evaluation."""
from __future__ import annotations

import importlib
from functools import lru_cache

from rag.pipeline import Pipeline, RagConfig

VARIANTS = ["v0_naive", "v1_decompose", "v2_hybrid", "v3_rerank", "v4_outcome", "v5_multivector",
            "v6_contextual", "v7_hyde", "v8_corrective", "best"]


def variant_config(name: str) -> RagConfig:
    return importlib.import_module(f"rag.variants.{name}").CONFIG


@lru_cache(maxsize=32)
def get_pipeline(persona: str, variant: str | None = None) -> Pipeline:
    if variant is None:
        from agent.llm import config
        variant = config()["rag"]["variant"]
    return Pipeline(persona, variant_config(variant))


def invalidate(persona: str | None = None) -> None:
    """Called after the user uploads new portfolio work (incremental re-index on next use)."""
    get_pipeline.cache_clear()

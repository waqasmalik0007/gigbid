"""Open embedding + reranker models (both profiles), cached on disk by content hash.

Backends tried in order: fastembed (ONNX, light, used in deployment) -> sentence-transformers.
Models come from agent/config.yaml; CPU is enough.
"""
from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path

import numpy as np

import agent.llm  # noqa: F401  (loads .env, e.g. HF_HOME for the model cache)

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # xet CDN downloads stall on some networks
CACHE = ROOT / "data" / "processed" / "emb_cache"

EMBED_MODEL = os.getenv("GIGBID_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
RERANK_MODEL = os.getenv("GIGBID_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # BGE convention


@lru_cache
def _embedder():
    try:
        from fastembed import TextEmbedding
        m = TextEmbedding(EMBED_MODEL)
        return "fastembed", m
    except ImportError:
        from sentence_transformers import SentenceTransformer
        return "st", SentenceTransformer(EMBED_MODEL, device="cpu")


def _raw_embed(texts: list[str]) -> np.ndarray:
    kind, m = _embedder()
    if kind == "fastembed":
        v = np.array(list(m.embed(texts, batch_size=64)), dtype=np.float32)
    else:
        v = m.encode(texts, batch_size=64, convert_to_numpy=True, show_progress_bar=False).astype(np.float32)
    return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)


class EmbedCache:
    """Disk cache keyed by sha1(model + text) so re-indexing only embeds new/changed text."""

    def __init__(self) -> None:
        CACHE.mkdir(parents=True, exist_ok=True)
        slug = EMBED_MODEL.replace("/", "_")
        self.vec_path, self.key_path = CACHE / f"{slug}.npy", CACHE / f"{slug}.json"
        self.keys: dict[str, int] = json.loads(self.key_path.read_text(encoding="utf-8")) if self.key_path.exists() else {}
        self.vecs = np.load(self.vec_path) if self.vec_path.exists() else np.zeros((0, 384), np.float32)

    @staticmethod
    def key(t: str) -> str:
        return hashlib.sha1((EMBED_MODEL + "|" + t).encode()).hexdigest()

    def embed(self, texts: list[str]) -> np.ndarray:
        missing = list({t for t in texts if self.key(t) not in self.keys})
        if missing:
            new = _raw_embed(missing)
            if self.vecs.shape[0] == 0:
                self.vecs = np.zeros((0, new.shape[1]), np.float32)
            base = self.vecs.shape[0]
            self.vecs = np.vstack([self.vecs, new])
            for i, t in enumerate(missing):
                self.keys[self.key(t)] = base + i
            np.save(self.vec_path, self.vecs)
            self.key_path.write_text(json.dumps(self.keys))
        return self.vecs[[self.keys[self.key(t)] for t in texts]]


@lru_cache
def cache() -> EmbedCache:
    return EmbedCache()


def embed_docs(texts: list[str]) -> np.ndarray:
    return cache().embed(texts)


def embed_query(q: str) -> np.ndarray:
    return cache().embed([QUERY_PREFIX + q])[0]


@lru_cache
def _reranker():
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        name = "Xenova/ms-marco-MiniLM-L-6-v2" if "ms-marco" in RERANK_MODEL else RERANK_MODEL
        return "fastembed", TextCrossEncoder(name)
    except ImportError:
        from sentence_transformers import CrossEncoder
        return "st", CrossEncoder(RERANK_MODEL, device="cpu")


def rerank_scores(query: str, docs: list[str]) -> list[float]:
    if not docs:
        return []
    kind, m = _reranker()
    if kind == "fastembed":
        return [float(s) for s in m.rerank(query, docs)]
    return [float(s) for s in m.predict([(query, d) for d in docs], show_progress_bar=False)]

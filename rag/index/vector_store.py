"""Vector store: Qdrant (REST) when QDRANT_URL is set, in-memory numpy otherwise.

Both hold the same normalised BGE vectors and return cosine top-k, so evaluation numbers do not
depend on which one is used (docs/adr/0003-vector-store.md). Qdrant is used in docker compose and
deployment so indexes persist and are shared between the API, worker and Portfolio MCP server.
"""
from __future__ import annotations

import hashlib
import os

import httpx
import numpy as np


class NumpyIndex:
    def __init__(self, mat: np.ndarray):
        self.mat = mat

    def search(self, q: np.ndarray, k: int) -> list[tuple[int, float]]:
        s = self.mat @ q
        idx = np.argsort(-s)[:k]
        return [(int(i), float(s[i])) for i in idx]


class QdrantIndex:
    def __init__(self, name: str, mat: np.ndarray, url: str):
        self.url, self.mat = url.rstrip("/"), mat
        # content-addressed collection name: re-indexing only happens when the chunks change
        digest = hashlib.sha1(mat.tobytes()).hexdigest()[:10]
        self.coll = f"{name}_{digest}"
        with httpx.Client(timeout=30) as c:
            if c.get(f"{self.url}/collections/{self.coll}").status_code != 200:
                c.put(f"{self.url}/collections/{self.coll}",
                      json={"vectors": {"size": int(mat.shape[1]), "distance": "Cosine"}}).raise_for_status()
                for start in range(0, len(mat), 256):
                    pts = [{"id": i, "vector": mat[i].tolist()} for i in range(start, min(len(mat), start + 256))]
                    c.put(f"{self.url}/collections/{self.coll}/points?wait=true", json={"points": pts}).raise_for_status()

    def search(self, q: np.ndarray, k: int) -> list[tuple[int, float]]:
        r = httpx.post(f"{self.url}/collections/{self.coll}/points/search",
                       json={"vector": q.tolist(), "limit": k}, timeout=15)
        r.raise_for_status()
        return [(int(p["id"]), float(p["score"])) for p in r.json()["result"]]


def make_index(name: str, mat: np.ndarray):
    url = os.getenv("QDRANT_URL")
    if url:
        try:
            return QdrantIndex(name, mat, url)
        except httpx.HTTPError:
            pass  # vector store down -> degrade to in-memory search over the same vectors
    return NumpyIndex(mat)

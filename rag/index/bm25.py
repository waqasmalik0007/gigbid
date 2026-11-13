"""Lexical index. Tokeniser keeps tool names like "next.js", "a/b", "scikit-learn" intact."""
from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

TOKEN = re.compile(r"[a-z0-9][a-z0-9.+#/_-]*[a-z0-9+#]|[a-z0-9]")
STOP = set("the a an and or of to in for on with we our you your i is are be this that it as at by from "
           "will can need needs looking have has".split())


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP]


class BM25Index:
    def __init__(self, texts: list[str]):
        self.bm = BM25Okapi([tokenize(t) or ["_"] for t in texts])

    def scores(self, query: str):
        return self.bm.get_scores(tokenize(query) or ["_"])

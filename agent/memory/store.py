"""LangGraph store + checkpointer factories.

Postgres when DATABASE_URL is a postgres URL (docker compose / deployment). Otherwise a file-backed
saver/store under data/processed (pickled after every write), so crash-and-resume also works on a
laptop without Postgres.
Namespaces:
  ("style", user)            promoted style rules        -> injected into the Writer prompt
  ("style_obs", user)        raw edit observations       -> never shown to an LLM
  ("outcome", user)          promoted outcome insights   -> injected into the Writer prompt
  ("outcome_stats", user)    raw per-category counters   -> never shown to an LLM
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _pg() -> str | None:
    url = os.getenv("DATABASE_URL", "")
    return url if url.startswith("postgres") else None


class FileSaver:
    """InMemorySaver whose storage is flushed to disk after each write and reloaded at start."""

    def __new__(cls, path: Path):
        import pickle
        from langgraph.checkpoint.memory import InMemorySaver

        import threading
        import time
        lock = threading.Lock()

        class _Saver(InMemorySaver):
            def _flush(self):
                with lock:
                    data = pickle.dumps({"storage": {k: dict(v) for k, v in self.storage.items()},
                                         "writes": dict(self.writes), "blobs": dict(self.blobs)})
                    tmp = path.with_suffix(".tmp")
                    for attempt in range(20):  # Windows: AV scanners briefly lock fresh files
                        try:
                            tmp.write_bytes(data)
                            tmp.replace(path)
                            return
                        except PermissionError:
                            time.sleep(0.05 * (attempt + 1))
                    path.write_bytes(data)

            def put(self, *a, **kw):
                r = super().put(*a, **kw)
                self._flush()
                return r

            def put_writes(self, *a, **kw):
                r = super().put_writes(*a, **kw)
                self._flush()
                return r

            async def aput(self, *a, **kw):
                return self.put(*a, **kw)

            async def aput_writes(self, *a, **kw):
                return self.put_writes(*a, **kw)

        saver = _Saver()
        if path.exists():
            data = pickle.loads(path.read_bytes())
            for k, v in data["storage"].items():
                for ns, cps in v.items():
                    saver.storage[k][ns].update(cps)
            saver.writes.update(data["writes"])
            saver.blobs.update(data["blobs"])
        return saver


class FileStore:
    """InMemoryStore persisted to a JSON file after each put/delete."""

    def __new__(cls, path: Path):
        import json
        from langgraph.store.memory import InMemoryStore

        class _Store(InMemoryStore):
            def _flush(self):
                rows = [[list(ns), k, it.value] for ns, d in self._data.items() for k, it in d.items()]
                path.write_text(json.dumps(rows))

            def batch(self, ops):
                r = super().batch(ops)
                if any(type(o).__name__ == "PutOp" for o in ops):
                    self._flush()
                return r

        st = _Store()
        if path.exists():
            for ns, k, v in json.loads(path.read_text()):
                st.put(tuple(ns), k, v)
        return st


@lru_cache
def get_store():
    if url := _pg():
        from langgraph.store.postgres import PostgresStore
        from psycopg import Connection
        conn = Connection.connect(url, autocommit=True, prepare_threshold=0)
        store = PostgresStore(conn)
        store.setup()
        return store
    path = Path(os.getenv("GIGBID_MEMORY_FILE", ROOT / "data/processed/memory.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileStore(path)


@lru_cache
def get_checkpointer():
    if url := _pg():
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg import Connection
        conn = Connection.connect(url, autocommit=True, prepare_threshold=0)
        cp = PostgresSaver(conn)
        cp.setup()
        return cp
    path = Path(os.getenv("GIGBID_CHECKPOINT_FILE", ROOT / "data/processed/checkpoints.pkl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return FileSaver(path)

"""Application tables with plain SQL. Postgres (psycopg) via DATABASE_URL, SQLite file otherwise.

    rows("SELECT * FROM jobs WHERE user_id=?", uid)   -> list[dict]   (JSON columns decoded)
    row(...) -> dict | None;  run(...) -> None;  upsert(table, keys, values);  add_event(...)

Placeholders are always `?` (translated to %s for Postgres). Every query in the app filters by
user_id (tenant isolation, E4).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
JSON_COLS = {"settings", "job", "summary", "usage", "data"}
_lock = threading.Lock()

TABLES = {
    "users": "id TEXT PRIMARY KEY, email TEXT UNIQUE, pw_hash TEXT, persona_id TEXT, settings TEXT, created REAL",
    "oauth_tokens": "user_id TEXT, provider TEXT, enc_token TEXT, updated REAL, PRIMARY KEY (user_id, provider)",
    "jobs": "user_id TEXT, job_id TEXT, job TEXT, thread_id TEXT, status TEXT, action TEXT, fit REAL, risk REAL, "
            "price REAL, connects INTEGER, summary TEXT, updated REAL, PRIMARY KEY (user_id, job_id)",
    "runs": "thread_id TEXT PRIMARY KEY, user_id TEXT, kind TEXT, job_id TEXT, status TEXT, trace_id TEXT, "
            "usage TEXT, created REAL, updated REAL",
    "run_events": "{serial}, thread_id TEXT, user_id TEXT, node TEXT, data TEXT, ts REAL",
    "pipeline": "user_id TEXT, proposal_id TEXT, job_id TEXT, title TEXT, stage TEXT, category TEXT, "
                "connects INTEGER, amount REAL, thread_id TEXT, letter TEXT, updated REAL, "
                "PRIMARY KEY (user_id, proposal_id)",
}


def _pg_url() -> str | None:
    url = os.getenv("DATABASE_URL", "")
    return url if url.startswith("postgres") else None


@lru_cache
def _conn():
    if url := _pg_url():
        import psycopg
        conn = psycopg.connect(url, autocommit=True)
        serial = "id SERIAL PRIMARY KEY"
    else:
        p = Path(os.getenv("GIGBID_APP_DB", ROOT / "data/processed/app.sqlite3"))
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(p, check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        serial = "id INTEGER PRIMARY KEY AUTOINCREMENT"
    for name, cols in TABLES.items():
        conn.execute(f"CREATE TABLE IF NOT EXISTS {name} ({cols.format(serial=serial)})")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_events_thread ON run_events(thread_id)")
    return conn


def _sql(q: str) -> str:
    return q.replace("?", "%s") if _pg_url() else q


def _enc(v: Any) -> Any:
    return json.dumps(v, default=str) if isinstance(v, (dict, list)) else v


def _dec(d: dict) -> dict:
    return {k: (json.loads(v) if k in JSON_COLS and isinstance(v, str) and v[:1] in "[{" else v) for k, v in d.items()}


def rows(q: str, *params) -> list[dict]:
    with _lock:
        cur = _conn().execute(_sql(q), tuple(_enc(p) for p in params))
        cols = [c[0] for c in cur.description]
        return [_dec(dict(zip(cols, r))) for r in cur.fetchall()]


def row(q: str, *params) -> dict | None:
    r = rows(q, *params)
    return r[0] if r else None


def run(q: str, *params) -> None:
    with _lock:
        _conn().execute(_sql(q), tuple(_enc(p) for p in params))


def upsert(table: str, keys: dict, values: dict) -> None:
    where = " AND ".join(f"{k}=?" for k in keys)
    if row(f"SELECT 1 AS x FROM {table} WHERE {where}", *keys.values()):
        if values:
            run(f"UPDATE {table} SET {', '.join(f'{k}=?' for k in values)} WHERE {where}",
                *values.values(), *keys.values())
    else:
        allv = keys | values
        run(f"INSERT INTO {table} ({', '.join(allv)}) VALUES ({', '.join('?' for _ in allv)})", *allv.values())


def add_event(thread_id: str, user_id: str, node: str, data: dict) -> None:
    run("INSERT INTO run_events (thread_id, user_id, node, data, ts) VALUES (?,?,?,?,?)",
        thread_id, user_id, node, json.loads(json.dumps(data, default=str)), time.time())

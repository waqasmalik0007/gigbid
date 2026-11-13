"""GigSim: the marketplace simulator (built by us; no simulator was provided).

    uvicorn gigsim.app:app --port 8100

Realism knobs (env):
  GIGSIM_RATE_LIMIT_PER_MIN  default 60   -> 429 + Retry-After
  GIGSIM_ERROR_RATE          default 0.03 -> random 503s
  GIGSIM_CLOCK_SPEED         default 60   -> simulated seconds per real second (stream release)
  GIGSIM_SEED                default 0
  GIGSIM_POLICY_FILE         default gigsim/policy.yaml (the "platform policy update" lives here)

State is SQLite so idempotency keys and Connects survive restarts.
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import statistics
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from gigsim.client_sim import decide

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "gigsim"
DB_PATH = Path(os.getenv("GIGSIM_DB", DATA / "gigsim_state.sqlite3"))
RATE = int(os.getenv("GIGSIM_RATE_LIMIT_PER_MIN", "60"))
ERR = float(os.getenv("GIGSIM_ERROR_RATE", "0.03"))
SPEED = float(os.getenv("GIGSIM_CLOCK_SPEED", "60"))
SEED = int(os.getenv("GIGSIM_SEED", "0"))
SIM_START = datetime(2026, 11, 1)
BOOT = time.time()


def load_policy() -> dict:
    p = Path(os.getenv("GIGSIM_POLICY_FILE", ROOT / "gigsim" / "policy.yaml"))
    return yaml.safe_load(p.read_text()) if p.exists() else {}


# ---------------------------------------------------------------- world
class World:
    def __init__(self) -> None:
        self.clients = {c["client_id"]: c for c in json.loads((DATA / "clients.json").read_text())}
        self.history = json.loads((DATA / "jobs_history.json").read_text())
        self.stream = sorted(json.loads((DATA / "jobs_stream.json").read_text()), key=lambda j: j["posted_at"])
        extra = DATA / "jobs_extra.json"  # hidden-test runner can drop new jobs/clients here
        if extra.exists():
            ex = json.loads(extra.read_text())
            self.stream += ex.get("jobs", [])
            self.clients |= {c["client_id"]: c for c in ex.get("clients", [])}
        self.jobs = {j["job_id"]: j for j in self.history + self.stream}
        self.sim_offset = 0.0

    def now(self) -> datetime:
        return SIM_START + timedelta(seconds=(time.time() - BOOT) * SPEED + self.sim_offset)

    def visible(self, job: dict) -> bool:
        return job["posted_at"] <= self.now().isoformat()


world = World()
_lock = threading.Lock()


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS connects(user_id TEXT PRIMARY KEY, balance INTEGER);
    CREATE TABLE IF NOT EXISTS idem(key TEXT PRIMARY KEY, user_id TEXT, response TEXT);
    CREATE TABLE IF NOT EXISTS proposals(proposal_id TEXT PRIMARY KEY, user_id TEXT, job_id TEXT,
        status TEXT, payload TEXT, outcome TEXT, created_at TEXT, submitted_at TEXT);
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, proposal_id TEXT,
        type TEXT, data TEXT, sim_time TEXT);
    CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, thread_id TEXT, user_id TEXT, sender TEXT,
        body TEXT, sim_time TEXT);
    """)
    return con


con = db()

# ---------------------------------------------------------------- auth, rate limit, chaos
USERS = {  # bearer token -> freelancer user id; tokens come from env in deployment
    os.getenv("GIGSIM_TOKEN_AI", "sim-ai_ml_engineer"): "ai_ml_engineer",
    os.getenv("GIGSIM_TOKEN_WEB", "sim-fullstack_web"): "fullstack_web",
    os.getenv("GIGSIM_TOKEN_DATA", "sim-data_analyst"): "data_analyst",
}
_hits: dict[str, deque] = defaultdict(deque)
_chaos = random.Random(SEED)


def user(request: Request, authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    uid = USERS.get(token)
    if not uid:
        raise HTTPException(401, "invalid or missing bearer token")
    q, now = _hits[uid], time.time()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE:
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(int(60 - (now - q[0])) + 1)})
    q.append(now)
    if request.url.path != "/health" and _chaos.random() < ERR:
        raise HTTPException(503, "GigSim temporarily unavailable (simulated)")
    return uid


app = FastAPI(title="GigSim", version="1.0", description="Synthetic freelance marketplace for GigBid")


@app.exception_handler(HTTPException)
async def _http(_, exc: HTTPException):
    return JSONResponse({"error": exc.detail, "status": exc.status_code}, exc.status_code, headers=exc.headers)


def _balance(uid: str) -> int:
    row = con.execute("SELECT balance FROM connects WHERE user_id=?", (uid,)).fetchone()
    if row is None:
        start = int(load_policy().get("starting_connects", 120))
        con.execute("INSERT INTO connects VALUES(?,?)", (uid, start))
        con.commit()
        return start
    return row["balance"]


def _connects_cost(job: dict) -> int:
    pol = load_policy()
    cost = job["connects_cost"] * float(pol.get("connects_multiplier", 1.0))
    if job.get("budget_max", 0) >= pol.get("premium_job_threshold", 10**12):
        cost += pol.get("premium_job_extra_connects", 0)
    return int(round(cost))


def _idempotent(key: str | None, uid: str):
    if not key:
        raise HTTPException(400, "Idempotency-Key header is required for write operations")
    row = con.execute("SELECT response FROM idem WHERE key=? AND user_id=?", (key, uid)).fetchone()
    return json.loads(row["response"]) if row else None


def _remember(key: str, uid: str, resp: dict) -> dict:
    con.execute("INSERT OR IGNORE INTO idem VALUES(?,?,?)", (key, uid, json.dumps(resp)))
    con.commit()
    return resp


# ---------------------------------------------------------------- read endpoints
@app.get("/health")
def health():
    return {"ok": True, "sim_time": world.now().isoformat(), "policy_version": load_policy().get("version")}


@app.get("/policy")
def policy(uid: str = Depends(user)):
    return load_policy()


@app.get("/jobs")
def search_jobs(uid: str = Depends(user), q: str = "", category: str | None = None,
                since: str | None = None, limit: int = Query(50, le=200)):
    ql = q.lower().split()
    out = []
    for j in reversed(world.stream):
        if not world.visible(j) or (since and j["posted_at"] <= since):
            continue
        if category and j["category"] != category:
            continue
        hay = (j["title"] + " " + j["description"] + " " + " ".join(j["skills"])).lower()
        if ql and not all(t in hay for t in ql):
            continue
        out.append(j | {"connects_cost": _connects_cost(j)})
        if len(out) >= limit:
            break
    return {"jobs": out, "sim_time": world.now().isoformat()}


@app.get("/jobs/{job_id}")
def get_job(job_id: str, uid: str = Depends(user)):
    j = world.jobs.get(job_id)
    if not j or (j in world.stream and not world.visible(j)):
        raise HTTPException(404, "job not found")
    return j | {"connects_cost": _connects_cost(j)}


@app.get("/clients/{client_id}")
def client_history(client_id: str, uid: str = Depends(user)):
    c = world.clients.get(client_id)
    if not c:
        raise HTTPException(404, "client not found")
    return {k: v for k, v in c.items() if k != "hidden"}  # hidden preferences never leave GigSim


@app.get("/market/stats")
def market_stats(category: str, uid: str = Depends(user)):
    js = [j for j in world.history if j["category"] == category and j.get("budget_max")]
    if not js:
        raise HTTPException(404, "no data for category")
    out = {}
    for bt in ("fixed", "hourly"):
        vals = sorted((j["budget_min"] + j["budget_max"]) / 2 for j in js if j["budget_type"] == bt)
        if vals:
            q = statistics.quantiles(vals, n=4)
            out[bt] = {"p25": round(q[0], 2), "median": round(q[1], 2), "p75": round(q[2], 2), "n": len(vals)}
    return {"category": category, **out}


@app.get("/connects")
def connects(uid: str = Depends(user)):
    return {"user_id": uid, "balance": _balance(uid)}


@app.get("/proposals")
def list_proposals(uid: str = Depends(user)):
    rows = con.execute("SELECT * FROM proposals WHERE user_id=? ORDER BY created_at DESC", (uid,)).fetchall()
    return {"proposals": [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]}


@app.get("/events")
def events(uid: str = Depends(user), after_id: int = 0):
    _release_due_events(uid)
    rows = con.execute("SELECT * FROM events WHERE user_id=? AND id>? ORDER BY id", (uid, after_id)).fetchall()
    return {"events": [dict(r) | {"data": json.loads(r["data"])} for r in rows]}


@app.get("/threads")
def threads(uid: str = Depends(user)):
    _release_due_events(uid)
    rows = con.execute("SELECT thread_id, MAX(sim_time) last, COUNT(*) n FROM messages WHERE user_id=? "
                       "GROUP BY thread_id ORDER BY last DESC", (uid,)).fetchall()
    return {"threads": [dict(r) for r in rows]}


@app.get("/threads/{thread_id}/messages")
def thread_messages(thread_id: str, uid: str = Depends(user)):
    _release_due_events(uid)
    rows = con.execute("SELECT * FROM messages WHERE user_id=? AND thread_id=? ORDER BY sim_time",
                       (uid, thread_id)).fetchall()
    return {"messages": [dict(r) for r in rows]}


# ---------------------------------------------------------------- write endpoints
class DraftIn(BaseModel):
    job_id: str
    cover_letter: str = Field(min_length=20, max_length=5000)
    screening_answers: list[str] = []
    amount: float = Field(gt=0)
    budget_type: str = "fixed"


@app.post("/proposals/drafts")
def create_draft(body: DraftIn, uid: str = Depends(user), idempotency_key: str | None = Header(None),
                 dry_run: bool = False):
    job = world.jobs.get(body.job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    pol = load_policy()
    max_words = pol.get("max_cover_letter_words")
    if max_words and len(body.cover_letter.split()) > max_words:
        raise HTTPException(422, f"cover letter exceeds policy limit of {max_words} words")
    preview = {"would_create": body.model_dump(), "connects_cost_on_submit": _connects_cost(job),
               "balance": _balance(uid)}
    if dry_run:
        return {"dry_run": True, **preview}
    if (prev := _idempotent(idempotency_key, uid)) is not None:
        return prev | {"idempotent_replay": True}
    pid = "prop_" + uuid.uuid4().hex[:12]
    with _lock:
        con.execute("INSERT INTO proposals VALUES(?,?,?,?,?,?,?,?)",
                    (pid, uid, body.job_id, "draft", body.model_dump_json(), None, world.now().isoformat(), None))
        con.commit()
    return _remember(idempotency_key, uid, {"proposal_id": pid, "status": "draft", **preview})


@app.post("/proposals/{proposal_id}/submit")
def submit(proposal_id: str, uid: str = Depends(user), idempotency_key: str | None = Header(None),
           dry_run: bool = False):
    row = con.execute("SELECT * FROM proposals WHERE proposal_id=? AND user_id=?", (proposal_id, uid)).fetchone()
    if not row:
        raise HTTPException(404, "proposal not found")
    job = world.jobs[row["job_id"]]
    cost = _connects_cost(job)
    if dry_run:
        return {"dry_run": True, "proposal_id": proposal_id, "connects_cost": cost, "balance": _balance(uid),
                "payload": json.loads(row["payload"])}
    if (prev := _idempotent(idempotency_key, uid)) is not None:
        return prev | {"idempotent_replay": True}
    if row["status"] != "draft":
        return {"proposal_id": proposal_id, "status": row["status"], "note": "already submitted"}
    with _lock:
        bal = _balance(uid)
        if bal < cost:
            raise HTTPException(402, f"not enough Connects ({bal} < {cost})")
        payload = json.loads(row["payload"]) | {"proposal_id": proposal_id}
        result = decide(job, world.clients[job["client_id"]], payload, seed=SEED)
        con.execute("UPDATE connects SET balance=balance-? WHERE user_id=?", (cost, uid))
        con.execute("UPDATE proposals SET status='submitted', outcome=?, submitted_at=? WHERE proposal_id=?",
                    (json.dumps(result), world.now().isoformat(), proposal_id))
        _schedule(uid, proposal_id, job, result)
        con.commit()
    return _remember(idempotency_key, uid, {"proposal_id": proposal_id, "status": "submitted",
                                             "connects_spent": cost, "balance": bal - cost})


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@app.post("/threads/{thread_id}/messages")
def send_message(thread_id: str, body: MessageIn, uid: str = Depends(user),
                 idempotency_key: str | None = Header(None), dry_run: bool = False):
    if dry_run:
        return {"dry_run": True, "thread_id": thread_id, "would_send": body.body, "connects_cost": 0}
    if (prev := _idempotent(idempotency_key, uid)) is not None:
        return prev | {"idempotent_replay": True}
    mid = "msg_" + uuid.uuid4().hex[:12]
    con.execute("INSERT INTO messages VALUES(?,?,?,?,?,?)",
                (mid, thread_id, uid, "freelancer", body.body, world.now().isoformat()))
    con.commit()
    return _remember(idempotency_key, uid, {"message_id": mid, "thread_id": thread_id, "status": "sent"})


# ---------------------------------------------------------------- simulated time & events
def _schedule(uid: str, pid: str, job: dict, r: dict) -> None:
    t0 = world.now()
    plan = [("viewed", r["viewed"], 2), ("replied", r["replied"], 6), ("interview", r["interview"], 24),
            ("hired", r["hired"], 72)]
    for typ, happened, hours in plan:
        if happened:
            con.execute("INSERT INTO events(user_id,proposal_id,type,data,sim_time) VALUES(?,?,?,?,?)",
                        (uid, pid, f"pending:{typ}", json.dumps({"job_id": job["job_id"],
                         "reply_text": r["reply_text"] if typ == "replied" else None}),
                         (t0 + timedelta(hours=hours)).isoformat()))
    if not r["hired"]:
        con.execute("INSERT INTO events(user_id,proposal_id,type,data,sim_time) VALUES(?,?,?,?,?)",
                    (uid, pid, "pending:lost", json.dumps({"job_id": job["job_id"]}),
                     (t0 + timedelta(hours=96)).isoformat()))


def _release_due_events(uid: str) -> None:
    now = world.now().isoformat()
    with _lock:
        due = con.execute("SELECT * FROM events WHERE user_id=? AND type LIKE 'pending:%' AND sim_time<=?",
                          (uid, now)).fetchall()
        for e in due:
            typ, data = e["type"].split(":", 1)[1], json.loads(e["data"])
            con.execute("UPDATE events SET type=? WHERE id=?", (typ, e["id"]))
            if typ == "replied" and data.get("reply_text"):
                con.execute("INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?)",
                            ("msg_c_" + e["proposal_id"], "thr_" + e["proposal_id"], uid, "client",
                             data["reply_text"], e["sim_time"]))
        con.commit()


@app.post("/admin/advance")
def advance(hours: float = 24, x_admin_token: str = Header("")):
    if x_admin_token != os.getenv("GIGSIM_ADMIN_TOKEN", "sim-admin"):
        raise HTTPException(403, "admin only")
    world.sim_offset += hours * 3600
    return {"sim_time": world.now().isoformat()}

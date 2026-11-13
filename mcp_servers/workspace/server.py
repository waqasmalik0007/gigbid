"""Workspace MCP server (A3): calendar slots, tentative interview holds, email drafts.

Backed by a local mock (SQLite) so it runs anywhere; the same tool surface could be backed by the
Google Calendar / Gmail APIs. The agent only ever sees these MCP tools.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from mcp_servers.common.audit import audit

DB = Path(os.getenv("WORKSPACE_DB", Path(__file__).resolve().parents[2] / "data/processed/workspace.sqlite3"))
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WORK_HOURS = (9, 17)

mcp = FastMCP("gigbid-workspace", instructions="The freelancer's calendar and email drafts (never sends email).")


def db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript("""CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, user_id TEXT, start TEXT, end TEXT,
                       title TEXT, status TEXT, idem TEXT UNIQUE);
                       CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, user_id TEXT, to_addr TEXT,
                       subject TEXT, body TEXT, idem TEXT UNIQUE);""")
    return c


class Slot(BaseModel):
    start: str
    end: str


class Hold(BaseModel):
    event_id: str
    start: str
    end: str
    title: str
    status: str = "tentative"
    idempotent_replay: bool = False


class EmailDraft(BaseModel):
    draft_id: str
    to: str
    subject: str
    status: str = "draft (not sent)"
    idempotent_replay: bool = False


def _busy(c, user_id: str) -> list[tuple[datetime, datetime]]:
    return [(datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"]))
            for r in c.execute("SELECT start,end FROM events WHERE user_id=?", (user_id,))]


@mcp.tool(annotations=READ, structured_output=True)
def free_slots(user_id: str, days_ahead: int = Field(5, ge=1, le=21), duration_min: int = Field(30, ge=15, le=180),
               start_from: str | None = None, max_slots: int = Field(6, ge=1, le=20)) -> list[Slot]:
    """Free working-hour slots (09:00-17:00, weekdays) not overlapping existing events."""
    c = db()
    busy = _busy(c, user_id)
    t = datetime.fromisoformat(start_from) if start_from else datetime.now().replace(minute=0, second=0,
                                                                                       microsecond=0)
    t = t + timedelta(hours=1)
    out, end = [], t + timedelta(days=days_ahead)
    while t < end and len(out) < max_slots:
        e = t + timedelta(minutes=duration_min)
        if t.weekday() < 5 and WORK_HOURS[0] <= t.hour and e.hour <= WORK_HOURS[1] and \
                not any(s < e and t < b for s, b in busy):
            out.append(Slot(start=t.isoformat(), end=e.isoformat()))
            t = e + timedelta(hours=2)  # spread suggestions out
        else:
            t += timedelta(minutes=30)
    audit("workspace", "free_slots", {"user_id": user_id, "days_ahead": days_ahead}, [s.model_dump() for s in out])
    return out


@mcp.tool(annotations=WRITE, structured_output=True)
def create_interview_hold(user_id: str, start: str, end: str, title: str,
                          idempotency_key: str = Field(min_length=8)) -> Hold:
    """Create a TENTATIVE calendar hold for an interview the user approved."""
    c = db()
    row = c.execute("SELECT * FROM events WHERE idem=?", (idempotency_key,)).fetchone()
    if row:
        return Hold(event_id=row["id"], start=row["start"], end=row["end"], title=row["title"], idempotent_replay=True)
    eid = "evt_" + uuid.uuid4().hex[:10]
    c.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?)", (eid, user_id, start, end, title, "tentative",
                                                           idempotency_key))
    c.commit()
    h = Hold(event_id=eid, start=start, end=end, title=title)
    audit("workspace", "create_interview_hold", {"user_id": user_id, "start": start, "title": title}, h.model_dump())
    return h


@mcp.tool(annotations=WRITE, structured_output=True)
def create_email_draft(user_id: str, to: str, subject: str, body: str,
                       idempotency_key: str = Field(min_length=8)) -> EmailDraft:
    """Save an email DRAFT (never sends)."""
    c = db()
    row = c.execute("SELECT * FROM drafts WHERE idem=?", (idempotency_key,)).fetchone()
    if row:
        return EmailDraft(draft_id=row["id"], to=row["to_addr"], subject=row["subject"], idempotent_replay=True)
    did = "drf_" + uuid.uuid4().hex[:10]
    c.execute("INSERT INTO drafts VALUES(?,?,?,?,?,?)", (did, user_id, to, subject, body, idempotency_key))
    c.commit()
    d = EmailDraft(draft_id=did, to=to, subject=subject)
    audit("workspace", "create_email_draft", {"user_id": user_id, "to": to, "subject": subject}, d.model_dump())
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", type=int, default=None)
    a = ap.parse_args()
    if a.http:
        from mcp_servers.common.http import serve_with_bearer
        serve_with_bearer(mcp, a.http, os.getenv("MCP_BEARER_TOKEN", ""))
    else:
        mcp.run()


if __name__ == "__main__":
    main()

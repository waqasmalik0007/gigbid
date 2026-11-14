"""Job inbox, job detail, scout trigger, portfolio upload."""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile

from apps.api.auth.security import current_user
from apps.api.db import row, rows, upsert
from apps.api.schemas.models import Accepted, JobRow
from apps.api.tasks import enqueue

router = APIRouter(prefix="/jobs", tags=["jobs"])
studio = APIRouter(prefix="/studio", tags=["studio"])
ROOT = Path(__file__).resolve().parents[3]


def _row(r: dict) -> JobRow:
    j, s = r["job"] or {}, r["summary"] or {}
    b = f"{j.get('budget_type', '')} {j.get('budget_min') or ''}-{j.get('budget_max') or ''}".strip()
    return JobRow(job_id=r["job_id"], title=j.get("title", r["job_id"]), category=j.get("category"),
                  status=r["status"], action=r["action"], fit=r["fit"], risk=r["risk"], price=r["price"],
                  connects=r["connects"] or j.get("connects_cost"),
                  budget=b, flags=s.get("flags", []), is_scam=s.get("is_scam", False), reasons=s.get("reasons", []),
                  thread_id=r["thread_id"], posted_at=j.get("posted_at"))


@router.get("", response_model=list[JobRow])
def inbox(action: str | None = None, min_fit: float = 0, user: dict = Depends(current_user)):
    found = rows("SELECT * FROM jobs WHERE user_id=?", user["id"])
    out = [_row(r) for r in found if (not action or r["action"] == action) and (r["fit"] or 0) >= min_fit]
    order = {"bid": 0, "watch": 1, None: 2, "skip": 3}
    return sorted(out, key=lambda j: (order.get(j.action, 2), -(j.fit or 0), j.posted_at or ""))


@router.post("/scout", response_model=dict)
async def scout_now(user: dict = Depends(current_user)):
    """Pull new jobs now (the worker also does this on schedule) and queue analysis for each."""
    from worker.scout import scout_user
    new = await scout_user(user)
    for j in new[:15]:
        await enqueue("run_bid", user, j["job_id"], j, job_key=f"bid:{user['id']}:{j['job_id']}")
    return {"new_jobs": len(new), "queued": min(15, len(new))}


@router.post("/{job_id}/analyze", response_model=Accepted)
async def analyze(job_id: str, request: Request, user: dict = Depends(current_user)):
    tid = f"bid:{user['id']}:{job_id}"
    upsert("jobs", {"user_id": user["id"], "job_id": job_id}, {"status": "queued", "thread_id": tid,
                                                              "updated": time.time()})
    d = await enqueue("run_bid", user, job_id, None, request.state.trace_id, job_key=tid)
    return Accepted(thread_id=tid, dispatch=d)


@router.get("/{job_id}")
def job_detail(job_id: str, user: dict = Depends(current_user)):
    r = row("SELECT * FROM jobs WHERE user_id=? AND job_id=?", user["id"], job_id)
    if not r:
        raise HTTPException(404, "job not in your inbox")
    out = {"row": _row(r).model_dump(), "job": r["job"]}
    if r["thread_id"]:
        from worker.runner import _jsonable, graph
        st = graph("bid").get_state({"configurable": {"thread_id": r["thread_id"]}})
        v = _jsonable(st.values)
        out |= {k: v.get(k) for k in ("job_spec", "risk_report", "fit_report", "decision", "price", "draft",
                                      "critiques", "compliance", "verification", "status", "errors", "submission")}
        out["awaiting_approval"] = bool(st.next)
    return out


@router.post("/portfolio/upload")
async def upload_portfolio(file: UploadFile, user: dict = Depends(current_user)):
    """Add a case study (.md/.txt/.pdf) to the user's portfolio; re-indexed incrementally on next use."""
    if not re.search(r"\.(md|txt|pdf)$", file.filename or "", re.I):
        raise HTTPException(400, "upload .md, .txt or .pdf")
    data = await file.read()
    if len(data) > 2_000_000:
        raise HTTPException(413, "file too large")
    d = ROOT / "data/personas" / user["persona_id"] / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"\W+", "_", Path(file.filename).stem)[:40] + "_" + uuid.uuid4().hex[:6]
    if file.filename.lower().endswith(".pdf"):
        tmp = d / f"{stem}.pdf"
        tmp.write_bytes(data)
        from rag.ingest.loaders import read_pdf
        (d / f"{stem}.md").write_text(read_pdf(tmp), encoding="utf-8")
        tmp.unlink()
    else:
        (d / f"{stem}.md").write_text(data.decode("utf-8", "ignore"), encoding="utf-8")
    from rag.retriever import invalidate
    invalidate(user["persona_id"])
    return {"evidence_id": f"upload_{stem}", "status": "indexed on next retrieval"}


@studio.post("/regenerate")
async def regenerate_paragraph(body: dict, user: dict = Depends(current_user)):
    """Rewrite one paragraph of a pending proposal with the Writer model; the result is claim-verified
    and still has to go through approval (and Compliance, as an edit)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from agent.llm import atext
    from agent.prompts.loader import fill
    from rag.retriever import get_pipeline
    from rag.verify import split_claims, strip_unsupported, verify_claims
    tid, para = body.get("thread_id", ""), (body.get("paragraph") or "")[:2000]
    if f":{user['id']}:" not in tid or not para.strip():
        raise HTTPException(400, "thread_id and paragraph required")
    pipe = get_pipeline(user["persona_id"])
    ev = pipe.retrieve({"title": para, "description": para}, k=4).evidence
    out, _ = await atext("writer", [SystemMessage(fill("regenerate")),
                             HumanMessage(f"Instruction: {body.get('instruction', 'improve it')}\n\nEVIDENCE:\n" +
                                          "\n".join(f"[{e.evidence_id}] {e.snippet}" for e in ev) +
                                          f"\n\nPARAGRAPH:\n{para}")], max_tokens=2000)  # gpt-oss reasons first: ~1k hidden tokens
    checked = verify_claims(split_claims(out), pipe, [e.evidence_id for e in ev])
    return {"text": strip_unsupported(out.strip(), checked),
            "removed": [c.text for c in checked if c.status == "unsupported"]}


@router.delete("/portfolio/{evidence_id}")
async def delete_evidence(evidence_id: str, user: dict = Depends(current_user)):
    """Remove one evidence item from the user's portfolio. Claims that relied on it become unsupported the
    next time a draft is verified or regenerated (viva Q3)."""
    if not re.fullmatch(r"[\w.-]{3,120}", evidence_id):
        raise HTTPException(400, "bad evidence id")
    from rag.ingest.loaders import remove_evidence
    from rag.retriever import invalidate
    remove_evidence(user["persona_id"], evidence_id)
    invalidate(user["persona_id"])
    return {"removed": evidence_id}

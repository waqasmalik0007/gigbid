"""GigBid API (FastAPI).

    uvicorn apps.api.main:app --port 8000

Every request gets a trace id (x-trace-id in/out) that flows into the graph run, every MCP call
and the audit log (E3). The OpenAPI schema at /openapi.json generates the web client.
"""
from __future__ import annotations

import os
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

import agent.llm  # noqa: F401  (loads .env)
from apps.api.auth.security import seed_reviewer
from apps.api.routers import approvals, auth, jobs, runs

app = FastAPI(title="GigBid API", version="1.0.0",
              description="Multi-agent freelancer copilot: nothing reaches a client until a human approves it.")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","), allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["x-trace-id"])


@app.middleware("http")
async def trace(request: Request, call_next):
    request.state.trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex
    resp = await call_next(request)
    resp.headers["x-trace-id"] = request.state.trace_id
    return resp


@app.on_event("startup")
def startup() -> None:
    seed_reviewer()


@app.get("/health", tags=["meta"])
def health():
    return {"ok": True}


for r in (auth.router, jobs.router, jobs.studio, approvals.router, runs.router):
    app.include_router(r, prefix="/api")

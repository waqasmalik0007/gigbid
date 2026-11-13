"""Streamable-HTTP serving for our MCP servers: bearer auth, per-user rate limit, trace-id passthrough."""
from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_servers.common.audit import current_trace, current_user


class BearerAndRateLimit(BaseHTTPMiddleware):
    def __init__(self, app, token: str, per_min: int = 120):
        super().__init__(app)
        self.token, self.per_min = token, per_min
        self.hits: dict[str, deque] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        if request.url.path.endswith("/health"):
            return JSONResponse({"ok": True})
        auth = request.headers.get("authorization", "")
        if not self.token or not hmac.compare_digest(auth.removeprefix("Bearer ").strip(), self.token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        user = request.headers.get("x-gigbid-user", "anonymous")
        q, now = self.hits[user], time.time()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_min:
            return JSONResponse({"error": "rate limited"}, status_code=429, headers={"Retry-After": "10"})
        q.append(now)
        tu, tt = current_user.set(user), current_trace.set(request.headers.get("x-trace-id", ""))
        try:
            return await call_next(request)
        finally:
            current_user.reset(tu)
            current_trace.reset(tt)


def build_app(mcp, token: str, per_min: int = 120):
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAndRateLimit, token=token, per_min=per_min)
    return app


def serve_with_bearer(mcp, port: int, token: str) -> None:
    import uvicorn
    if not token:
        raise SystemExit("MCP_BEARER_TOKEN must be set to serve over HTTP")
    uvicorn.run(build_app(mcp, token), host="0.0.0.0", port=port)

"""OAuth 2.1 (authorization code + PKCE) for the official Upwork MCP server (A4).

/auth/upwork/start   -> redirect to Upwork's authorize URL with a PKCE challenge
/auth/upwork/callback-> exchange the code, store the access token ENCRYPTED per user
The live adapter calls get_access_token(user_id); tokens never appear in logs or responses.
Endpoints come from env so they can follow Upwork's published OAuth metadata.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time

import httpx

from apps.api.auth.security import load_token, store_token

AUTHORIZE_URL = os.getenv("UPWORK_AUTHORIZE_URL", "https://www.upwork.com/ab/account-security/oauth2/authorize")
TOKEN_URL = os.getenv("UPWORK_TOKEN_URL", "https://www.upwork.com/api/v3/oauth2/token")
_pending: dict[str, tuple[str, str, float]] = {}  # state -> (user_id, verifier, ts)


def start(user_id: str, redirect_uri: str) -> str:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    _pending[state] = (user_id, verifier, time.time())
    q = httpx.QueryParams({"response_type": "code", "client_id": os.getenv("UPWORK_CLIENT_ID", ""),
                           "redirect_uri": redirect_uri, "state": state, "code_challenge": challenge,
                           "code_challenge_method": "S256"})
    return f"{AUTHORIZE_URL}?{q}"


async def callback(code: str, state: str, redirect_uri: str) -> str:
    user_id, verifier, ts = _pending.pop(state, (None, None, 0))
    if not user_id or time.time() - ts > 600:
        raise ValueError("invalid or expired OAuth state")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": code,
                                          "redirect_uri": redirect_uri, "code_verifier": verifier,
                                          "client_id": os.getenv("UPWORK_CLIENT_ID", ""),
                                          "client_secret": os.getenv("UPWORK_CLIENT_SECRET", "")})
    r.raise_for_status()
    store_token(user_id, "upwork", r.json()["access_token"])
    return user_id


def get_access_token(user_id: str) -> str | None:
    return load_token(user_id, "upwork")

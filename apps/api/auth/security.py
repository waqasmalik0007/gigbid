"""Multi-user auth: bcrypt passwords, JWT bearer sessions, Fernet-encrypted OAuth tokens (E4).

Tokens are encrypted with TOKEN_ENCRYPTION_KEY, never logged, and only decrypted inside the live
marketplace adapter. A reviewer account is seeded from REVIEWER_EMAIL / REVIEWER_PASSWORD.
"""
from __future__ import annotations

import os
import time
import uuid

import bcrypt
import jwt
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from apps.api.db import row, run, upsert

JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-change-me")
bearer = HTTPBearer(auto_error=False)
DEFAULT_SETTINGS = {"floor_rate": 30, "weekly_connects_budget": 80, "autonomy": "suggest",  # never auto-send
                    "saved_searches": [{"query": "", "category": None, "every_min": 30}], "filters": {}}


def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def check_pw(pw: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), h.encode())
    except ValueError:
        return False


def make_token(user_id: str) -> str:
    return jwt.encode({"sub": user_id, "exp": int(time.time()) + 7 * 86400}, JWT_SECRET, algorithm="HS256")


def create_user(email: str, password: str, persona_id: str, settings: dict | None = None) -> str:
    uid = "u_" + uuid.uuid4().hex[:10]
    run("INSERT INTO users (id, email, pw_hash, persona_id, settings, created) VALUES (?,?,?,?,?,?)",
        uid, email.lower(), hash_pw(password), persona_id, DEFAULT_SETTINGS | (settings or {}), time.time())
    return uid


def get_user_by_email(email: str):
    return row("SELECT * FROM users WHERE email=?", email.lower())


def current_user(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if not cred:
        raise HTTPException(401, "not signed in")
    try:
        uid = jwt.decode(cred.credentials, JWT_SECRET, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid session") from None
    u = row("SELECT * FROM users WHERE id=?", uid)
    if not u:
        raise HTTPException(401, "unknown user")
    return dict(u)


def seed_reviewer() -> None:
    email = os.getenv("REVIEWER_EMAIL", "reviewer@gigbid.local")
    pw = os.getenv("REVIEWER_PASSWORD")
    if pw and not get_user_by_email(email):
        from gigsim.seed.vocab import PERSONAS
        p = PERSONAS["ai_ml_engineer"]
        create_user(email, pw, "ai_ml_engineer", {"floor_rate": p["floor_rate"]})


# ---------------------------------------------------------------- encrypted OAuth token store
def _fernet() -> Fernet:
    key = os.getenv("TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
    return Fernet(key.encode())


def store_token(user_id: str, provider: str, token: str) -> None:
    upsert("oauth_tokens", {"user_id": user_id, "provider": provider},
           {"enc_token": _fernet().encrypt(token.encode()).decode(), "updated": time.time()})


def load_token(user_id: str, provider: str) -> str | None:
    r = row("SELECT enc_token FROM oauth_tokens WHERE user_id=? AND provider=?", user_id, provider)
    return _fernet().decrypt(r["enc_token"].encode()).decode() if r else None

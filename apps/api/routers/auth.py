from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from apps.api.auth import upwork_oauth
from apps.api.auth.security import (DEFAULT_SETTINGS, check_pw, create_user, current_user, get_user_by_email,
                                    make_token)
from apps.api.db import run
from apps.api.schemas.models import LoginIn, SettingsIn, SignupIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _out(u: dict) -> UserOut:
    return UserOut(id=u["id"], email=u["email"], persona_id=u["persona_id"], settings=u.get("settings") or {})


@router.post("/signup", response_model=TokenOut)
def signup(body: SignupIn):
    if get_user_by_email(body.email):
        raise HTTPException(409, "email already registered")
    from gigsim.seed.vocab import PERSONAS
    uid = create_user(body.email, body.password, body.persona_id,
                      {"floor_rate": PERSONAS[body.persona_id]["floor_rate"]})
    return TokenOut(token=make_token(uid), user=_out(dict(get_user_by_email(body.email))))


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn):
    u = get_user_by_email(body.email)
    if not u or not check_pw(body.password, u["pw_hash"]):
        raise HTTPException(401, "wrong email or password")
    return TokenOut(token=make_token(u["id"]), user=_out(dict(u)))


@router.get("/me", response_model=UserOut)
def me(user: dict = Depends(current_user)):
    return _out(user)


@router.put("/settings", response_model=UserOut)
def put_settings(body: SettingsIn, user: dict = Depends(current_user)):
    s = DEFAULT_SETTINGS | (user.get("settings") or {}) | body.model_dump(exclude_none=True)
    run("UPDATE users SET settings=? WHERE id=?", s, user["id"])
    return _out(user | {"settings": s})


@router.put("/persona/{persona_id}", response_model=UserOut)
def set_persona(persona_id: str, user: dict = Depends(current_user)):
    if persona_id not in ("ai_ml_engineer", "fullstack_web", "data_analyst"):
        raise HTTPException(400, "unknown persona")
    run("UPDATE users SET persona_id=? WHERE id=?", persona_id, user["id"])
    return _out(user | {"persona_id": persona_id})


@router.get("/upwork/start")
def upwork_start(user: dict = Depends(current_user)):
    if not os.getenv("UPWORK_CLIENT_ID"):
        raise HTTPException(400, "Upwork live mode is not configured on this deployment (UPWORK_CLIENT_ID)")
    return {"authorize_url": upwork_oauth.start(user["id"], os.getenv("UPWORK_REDIRECT_URI", ""))}


@router.get("/upwork/callback")
async def upwork_callback(code: str, state: str):
    await upwork_oauth.callback(code, state, os.getenv("UPWORK_REDIRECT_URI", ""))
    return RedirectResponse(os.getenv("WEB_URL", "/") + "/settings?upwork=connected")

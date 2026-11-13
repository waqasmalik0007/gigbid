"""Request/response models (the TypeScript client is generated from these via OpenAPI)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def Email():  # noqa: N802 - a fresh FieldInfo per use
    return Field(pattern=EMAIL_RE, max_length=200)


class SignupIn(BaseModel):
    email: str = Email()
    password: str = Field(min_length=8)
    persona_id: Literal["ai_ml_engineer", "fullstack_web", "data_analyst"] = "ai_ml_engineer"


class LoginIn(BaseModel):
    email: str = Email()
    password: str


class TokenOut(BaseModel):
    token: str
    user: "UserOut"


class UserOut(BaseModel):
    id: str
    email: str
    persona_id: str
    settings: dict[str, Any]


class SettingsIn(BaseModel):
    floor_rate: float | None = Field(None, ge=1, le=1000)
    weekly_connects_budget: int | None = Field(None, ge=0, le=1000)
    autonomy: Literal["suggest", "auto_skip_low_fit"] | None = None
    saved_searches: list[dict[str, Any]] | None = None
    marketplace: Literal["gigsim", "upwork_live"] | None = None


class JobRow(BaseModel):
    job_id: str
    title: str
    category: str | None = None
    status: str | None = None
    action: str | None = None
    fit: float | None = None
    risk: float | None = None
    price: float | None = None
    connects: int | None = None
    budget: str | None = None
    flags: list[str] = []
    is_scam: bool = False
    reasons: list[str] = []
    thread_id: str | None = None
    posted_at: str | None = None


class ApprovalIn(BaseModel):
    action: Literal["approve", "edit", "reject"]
    edited_text: str | None = Field(None, max_length=6000)
    reason: str | None = Field(None, max_length=500)
    slot: str | None = None


class RunOut(BaseModel):
    thread_id: str
    status: str
    kind: str | None = None
    job_id: str | None = None
    trace_id: str | None = None
    usage: dict[str, Any] = {}
    created: float | None = None
    updated: float | None = None


class Accepted(BaseModel):
    thread_id: str
    dispatch: str

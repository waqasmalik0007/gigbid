"""Append-only audit log (user, tool, arguments, result, trace id) with PII masking (R9, E4)."""
from __future__ import annotations

import contextvars
import json
import os
import re
import time
from pathlib import Path

current_user: contextvars.ContextVar[str] = contextvars.ContextVar("current_user", default="local")
current_trace: contextvars.ContextVar[str] = contextvars.ContextVar("current_trace", default="")

LOG = Path(os.getenv("GIGBID_AUDIT_LOG", Path(__file__).resolve().parents[2] / "data/processed/audit.jsonl"))

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
HANDLE = re.compile(r"(?i)\b(telegram|whatsapp|skype|signal)\b\s*[:@]?\s*@?[\w.+-]+")


def mask(text: str) -> str:
    """Mask emails, phone numbers, card/IBAN numbers and messenger handles."""
    if not isinstance(text, str):
        return text
    text = EMAIL.sub("[email]", text)
    text = CARD.sub("[card]", text)
    text = IBAN.sub("[iban]", text)
    text = HANDLE.sub(lambda m: f"{m.group(1)} [handle]", text)
    return PHONE.sub("[phone]", text)


def mask_obj(o):
    if isinstance(o, str):
        return mask(o)
    if isinstance(o, dict):
        return {k: ("[secret]" if any(s in k.lower() for s in ("token", "secret", "password", "authorization"))
                    else mask_obj(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [mask_obj(v) for v in o]
    return o


def audit(server: str, tool: str, args: dict, result, ok: bool = True) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(result, (dict, list)):
        result = str(result)[:2000]
    rec = dict(ts=time.time(), server=server, tool=tool, user=current_user.get(), trace_id=current_trace.get(),
               args=mask_obj(args), ok=ok, result=mask_obj(result))
    with LOG.open("a", encoding="utf-8") as fh:  # append-only by construction
        fh.write(json.dumps(rec, default=str) + "\n")

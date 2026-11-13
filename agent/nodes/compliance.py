"""Compliance Guard (no tools): enforces data/policy_pack/policy.yaml on any outgoing text.

Rules are data: regex patterns with an action (redact / block). Contact details, links, guarantees
and free-work offers are removed; a draft that still has unsupported claims, or that is out of the
length limits after redaction, is blocked with a reason shown in the UI.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from agent.handoffs import ComplianceVerdict
from agent.llm import config
from agent.nodes.common import node

ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def policy() -> dict:
    return yaml.safe_load((ROOT / config()["policy_pack_path"] / "policy.yaml").read_text())


def check_text(text: str, claims=None, for_message: bool = False) -> ComplianceVerdict:
    pol = policy()
    violations, redactions = [], []
    cleaned = text
    for rule in pol["rules"]:
        if rule["action"] == "redact":
            for pat in rule.get("patterns", []):
                for m in re.finditer(pat, cleaned):
                    redactions.append(m.group(0))
                if re.search(pat, cleaned):
                    violations.append(rule["id"])
                cleaned = re.sub(pat, "", cleaned)
        elif rule["action"] == "block_if_unsupported_claims" and claims:
            bad = [c.text for c in claims if c.status == "unsupported" and c.text in cleaned]
            if bad:
                return ComplianceVerdict(allowed=False, violations=[rule["id"]], cleaned_text=cleaned,
                                         blocked_reason=f"unsupported claims still present: {bad[:2]}")
    cleaned = re.sub(r"\s{2,}", " ", re.sub(r"\s+([.,;:])", r"\1", cleaned)).strip()
    words = len(cleaned.split())
    lim = pol.get("limits", {})
    if not for_message:
        if lim.get("max_cover_letter_words") and words > lim["max_cover_letter_words"]:
            return ComplianceVerdict(allowed=False, violations=sorted(set(violations)) + ["too_long"],
                                     redactions=redactions, cleaned_text=cleaned,
                                     blocked_reason=f"{words} words > policy limit {lim['max_cover_letter_words']}")
        if lim.get("min_cover_letter_words") and words < lim["min_cover_letter_words"]:
            return ComplianceVerdict(allowed=False, violations=sorted(set(violations)) + ["too_short"],
                                     redactions=redactions, cleaned_text=cleaned,
                                     blocked_reason=f"only {words} words left after verification/redaction")
    return ComplianceVerdict(allowed=True, violations=sorted(set(violations)), redactions=redactions,
                             cleaned_text=cleaned)


@node("compliance")
async def compliance(state: dict) -> dict:
    draft = state["draft"]
    approval = state.get("approval") or {}
    text = approval.get("edited_text") if approval.get("action") == "edit" else draft.cover_letter
    verdict = check_text(text, draft.claims)
    answers = [check_text(a, for_message=True).cleaned_text for a in draft.screening_answers]
    out = {"compliance": verdict, "draft": draft.model_copy(update={"screening_answers": answers})}
    if not verdict.allowed:
        out["status"] = "blocked"
    return out

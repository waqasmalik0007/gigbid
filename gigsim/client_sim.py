"""Simulated client: decides view / reply / interview / hire from hidden preferences.

Deterministic given (proposal_id, seed) so reply-rate evaluations are reproducible.
Optionally an LLM can add a judgement (GIGSIM_CLIENT_LLM=groq); the heuristic is always the
base so GigSim runs with no API key.
"""
from __future__ import annotations

import hashlib
import math
import os
import random
import re

GENERIC_MARKERS = [
    "dear sir", "i am very passionate", "perfect fit", "100% satisfaction", "i have read your job",
    "expert with many years", "hello, i", "to whom it may concern",
]


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16))


def score_proposal(job: dict, client: dict, proposal: dict) -> dict:
    hidden = client["hidden"]
    text = (proposal.get("cover_letter") or "").lower()
    first = text.strip().split(".")[0] if text.strip() else ""
    must = [m.lower() for m in hidden["must_have"]]
    job_skills = [s.lower() for s in job.get("skills", [])]
    covered_must = sum(1 for m in must if m in text)
    covered_job = sum(1 for s in job_skills if s in text) / max(1, len(job_skills))
    generic = any(g in first for g in GENERIC_MARKERS) or len(first) < 15
    words = len(text.split())
    has_numbers = bool(re.search(r"\d+%|\d+ (users|teams|minutes|hours|bookings|questions)", text))
    budget_cap = (job.get("budget_max") or 0) * hidden["budget_ceiling_factor"]
    amount = float(proposal.get("amount") or 0)
    over_budget = budget_cap > 0 and amount > budget_cap

    z = -1.2
    z += 0.9 * covered_must + 1.2 * covered_job
    z += 0.6 if has_numbers else 0
    z -= 1.6 if (generic and hidden["dislikes_generic_opening"]) else (0.4 if generic else 0)
    z -= 0.8 if (hidden["prefers_short"] and words > 220) else 0
    z -= 0.5 if words < 40 else 0
    z -= 1.5 if over_budget else 0
    p_reply = (1 / (1 + math.exp(-z))) * hidden["responsiveness"]
    return dict(p_reply=round(p_reply, 4), covered_must=covered_must, covered_job=round(covered_job, 2),
                generic=generic, words=words, over_budget=over_budget, has_numbers=has_numbers)


def decide(job: dict, client: dict, proposal: dict, seed: int = 0) -> dict:
    s = score_proposal(job, client, proposal)
    r = _rng(proposal["proposal_id"], seed)
    viewed = r.random() < min(1.0, 0.4 + s["p_reply"] + 0.3 * client["hidden"]["responsiveness"])
    replied = viewed and r.random() < s["p_reply"] / max(0.05, 0.4 + 0.3 * client["hidden"]["responsiveness"]) * 0.9
    interview = replied and r.random() < 0.55
    hired = interview and r.random() < 0.45
    outcome = "hired" if hired else "interview" if interview else "replied" if replied else \
        "viewed" if viewed else "ignored"
    reply_text = None
    if replied:
        reply_text = r.choice([
            "Thanks for the proposal. Can you tell me more about how you would approach the first milestone?",
            "Your past project looks relevant. Are you available for a 30 minute call this week?",
            "What would the timeline look like if we cut scope to the essentials?",
            "Could you do it for a bit less? Our budget is tight this month.",
        ])
        if os.getenv("GIGSIM_CLIENT_LLM") == "groq":
            reply_text = _llm_reply(job, proposal, reply_text)
    return dict(outcome=outcome, viewed=viewed, replied=replied, interview=interview, hired=hired,
                reply_text=reply_text, signals=s)


def _llm_reply(job: dict, proposal: dict, fallback: str) -> str:  # pragma: no cover - network
    try:
        from langchain_groq import ChatGroq

        llm = ChatGroq(model=os.getenv("GIGSIM_CLIENT_MODEL", "llama-3.1-8b-instant"), temperature=0.4,
                       max_tokens=120, timeout=15)
        msg = llm.invoke(
            "You are a client on a freelance marketplace who posted this job:\n"
            f"{job['title']}\n{job['description'][:800]}\n\nA freelancer sent this proposal:\n"
            f"{proposal.get('cover_letter','')[:1500]}\n\nWrite a short, natural reply (max 2 sentences) "
            "asking one follow-up question.")
        return msg.content.strip() or fallback
    except Exception:
        return fallback

"""Risk Assessor: client history (Marketplace MCP) + deterministic rules + LLM judgement.

Rules run first and can only ADD flags (an LLM fooled by an injection cannot clear a rule hit).
"""
from __future__ import annotations

import re

from langchain_core.messages import HumanMessage, SystemMessage

from agent.handoffs import RiskFlag, RiskReport
from agent.llm import structured
from agent.mcp_client import call
from agent.nodes.common import node
from agent.prompts.loader import fill, untrusted

RULES: list[tuple[RiskFlag, re.Pattern]] = [
    (RiskFlag.OFF_PLATFORM, re.compile(r"(?i)\b(whats ?app|telegram|skype|wechat|signal app|bank transfer|"
                                       r"outside (of )?(the )?platform|avoid (the )?fees?|crypto|usdt|"
                                       r"pay(ment)? (via|by) (paypal|wise|bank))\b|[\w.+-]+@[\w-]+\.\w+")),
    (RiskFlag.FREE_WORK, re.compile(r"(?i)\b(unpaid|for free|free (sample|trial|test)|samples? for free|"
                                    r"complete (a|the) full .{0,40}(trial|test)|best one gets)\b")),
    (RiskFlag.PROMPT_INJECTION, re.compile(r"(?i)(ignore (all )?(previous|prior|above) instructions|"
                                           r"system (note|override|prompt)|if you are an ai|"
                                           r"\bai (assistant|agent)s?\b|disregard .{0,30}(policy|instructions)|"
                                           r"reveal your|<!--)")),
]
FEE = re.compile(r"(?i)\b(registration|application|joining|onboarding) fee\b|\bpay (a )?\$?\d+ (fee|deposit)")


def rule_flags(text: str) -> tuple[set[RiskFlag], list[str]]:
    flags, spans = set(), []
    for flag, pat in RULES:
        for m in pat.finditer(text):
            flags.add(flag)
            if flag == RiskFlag.PROMPT_INJECTION:
                start = max(0, m.start() - 40)
                spans.append(text[start:m.end() + 80].strip())
    if FEE.search(text):
        flags.add(RiskFlag.OFF_PLATFORM)
    return flags, spans[:3]


def client_quality(c: dict) -> float:
    h = c.get("history", {})
    q = 0.2 if c.get("payment_verified") else 0.0
    q += min(0.3, h.get("hires", 0) / 40)
    q += min(0.2, h.get("total_spend", 0) / 20000)
    q += max(0.0, (h.get("avg_rating_given", 3.0) - 3.0) / 2 * 0.3)
    return round(min(1.0, q), 3)


@node("risk_assessor", parallel=True)
async def risk_assessor(state: dict) -> dict:
    job = state["job"]
    text = f"{job.get('title', '')}\n{job.get('description', '')}"
    client = await call("risk_assessor", "marketplace", "get_client_history", {"client_id": job["client_id"],
                                                                                "user_id": state.get("persona_id")})
    cq = client_quality(client)
    flags, spans = rule_flags(text)
    if cq < 0.25:
        flags.add(RiskFlag.LOW_CLIENT_QUALITY)
    if (job.get("budget_max") or 0) < 20 and job.get("budget_type") == "fixed":
        flags.add(RiskFlag.UNREALISTIC_BUDGET)
    llm, usage = structured("risk_assessor", RiskReport, [
        SystemMessage(fill("risk_assessor")),
        HumanMessage(f"job_id: {job['job_id']}\nRule-based flags already found: {sorted(f.value for f in flags)}\n"
                     f"Client history: {client.get('history')} payment_verified={client.get('payment_verified')}\n\n"
                     + untrusted("untrusted_job_post", text[:4000])),
    ])
    all_flags = sorted(flags | set(llm.flags), key=lambda f: f.value)
    rule_scam = bool(flags & {RiskFlag.OFF_PLATFORM, RiskFlag.FREE_WORK})
    score = max(llm.risk_score, 0.85 if rule_scam else 0.0, 0.5 if RiskFlag.PROMPT_INJECTION in flags else 0.0,
                0.6 if RiskFlag.LOW_CLIENT_QUALITY in flags else 0.0)
    report = RiskReport(job_id=job["job_id"], risk_score=round(score, 3), is_scam=llm.is_scam or rule_scam,
                        flags=all_flags, injection_spans=(spans + llm.injection_spans)[:4], client_quality=cq,
                        explanation=llm.explanation)
    return {"risk_report": report, "_usage": usage}

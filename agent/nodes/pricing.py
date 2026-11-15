"""Decision node and Pricing Strategist.

decide: rules over the three parallel reports; every threshold comes from agent/config.yaml.
pricing: market quartiles (Marketplace MCP; real Kaggle Upwork data) + client quality + the user's
floor -> amount inside the job's budget; Connects are worth it only if expected value is positive
and the weekly Connects budget allows it. Deterministic on purpose: explainable, free, and its
price-in-fair-band rate is measured by evals/runners/decision_eval.py.
"""
from __future__ import annotations

from agent.handoffs import BidDecision, PriceQuote
from agent.llm import config
from agent.mcp_client import call
from agent.nodes.common import node

USD_PER_CONNECT = 0.15


def _persona_floor(persona: str | None) -> float:
    """The persona's own floor rate when the user has not set one (failure analysis: evaluations ran with
    empty settings and priced at a hard-coded $30 floor, below the ML persona's $40)."""
    import json
    from pathlib import Path
    f = Path(__file__).resolve().parents[2] / "data" / "personas" / str(persona) / "profile.json"
    try:
        return float(json.loads(f.read_text(encoding="utf-8")).get("floor_rate", 30))
    except (OSError, ValueError):
        return 30.0


@node("decide")
async def decide(state: dict) -> dict:
    d = config()["decision"]
    if state.get("halts"):  # a parallel branch failed: surface the most severe outcome
        sev = ["needs_attention", "dead_letter"]
        return {"status": max(state["halts"], key=lambda h: sev.index(h) if h in sev else -1)}
    risk, fit, spec = state["risk_report"], state["fit_report"], state.get("job_spec")
    reasons = [f"fit {fit.fit_score:.2f}", f"risk {risk.risk_score:.2f}"]
    if risk.is_scam:
        action, reasons = "skip", reasons + [f"scam signals: {[f.value for f in risk.flags]}"]
    elif risk.risk_score >= d.get("max_risk", 0.7):
        action, reasons = "skip", reasons + ["risk above threshold"]
    elif fit.fit_score >= d["bid_threshold"]:
        action = "bid"
        reasons += [f"strong evidence for {len(fit.matched_skills)} of {len(fit.matrix)} requirements"]
    elif fit.fit_score >= d["watch_threshold"]:
        action, reasons = "watch", reasons + [f"missing evidence for: {fit.missing_skills}"]
    else:
        action, reasons = "skip", reasons + [f"weak fit; missing {fit.missing_skills}"]
    job = state.get("job") or {}
    floor = float((state.get("settings") or {}).get("floor_rate") or _persona_floor(state.get("persona_id")))
    if action == "bid" and job.get("budget_type") == "hourly" and job.get("budget_max") and job["budget_max"] < floor:
        # failure analysis: bidding at our floor would exceed what the client said they pay
        action, reasons = "watch", reasons + [f"client max ${job['budget_max']}/h is below floor ${floor:g}/h"]
    if spec and spec.category not in ("other", None) and action == "bid":
        reasons.append(f"category {spec.category}")
    conf = abs(fit.fit_score - d["bid_threshold"]) + (0.3 if risk.is_scam else 0)
    out = {"decision": BidDecision(job_id=state["job_id"], action=action, confidence=round(min(1, 0.5 + conf), 2),
                                   reasons=reasons)}
    if action != "bid":
        out["status"] = "skipped"
    return out


@node("pricing")
async def pricing(state: dict) -> dict:
    job, fit, risk = state["job"], state["fit_report"], state["risk_report"]
    user = state.get("persona_id")
    btype = job.get("budget_type") if job.get("budget_type") in ("fixed", "hourly") else "fixed"
    stats = await call("pricing", "marketplace", "market_stats", {"category": job["category"], "user_id": user})
    q = stats.get(btype) or {}
    bal = (await call("pricing", "marketplace", "get_connects_balance", {"user_id": user}))["balance"]
    floor = float(state.get("settings", {}).get("floor_rate") or _persona_floor(user))
    lo, hi = job.get("budget_min") or 0, job.get("budget_max") or 0
    if btype == "hourly":
        target = q.get("median", floor) + (q.get("p75", floor) - q.get("median", floor)) * fit.fit_score
        if hi:
            target = min(max(target, lo or target), hi)
        amount = max(target, floor)
    else:
        if hi:
            amount = lo + (hi - lo) * (0.55 + 0.4 * fit.fit_score) if lo else hi * (0.8 + 0.15 * fit.fit_score)
        else:
            amount = q.get("median", 500)
    band = (round(lo or q.get("p25", amount * 0.8), 2), round(hi or q.get("p75", amount * 1.2), 2))
    p_reply = min(0.6, 0.05 + 0.45 * fit.fit_score * (0.5 + risk.client_quality))
    p_hire = 0.35 * p_reply
    value = amount * (40 if btype == "hourly" else 1)  # hourly: assume a 40h engagement
    cost = int(job.get("connects_cost", 8))
    ev = p_hire * value * 0.1 - cost * USD_PER_CONNECT  # 10% of contract value as margin proxy
    weekly_left = state.get("settings", {}).get("connects_left_this_week", bal)
    worth = ev > 0 and cost <= min(bal, weekly_left)
    rationale = (f"{btype} market median {q.get('median')} (p25 {q.get('p25')}, p75 {q.get('p75')}, "
                 f"n={q.get('n')}, {stats.get('source', 'gigsim')}); job budget {lo}-{hi}; fit {fit.fit_score:.2f} "
                 f"moves the bid toward the top of the band; floor {floor}. Est. reply {p_reply:.0%}, "
                 f"hire {p_hire:.0%}; EV {ev:+.2f} USD vs {cost} Connects (balance {bal}).")
    quote = PriceQuote(job_id=job["job_id"], amount=round(amount, 2), budget_type=btype, fair_band=band,
                       connects_cost=cost, connects_worth_it=worth, expected_value=round(ev, 2), rationale=rationale)
    out = {"price": quote}
    if not worth:
        out["status"] = "skipped"
        out["errors"] = [f"Connects not worth it: EV {ev:.2f}, cost {cost}, balance {bal}"]
    return out

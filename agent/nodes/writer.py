"""Proposal Writer, claim Verifier and Critic.

writer   -> ProposalDraft citing evidence ids (Portfolio MCP for extra evidence; memories in prompt)
verifier -> splits claims, checks each against evidence (rag/verify.py), removes unsupported ones
           BEFORE the Critic sees the draft; records unsupported rate before/after (B3)
critic   -> Critique; sends the draft back to the writer at most `critic_max_loops` times (C4)
"""
from __future__ import annotations

import re

from langchain_core.messages import HumanMessage, SystemMessage

from agent.handoffs import Claim, Critique, ProposalDraft
from agent.llm import astructured, config
from agent.mcp_client import call
from agent.memory.promotion_rules import outcome_memory, style_memory
from agent.nodes.common import node
from agent.prompts.loader import fill, untrusted
from rag.verify import split_claims, strip_unsupported, unsupported_rate, verify_claims

CITE = re.compile(r"\[([a-z]+_[\w-]+)\]")


def experiment_arm(job_id: str) -> tuple[str | None, str]:
    """X2 A/B: deterministic arm per job (hash of job_id) from config experiments.proposal_opening."""
    import hashlib
    exp = (config().get("experiments") or {}).get("proposal_opening") or {}
    if not exp.get("enabled"):
        return None, ""
    arms = sorted(exp["arms"])
    arm = arms[int(hashlib.sha1(job_id.encode()).hexdigest(), 16) % len(arms)]
    return arm, exp["arms"][arm]


def _evidence_block(evidence) -> str:
    return "\n".join(f"[{e.evidence_id}] ({e.type}{', outcome ' + e.outcome if e.outcome else ''}) "
                     f"{e.title}: {e.snippet}" for e in evidence)


@node("writer")
async def writer(state: dict) -> dict:
    job, spec, persona = state["job"], state.get("job_spec"), state["persona_id"]
    user = state.get("user_id", persona)
    evidence = list(state.get("evidence", []))[:10]
    won = await call("writer", "portfolio", "search_evidence", {
        "requirement": job["title"], "k": 2, "persona": persona,
        "filters": {"type": "past_proposal", "outcome": ["won", "replied"]}})
    examples = "\n".join(f"- ({i['outcome']}) {i['snippet'][:250]}" for i in won["items"])
    critiques = state.get("critiques") or []
    feedback = ""
    if critiques:
        c = critiques[-1]
        feedback = (f"REVISION {state.get('revisions', 0)}: the Critic rejected the previous draft. Issues: "
                    f"{c.issues}. Instructions: {c.instructions_for_writer}\nPrevious draft:\n"
                    f"{state['draft'].cover_letter}")
    style = "\n".join(f"- {s['text']}" for s in style_memory(user)) or "- (none learned yet)"
    outcome = "\n".join(f"- {o['text']}" for o in outcome_memory(user, job.get("category"))) or "- (not enough data yet)"
    from gigsim.seed.vocab import PERSONAS
    arm, arm_text = experiment_arm(job["job_id"])
    sys = fill("writer", persona_name=PERSONAS.get(persona, {}).get("name", persona), style_memory=style,
               outcome_memory=outcome, critic_feedback=f"{arm_text}\n\n{feedback}".strip())
    human = (f"JobSpec: {spec.model_dump_json() if spec else '{}'}\n"
             f"Price: {state['price'].amount} ({state['price'].budget_type})\n"
             f"Screening questions: {job.get('screening_questions')}\n\n"
             f"EVIDENCE (cite these ids only):\n{_evidence_block(evidence)}\n\n"
             f"Phrasing that worked before (NOT evidence, do not cite):\n{examples}\n\n"
             + untrusted("untrusted_job_post", f"{job['title']}\n{job.get('description', '')[:3000]}"))
    draft, usage = await astructured("writer", ProposalDraft, [SystemMessage(sys), HumanMessage(human)],
                              temperature=0.4, max_tokens=2500)
    draft.job_id = job["job_id"]
    draft.revision = state.get("revisions", 0)
    draft.evidence_ids = sorted(set(CITE.findall(draft.cover_letter)) | set(draft.evidence_ids))
    return {"draft": draft, "experiment_arm": arm, "_usage": usage}


@node("verifier")
async def verifier(state: dict) -> dict:
    from rag.retriever import get_pipeline
    draft: ProposalDraft = state["draft"]
    pipe = get_pipeline(state["persona_id"], "v2_hybrid")
    text = CITE.sub("", draft.cover_letter)
    claims_text = split_claims(text)
    cited = draft.evidence_ids
    checked = verify_claims(claims_text, pipe, cited)
    before = unsupported_rate(checked)
    # remove unsupported sentences from the (citation-bearing) letter
    bad = {c.text for c in checked if c.status == "unsupported"}
    kept_sents = [s for s in re.split(r"(?<=[.!?])\s+", draft.cover_letter) if CITE.sub("", s).strip() not in bad]
    cleaned = " ".join(kept_sents).strip()
    answers = [strip_unsupported(a, verify_claims(split_claims(a), pipe, cited)) for a in draft.screening_answers]
    after_claims = [c for c in checked if c.status != "unsupported"]
    new = draft.model_copy(update={"cover_letter": cleaned, "screening_answers": answers, "claims": checked})
    metrics = {"claims": len(checked), "unsupported_before": round(before, 3),
               "unsupported_after": round(unsupported_rate(after_claims), 3),
               "removed": sorted(bad)}
    return {"draft": new, "verification": metrics}


@node("critic")
async def critic(state: dict) -> dict:
    draft, job = state["draft"], state["job"]
    ver = state.get("verification", {})
    crit, usage = await astructured("critic", Critique, [
        SystemMessage(fill("critic")),
        HumanMessage(f"JobSpec: {state['job_spec'].model_dump_json() if state.get('job_spec') else ''}\n"
                     f"Verification: {ver}\nClaims: {[c.model_dump() for c in draft.claims]}\n"
                     f"Word count: {len(draft.cover_letter.split())}\n\nDRAFT:\n{draft.cover_letter}\n\n"
                     f"SCREENING ANSWERS: {draft.screening_answers}\n\n"
                     + untrusted("untrusted_job_post", job.get("description", "")[:2000])),
    ])
    crit.job_id = job["job_id"]
    words = len(draft.cover_letter.split())
    crit.length_ok = 90 <= words <= 260
    crit.passed = bool(crit.passed and crit.length_ok)
    out = {"critiques": [crit], "_usage": usage}
    if not crit.passed:
        out["revisions"] = state.get("revisions", 0) + 1
    return out


def after_critic(state: dict) -> str:
    if state.get("status") in ("needs_attention", "dead_letter"):
        return "end"
    last = state["critiques"][-1]
    if not last.passed and state.get("revisions", 0) <= config()["budgets"]["critic_max_loops"]:
        return "writer"
    return "compliance"


__all__ = ["writer", "verifier", "critic", "after_critic", "Claim"]

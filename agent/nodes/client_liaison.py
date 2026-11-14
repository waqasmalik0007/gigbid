"""Client Liaison nodes for the conversation graph.

read_thread -> classify_intent -> (interview_request: free_slots) -> draft_reply -> compliance
-> approval (interrupt) -> send (+ tentative interview hold). Client text is untrusted.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from agent.handoffs import MessageIntent, ReplyDraft
from agent.llm import astructured, atext
from agent.mcp_client import call
from agent.memory.promotion_rules import record_edit, record_outcome
from agent.nodes.common import node
from agent.nodes.compliance import check_text
from agent.nodes.risk_assessor import rule_flags
from agent.prompts.loader import fill, untrusted


def _conversation(msgs: list[dict]) -> str:
    return "\n".join(f"{m['sender'].upper()}: {m['body']}" for m in msgs[-10:])


@node("read_thread")
async def read_thread(state: dict) -> dict:
    msgs = await call("liaison", "marketplace", "list_messages", {"thread_id": state["thread_id"].split(":")[-1],
                                                                  "user_id": state["persona_id"]})
    return {"messages": msgs["items"], "status": "running"}


@node("classify_intent")
async def classify_intent(state: dict) -> dict:
    last = next((m for m in reversed(state["messages"]) if m["sender"] == "client"), None)
    if last is None:
        return {"status": "skipped", "errors": ["no client message to answer"]}
    flags, _ = rule_flags(last["body"])
    intent, usage = await astructured("liaison", MessageIntent, [
        SystemMessage(fill("liaison_intent")),
        HumanMessage(untrusted("untrusted_client_message", last["body"])),
    ])
    if flags:  # rule hits (off-platform, fees, injection) override the LLM
        intent = MessageIntent(intent="scam_signal", confidence=1.0,
                               summary=f"rule flags {[f.value for f in flags]}; {intent.summary}")
    return {"intent": intent, "_usage": usage}


@node("find_slots")
async def find_slots(state: dict) -> dict:
    if state["intent"].intent != "interview_request":
        return {"slots": []}
    slots = await call("liaison", "workspace", "free_slots", {"user_id": state["persona_id"], "days_ahead": 5})
    return {"slots": slots[:3]}


@node("draft_reply")
async def draft_reply(state: dict) -> dict:
    from gigsim.seed.vocab import PERSONAS
    from rag.retriever import get_pipeline
    persona = state["persona_id"]
    intent = state["intent"]
    pipe = get_pipeline(persona)
    last = next(m for m in reversed(state["messages"]) if m["sender"] == "client")
    ev = pipe.retrieve({"title": last["body"], "description": last["body"]}, k=3,
                       filters={"type": ["case_study", "review"]}).evidence
    sys = fill("liaison_reply", persona_name=PERSONAS.get(persona, {}).get("name", persona), intent=intent.intent,
               floor_rate=state.get("settings", {}).get("floor_rate", PERSONAS.get(persona, {}).get("floor_rate", 30)),
               slots=[s["start"] for s in state.get("slots", [])] or "none",
               evidence="\n".join(f"[{e.evidence_id}] {e.snippet[:200]}" for e in ev))
    body, usage = await atext("liaison", [SystemMessage(sys),
                                   HumanMessage(untrusted("untrusted_client_message", _conversation(state["messages"])))],
                       temperature=0.3, max_tokens=2000)  # gpt-oss reasons first: ~1k hidden tokens
    from rag.verify import split_claims, strip_unsupported, verify_claims
    checked = verify_claims(split_claims(body), pipe, [e.evidence_id for e in ev])  # R5 applies to replies too
    body = strip_unsupported(body, checked)
    reply = ReplyDraft(thread_id=state["thread_id"], body=body.strip(),
                       proposed_slots=[s["start"] for s in state.get("slots", [])], intent=intent)
    verdict = check_text(reply.body, for_message=True)
    removed = [c.text for c in checked if c.status == "unsupported"]
    return {"reply": reply.model_copy(update={"body": verdict.cleaned_text}), "compliance": verdict,
            "errors": [f"removed unsupported claim: {r}" for r in removed], "_usage": usage}


async def approve_reply(state: dict) -> dict:
    r = state["reply"]
    first_slot = r.proposed_slots[0] if r.proposed_slots else None
    decision = interrupt({"kind": "message", "thread_id": state["thread_id"], "conversation": state["messages"][-10:],
                          "reply": r.model_dump(), "compliance": state["compliance"].model_dump(),
                          "dry_run": {"would_send": r.body, "connects_cost": 0,
                                      "interview_hold": first_slot}})
    if decision.get("action") == "reject":
        return {"approval": decision, "status": "rejected"}
    if decision.get("action") == "edit":
        record_edit(state.get("user_id", state["persona_id"]), r.body, decision.get("edited_text", ""), state["thread_id"])
        v = check_text(decision.get("edited_text", ""), for_message=True)
        r = r.model_copy(update={"body": v.cleaned_text})
        return {"approval": decision, "reply": r, "compliance": v, "status": "approved"}
    return {"approval": decision | {"slot": decision.get("slot", first_slot)}, "status": "approved"}


@node("send_reply", retries=3)
async def send_reply(state: dict) -> dict:
    tid, user = state["thread_id"], state["persona_id"]
    thread = tid.split(":")[-1]
    n = len(state["messages"])
    sent = await call("liaison", "marketplace", "send_message", {
        "thread_id": thread, "body": state["reply"].body, "idempotency_key": f"{tid}:reply:{n}", "user_id": user})
    out = {"sent": sent, "status": "submitted"}
    slot = (state.get("approval") or {}).get("slot")
    if state["intent"].intent == "interview_request" and slot:
        from datetime import datetime, timedelta
        end = (datetime.fromisoformat(slot) + timedelta(minutes=30)).isoformat()
        out["sent"] = sent | {"hold": await call("liaison", "workspace", "create_interview_hold", {
            "user_id": user, "start": slot, "end": end, "title": f"Client interview ({thread})",
            "idempotency_key": f"{tid}:hold:{n}"})}
    if state["intent"].intent in ("interview_request", "negotiation", "question"):
        record_outcome(state.get("user_id", user), state.get("category", "other"), state["reply"].body, "replied")
    return out

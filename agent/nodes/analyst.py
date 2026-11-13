"""Job Analyst: untrusted job post -> JobSpec (no tools)."""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from agent.handoffs import JobSpec
from agent.llm import structured
from agent.nodes.common import node
from agent.prompts.loader import fill, untrusted


@node("job_analyst", parallel=True)
async def job_analyst(state: dict) -> dict:
    job = state["job"]
    post = (f"Title: {job.get('title')}\nBudget: {job.get('budget_type')} {job.get('budget_min')}-"
            f"{job.get('budget_max')}\nScreening questions: {job.get('screening_questions')}\n\n"
            f"{job.get('description', '')[:4000]}")
    spec, usage = structured("job_analyst", JobSpec, [
        SystemMessage(fill("job_analyst")),
        HumanMessage(f"job_id: {job['job_id']}\n" + untrusted("untrusted_job_post", post)),
    ])
    spec.job_id = job["job_id"]
    if not spec.screening_questions:
        spec.screening_questions = job.get("screening_questions") or []
    return {"job_spec": spec, "_usage": usage}

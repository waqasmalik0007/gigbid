"""R&D question A: does tool granularity change agent behaviour?

    python -m evals.runners.tool_design            # 40 tasks x 3 tool sets

Tasks (evals/datasets/tool_tasks.jsonl, generated from real GigSim/portfolio ids) each have the
acceptable tool name(s) per tool set and the argument that must be right. The model sees the task and
ONE tool set (bound as tools) and must make the first call; we score:
  selection accuracy  - first call uses an acceptable tool
  argument accuracy   - the key argument is correct (exact id / category, or the keyword for search)
  tokens per task     - prompt + completion tokens (tool schemas are part of the prompt)
Tool sets:
  fine   - 14 narrow tools (one per field/source)
  coarse - 2 tools with an `action` string and free-form `params`
  best   - the shipped MCP tool surface (8 tools) with descriptions rewritten for selection
No tools are executed; only the model's decision is measured. Results: evals/results/tool_design.json
"""
from __future__ import annotations

import json
import random
import statistics
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "evals/datasets/tool_tasks.jsonl"


def _noop(**kw):  # never executed
    return kw


def T(name: str, desc: str, schema: type[BaseModel]) -> StructuredTool:
    return StructuredTool.from_function(func=_noop, name=name, description=desc, args_schema=schema)


class Q(BaseModel):
    query: str


class Id(BaseModel):
    id: str


class Cat(BaseModel):
    category: str = Field(description="ml_engineering | web_dev | data_analysis")


class Empty(BaseModel):
    pass


class Act(BaseModel):
    action: str
    params: dict = Field(default_factory=dict)


class SearchEv(BaseModel):
    requirement: str = Field(description="one job requirement, e.g. 'built a RAG system over PDFs'")
    k: int = 5
    filters: dict | None = Field(None, description='e.g. {"type": ["case_study"]} or {"outcome": "won"}')


class JobId(BaseModel):
    job_id: str


class ClientId(BaseModel):
    client_id: str


class EvId(BaseModel):
    evidence_id: str


class Search(BaseModel):
    query: str = ""
    category: str | None = None


FINE = [
    T("search_case_studies", "Search case studies.", Q), T("search_repos", "Search repository READMEs and code.", Q),
    T("search_reviews", "Search client reviews.", Q), T("search_past_proposals", "Search past proposals.", Q),
    T("get_case_study_text", "Get a case study.", Id), T("list_portfolio_skills", "List skills.", Empty),
    T("get_job_title", "Get a job's title.", Id), T("get_job_description", "Get a job's description.", Id),
    T("get_job_budget", "Get a job's budget.", Id), T("get_client_hires", "Get how many hires a client made.", Id),
    T("get_client_spend", "Get a client's total spend.", Id), T("get_connects", "Get Connects.", Empty),
    T("get_market_median", "Get median rate for a category.", Cat), T("find_jobs", "Find jobs.", Q),
]
COARSE = [
    T("portfolio", "Portfolio operations. action: search|get|skills. params: query, id.", Act),
    T("marketplace", "Marketplace operations. action: jobs|job|client|connects|stats. params: query, id, category.", Act),
]
BEST = [
    T("search_evidence", "Find portfolio evidence (case studies, repos, reviews, past proposals with outcomes) for ONE "
      "job requirement. Use for any 'have I done X / prove X / find my work on X' request. Narrow with filters.",
      SearchEv),
    T("get_case_study", "Full text of ONE evidence item by its evidence_id (e.g. 'case_proj_…'), plus related items.",
      EvId),
    T("list_skills", "Skills the portfolio can evidence, with counts. Use for 'what can I prove / my skills'.", Empty),
    T("search_jobs", "Search open jobs by keywords and/or category. Use to FIND jobs, not to read one.", Search),
    T("get_job", "Read ONE job (title, description, budget, skills, screening questions) by job_id ('job_…').", JobId),
    T("get_client_history", "A client's hires, total spend, ratings and payment verification by client_id ('cli_…'). "
      "Use for any question about a client's quality or history.", ClientId),
    T("get_connects_balance", "The freelancer's current Connects balance.", Empty),
    T("market_stats", "Budget/rate quartiles (p25/median/p75) for a category: ml_engineering, web_dev or "
      "data_analysis. Use for pricing questions.", Cat),
]
SETS = {"fine": FINE, "coarse": COARSE, "best": BEST}


def make_tasks(seed: int = 3) -> list[dict]:
    rng = random.Random(seed)
    jobs = json.loads((ROOT / "data/gigsim/jobs_history.json").read_text())[:200]
    ev = json.loads((ROOT / "data/personas/ai_ml_engineer/evidence_index.json").read_text())
    cases = [e for e in ev if e["type"] == "case_study"]
    tasks = []
    for j in rng.sample(jobs, 8):
        tasks.append({"task": f"What is the budget of job {j['job_id']}?", "arg": j["job_id"],
                      "ok": {"fine": ["get_job_budget"], "coarse": ["marketplace"], "best": ["get_job"]}})
    for j in rng.sample(jobs, 8):
        tasks.append({"task": f"Is the client {j['client_id']} trustworthy? Check how many people they hired and spent.",
                      "arg": j["client_id"],
                      "ok": {"fine": ["get_client_hires", "get_client_spend"], "coarse": ["marketplace"],
                             "best": ["get_client_history"]}})
    for kw in ["RAG", "computer vision", "Docker", "FastAPI", "churn prediction", "semantic search", "LangGraph",
               "fine-tuning"]:
        tasks.append({"task": f"Have I ever built something with {kw}? Find proof in my portfolio.", "arg": kw,
                      "ok": {"fine": ["search_case_studies", "search_repos", "search_reviews"],
                             "coarse": ["portfolio"], "best": ["search_evidence"]}})
    for c in rng.sample(cases, 6):
        tasks.append({"task": f"Show me the full write-up of {c['evidence_id']}.", "arg": c["evidence_id"],
                      "ok": {"fine": ["get_case_study_text"], "coarse": ["portfolio"], "best": ["get_case_study"]}})
    for cat, name in [("web_dev", "web development"), ("data_analysis", "data analysis"),
                      ("ml_engineering", "machine learning engineering")]:
        tasks.append({"task": f"What do clients usually pay for {name} work?", "arg": cat,
                      "ok": {"fine": ["get_market_median"], "coarse": ["marketplace"], "best": ["market_stats"]}})
    tasks += [{"task": "How many Connects do I have left?", "arg": None,
               "ok": {"fine": ["get_connects"], "coarse": ["marketplace"], "best": ["get_connects_balance"]}}] * 2
    tasks += [{"task": "Which skills can my portfolio actually back up?", "arg": None,
               "ok": {"fine": ["list_portfolio_skills"], "coarse": ["portfolio"], "best": ["list_skills"]}}] * 2
    for kw in ["Shopify", "dashboard", "scraping"]:
        tasks.append({"task": f"Find open jobs about {kw}.", "arg": kw,
                      "ok": {"fine": ["find_jobs"], "coarse": ["marketplace"], "best": ["search_jobs"]}})
    return tasks[:40]


def arg_ok(call: dict, arg) -> bool:
    if arg is None:
        return True
    blob = json.dumps(call.get("args", {})).lower()
    return str(arg).lower() in blob


def main() -> None:
    from agent.llm import Usage, _estimate, _model_of, get_llm, throttle
    if not TASKS.exists():
        TASKS.write_text("\n".join(json.dumps(t) for t in make_tasks()) + "\n")
    tasks = [json.loads(x) for x in TASKS.read_text().splitlines()]
    from agent.prompts.loader import fill
    sysmsg = SystemMessage(fill("tool_task"))
    results: dict = {"meta": {"n_tasks": len(tasks), "ts": time.strftime("%Y-%m-%d %H:%M")}, "sets": {}}
    for name, tools in SETS.items():
        llm = get_llm("critic", temperature=0.0, max_tokens=400)
        model = _model_of(llm)
        bound = llm.bind_tools(tools, tool_choice="required")
        rows = []
        for t in tasks:
            msgs = [sysmsg, HumanMessage(t["task"])]
            throttle.wait(model, _estimate(msgs, 400) + 120 * len(tools))
            t0 = time.perf_counter()
            try:
                m = bound.invoke(msgs)
                call = (m.tool_calls or [{}])[0]
                u = Usage.from_response(model, m, time.perf_counter() - t0)
                coarse_action_ok = name != "coarse" or call.get("args", {}).get("action") is not None
                rows.append({"task": t["task"], "tool": call.get("name"), "args": call.get("args"),
                             "selected_ok": call.get("name") in t["ok"][name] and coarse_action_ok,
                             "arg_ok": arg_ok(call, t["arg"]), "tokens": u["tokens_in"] + u["tokens_out"]})
            except Exception as e:  # noqa: BLE001
                rows.append({"task": t["task"], "error": str(e)[:200], "selected_ok": False, "arg_ok": False,
                             "tokens": 0})
            print(name, len(rows), rows[-1].get("tool"), rows[-1]["selected_ok"], rows[-1]["arg_ok"], flush=True)
        ok = [r for r in rows if "error" not in r]
        results["sets"][name] = {
            "n_tools": len(tools), "selection_accuracy": round(sum(r["selected_ok"] for r in rows) / len(rows), 3),
            "argument_accuracy": round(sum(r["selected_ok"] and r["arg_ok"] for r in rows) / len(rows), 3),
            "tokens_per_task": round(statistics.mean(r["tokens"] for r in ok)) if ok else None,
            "errors": len(rows) - len(ok), "model": model, "rows": rows}
        (ROOT / "evals/results/tool_design.json").write_text(json.dumps(results, indent=2))
    print({k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in results["sets"].items()})


if __name__ == "__main__":
    main()

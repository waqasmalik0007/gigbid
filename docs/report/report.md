# GigBid: R&D design review

AI-4016 · individual assignment · repository: REPO_URL · live app: LIVE_URL · every number below is
generated from `evals/results/*.json` (`tools/results_tables.py`); file names are given in brackets.

## 1. Problem and constraints

A freelancer spends hours scanning feeds, pays Connects to apply, sends generic proposals that clients
ignore, and meets scams that ask for free work or off-platform payment. GigBid automates discovery,
assessment and drafting; **the human keeps every decision that has a cost or reaches a person**.

**Never automated:** sending a proposal or message, spending Connects, claiming experience the portfolio
does not show, contacting a client off-platform, browser automation or scraping. These are enforced in
code, not prompts: `interrupt()` before every send/spend, the claim verifier and Compliance Guard on every
outgoing text, and per-agent MCP tool allowlists (the Writer cannot submit; only the post-approval
submitter can).

**Budgets declared before measuring** (`agent/config.yaml`): p95 latency 20 s per job analysed,
USD 0.01 per proposal, 40 steps and 60k tokens per thread. All models are open-weight (gpt-oss-20b/120b,
Qwen 3.8 27B on Groq's free tier; BGE-small embeddings and a MiniLM cross-encoder on CPU). The free tier
(8k tokens/min, 200k tokens/day and 1k requests/day per model) shaped both the architecture (nodes
spread across models, a cross-process token throttle, quota-aware model routing) and the evaluation
sizes, which are reported with every result.

**Data.** No simulator or persona corpus was provided this term, so I built GigSim (FastAPI: jobs, clients
with hidden preferences, Connects, proposals, messages, rate limits, random 5xx, an outcome model) and a
generator for three synthetic personas. About 70% of job posts are real Upwork posts from Kaggle; rate
statistics come from 830k real postings (`docs/DATA_CARD.md`). Gold labels are rule-generated; that
limitation is discussed where it matters.

## 2. Architecture

Two LangGraph `StateGraph`s share one state discipline. The **bid graph** (one thread per job) fans out
Job Analyst, Risk Assessor and Fit Matcher with `Send`, joins at a rule-based decision, then runs Pricing,
Writer, claim verification, Critic (at most two rewrites), Compliance, a dry-run preview and an approval
interrupt before an exactly-once submit. The **conversation graph** (one thread per client thread)
classifies intent, offers calendar slots from the Workspace MCP, drafts a reply and waits for approval.

All capabilities are MCP tools on three servers I wrote (Portfolio read-only, Marketplace read+write with
idempotency keys, dry_run and annotations, Workspace) plus Upwork's official server and GitHub's
(`docs/TOOLS.md`). The marketplace is one port with two adapters (GigSim, live Upwork), switched by config.
Typed Pydantic hand-offs carry every decision; state, execution trace and long-term memory are kept apart
(memory is written only by explicit promotion rules: a style rule needs the same edit in 2 of the last 5
edits; an outcome insight needs at least 5 proposals per group and a 10-point reply-rate gap).
Checkpoints live in Postgres; an approval survives an API crash and is submitted once (keys
`{thread}:draft`, `{thread}:submit`).

Why each agent exists is argued in `docs/ARCHITECTURE.md`; the key design rule is that **anything an LLM
reads from a stranger can only change a typed object, never trigger an action**, and rule-based checks can
only add risk, never remove it.

## 3. R&D question A: tool granularity

**Hypothesis.** A small set of well-described tools beats both many narrow tools (selection errors) and two
coarse "action" tools (argument errors), at similar token cost.

**Setup.** 40 tasks generated from real GigSim and portfolio ids with known correct calls; the same model
(gpt-oss-120b) sees one tool set at a time and must make one call; nothing is executed
(`evals/runners/tool_design.py`).

{{results:tools}}

**Result: the hypothesis was not supported.** Tool *selection* hit the ceiling: all three sets chose an
acceptable tool on 40/40 tasks, so these tasks are too easy for a 120B model to separate the designs. The
differences are in arguments and tokens [tool_design.json]. The coarse set failed exactly where its
free-form `params` dictionary allowed it: on 3 pricing tasks it passed "web development" where the
category enum is `web_dev` (92.5% argument accuracy); typed schemas with enum descriptions prevent that.
The rewritten descriptions of the shipped set cost 36% more prompt tokens than 14 terse narrow tools (718
vs 527 per task) for no accuracy gain at this difficulty. **Decision:** keep typed, schema-first tools
(the coarse failure mode is real and silent), and trim the long descriptions, which bought nothing
measurable here. A harder task set (multi-step, ambiguous requests) is needed to test selection itself.

## 4. R&D question B: retrieval, verification, what ships

**Hypothesis (stated before running).** Requirement decomposition plus hybrid retrieval raises evidence
Recall@5 by 15 points over V0 on jobs with four or more requirements.

**Setup.** Seven single-switch variants of one composable pipeline (`rag/pipeline.py`) on 544 dev jobs with
gold evidence, three personas; then an ablation of the shipped combination [rag_main.json,
rag_ablation.json].

{{results:rag}}

{{results:breakdown}}

**Result: the hypothesis was wrong in an instructive way.** Hybrid retrieval helps on every slice, but
decomposition *halves* recall on jobs with four or more requirements (V1 vs V0 in the breakdown table).
My explanation, not yet tested separately: whole-post queries already contain every named tool, and
splitting them into "experience with X" queries loses the co-occurrence that identifies the right project.
The cross-encoder reranker also scored below V0 while adding seconds of latency; the likely reason is that
it was trained on web-search questions, and during development it scored a matching Python case study at
about -8 for "experience with Python". Outcome boosting was the worst variant; it multiplies the scores of
winning past proposals regardless of topic. Contextual chunks (a generated context line naming the project, type and skills)
were the best single switch, because terse READMEs and reviews become findable.

**Ablation.** Removing contextual chunks costs 14.2 points of Recall@5 and removing hybrid retrieval costs
11.1 points [rag_ablation.json]. Adding back any losing switch also hurts: decomposition -12.7 points
(0.375), outcome boosting -14.3 (0.359), reranking -21.0 (0.292) while raising p95 retrieval latency from
5 ms to 3.4 s. The two shipped switches are complementary (each alone reaches 0.39 or less; together
0.50), and none of the others earns its place on this corpus.

{{results:ablation}}

**Claim verification (B3).** After drafting, each experience claim is split out and checked against
retrieved evidence: numbers and tool names must appear in the evidence (checked lexically, because an LLM
verifier accepts "40%" against "38%") and the cross-encoder must agree the evidence is about the same thing.
TODO(result): unsupported-claim rate before/after from proposals_best.json.

**Where better retrieval did not translate into replies.** TODO(result): compare proposals_v0 vs
proposals_best reply rate.

**Judge calibration.** TODO(result): kappa from judge_calibration.json (requires my 30 labels).

**What ships.** Contextual chunks + hybrid BM25/dense (`rag/variants/best.py`), chosen by Recall@5 and
nDCG@10 on the full dev set, with sub-millisecond-to-millisecond retrieval latency; the reranker,
decomposition and outcome boosting stay available as configurable components but are off.

## 5. R&D question C: is the multi-agent design worth it?

{{results:compare}}

TODO(result): analysis; where the single agent wins, and whether I would ship it.

## 6. The change request

The platform-policy update is absorbed in configuration: Connects multipliers, premium surcharges, letter
length limits and forbidden content are YAML (`gigsim/policy.yaml`, `data/policy_pack/policy.yaml`); the
Pricing Strategist reads the Connects cost from the job, the Compliance Guard reads rules and limits.
A rehearsal (x1.5-style surcharge on jobs over $1,000 plus a 250-word cap) changed 6 YAML lines and no Python
or prompts; `tests/unit/test_change_request.py` proves the dry-run cost and both length checks change
(`docs/CHANGE_REQUEST.md`). The design lesson: keep every number that a platform can change out of code and
out of prompts.

## 7. Signature extension: proposal A/B experiments (X2)

Two opening strategies are config arms; a job's arm is a hash of its id (stable across retries). The test
compares GigSim reply probability (Welch) and simulated replies (exact Fisher) and reports the minimum
detectable difference. TODO(result): ab_test.json.

## 8. Failure analysis

TODO(result): at least 10 failed dev jobs from decision_open.json / proposals_*.json grouped into missed
evidence, wrong skip, mispriced, unsupported claim, generic opening, compliance miss, injection, tool error,
each with the fix tried and whether it worked. Fixes already made during development (each with a commit):
fit thresholds that marked every requirement "none"; parallel branches that ran serially; dropped state
keys; Groq tool-call 400s; and the first "best" pipeline that switched on components that hurt.

## 9. Safety and platform respect

**Never does:** send or spend without approval; claim unsupported experience; share contacts or move
off-platform; follow instructions inside job posts or client messages; scrape or automate a browser.
**How enforced:** untrusted text is wrapped in delimiters that cannot be closed early; the Risk Assessor's
rules flag injections and scams and cannot be overridden by the LLM; the claim verifier and policy-pack
Compliance Guard run on every outgoing text; PII (emails, phones, cards, IBANs, messenger handles) is masked
in logs and traces; OAuth tokens are Fernet-encrypted per user and never logged; every query is scoped by
user id; live mode reads at most every 3 hours and only drafts.

{{results:injection}}

**What still gets through.** TODO(result): from injection.json rows.

## 10. Hidden test results

TODO(after deadline run): results against the declared budgets, without excuses.

## Appendix: reproducing

`make eval` regenerates every results file (`tools/run_all_evals.sh` does the same resumably within the
free-tier quotas); `python docs/report/build.py` rebuilds this PDF. Tests: 36 Python tests (unit, MCP
contract snapshots, MCP server integration, API tenant isolation) and 12 Playwright end-to-end tests (6
flows on desktop and phone viewports) pass.

**Quota note.** When a model returns a per-day 429, `agent/llm.py` marks it exhausted until Groq's
reported reset and routes that node to the next model in the profile's `quota_fallbacks` (gpt-oss-20b
nodes to gpt-oss-120b). Every re-route is logged (`data/processed/quota_routes.log`) and reported with
the run it affected.

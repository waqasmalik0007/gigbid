# Runbook

## Run locally

```bash
cp .env.example .env                      # set GROQ_API_KEY; never commit .env
docker compose -f infra/docker-compose.yml up --build     # web :3000, api :8000
# or without Docker:
pip install -e ".[dev]"
uvicorn gigsim.app:app --port 8100 &
uvicorn apps.api.main:app --port 8000 &   # in-process worker when REDIS_URL is empty
cd apps/web && npm ci && npm run dev
```

Reviewer account: `REVIEWER_EMAIL` / `REVIEWER_PASSWORD` env vars seed it at API start-up (AI/ML persona).

**On Windows, set `PYTHONUTF8=1` before starting GigSim.** GigSim is frozen (`gigsim/MANIFEST.sha256`)
and reads its JSON data with the platform's default encoding. Windows defaults to cp1252, which garbles
non-ASCII characters in real job posts (dashes and quotes appear as `â€”` in the Writer's letters). The
agent and RAG code read files as UTF-8 explicitly; GigSim relies on this setting rather than a code change
to the frozen simulator. Linux and Docker already default to UTF-8.

## Scheduling (E1)

The Arq worker (`arq worker.tasks.WorkerSettings`) runs `scout_all` every 5 minutes but only acts when
the configured interval has passed, and `poll_events` every 2 minutes.

| Mode | Scout interval | Why |
|---|---|---|
| GigSim | 30 min (`scheduling.gigsim_scout_interval_min`) | Simulator; frequent enough for demos, well under its 60 req/min limit (one search per saved search). |
| Live Upwork | 180 min (`scheduling.upwork_scout_interval_min`) | A human freelancer checks a feed a few times a day. Polling more often adds load with no benefit (new posts get dozens of proposals within hours anyway) and would look like scraping. One read per saved search per cycle, a 1.5 s pause between searches, no page fetches, only the official MCP tools. |

Idempotency: each job's bid run uses Arq `_job_id = bid:<user>:<job>` and the graph checks for an existing
checkpoint before starting, so a worker restart never double-analyses; submission is exactly-once via
idempotency keys (see ARCHITECTURE.md).

## Budgets (E5)

Declared in `agent/config.yaml` (`budgets`): 40 steps and 60k tokens per thread; p95 latency 20 s per job
analysed; USD 0.01 per proposal. Exceeding a step/token budget moves the thread to `needs_attention`.
Measured values are in `evals/results/decision_*.json` and `proposals_*.json` and reported in the README.

## Free-tier limits

Groq free tier, **per model**: 8,000 tokens/minute, 200,000 tokens/day and 1,000 requests/day.
`agent/llm.py` throttles each model with a sliding window shared by every process on the machine (a SQLite
ledger; `TPM_LIMIT`, default 7,000; `RPM_LIMIT`, default 25) and spreads nodes across models. A per-day
429 marks the model exhausted until Groq's reported reset and routes calls to the profile's
`quota_fallbacks` (logged in `data/processed/quota_routes.log`); when every model is out, nodes fail fast
with `QuotaExhausted` and the resumable evaluation runners (`tools/run_all_evals.sh`) retry later.

## Incidents

| Symptom | Where to look | Action |
|---|---|---|
| Red "Marketplace unavailable" banner | `/api/health/marketplace`, GigSim / marketplace MCP logs | Restart GigSim/marketplace server. Nothing else: threads parked as `needs_attention` by the outage resume automatically from the checkpoint before the failed step (`worker/runner.py::recover`, triggered by the `recover_degraded` cron and by the health check the banner polls); an approved proposal is submitted exactly once (`tests/unit/test_degraded_recovery.py`). |
| Run stuck in `dead_letter` | Runs page, `run_events`, trace id in audit log | Read the error; replay from the failed node with `get_state_history`. |
| 429s from Groq | API logs | Lower concurrency or `TPM_LIMIT`; switch profile to `open_qwen` for cheap nodes. |
| Policy update released | `docs/CHANGE_REQUEST.md` | Edit `gigsim/policy.yaml` and `data/policy_pack/policy.yaml` only. |

## Crash-and-resume demo (C6)

1. Analyse a job until it waits for approval (Approvals page shows it).
2. Stop the API (and worker). Start them again.
3. Open Approvals: the same draft is there (read from the checkpoint). Approve.
4. The proposal is submitted once; approving again or retrying returns `idempotent_replay`.

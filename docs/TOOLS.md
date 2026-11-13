# Tools (MCP surface)

Every capability an agent uses is an MCP tool with a schema, an owner and a test. Agents never call
GigSim, Upwork, a database or a file directly; each agent only gets the tools on its allowlist
(`agent/mcp_client.py: ALLOW`). Schemas are snapshot-tested (`tests/contract/snapshots/*.json`): an
unintentional signature change fails CI.

Auth for our servers over HTTP: bearer token (`MCP_BEARER_TOKEN`), per-user rate limit (120/min,
under GigSim's 60/min per freelancer token once the adapter's own limiter is counted), and an
append-only, PII-masked audit log (`data/processed/audit.jsonl`: user, tool, arguments, result,
trace id). In-process transport (laptop, evals, single-container deploy) uses the same MCP protocol
through in-memory streams.

| Tool | Server | R/W | Annotations | Auth | Notes |
|---|---|---|---|---|---|
| `search_evidence(requirement, k, filters, persona)` | portfolio | read | readOnly, idempotent, closed-world | bearer | Shipped RAG pipeline (contextual + hybrid); filters by metadata (type, outcome) |
| `get_case_study(evidence_id, persona)` | portfolio | read | readOnly, idempotent | bearer | Full item + related items of the same project |
| `list_skills(persona)` | portfolio | read | readOnly, idempotent | bearer | Skills with evidence counts (proposals excluded: phrasing, not proof) |
| resource `portfolio://case/{id}` | portfolio | read | - | bearer | Markdown for MCP hosts |
| resource `portfolio://proposal/{id}` | portfolio | read | - | bearer | Past proposal with outcome |
| prompt `answer_screening_question` | portfolio | - | - | - | For Claude Desktop / Cursor users |
| prompt `proposal_opening` | portfolio | - | - | - | Untrusted post is delimited |
| `search_jobs(query, category, since, limit)` | marketplace | read | readOnly, idempotent, open-world | bearer | Scout; `since` makes polling incremental |
| `get_job(job_id)` | marketplace | read | readOnly, idempotent, open-world | bearer | Description is untrusted |
| `get_client_history(client_id)` | marketplace | read | readOnly, idempotent, open-world | bearer | Hidden GigSim preferences never leave GigSim |
| `get_connects_balance()` | marketplace | read | readOnly, idempotent, open-world | bearer | |
| `list_messages(thread_id?)` | marketplace | read | readOnly, idempotent, open-world | bearer | Client text untrusted |
| `list_events(after_id)` | marketplace | read | readOnly, idempotent, open-world | bearer | Pipeline board and outcome memory |
| `market_stats(category)` | marketplace | read | readOnly, idempotent, open-world | bearer | Real quartiles from Kaggle Upwork data |
| `create_proposal_draft(draft, idempotency_key, dry_run)` | marketplace | **write** | not readOnly, not destructive, idempotent | bearer | Pydantic `ProposalDraftIn`; dry_run returns payload + Connects cost |
| `submit_proposal(proposal_id, idempotency_key, dry_run)` | marketplace | **write** | **destructive** (spends Connects), idempotent | bearer | Same key never spends twice; live mode returns "confirm on Upwork" |
| `send_message(thread_id, body, idempotency_key, dry_run)` | marketplace | **write** | not destructive, idempotent | bearer | Only after approval |
| `free_slots(user_id, days_ahead, duration_min)` | workspace | read | readOnly, idempotent | bearer | Weekday 09:00-17:00, no overlaps |
| `create_interview_hold(user_id, start, end, title, idempotency_key)` | workspace | write | idempotent | bearer | Tentative hold only |
| `create_email_draft(user_id, to, subject, body, idempotency_key)` | workspace | write | idempotent | bearer | Never sends email |

## Per-agent allowlists

| Agent | Tools |
|---|---|
| Scout | marketplace: search_jobs, get_job |
| Job Analyst | none |
| Risk Assessor | marketplace: get_client_history, get_job |
| Fit Matcher | portfolio: search_evidence, get_case_study, list_skills |
| Pricing Strategist | marketplace: market_stats, get_client_history, get_connects_balance |
| Proposal Writer / Critic | portfolio: search_evidence, get_case_study |
| Compliance Guard | none |
| Submitter (after approval only) | marketplace: create_proposal_draft, submit_proposal |
| Client Liaison | marketplace: list_messages, list_events, send_message; workspace: all three |

## Third-party servers (A5)

| Server | Used for | Trust rationale |
|---|---|---|
| Upwork official MCP (`https://mcp.upwork.com/mcp`) | Live mode behind the same port (`mcp_servers/marketplace/adapters/upwork_live.py`) | First-party, OAuth 2.1 with user consent; every write is a draft the user confirms on Upwork, and GigBid adds its own approval gate in front. |
| GitHub MCP (`github/github-mcp-server`, read-only token) | Repo README/code ingestion into the portfolio (`rag/ingest/github.py`) | Official GitHub server, scoped fine-grained read-only token, only public repos the user names; ingested text is treated as untrusted portfolio data. |

## Degraded mode (A6)

Kill the marketplace server (or GigSim): the adapter's bounded retries end in `MARKETPLACE_UNAVAILABLE`,
the node wrapper parks the job as `needs_attention` (reason "marketplace unavailable") instead of
failing it, the scout skips its cycle, and `/api/health/marketplace` drives the red banner in the UI.
Approvals already given are kept in the checkpoint and submitted once the server is back
(`agent/nodes/common.py`, `apps/web/src/components/DegradedBanner.tsx`).

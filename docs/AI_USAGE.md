# AI usage

AI coding assistants were allowed for this assignment. I am accountable for every line.

| Tool | Used for | Files |
|---|---|---|
| Claude Code (Claude Opus) | Scaffolding, first drafts of most modules, test drafting, docs drafts, debugging sessions | All directories; every commit made with its help carries a `Co-Authored-By` trailer |
| Groq-hosted open models (gpt-oss-20b/120b, Qwen 3.8 27B) | The product's own agents and LLM judge (not coding) | `agent/`, `evals/` |

## Things the assistant got wrong that I caught

1. **Fit scores were all zero.** The first Fit Matcher used fixed cross-encoder thresholds (+2 / -1).
   On this corpus the ms-marco cross-encoder scores "experience with Python" against a Python case study
   at about -8, so every requirement was "none" and a clearly matching ML job was skipped. Fixed by exact
   tool-name matching for skill requirements and thresholds calibrated on observed scores
   (commit "Fix fit scoring and state schema…").
2. **Parallel agents were not parallel.** LLM calls were synchronous inside async nodes, so the three
   "parallel" branches ran one after another. Fixed with thread-offloaded async calls.
3. **Undeclared state keys vanished.** `thread_id` was not in the `BidState` schema; LangGraph dropped it
   and the dry-run preview crashed after a full critic loop. Fixed by declaring every key.
4. **400 errors from Groq.** Function-calling structured output sometimes returned bare JSON; switched to
   JSON-schema mode after measuring both modes on every hand-off schema.
5. **An unverified claim in a docstring.** The pricing module claimed deterministic pricing was "as
   accurate as an LLM pricer"; it was never measured, so the sentence was replaced with a pointer to the
   metric that is measured. Same for a change-request time estimate, replaced by a test.
6. **A "best" RAG pipeline chosen by default.** The first `best` turned on every switch; evaluation
   showed reranking, decomposition and outcome boosting each hurt on this corpus, so the shipped design
   was changed to contextual + hybrid (R6).
7. **The CI checker flagged false positives** (the `f` in `fill(` as an inline prompt; a Kaggle post
   containing "sk-SQLAlchemy" as an API key). Patterns tightened.
8. **A quota failure counted as a correct answer.** In the R&D C runner, a graph run that dead-lettered
   on Groq's daily quota had no decision, and the code defaulted it to "skip"; on skip jobs that scored as
   correct with 0 tokens. Six such rows were found by their zero token counts, deleted, and the runner now
   records an error and re-runs the job.
9. **A Dockerfile that could never build.** All pinned dependencies were passed to pip as one quoted
   string; Docker was not available locally, so it was caught by review. Now installed from a generated
   requirements file.
10. **Degraded mode without recovery.** The banner and `needs_attention` status existed, but nothing
    resumed parked work when the marketplace came back. Added checkpoint-based recovery and a test that
    proves an approved proposal is still submitted exactly once.

**Judge calibration labels were made by Claude, not by me.** The brief (B4) asks the student to label 30
items. Short on time, I asked the coding assistant to label them, blind to the judge's scores. Every row
of `evals/datasets/judge_labels.csv` says so in its notes, and report section 4 says the resulting kappa
compares two models, not the judge against a human.

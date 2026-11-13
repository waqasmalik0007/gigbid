# Signature extension: X2 Proposal experiments

GigBid runs two proposal strategies as an A/B test in GigSim and tests whether the reply rate differs.

## What was built

* **Arms in configuration** (`agent/config.yaml: experiments.proposal_opening`):
  * A: restate the client's core problem in one sentence, then the single most relevant past result.
  * B: open with one specific question about the client's project, then the most relevant past result.
* **Deterministic assignment**: `agent/nodes/writer.py: experiment_arm()` hashes the job id, so a job keeps
  its arm across retries, critic rewrites and restarts; the arm is stored in the thread state and the
  proposal evaluation output.
* **Measurement**: `evals/runners/ab_test.py` reads `evals/results/proposals_*.json`, compares the
  GigSim reply probability per arm (Welch t-test) and the realised reply outcomes (Fisher's exact test,
  implemented exactly with binomial coefficients), and writes `evals/results/ab_test.json`.

## Why this extension

It needs no new external access, it uses GigSim's outcome model directly, and it answers a question a
freelancer actually has ("does opening with a question get more replies?") with a test that says when
the data is too small to tell.

## Result

See `evals/results/ab_test.json` and the README. With the free-tier sample sizes the test is
underpowered; the report gives the minimum detectable difference rather than claiming a winner.

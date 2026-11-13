# 0006 Deterministic pricing and decision rules

**Decision.** `decide` and the Pricing Strategist are rules over typed reports, not LLM calls.
**Why.** They must be explainable on screen, cheap, and stable across runs; every threshold is in
`agent/config.yaml`, so a policy or budget change is a config edit. Measured by price-in-fair-band and
bid/skip F1 in `evals/results/decision_*.json`.

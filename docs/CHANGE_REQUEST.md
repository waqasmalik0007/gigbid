# Change request: platform policy update

The brief announced a GigSim "platform policy update" on 2 November 2026 introducing a new rule or cost
that affects bidding. Because no GigSim was provided, GigBid was designed so that any such update is a
**configuration change only**:

| Kind of change | File(s) edited | Code changed |
|---|---|---|
| Connects price change (multiplier, extra Connects for premium jobs) | `gigsim/policy.yaml` | none: GigSim and the dry_run preview read it; Pricing reads the cost from the job |
| Maximum proposal length | `gigsim/policy.yaml` (`max_cover_letter_words`) and `data/policy_pack/policy.yaml` (`limits`) | none: Compliance Guard reads limits |
| New forbidden content (e.g. no external links, no AI-generated disclaimer) | `data/policy_pack/policy.yaml` (`rules`, regex + action) | none |
| Starting Connects / weekly budget | `gigsim/policy.yaml`, `agent/config.yaml` | none |

## Rehearsal

Change applied (example used to test the mechanism): "Connects cost x1.5 for jobs over $1,000 and
cover letters capped at 250 words".

```diff
# gigsim/policy.yaml
-version: "2026-10-27"
-connects_multiplier: 1.0
-premium_job_threshold: 1000000000000
-premium_job_extra_connects: 0
-max_cover_letter_words: null
+version: "2026-11-02"
+connects_multiplier: 1.0
+premium_job_threshold: 1000
+premium_job_extra_connects: 4
+max_cover_letter_words: 250
# data/policy_pack/policy.yaml
-  max_cover_letter_words: 300
+  max_cover_letter_words: 250
```

Lines changed: 6 lines in 2 YAML files, 0 lines of Python, no prompt changes. Verified by
`tests/unit/test_change_request.py`: with only the YAML edited, GigSim (and therefore the dry_run
preview the user approves) charges 4 more Connects on a job over $1,000 and rejects a 260-word letter,
and the Compliance Guard blocks a ~300-word draft with a reason the UI shows. Because the Pricing
Strategist reads the Connects cost from the job, its expected-value check uses the new cost without
any change.

If the instructor's actual update differs, record here: what changed, the diff, how long it took.
That it is absorbed in configuration is the design property being graded.

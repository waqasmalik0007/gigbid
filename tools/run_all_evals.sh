#!/usr/bin/env bash
# Run every LLM-backed evaluation on the Groq free tier, resumably.
#
#   bash tools/run_all_evals.sh <lane> [logdir]      lane = oss | qwen
#
# Free-tier limits that shape this script: 8k tokens/min and 200k tokens/day per model. The two lanes use
# different models (gpt-oss vs qwen), so they run in parallel without sharing a budget. Every runner is
# resumable (finished jobs are kept, infrastructure failures are retried), so a step is simply re-run
# until it reports no errors; when every model is out of daily quota the step fails fast and we wait.
set -u
cd "$(dirname "$0")/.."
LANE=${1:-oss}
LOG=${2:-evals/results/logs}
mkdir -p "$LOG"
export PYTHONPATH=. PYTHONIOENCODING=utf-8 FASTMCP_LOG_LEVEL=WARNING NODE_TIMEOUT_SCALE=${NODE_TIMEOUT_SCALE:-5}
export OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
PY=${PY:-python}

# step <name> <done-check python expr on the result file> -- <command...>
step() {
  local name=$1 check=$2; shift 3
  # one lane per step at a time: a second lane waits for the first, then finds the step complete
  local lock="$LOG/$name.lock"
  until mkdir "$lock" 2>/dev/null; do
    if [ -f "$lock/pid" ] && ! kill -0 "$(cat "$lock/pid")" 2>/dev/null; then rm -rf "$lock"; continue; fi
    sleep 60
  done
  echo $$ > "$lock/pid"
  _step "$name" "$check" "$@"
  rm -rf "$lock"
}

_step() {
  local name=$1 check=$2; shift 2
  for attempt in $(seq 1 ${MAX_ATTEMPTS:-40}); do
    if $PY -c "$check" 2>/dev/null; then echo "$(date +%T) $name complete"; return 0; fi
    echo "$(date +%T) $name attempt $attempt"
    GIGBID_EVAL_TAG=$name "$@" >> "$LOG/$name.log" 2>&1
    $PY -c "$check" 2>/dev/null && { echo "$(date +%T) $name complete"; return 0; }
    sleep ${RETRY_SLEEP:-600}   # rate limit or daily quota: let the window move
  done
  echo "$(date +%T) $name gave up after $attempt attempts"
}

ok_rows() {  # resumable runner finished: >= $2 rows without an infrastructure error in its partial file
  echo "import json,sys; r=[json.loads(x) for x in open('evals/results/$1')]; sys.exit(0 if sum('error' not in x for x in r)>=$2 else 1)"
}

ok_json() {  # result file exists and its overall summary has n>=$2 and no errors
  echo "import json,sys; d=json.load(open('evals/results/$1')); o=d.get('overall',d); sys.exit(0 if o.get('n',0)>=$2 and not o.get('errors') else 1)"
}

if [ "$LANE" = v0 ]; then  # with several API keys the V0 proposal run can go in parallel with "best"
  step proposals_v0 "$(ok_rows proposals_v0.partial.jsonl 9)" -- \
    $PY -m evals.runners.proposal_eval --per-persona 3 --variant v0_naive --tag v0
  exit 0
fi

if [ "$LANE" = qwen ]; then
  # R&D C: all three systems on the same model; Qwen's quota is separate from the gpt-oss proposal runs
  step multiagent_compare "import json,sys; d=json.load(open('evals/results/multiagent_compare.json'))['systems']; sys.exit(0 if all(d.get(s,{}).get('n',0)>=12 and not d[s].get('errors') for s in ('react','supervisor','graph')) else 1)" -- \
    $PY -m evals.runners.multiagent_compare --per-persona 2 --profile open_qwen
  step decision_open_qwen "$(ok_rows decision_open_qwen.partial.jsonl 60)" -- \
    $PY -m evals.runners.decision_eval --per-persona 20 --profile open_qwen --concurrency 2
  exit 0
fi

step decision_open "$(ok_rows decision_open.partial.jsonl 60)" -- \
  $PY -m evals.runners.decision_eval --per-persona 20 --profile open --concurrency 2
step proposals_best "$(ok_rows proposals_best.partial.jsonl 9)" -- \
  $PY -m evals.runners.proposal_eval --per-persona 3 --variant best --tag best
step proposals_v0 "$(ok_rows proposals_v0.partial.jsonl 9)" -- \
  $PY -m evals.runners.proposal_eval --per-persona 3 --variant v0_naive --tag v0
step injection "import json,sys; d=json.load(open('evals/results/injection.json')); sys.exit(0 if d else 1)" -- \
  $PY -m evals.runners.injection_eval --n 8
$PY -m evals.runners.ab_test > "$LOG/ab_test.log" 2>&1
$PY -m evals.runners.judge_calibration export > "$LOG/judge_export.log" 2>&1
step single_agent "import json,sys; json.load(open('evals/results/single_agent_proposal.json'))" -- \
  $PY -m evals.runners.single_agent_proposal
step rag_v8 "import json,sys; d=json.load(open('evals/results/rag_main.json')); sys.exit(0 if 'v8_corrective' in json.dumps(d) else 1)" -- \
  $PY -m evals.runners.rag_eval --variants v8_corrective
echo "$(date +%T) ALL_DONE"

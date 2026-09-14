#!/bin/bash
# Serial (workers=1) latency measurement: true per-request wall time for each voter
# plus the no-verifier control, so the retry overhead is isolated.
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
S4=$ROOT/results/optimized/bootstrap46_seeded4_b3/program.json
PY="/workspace/telelogs-bench4/dspy/.venv/bin/python"
cd "$ROOT/code"

run () {
  local method=$1 prog=$2 name=$3; shift 3
  echo "### $name" >&2
  "$PY" run_tool_experiment.py \
    --methods "$method" --compiled "$method=$prog" \
    --eval-split dev --eval-per-label 2 --workers 1 \
    --run-name "$name" "$@"
}

# control: single ReAct pass, no consistency audit
run b3_react_tools    "$S11" tool_lat_s11_plain_serial   --max-tokens 1000
# voter A: s11 + verifier v2.1 retries
run b3_react_verified "$S11" tool_lat_s11v21_serial      --max-tokens 1000
# voter B: s4 + verifier v2.1 retries
run b3_react_verified "$S4"  tool_lat_s4v21_serial       --max-tokens 1000
# voter C: native thinking, single pass
run b3_react_tools    "$S11" tool_lat_think_serial       --max-tokens 6000 --thinking

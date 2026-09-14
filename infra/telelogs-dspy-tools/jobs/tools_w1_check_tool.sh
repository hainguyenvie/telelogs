#!/bin/bash
# ①⁺ experiment (Figure 6): the consistency audit packaged as a callable 7th
# tool, so the model can fix its verification lines inside the trajectory
# instead of burning post-hoc retry rounds. Layer ②, the code acceptance gate,
# is unchanged and still guarantees the floor.
#
# The old compiled demos never call the new tool, so this compiles fresh demos
# (same recipe that produced seed-v11: bootstrap, 8/label train, 2 demos) on
# the CHAMPION weights (GRPO), then A/Bs the full stack on dev-96 against the
# champion's dev-96 run (tool_dev96_s11_specialist_grpo1_narrated).
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
PY=/workspace/telelogs-bench4/dspy/.venv/bin/python
export DSPY_API_BASE=http://telelogs-rl-vllm:8000/v1

cd "$ROOT/code"

# 0. pure-code unit test of the tool; a failure aborts the compile
"$PY" "$ROOT/test_check_tool.py" "$ROOT/code"

# 1. re-bootstrap: 2 demos for the 7-tool program under seed v12
"$PY" optimize_tool_program.py \
  --optimizer bootstrap \
  --method b3_react_check_tools \
  --seed-instructions "$ROOT/seed_b3_calibrated_v12_check.txt" \
  --train-per-label 8 --val-per-label 4 --val-offset-per-label 8 \
  --workers 8 --max-tokens 1000 --max-bootstrapped-demos 2 \
  --run-name tool_opt_check_v12_grpo

W1=$ROOT/results/optimized/tool_opt_check_v12_grpo/program.json
if [ ! -f "$W1" ]; then W1=$ROOT/results/optimized/tool_opt_check_v12_grpo/program.pkl; fi

# 2. full stack with the 7th tool on dev-96
"$PY" run_tool_experiment.py \
  --methods b3_react_check_specialist \
  --compiled "b3_react_check_specialist=$W1" \
  --eval-split dev --eval-per-label 12 --workers 8 --max-tokens 1000 \
  --run-name tool_dev96_check_v12_grpo

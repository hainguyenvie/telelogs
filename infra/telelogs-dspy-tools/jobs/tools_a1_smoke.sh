#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
PY=/workspace/telelogs-bench4/dspy/.venv/bin/python
cd "$ROOT/code"
"$PY" run_tool_experiment.py \
  --methods b3_react_verified \
  --compiled "b3_react_verified=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json" \
  --eval-split train --eval-per-label 1 --workers 4 --max-tokens 1000 \
  --run-name tool_smoke_verified
"$PY" run_tool_experiment.py \
  --methods b3_react_tools \
  --compiled "b3_react_tools=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json" \
  --eval-split train --eval-per-label 1 --workers 4 --max-tokens 6000 --thinking \
  --run-name tool_smoke_think

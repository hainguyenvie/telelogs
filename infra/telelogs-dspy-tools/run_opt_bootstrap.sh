#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
METHOD=${1:-b3_react_tools}
SEED=${2:-$ROOT/seed_b3_calibrated.txt}
RUN_NAME=${3:-bootstrap_seeded_${METHOD}}
PRIORITY=${4:-}
EXTRA=()
if [ -n "$PRIORITY" ]; then
  EXTRA+=(--demo-priority-labels "$PRIORITY")
fi
cd "$ROOT/code"
"/workspace/telelogs-bench4/dspy/.venv/bin/python" optimize_tool_program.py \
  --optimizer bootstrap \
  --method "$METHOD" \
  --seed-instructions "$SEED" \
  --train-per-label 8 \
  --val-per-label 4 \
  --val-offset-per-label 8 \
  --workers 8 \
  --max-tokens 1000 \
  --max-bootstrapped-demos 2 \
  --run-name "$RUN_NAME" \
  "${EXTRA[@]}"

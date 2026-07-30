#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
METHOD=${1:-b3_react_tools}
SEED=${2:-$ROOT/seed_b3_calibrated_v11.txt}
RUN_NAME=${3:-mipro_seeded11_b3}
cd "$ROOT/code"
"/workspace/telelogs-bench4/dspy/.venv/bin/python" optimize_tool_program.py \
  --optimizer mipro \
  --method "$METHOD" \
  --seed-instructions "$SEED" \
  --train-per-label 8 \
  --val-per-label 4 \
  --val-offset-per-label 8 \
  --workers 8 \
  --max-tokens 1000 \
  --run-name "$RUN_NAME"

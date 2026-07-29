#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
METHOD=${1:-b3_react_tools}
SEED=${2:-$ROOT/seed_b3_calibrated.txt}
PROVIDER=${3:-deepseek-flash}
RUN_NAME=${4:-gepa_${PROVIDER}_seeded_${METHOD}}
if [ "$PROVIDER" != "qwen" ]; then
  set -a
  source /workspace/telelogs-bench4/secrets/deepseek.env
  set +a
fi
cd "$ROOT/code"
"/workspace/telelogs-bench4/dspy/.venv/bin/python" optimize_tool_program.py \
  --optimizer gepa \
  --method "$METHOD" \
  --seed-instructions "$SEED" \
  --train-per-label 8 \
  --val-per-label 8 \
  --val-offset-per-label 8 \
  --workers 8 \
  --max-tokens 1000 \
  --gepa-max-metric-calls 400 \
  --reflection-provider "$PROVIDER" \
  --run-name "$RUN_NAME"

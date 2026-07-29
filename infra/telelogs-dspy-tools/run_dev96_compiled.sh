#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
METHOD=${1:?method}
PROGRAM=${2:?program.json path}
RUN_NAME=${3:?run name}
cd "$ROOT/code"
"/workspace/telelogs-bench4/dspy/.venv/bin/python" run_tool_experiment.py \
  --methods "$METHOD" \
  --compiled "$METHOD=$PROGRAM" \
  --eval-split dev \
  --eval-per-label 12 \
  --workers 8 \
  --max-tokens 1000 \
  --run-name "$RUN_NAME"

#!/bin/bash
# BootstrapFewShot with a configurable demo budget (the sweep never left 2).
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
METHOD=${1:?method}
SEED=${2:?seed}
RUN_NAME=${3:?run name}
NDEMOS=${4:?n demos}
PRIORITY=${5:-}
EXTRA=()
[ -n "$PRIORITY" ] && EXTRA+=(--demo-priority-labels "$PRIORITY")
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
  --max-bootstrapped-demos "$NDEMOS" \
  --run-name "$RUN_NAME" \
  "${EXTRA[@]}"

#!/bin/bash
# Selection set #2: 200 TRAIN-split cases starting after the 8-per-label window the
# demo bootstrapper drew from, so nothing here fed a demo. Added in round 4 because
# dev-96 has 1 sigma ~ +-4.4 points at p~0.8 and demonstrably selected noise twice.
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
METHOD=${1:?method}
PROGRAM=${2:?program.json path or NONE}
RUN_NAME=${3:?run name}
EXTRA=()
[ "$PROGRAM" != "NONE" ] && EXTRA+=(--compiled "$METHOD=$PROGRAM")
cd "$ROOT/code"
"/workspace/telelogs-bench4/dspy/.venv/bin/python" run_tool_experiment.py \
  --methods "$METHOD" \
  --eval-split train \
  --eval-per-label 25 \
  --eval-offset-per-label 8 \
  --workers 12 \
  --max-tokens 1000 \
  --run-name "$RUN_NAME" \
  "${EXTRA[@]}"

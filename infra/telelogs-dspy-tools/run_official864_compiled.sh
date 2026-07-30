#!/bin/bash
# Final official-864 evaluation of one compiled program (optionally thinking).
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
METHOD=${1:?method}
PROGRAM=${2:?program.json path}
RUN_NAME=${3:?run name}
MODE=${4:-}
EXTRA=()
MAXTOK=1000
if [ "$MODE" = "think" ]; then
  EXTRA+=(--thinking)
  MAXTOK=6000
fi
cd "$ROOT/code"
"/workspace/telelogs-bench4/dspy/.venv/bin/python" run_tool_experiment.py \
  --methods "$METHOD" \
  --compiled "$METHOD=$PROGRAM" \
  --eval-split all \
  --raw-data "$ROOT/data/test_official864.json" \
  --workers 12 \
  --max-tokens "$MAXTOK" \
  "${EXTRA[@]}" \
  --run-name "$RUN_NAME"

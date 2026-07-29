#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
cd "$ROOT/code"
"/workspace/telelogs-bench4/dspy/.venv/bin/python" run_tool_experiment.py \
  --methods b0_raw,b1_all_tools,b2_planned_tools,b3_react_tools \
  --eval-split train \
  --eval-per-label 1 \
  --workers 6 \
  --max-tokens 1000 \
  --run-name tool_smoke_train8

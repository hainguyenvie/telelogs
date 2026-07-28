#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy
cd "$ROOT/code"
"$ROOT/.venv/bin/python" run_stage.py calibrated_prompt \
  --run-name calibrated_prompt_boolean_holdout479 \
  --eval-split holdout \
  --eval-per-label 0 \
  --workers 16 \
  --max-tokens 1200 \
  --c3-advantage-threshold-mbps 142.5

#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy
cd "$ROOT/code"
"$ROOT/.venv/bin/python" run_stage.py hybrid \
  --run-name hybrid_holdout \
  --eval-split holdout \
  --eval-per-label 0 \
  --train-per-label 4 \
  --workers 32 \
  --max-tokens 1200

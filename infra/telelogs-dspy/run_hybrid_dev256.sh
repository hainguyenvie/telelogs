#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy
cd "$ROOT/code"
"$ROOT/.venv/bin/python" run_stage.py hybrid \
  --run-name hybrid_dev256 \
  --eval-per-label 32 \
  --train-per-label 4 \
  --workers 32 \
  --max-tokens 1200

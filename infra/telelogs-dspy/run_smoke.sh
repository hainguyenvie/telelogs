#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy
cd "$ROOT/code"
"$ROOT/.venv/bin/python" run_stage.py structured_zero \
  --eval-per-label 1 \
  --train-per-label 1 \
  --workers 8 \
  --max-tokens 1200

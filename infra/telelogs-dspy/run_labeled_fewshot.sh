#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy
cd "$ROOT/code"
"$ROOT/.venv/bin/python" run_stage.py labeled_fewshot \
  --eval-per-label 8 \
  --train-per-label 4 \
  --workers 16 \
  --max-tokens 1200

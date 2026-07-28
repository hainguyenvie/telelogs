#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy
cd "$ROOT/code"
"$ROOT/.venv/bin/python" run_stage.py contrastive_fewshot \
  --run-name contrastive_residual_dev224 \
  --eval-per-label 28 \
  --eval-offset-per-label 4 \
  --workers 32 \
  --max-tokens 1200

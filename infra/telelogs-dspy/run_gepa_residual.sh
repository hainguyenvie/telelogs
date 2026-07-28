#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy
cd "$ROOT/code"
"$ROOT/.venv/bin/python" run_stage.py gepa \
  --run-name gepa_residual \
  --eval-per-label 4 \
  --train-focus residual \
  --workers 16 \
  --max-tokens 1200 \
  --gepa-max-metric-calls 512

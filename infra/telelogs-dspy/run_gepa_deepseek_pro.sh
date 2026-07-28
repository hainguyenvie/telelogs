#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy
set -a
source /workspace/telelogs-bench4/secrets/deepseek.env
set +a
cd "$ROOT/code"
"$ROOT/.venv/bin/python" run_stage.py gepa \
  --run-name gepa_deepseek_pro \
  --eval-per-label 28 \
  --eval-offset-per-label 4 \
  --gepa-val-per-label 4 \
  --train-focus residual \
  --workers 16 \
  --max-tokens 1200 \
  --reflection-provider deepseek-pro \
  --gepa-max-metric-calls 384

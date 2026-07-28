#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy
export DSPY_DATA="$ROOT/data/telelogs_official_facts.jsonl"
cd "$ROOT/code"
"$ROOT/.venv/bin/python" run_stage.py hybrid \
  --run-name hybrid_official_frozen \
  --eval-split official \
  --eval-per-label 0 \
  --workers 32 \
  --max-tokens 1200

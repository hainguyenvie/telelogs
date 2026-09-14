#!/bin/bash
# Q3: soft narration layer — separate prose from the audited evidence lines.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
PY="/workspace/telelogs-bench4/dspy/.venv/bin/python"
cd "$ROOT/code"
"$PY" run_tool_experiment.py --methods b3_react_verified --compiled "b3_react_verified=$S11" \
  --eval-split dev --eval-per-label 2 --workers 4 --max-tokens 1000 \
  --narrate --run-name tool_narrate_dev16

#!/bin/bash
# E1: single program, NON-OPTIONAL stage-2 measurement. Smoke first, then dev-96.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
PY="/workspace/telelogs-bench4/dspy/.venv/bin/python"
cd "$ROOT/code"
echo "### smoke 8 train cases"
"$PY" run_tool_experiment.py --methods b3_react_forced --compiled "b3_react_forced=$S11" \
  --eval-split train --eval-per-label 1 --workers 4 --max-tokens 1000 --run-name tool_smoke_forced
echo "### dev-96"
"$ROOT/run_dev96_compiled.sh" b3_react_forced "$S11" tool_dev96_s11boot26_forced

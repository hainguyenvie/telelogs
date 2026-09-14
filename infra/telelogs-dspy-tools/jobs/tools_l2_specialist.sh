#!/bin/bash
# Round-4 lever 3: dedicated residual decider instead of one monolithic prompt.
# Smoke first (8 train cases) so a signature bug shows up cheaply.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
PY="/workspace/telelogs-bench4/dspy/.venv/bin/python"
cd "$ROOT/code"
"$PY" run_tool_experiment.py --methods b3_react_specialist --compiled "b3_react_specialist=$S11" \
  --eval-split train --eval-per-label 1 --workers 4 --max-tokens 1000 --run-name tool_smoke_specialist
"$ROOT/run_dev96_compiled.sh"  b3_react_specialist "$S11" tool_dev96_s11_specialist
"$ROOT/run_sel200_compiled.sh" b3_react_specialist "$S11" tool_sel200_s11_specialist

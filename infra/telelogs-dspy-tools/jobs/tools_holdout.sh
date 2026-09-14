#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_holdout96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/bootstrap46_seeded4_b3/program.json" tool_holdout96_selected

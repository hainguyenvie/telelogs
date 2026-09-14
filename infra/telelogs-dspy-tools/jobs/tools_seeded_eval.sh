#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_opt_none.sh" b3_react_tools "$ROOT/seed_b3_calibrated.txt"
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/seeded_b3_react_tools/program.json" tool_dev96_b3seed
"$ROOT/run_opt_none.sh" b1_all_tools "$ROOT/seed_b1_calibrated.txt"
"$ROOT/run_dev96_compiled.sh" b1_all_tools \
  "$ROOT/results/optimized/seeded_b1_all_tools/program.json" tool_dev96_b1seed

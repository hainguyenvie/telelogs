#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_opt_bootstrap.sh" b3_react_tools "$ROOT/seed_b3_calibrated.txt" \
  bootstrap46_seeded_b3 "C4,C6"
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/bootstrap46_seeded_b3/program.json" tool_dev96_b3boot46
"$ROOT/run_opt_none.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v4.txt" seeded4_b3_react_tools
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/seeded4_b3_react_tools/program.json" tool_dev96_b3seed4

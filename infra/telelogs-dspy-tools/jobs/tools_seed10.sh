#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_opt_none.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v10.txt" seeded10_b3
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/seeded10_b3/program.json" tool_dev96_b3s10
"$ROOT/run_opt_bootstrap.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v10.txt" \
  bootstrap26_seeded10_b3 "C2,C6"
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/bootstrap26_seeded10_b3/program.json" tool_dev96_b3s10boot26
"$ROOT/run_opt_bootstrap.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v10.txt" \
  bootstrap46_seeded10_b3 "C4,C6"
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/bootstrap46_seeded10_b3/program.json" tool_dev96_b3s10boot46

#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_opt_none.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v8.txt" seeded8_b3
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/seeded8_b3/program.json" tool_dev96_b3s8
"$ROOT/run_opt_bootstrap.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v8.txt" \
  bootstrap46_seeded8_b3 "C4,C6"
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/bootstrap46_seeded8_b3/program.json" tool_dev96_b3s8boot46

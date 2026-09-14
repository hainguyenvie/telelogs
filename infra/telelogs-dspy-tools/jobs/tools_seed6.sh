#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_opt_none.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v6.txt" seeded6_b3
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/seeded6_b3/program.json" tool_dev96_b3s6
"$ROOT/run_opt_bootstrap.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v6.txt" \
  bootstrap46_seeded6_b3 "C4,C6"
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/bootstrap46_seeded6_b3/program.json" tool_dev96_b3s6boot46

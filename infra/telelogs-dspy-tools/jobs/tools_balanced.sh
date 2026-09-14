#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_opt_bootbal.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v4.txt" \
  bootbal46_seeded4_b3 "C4,C6"
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/bootbal46_seeded4_b3/program.json" tool_dev96_b3s4bal46

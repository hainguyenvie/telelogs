#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_opt_bootbal.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v11.txt" \
  bootbal1346_seeded11_b3 "C1,C3,C4,C6"
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/bootbal1346_seeded11_b3/program.json" tool_dev96_b3s11bal1346

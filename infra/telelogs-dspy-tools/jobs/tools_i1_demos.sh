#!/bin/bash
# E3: demo budget — every compiled program so far used max_bootstrapped_demos=2.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_opt_bootstrap_n.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v11.txt" boot4_seeded11_b3 4
"$ROOT/run_dev96_compiled.sh" b3_react_verified "$ROOT/results/optimized/boot4_seeded11_b3/program.json" tool_dev96_boot4_verified
"$ROOT/run_opt_bootstrap_n.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v11.txt" boot6_seeded11_b3 6
"$ROOT/run_dev96_compiled.sh" b3_react_verified "$ROOT/results/optimized/boot6_seeded11_b3/program.json" tool_dev96_boot6_verified

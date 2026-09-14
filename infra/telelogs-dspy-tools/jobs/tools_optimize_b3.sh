#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_opt_bootstrap.sh" b3_react_tools "$ROOT/seed_b3_calibrated.txt"
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/bootstrap_seeded_b3_react_tools/program.json" tool_dev96_b3boot
"$ROOT/run_opt_gepa.sh" b3_react_tools "$ROOT/seed_b3_calibrated.txt"
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/gepa_flash_seeded_b3_react_tools/program.json" tool_dev96_b3gepa

#!/bin/bash
# MIPROv2 attempt 3. Attempt 1 died on the missing optuna dep; attempt 2 finished the
# search (default program 62.5 -> best 75.0 on its own val-32) and then lost the whole
# compile to orjson refusing a non-string dict key inside MIPRO's bootstrapped demos.
# optimize_tool_program.py now falls back to the .pkl form dspy also reads.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_opt_mipro.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v11.txt" mipro_seeded11_b3

MP=$ROOT/results/optimized/mipro_seeded11_b3/program.json
[ -f "$MP" ] || MP=$ROOT/results/optimized/mipro_seeded11_b3/program.pkl
echo "using compiled program: $MP"
"$ROOT/run_dev96_compiled.sh"  b3_react_tools    "$MP" tool_dev96_mipro11
"$ROOT/run_dev96_compiled.sh"  b3_react_verified "$MP" tool_dev96_mipro11_verified
"$ROOT/run_sel200_compiled.sh" b3_react_verified "$MP" tool_sel200_mipro11_verified

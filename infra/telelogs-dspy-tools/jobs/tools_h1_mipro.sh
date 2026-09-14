#!/bin/bash
# E2: MIPROv2 — the one DSPy optimizer never run on this task (26 compiled
# programs so far: seed-only, bootstrap, bootstrap_balanced, one failed GEPA).
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_opt_mipro.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v11.txt" mipro_seeded11_b3
MP=$ROOT/results/optimized/mipro_seeded11_b3/program.json
"$ROOT/run_dev96_compiled.sh" b3_react_tools "$MP" tool_dev96_mipro11
"$ROOT/run_dev96_compiled.sh" b3_react_verified "$MP" tool_dev96_mipro11_verified

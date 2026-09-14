#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_opt_none.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v3.txt" seeded3_b3_react_tools
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/seeded3_b3_react_tools/program.json" tool_dev96_b3seed3
"$ROOT/run_opt_gepa.sh" b3_react_tools "$ROOT/seed_b3_calibrated.txt" qwen gepa_qwen_seeded_b3
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/gepa_qwen_seeded_b3/program.json" tool_dev96_b3gepaqwen

#!/bin/bash
# Does the forced-measurement mechanism transfer across compiled programs?
#   s4boot46 already called stage-2 in 376/376 official residual cases -> control,
#   boot4 is the 4-demo program from the demo-budget probe.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_dev96_compiled.sh" b3_react_forced \
  "$ROOT/results/optimized/bootstrap46_seeded4_b3/program.json" tool_dev96_s4boot46_forced
"$ROOT/run_dev96_compiled.sh" b3_react_forced \
  "$ROOT/results/optimized/boot4_seeded11_b3/program.json" tool_dev96_boot4_forced

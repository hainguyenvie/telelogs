#!/bin/bash
# E1 confirmation: holdout-96 + official-864 for the forced-measurement program.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
"$ROOT/run_holdout96_compiled.sh" b3_react_forced "$S11" tool_holdout96_s11boot26_forced
"$ROOT/run_official864_compiled.sh" b3_react_forced "$S11" tool_off864_s11boot26_forced

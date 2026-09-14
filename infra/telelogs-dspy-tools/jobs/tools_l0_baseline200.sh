#!/bin/bash
# Baselines on the new 200-case selection set, so v2.2 has something to beat:
# the shipped single program (v2.1 audit) and the forced program under v2.1.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
S4=$ROOT/results/optimized/bootstrap46_seeded4_b3/program.json
"$ROOT/run_sel200_compiled.sh" b3_react_verified "$S11" tool_sel200_s11_verified_v21
"$ROOT/run_sel200_compiled.sh" b3_react_forced   "$S11" tool_sel200_s11_forced_v21
"$ROOT/run_sel200_compiled.sh" b3_react_verified "$S4"  tool_sel200_s4_verified_v21

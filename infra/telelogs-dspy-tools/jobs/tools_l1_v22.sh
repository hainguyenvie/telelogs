#!/bin/bash
# Verifier v2.2 (observation-grounded witness check) x {forced, plain audit} x {s11, s4},
# scored on dev-96 AND the fresh 200-case train-split selection set.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
S4=$ROOT/results/optimized/bootstrap46_seeded4_b3/program.json

# a) v2.2a = zero-false-alarm branches only
"$ROOT/run_dev96_compiled.sh"  b3_react_forced   "$S11" tool_dev96_s11_forced_v22a
"$ROOT/run_sel200_compiled.sh" b3_react_forced   "$S11" tool_sel200_s11_forced_v22a
"$ROOT/run_dev96_compiled.sh"  b3_react_verified "$S11" tool_dev96_s11_verified_v22a
"$ROOT/run_sel200_compiled.sh" b3_react_verified "$S11" tool_sel200_s11_verified_v22a
# s4 never retried under v2.1 (attempts=1 everywhere); v2.2 flags 165/864 of its traces
"$ROOT/run_dev96_compiled.sh"  b3_react_forced   "$S4"  tool_dev96_s4_forced_v22a
"$ROOT/run_sel200_compiled.sh" b3_react_forced   "$S4"  tool_sel200_s4_forced_v22a

# b) v2.2b = also the C3-fallback branch (86.4% precision, 16 false alarms offline)
export TELELOGS_VERIFIER_C3_FALLBACK=1
"$ROOT/run_dev96_compiled.sh"  b3_react_forced   "$S11" tool_dev96_s11_forced_v22b
"$ROOT/run_sel200_compiled.sh" b3_react_forced   "$S11" tool_sel200_s11_forced_v22b

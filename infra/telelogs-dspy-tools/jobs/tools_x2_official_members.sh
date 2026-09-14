#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
S4=$ROOT/results/optimized/bootstrap46_seeded4_b3/program.json
"$ROOT/run_official864_compiled.sh" b3_react_verified "$S11" tool_off864_s11boot26_verified
"$ROOT/run_official864_compiled.sh" b3_react_verified "$S4" tool_off864_s4boot46_verified
"$ROOT/run_official864_compiled.sh" b3_react_tools "$S11" tool_off864_s11boot26_think1 think

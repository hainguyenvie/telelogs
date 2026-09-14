#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
"$ROOT/run_dev96_compiled.sh" b3_react_verified "$S11" tool_dev96_s11boot26_verified21
"$ROOT/run_holdout96_compiled.sh" b3_react_verified "$S11" tool_holdout96_s11boot26_verified21
"$ROOT/run_official864_compiled.sh" b3_react_verified "$S11" tool_off864_s11boot26_verified21

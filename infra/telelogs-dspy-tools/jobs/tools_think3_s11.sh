#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_dev96_compiled_think.sh" b3_react_tools \
  "$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json" tool_dev96_s11boot26_think3

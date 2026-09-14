#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_holdout96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json" tool_holdout96_s11boot26

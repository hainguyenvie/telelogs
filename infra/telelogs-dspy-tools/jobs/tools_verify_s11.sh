#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_dev96_compiled.sh" b3_react_verified \
  "$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json" tool_dev96_s11boot26_verified

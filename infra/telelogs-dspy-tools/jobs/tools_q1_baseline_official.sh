#!/bin/bash
# The one missing cell in the per-layer value table: plain ReAct + tools on
# official-864, with NO verifier, NO forced measurement, NO specialist.
#
# Every other layer already has an official number (verifier v2.1 76.85,
# forced 76.27, specialist 86.23), so without this one the contribution of
# layer 2 can only be quoted from dev-96 — which has misled three times.
# With it, every arrow in the architecture figure becomes a paired McNemar on
# the same 864 cases.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
"$ROOT/run_official864_compiled.sh" b3_react_tools "$S11" tool_off864_s11boot26_plain

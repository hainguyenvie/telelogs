#!/bin/bash
# PRE-REGISTERED confirmation for the residual specialist.
#
# Chosen on the round-4 selection set only (dev-96 + sel200 = 296 cases):
#   s11 + verifier v2.1        216/296 = 72.97%
#   s11 + forced measurement   229/296 = 77.36%   (McNemar vs v2.1 p=0.066 — not significant)
#   s11 + residual specialist  255/296 = 86.15%   (McNemar vs forced 13:39, p=4.1e-04)
# Config frozen: method b3_react_specialist, program bootstrap26_seeded11_b3,
# verifier v2.1 (v2.2 NOT uploaded yet), max_tokens 1000, workers 8/12.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
"$ROOT/run_holdout96_compiled.sh" b3_react_specialist "$S11" tool_holdout96_s11_specialist
"$ROOT/run_official864_compiled.sh" b3_react_specialist "$S11" tool_off864_s11_specialist

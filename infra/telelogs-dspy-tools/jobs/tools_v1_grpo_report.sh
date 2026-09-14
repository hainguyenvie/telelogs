#!/bin/bash
# Fill the champion's (specialist pipeline on GRPO weights, 88.77% official)
# remaining report gaps:
#   1. holdout-96      — the champion has dev-96 and official numbers but no
#                        holdout; the report's split table needs all four.
#   2. sel200          — routing/arbiter work must be fitted on selection data;
#                        fitting on official is forbidden. This is the missing
#                        selection half for the GRPO pipeline.
#   3. narrated dev-96 — the soft-reasoning samples shown in the report must
#                        come from the shipped configuration, not from the
#                        base-weight run we validated the plumbing on.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
export DSPY_API_BASE=http://telelogs-rl-vllm:8000/v1

"$ROOT/run_holdout96_compiled.sh" b3_react_specialist "$S11" tool_holdout96_s11_specialist_grpo1
"$ROOT/run_sel200_compiled.sh"   b3_react_specialist "$S11" tool_sel200_s11_specialist_grpo1

cd "$ROOT/code"
"/workspace/telelogs-bench4/dspy/.venv/bin/python" run_tool_experiment.py \
  --methods b3_react_specialist \
  --compiled "b3_react_specialist=$S11" \
  --eval-split dev --eval-per-label 12 --workers 8 --max-tokens 1000 \
  --narrate \
  --run-name tool_dev96_s11_specialist_grpo1_narrated

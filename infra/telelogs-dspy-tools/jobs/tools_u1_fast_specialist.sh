#!/bin/bash
# D-program: drop the ReAct re-run that the specialist makes redundant.
#
# Today, when the audit's only complaint is "a residual conclusion without its
# stage-two measurements", the pipeline re-runs the entire trajectory so the
# model can look at the numbers it skipped — about +7.7 LM calls, on 41 of 96
# dev cases. But the specialist then redecides from those same numbers anyway,
# so the re-run's answer is discarded. b3_react_specialist_fast attaches the
# measurements and goes straight to the specialist.
#
# This is an A/B, not a rollout. The claim being tested is that the re-run does
# no work: identical answers would confirm it, and any difference is the real
# cost of the shortcut. Stored baselines to compare against:
#   dev-96   b3_react_specialist  89/96 = 92.71%, 11.38 LM calls
#   sel200   b3_react_specialist  83.00%
# Selection stays on the pooled 296; official is not touched by this job.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
"$ROOT/run_dev96_compiled.sh"  b3_react_specialist_fast "$S11" tool_dev96_s11_specialist_fast
"$ROOT/run_sel200_compiled.sh" b3_react_specialist_fast "$S11" tool_sel200_s11_specialist_fast

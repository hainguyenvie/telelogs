#!/bin/bash
# Presentation pass, three changes, none of which may move the answer:
#   - specialist no longer duplicates the first pass's residual lines in
#     `reasoning`; the audited text is preserved verbatim in `audit_trace`
#   - narration is generated after the label is locked (`answer` is an
#     InputField, so it structurally cannot change the verdict)
#   - every number in the narrative is checked against the observations by
#     plain code and reported in `narrative_ungrounded`
#
# dev-96 with narration on, against the stored 89/96 = 92.71% without it. The
# accuracy is expected to be identical: this run exists to prove the plumbing
# did not disturb the decision, and to measure the grounding rate at n=96
# instead of the n=16 we had.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
cd "$ROOT/code"
"/workspace/telelogs-bench4/dspy/.venv/bin/python" run_tool_experiment.py \
  --methods b3_react_specialist \
  --compiled "b3_react_specialist=$S11" \
  --eval-split dev --eval-per-label 12 --workers 8 --max-tokens 1000 \
  --narrate \
  --run-name tool_dev96_s11_specialist_narrated

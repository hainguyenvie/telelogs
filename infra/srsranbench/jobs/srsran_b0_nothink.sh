#!/bin/bash
# Closed-book baseline, no native thinking — the arm the harness's own prompt
# asks for ("the entire content of your response should be 'ANSWER: $LETTER'").
#
# Both arms below run on the same 1502 rows. The permuted arm is the integrity
# control: identical questions and choices, gold position made uniform, so the
# gap between the two is the share of the plain score that is the position
# artefact rather than srsRAN knowledge.
#
# Reference points to compare against:
#   always-A constant   1139/1502 = 75.83%   (computed offline, no model)
#   always-A permuted    383/1502 = 25.50%
#   Gemini 3 Flash       82.8%               (GSMA published reference)
set -euo pipefail
ROOT=/workspace/telelogs-bench4/srsranbench
PY=/workspace/telelogs-bench4/dspy/.venv/bin/python
cd "$ROOT/code"

"$PY" run_baseline.py \
  --data "$ROOT/data/test.jsonl" \
  --out "$ROOT/results/b0_nothink" \
  --workers 16 --max-tokens 32 --temperature 0.0

"$PY" run_baseline.py \
  --data "$ROOT/data/test.jsonl" \
  --out "$ROOT/results/b0_nothink_perm" \
  --permute \
  --workers 16 --max-tokens 32 --temperature 0.0

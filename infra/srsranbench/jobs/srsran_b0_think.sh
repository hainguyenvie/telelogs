#!/bin/bash
# Same two arms with Qwen3 native thinking enabled.
#
# The point is not to expect a gain — the questions are short identifier
# lookups, not derivations — but to measure the cost side that the TeleLogs
# track already got bitten by: long reasoning under a token cap never emits the
# 'ANSWER:' line and scores wrong (commit 09fa2ed, 299/320 collection failures).
# max_tokens is set high enough that truncation, if it appears in the summary's
# `truncated` count, is the model's doing and not the cap's.
#
# temperature 0.6 / top_p 0.95 / top_k 20 is Qwen3's recommended thinking
# sampling and is what full4_eval.py used for the published 4-benchmark base.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/srsranbench
PY=/workspace/telelogs-bench4/dspy/.venv/bin/python
cd "$ROOT/code"

"$PY" run_baseline.py \
  --data "$ROOT/data/test.jsonl" \
  --out "$ROOT/results/b0_think" \
  --thinking \
  --workers 16 --max-tokens 6000 --temperature 0.6

"$PY" run_baseline.py \
  --data "$ROOT/data/test.jsonl" \
  --out "$ROOT/results/b0_think_perm" \
  --thinking --permute \
  --workers 16 --max-tokens 6000 --temperature 0.6

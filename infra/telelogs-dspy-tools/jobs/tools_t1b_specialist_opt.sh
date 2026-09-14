#!/bin/bash
# Layer 4 is the only high-value prompt surface that never saw an optimizer:
# +9.96 points on official-864, hand-written instruction, zero demos.
#
# Two stages. Collection runs the pipeline up to layer 3 and freezes the
# specialist's two inputs, which do not depend on the specialist's own prompt;
# after that every optimizer round costs one LM call per example instead of
# eleven, which is why this surface is affordable to search and layer 1 was not.
#
# Selection rule, fixed before any number exists: the pooled dev-96 + sel200
# residual subset decides, by paired McNemar against the hand-written baseline.
# Holdout and official are not touched by this job.
set -euo pipefail
# t1b: identical to t1; re-run after fixing the missing enable_thinking=False
# in the two new scripts (t1 lost 299/320 collection cases to <think> truncation).
ROOT=/workspace/telelogs-bench4/dspy-tools
PY=/workspace/telelogs-bench4/dspy/.venv/bin/python
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
SETS=$ROOT/results/residual_sets
mkdir -p "$SETS"

echo "=== collect: train (residual zone only) ==="
"$PY" "$ROOT/collect_residual_dataset.py" --raw "$ROOT/data/train.json" \
  --split train --per-label 40 --program "$S11" --workers 8 --out "$SETS/train.jsonl"

echo "=== collect: dev-96 ==="
"$PY" "$ROOT/collect_residual_dataset.py" --raw "$ROOT/data/train.json" \
  --split dev --per-label 12 --program "$S11" --workers 8 --out "$SETS/dev96.jsonl"

echo "=== collect: sel200 ==="
"$PY" "$ROOT/collect_residual_dataset.py" --raw "$ROOT/data/train.json" \
  --split train --per-label 25 --offset-per-label 8 --program "$S11" --workers 8 \
  --out "$SETS/sel200.jsonl"

for demos in 2 4 8; do
  echo "=== optimize: bootstrap, $demos demos ==="
  "$PY" "$ROOT/optimize_specialist.py" \
    --train "$SETS/train.jsonl" --eval "$SETS/dev96.jsonl" "$SETS/sel200.jsonl" \
    --optimizer bootstrap --demos "$demos" --workers 8 \
    --out "$ROOT/results/optimized/specialist_boot$demos" || echo "boot$demos failed, continuing"
done

echo "=== optimize: MIPROv2 ==="
"$PY" -m pip install -q optuna 2>/dev/null || true
"$PY" "$ROOT/optimize_specialist.py" \
  --train "$SETS/train.jsonl" --eval "$SETS/dev96.jsonl" "$SETS/sel200.jsonl" \
  --optimizer mipro --demos 4 --trials 12 --workers 8 \
  --out "$ROOT/results/optimized/specialist_mipro" || echo "mipro failed"

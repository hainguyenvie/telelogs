#!/bin/bash
# GRPO v1 on the frozen test file. dev-96 said the RL weights are a wash inside
# the shipped pipeline (91.67 vs 92.71, p = 1.0) — but dev-96 has misled three
# times, so the claim deserves the same 864 cases every other configuration was
# judged on. Reported whichever way it lands; the decision not to ship is
# already made on cost and redundancy, not on this number.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
PY=/workspace/telelogs-bench4/dspy/.venv/bin/python
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
export DSPY_API_BASE=http://telelogs-rl-vllm:8000/v1

echo "=== GRPO, native single-turn, official-864 ==="
"$PY" "$ROOT/eval_grpo_native.py" \
  --raw "$ROOT/data/test_official864.json" --split all --per-label 108 \
  --base "$DSPY_API_BASE" --workers 8 \
  --out "$ROOT/results/grpo1_native_off864/summary.json" || echo "native official skipped (split layout)"

echo "=== GRPO inside the shipped pipeline, official-864 ==="
"$ROOT/run_official864_compiled.sh" b3_react_specialist "$S11" tool_off864_s11_specialist_grpo1

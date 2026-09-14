#!/bin/bash
# GRPO v1 evaluation batch. Training finished 2026-07-30 22:44 UTC (1048/1048,
# 2 epochs, 21h); the merged model is served at telelogs-rl-vllm impersonating
# served-model-name Qwen/Qwen3-8B, so only DSPY_API_BASE changes.
#
# The RL train split and this dev-96 come from the same train.json (md5
# c2e363e8...) through the same hash split, so the RL model has never seen
# these 96 cases.
#
# Three questions, in order of how much they decide:
#   1 native   — the model under EXACTLY its training conditions (single turn,
#                observations pre-supplied, no tools). Compare against the
#                pre-RL model on the same information state: 47.9% residual.
#   2 shipped  — the RL model inside b3_react_specialist (base model: 92.71%
#                dev-96 / 86.23% official). This is the only number that
#                decides whether RL ships.
#   3 react    — plain b3_react_tools, both programs. RL never saw a tool call,
#                so this is the tool-discipline integrity check; the gate half
#                is currently 432/432 and that is what is at risk.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
PY=/workspace/telelogs-bench4/dspy/.venv/bin/python
S11=$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json
S4=$ROOT/results/optimized/bootstrap46_seeded4_b3/program.json
export DSPY_API_BASE=http://telelogs-rl-vllm:8000/v1

echo "=== 1 native single-turn (GRPO training shape) ==="
"$PY" "$ROOT/eval_grpo_native.py" \
  --raw "$ROOT/data/train.json" --split dev --per-label 12 \
  --base "$DSPY_API_BASE" --workers 8 \
  --out "$ROOT/results/grpo1_native_dev96/summary.json"

echo "=== 1b native single-turn, BASE model (same script, controls the prompt) ==="
"$PY" "$ROOT/eval_grpo_native.py" \
  --raw "$ROOT/data/train.json" --split dev --per-label 12 \
  --base http://telelogs-bench4-vllm:8000/v1 --workers 8 \
  --out "$ROOT/results/base_native_dev96/summary.json"

echo "=== 2 shipped specialist on the RL model ==="
"$ROOT/run_dev96_compiled.sh" b3_react_specialist "$S11" tool_dev96_s11_specialist_grpo1

echo "=== 3 plain ReAct on the RL model (tool discipline) ==="
"$ROOT/run_dev96_compiled.sh" b3_react_tools "$S11" tool_dev96_s11boot26_grpo1
"$ROOT/run_dev96_compiled.sh" b3_react_tools "$S4"  tool_dev96_s4boot46_grpo1

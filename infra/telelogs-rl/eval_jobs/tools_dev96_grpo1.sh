#!/bin/bash
# Staged bench4-client job: dev-96 of both reference programs against the GRPO
# model served at telelogs-rl-vllm (run only after serve_grpo_model is up).
set -euo pipefail

export DSPY_API_BASE=http://telelogs-rl-vllm:8000/v1
ROOT=/workspace/telelogs-bench4/dspy-tools
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json" tool_dev96_s11boot26_grpo1
"$ROOT/run_dev96_compiled.sh" b3_react_tools \
  "$ROOT/results/optimized/bootstrap46_seeded4_b3/program.json" tool_dev96_s4boot46_grpo1

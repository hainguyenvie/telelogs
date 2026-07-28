#!/bin/bash
set -euo pipefail

export PYTHONUNBUFFERED=1
export BENCH4_ROOT=/workspace/telelogs-bench4
export VLLM_CHAT_URL=http://telelogs-bench4-vllm:8000/v1/chat/completions
export EVAL_WORKERS=${EVAL_WORKERS:-64}
export EVAL_MAX_TOKENS=${EVAL_MAX_TOKENS:-38000}

exec /workspace/telelogs/venv/bin/python \
  /workspace/telelogs-bench4/scripts/full4_eval.py

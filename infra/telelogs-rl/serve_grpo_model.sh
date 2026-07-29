#!/bin/bash
# Job: serve the merged GRPO model with the same flags as the bench4 baseline
# server, impersonating the same served-model-name so the dspy client can point
# DSPY_API_BASE at service telelogs-rl-vllm without any code change.
set -u

ROOT=/workspace/telelogs-rl
VENV="$ROOT/venv"
MODEL="$ROOT/models/qwen3-8b-grpo-v1"

export HF_HOME="$ROOT/cache/hf"
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export VLLM_WORKER_MULTIPROC_METHOD=spawn

while true; do
  echo "starting GRPO vLLM at $(date)"
  CUDA_VISIBLE_DEVICES=0 "$VENV/bin/python" -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" \
    --served-model-name Qwen/Qwen3-8B \
    --host 0.0.0.0 \
    --port 8000 \
    --dtype bfloat16 \
    --max-model-len 40960 \
    --gpu-memory-utilization 0.90 \
    --max-num-seqs 64 \
    --enable-prefix-caching \
    --reasoning-parser qwen3
  rc=$?
  echo "vLLM exited rc=$rc at $(date); restarting in 10 seconds"
  sleep 10
done

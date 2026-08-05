#!/bin/bash
# Serve AT&T's OTel-LLM-8.3B-IT on the bench4 GPU pod, behind the same
# telelogs-bench4-vllm Service the Qwen3-8B arm used.
#
# Why this model: on GSMA/leaderboard the sibling OTel-LLM-8.3B-QnA scores 0.912
# on teleqna against qwen3-8b's 0.745 at the same parameter count, and against
# qwen3-235b-a22b's 0.797 at 30x. The QnA variant's weights are not released;
# -IT is, apache-2.0, same 606k-record RAG SFT set (scanned clean: 15/10,000).
# This run measures how much of that gap is available from the public checkpoint
# under our own harness, with the prompt byte-identical to the Qwen3-8B arm.
#
# Weights are a local directory, not an HF id, because the pod runs with
# HF_HUB_OFFLINE=1 and HF_HOME on a read-only mount. Stored fp32 (33 GB on disk);
# served bfloat16 per the model's own config.
set -u

MODEL=/workspace/telelogs-bench4/models/OTel-LLM-8.3B-IT
PYTHON=/workspace/telelogs/venv/bin/python

export HF_HOME=/workspace/telelogs/cache/hf
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export VLLM_WORKER_MULTIPROC_METHOD=spawn
TP_SIZE=${VLLM_TENSOR_PARALLEL_SIZE:-1}

echo "model=$MODEL"
echo "python=$PYTHON"
echo "tensor_parallel_size=$TP_SIZE"
"$PYTHON" -c 'import vllm; print("vllm=" + vllm.__version__)'
nvidia-smi --query-gpu=name,uuid,memory.total,memory.used --format=csv,noheader

# 8192 is far above what this eval needs (prompts run ~150 tokens) and keeps the
# KV cache small; Gemma3's declared context would reserve most of the H200 for it.
while true; do
  echo "starting vLLM at $(date)"
  "$PYTHON" -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" \
    --served-model-name OTel-LLM-8.3B-IT \
    --host 0.0.0.0 \
    --port 8000 \
    --dtype bfloat16 \
    --tensor-parallel-size "$TP_SIZE" \
    --max-model-len 8192 \
    --gpu-memory-utilization 0.90 \
    --max-num-seqs 64 \
    --enable-prefix-caching
  rc=$?
  echo "vLLM exited rc=$rc at $(date); restarting in 10 seconds"
  sleep 10
done

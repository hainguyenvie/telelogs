#!/bin/bash
set -u

MODEL=/workspace/telelogs/cache/hf/hub/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218
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

# Keep the endpoint alive after a recoverable vLLM failure. Killing the runner
# process group through done/serve_qwen3_8b.kill still stops this loop.
while true; do
  echo "starting vLLM at $(date)"
  "$PYTHON" -m vllm.entrypoints.openai.api_server \
    --model "$MODEL" \
    --served-model-name Qwen/Qwen3-8B \
    --host 0.0.0.0 \
    --port 8000 \
    --dtype bfloat16 \
    --tensor-parallel-size "$TP_SIZE" \
    --max-model-len 40960 \
    --gpu-memory-utilization 0.90 \
    --max-num-seqs 64 \
    --enable-prefix-caching \
    --reasoning-parser qwen3
  rc=$?
  echo "vLLM exited rc=$rc at $(date); restarting in 10 seconds"
  sleep 10
done

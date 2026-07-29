#!/bin/bash
# Job: GRPO v1 — rollout server on GPU1, LoRA trainer on GPU0.
set -euo pipefail

ROOT=/workspace/telelogs-rl
VENV="$ROOT/venv"
MODEL=/workspace/telelogs/cache/hf/hub/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218

export HF_HOME="$ROOT/cache/hf"
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false

echo "starting trl vllm-serve on GPU1 at $(date)"
CUDA_VISIBLE_DEVICES=1 "$VENV/bin/trl" vllm-serve \
  --model "$MODEL" \
  --port 8700 \
  --gpu-memory-utilization 0.85 \
  --max-model-len 12288 \
  > "$ROOT/done/vllm_rollout.log" 2>&1 &
VLLM_PID=$!

for _ in $(seq 1 120); do
  if curl -sf "http://127.0.0.1:8700/health/" > /dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$VLLM_PID" 2>/dev/null; then
    echo "rollout server died during startup; see done/vllm_rollout.log"
    exit 1
  fi
  sleep 5
done
echo "rollout server healthy at $(date)"

CUDA_VISIBLE_DEVICES=0 "$VENV/bin/python" "$ROOT/grpo_train.py" \
  --model-path "$MODEL" \
  --dataset "$ROOT/data/grpo_train.jsonl" \
  --output-dir "$ROOT/checkpoints/v1" \
  --merged-dir "$ROOT/models/qwen3-8b-grpo-v1" \
  --vllm-port 8700
rc=$?

kill "$VLLM_PID" 2>/dev/null || true
exit "$rc"

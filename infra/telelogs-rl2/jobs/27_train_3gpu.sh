#!/bin/bash
# Job: GRPO v2 across 3 H200s — rollout server on GPU2, LoRA trainer on GPU0-1.
#
# Three cards, not four. The 4-card attempt asked vLLM for data_parallel_size 2
# and got one replica: vLLM placed both on the same device, the second found
# 15.9 of 139.8 GiB free and died. Measured while the survivor ran: the two
# trainer cards sat at 100% in 5 of 6 samples and the rollout card at 0% in 6 of
# 6 — this workload is trainer-bound, so a second rollout replica would have
# bought nothing even had it started. The fourth card was pure waste and is
# handed back.
#
# v1 ran `--vllm-mode colocate` on a single card, which capped the rollout engine
# at 25% of that card's VRAM. GRPO spends most of its wall-clock generating
# (8 rollouts x ~2.1k prompts x 2 epochs), so a starved KV cache was the
# bottleneck. Giving the engine its own card at 0.85 utilisation and running the
# trainer data-parallel over the remaining three attacks both halves at once.
#
# Batch arithmetic, and why it is 2+2 rather than 1+3:
#
#   v1 (1 card, colocate) : 8 completions / 8 generations = 1 prompt per micro-step
#                           x grad_accum 4 = 4 prompts per optimizer step
#   1+3 (first attempt)   : 8 x 3 ranks / 8 = 3 prompts x grad_accum 4 = 12 per step
#                           -> 348 optimizer steps, a third of v1's, at the same
#                              learning rate. That is a hyperparameter change
#                              nobody chose, smuggled in by the GPU count.
#   2+2 (this file)       : 8 x 2 ranks / 8 = 2 prompts x grad_accum 2 = 4 per step
#                           -> identical optimizer trajectory to v1.
#
# So v2-vs-v1 compares the changes that were actually intended (magnitude ladder,
# arithmetic reward, answer extraction) and nothing else. The two freed cards go
# to the rollout engine, which is the half GRPO actually waits on.
#
# TRL enforces per_device x ranks divisible by num_generations: 8 x 2 = 16, 16 % 8 = 0.
set -euo pipefail

ROOT=/workspace/telelogs-rl
VENV="$ROOT/venv"
MODEL=/workspace/models/Qwen3-8B

export HF_HOME="$ROOT/cache/hf"
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
# 192-core box; leave torch's OMP pool room but do not let it oversubscribe.
export OMP_NUM_THREADS=8

nvidia-smi --query-gpu=index,name,uuid --format=csv

echo "=== rollout server on GPU2 $(date) ==="
CUDA_VISIBLE_DEVICES=2 "$VENV/bin/trl" vllm-serve \
  --model "$MODEL" \
  --port 8700 \
  --gpu-memory-utilization 0.85 \
  --max-model-len 12288 \
  > "$ROOT/done/vllm_rollout_v2.log" 2>&1 &
VLLM_PID=$!
echo "rollout pid=$VLLM_PID"

ready=0
for i in $(seq 1 120); do
  if "$VENV/bin/python" - <<'PY' 2>/dev/null
import sys, urllib.request
try:
    urllib.request.urlopen("http://127.0.0.1:8700/health/", timeout=3)
except Exception:
    sys.exit(1)
PY
  then ready=1; echo "rollout server healthy after ~$((i*5))s"; break; fi
  if ! kill -0 "$VLLM_PID" 2>/dev/null; then
    echo "rollout server died during startup; last 60 lines:"
    tail -60 "$ROOT/done/vllm_rollout_v2.log"
    exit 1
  fi
  sleep 5
done
if [ "$ready" != "1" ]; then
  echo "rollout server never became healthy; last 60 lines:"
  tail -60 "$ROOT/done/vllm_rollout_v2.log"
  kill "$VLLM_PID" 2>/dev/null || true
  exit 1
fi

echo "=== trainer on GPU0-1 $(date) ==="
CUDA_VISIBLE_DEVICES=0,1 "$VENV/bin/accelerate" launch \
  --num_processes 2 \
  --mixed_precision bf16 \
  "$ROOT/grpo_train_v2.py" \
  --model-path "$MODEL" \
  --dataset "$ROOT/data/grpo_train_v2.jsonl" \
  --output-dir "$ROOT/checkpoints/v2" \
  --merged-dir "$ROOT/models/qwen3-8b-grpo-v2" \
  --vllm-mode server \
  --vllm-port 8700 \
  --grad-accum 2
rc=$?

# Kill by PID: pkill -f would match this script's own command line.
kill "$VLLM_PID" 2>/dev/null || true
sleep 5
kill -9 "$VLLM_PID" 2>/dev/null || true

echo "train rc=$rc at $(date)"
exit $rc

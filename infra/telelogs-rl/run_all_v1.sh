#!/bin/bash
# Job: full GRPO v1 chain on the single hgx47 GPU — setup venv + weights,
# build the dataset (with a hard count assertion), then colocate training.
set -euo pipefail

ROOT=/workspace/telelogs-rl

echo "=== STAGE setup $(date) ==="
if [ -x "$ROOT/venv/bin/python" ] && [ -f "$ROOT/done/.setup_ok" ]; then
  echo "venv already present, skipping setup"
else
  bash "$ROOT/setup_env.sh"
  touch "$ROOT/done/.setup_ok"
fi

export HF_HOME="$ROOT/cache/hf"
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
SNAP=$("$ROOT/venv/bin/python" -c "from huggingface_hub import snapshot_download; print(snapshot_download('Qwen/Qwen3-8B'))")
echo "model snapshot: $SNAP"

echo "=== STAGE dataset $(date) ==="
"$ROOT/venv/bin/python" "$ROOT/make_grpo_dataset.py" \
  --raw "$ROOT/data/train.json" \
  --out "$ROOT/data/grpo_train.jsonl"
LINES=$(wc -l < "$ROOT/data/grpo_train.jsonl")
echo "dataset lines: $LINES"
if [ "$LINES" -lt 1500 ]; then
  echo "dataset too small ($LINES < 1500) — aborting before training"
  exit 1
fi

echo "=== STAGE train $(date) ==="
CUDA_VISIBLE_DEVICES=0 "$ROOT/venv/bin/python" "$ROOT/grpo_train.py" \
  --model-path "$SNAP" \
  --dataset "$ROOT/data/grpo_train.jsonl" \
  --output-dir "$ROOT/checkpoints/v1" \
  --merged-dir "$ROOT/models/qwen3-8b-grpo-v1" \
  --vllm-mode colocate
echo "=== DONE $(date) ==="

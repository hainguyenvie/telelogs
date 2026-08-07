#!/bin/bash
# Step 1 of the GRPO pipeline: profile pass-rate, then filter to the learnable
# band. Pure inference — runs on the vllm venv, no trl/peft needed. Profile
# whatever model GRPO will actually start from (MODEL_DIR), because pass rates
# move when the policy moves.
set -euo pipefail
ROOT=/workspace/teleqna-sft
BASE_ROOT=/workspace/telelogs-base
PYV="$BASE_ROOT/venvs/venv/bin/python"     # has vllm 0.11.0
BASE_MODEL="$BASE_ROOT/shared/hf-cache/hub/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218"
MODEL_DIR="${MODEL_DIR:-$BASE_MODEL}"
K="${K:-8}"
N="${N:-4000}"
export HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false

free_mib=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)
echo "GPU free=${free_mib}MiB  model=$MODEL_DIR  k=$K  n=$N"
[ "$free_mib" -gt 100000 ] || { echo "ABORT: GPU not free"; exit 14; }

echo "#### profile START $(date -Iseconds)"
timeout 21600 "$PYV" "$ROOT/infra/profile_pass_rate.py" \
  --model "$MODEL_DIR" \
  --data "$ROOT/data/grpo_full.jsonl" \
  --out "$ROOT/data/grpo_profile.jsonl" \
  -k "$K" --limit "$N" --max-tokens 1024 --temperature 1.0
echo "#### profile DONE $(date -Iseconds)"

"$PYV" "$ROOT/infra/filter_by_pass_rate.py" \
  --profile "$ROOT/data/grpo_profile.jsonl" \
  --out "$ROOT/data/grpo_band.jsonl" --lo 1 --limit 2000
head -200 "$ROOT/data/grpo_band.jsonl" > "$ROOT/data/grpo_smoke.jsonl"
wc -l "$ROOT/data/grpo_band.jsonl" "$ROOT/data/grpo_smoke.jsonl"
echo "#### filter DONE $(date -Iseconds)"

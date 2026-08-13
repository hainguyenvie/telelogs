#!/usr/bin/env bash
# Serve Qwen3.5-122B-A10B in BF16 on node cards 1+5 and score TeleQnA.
#
# Runs on the login pod, not through a job runner. That pod turns out to have
# the full device set injected (/dev/nvidia0..7, nvidiactl, uvm) plus 256GB of
# cgroup memory and a 32GB /dev/shm, so it can host the engine directly. The
# alternative was queueing behind the 5h GRPO run on spare4's single-slot runner
# for no gain.
#
# BF16, not the 4-bit build, and the reason is specific to this benchmark rather
# than general principle. Our remaining errors are distractor discrimination —
# the probe puts +15.8pp there — so the losses live on thin margins between
# close options, which is exactly where quantisation noise lands. A 10B-active
# MoE is also a bad host for 4-bit: moe_intermediate_size is 1024, so each
# expert is small and has little redundancy to absorb rounding. And dev-1000's
# MDE is ~2.2pp, so giving away 1-2 points to the weights format would cost the
# signal we are trying to read.
#
# Memory: 250GB of weights over 2 cards is 125.1GB each against 143.77GB, so
# 0.95 utilisation leaves ~11.5GB a card. That is ample here only because
# max_model_len is 8192 rather than the model's native 262144, and because the
# config has num_key_value_heads=2 with most layers on linear attention — the
# KV cache is small. Do not raise the context window to "be safe"; it is the one
# knob that turns a comfortable fit into an OOM.
#
# The checkpoint is the multimodal wrapper: config.json declares
# architectures=['Qwen3_5MoeForConditionalGeneration'] with a vision_config, and
# the nightly registers that name (verified against ModelRegistry, alongside the
# Qwen3_5MoeForCausalLM and Qwen3_5MoeMTP siblings). TeleQnA is pure text, so the
# vision tower is weight we would pay for and never call — language_model_only
# skips loading it. It is a real EngineArgs field, not a CLI-only switch, so it
# rides through the scorer's --engine-kwargs rather than needing a forked entry
# point.
set -euo pipefail
export CUDA_VISIBLE_DEVICES="${CARDS:-1,5}"
ENGINE_KWARGS="${ENGINE_KWARGS:-{\"language_model_only\": true\}}"
HOME_ROOT=/home/tensara
MODEL="${MODEL:-$HOME_ROOT/projects/telelogs/shared/models/Qwen3.5-122B-A10B}"
PY="${PY:-$HOME_ROOT/venv-vllm-nightly/bin/python}"
INFRA=$HOME_ROOT/projects/telelogs/runs/teleqna-sft/infra
DATA=$HOME_ROOT/projects/telelogs/runs/teleqna-sft/data
OUT="${OUT:-$HOME_ROOT/projects/telelogs/runs/teleqna-sft/results/vllm122b}"
export HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=8 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
mkdir -p "$OUT"

# Both cards, not just the first. TP=2 dies on whichever card is short, and the
# error surfaces as a confusing NCCL failure rather than as an OOM.
IFS=',' read -ra IDX <<< "$CUDA_VISIBLE_DEVICES"
for c in "${IDX[@]}"; do
  free=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits -i "$c")
  echo "card $c free=${free}MiB"
  [ "$free" -gt 135000 ] || { echo "ABORT: card $c has only ${free}MiB free"; exit 14; }
done

# Check the weights are all there before spending two card-loads on discovering
# they are not. The downloader has already produced a truncated shard once.
python3 - "$MODEL/.manifest" "$MODEL" <<'PY'
import os, sys
man, dest = sys.argv[1], sys.argv[2]
bad = []
for line in open(man):
    name, _, want = line.rstrip("\n").rpartition(" ")
    want = int(want)
    path = os.path.join(dest, name)
    have = os.path.getsize(path) if os.path.exists(path) else 0
    if want and have < want:
        bad.append((name, have, want))
if bad:
    print(f"ABORT: {len(bad)} files short, e.g. {bad[0]}")
    sys.exit(15)
print("weights complete")
PY

echo "#### dev-1000 START $(date -Iseconds)"
"$PY" "$INFRA/eval_dev_vllm.py" \
  --base "$MODEL" --data "$DATA/dev1000.jsonl" --out-dir "$OUT" \
  --tag dev1000 --with-base --thinking --tp 2 --gpu-mem 0.95 \
  --max-model-len 8192 ${ENGINE_KWARGS:+--engine-kwargs "$ENGINE_KWARGS"}
echo "#### dev-1000 DONE $(date -Iseconds)"

# Only after dev-1000 has a number. dev-1000 is the one split with twelve arms
# already scored on this harness, so it is where a 122B result means something
# immediately; the full 10,000 is for the headline and costs ten times as much.
echo "#### ot-full 10000 START $(date -Iseconds)"
"$PY" "$INFRA/eval_dev_vllm.py" \
  --base "$MODEL" --data "$DATA/otfull10000.jsonl" --out-dir "$OUT" \
  --tag otfull --with-base --thinking --tp 2 --gpu-mem 0.95 \
  --max-model-len 8192 ${ENGINE_KWARGS:+--engine-kwargs "$ENGINE_KWARGS"}
echo "#### ALL DONE $(date -Iseconds)"

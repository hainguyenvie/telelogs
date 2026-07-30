#!/bin/bash
# Merge a GRPO LoRA checkpoint into a standalone model directory, so a checkpoint
# can be served (and evaluated) without waiting for the whole run to finish.
#
# Usage as a pod job: merge_checkpoint.sh [checkpoint-dir|latest]
set -u

ROOT=/workspace/telelogs-rl
VENV="$ROOT/venv"
CKPT=${1:-latest}
OUT=${2:-$ROOT/models/qwen3-8b-grpo-v1}
BASE=${3:-Qwen/Qwen3-8B}

export HF_HOME="$ROOT/cache/hf"
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false

if [ "$CKPT" = "latest" ]; then
  CKPT=$(ls -d "$ROOT"/checkpoints/v1/checkpoint-* 2>/dev/null | sed 's/.*checkpoint-//' | sort -n | tail -1)
  CKPT="$ROOT/checkpoints/v1/checkpoint-$CKPT"
fi
echo "merging $CKPT -> $OUT"

"$VENV/bin/python" - "$CKPT" "$OUT" "$BASE" <<'PY'
import sys
import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

ckpt, out, base = sys.argv[1], sys.argv[2], sys.argv[3]
print("loading base", base, flush=True)
model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.bfloat16, device_map="cpu")
print("applying adapter", ckpt, flush=True)
model = PeftModel.from_pretrained(model, ckpt, torch_dtype=torch.bfloat16)
model = model.merge_and_unload()
model.save_pretrained(out, safe_serialization=True)
AutoTokenizer.from_pretrained(base).save_pretrained(out)
print("merged ->", out, flush=True)
PY

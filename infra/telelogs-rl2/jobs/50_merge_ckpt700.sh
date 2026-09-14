#!/bin/bash
# Job: turn the ckpt-700 LoRA adapter into a standalone servable model.
#
# Nothing servable exists right now: job 34 deleted its merge as soon as the
# score was written, and runs/rl-grpo2/models/qwen3-8b-grpo-v2 is NOT a
# substitute -- that one is 31 GB where a bf16 8B is ~16 GB, because TRL keeps
# fp32 master weights under mixed precision, and both DDP ranks wrote into it
# concurrently ("merged model saved" printed twice).
#
# Merged on CPU at bf16 in a single process, byte-identical in method to how
# checkpoint-300/700/1048 were merged for their ot-full scores. That is what
# makes this artefact the same object those numbers describe.
set -u

E=/workspace/eval
RL=/workspace/telelogs-rl
BASE=/workspace/models/Qwen3-8B
ADAPTER="$RL/results/ckpt_archive/checkpoint-700"
OUT="$E/models/qwen3-8b-grpo-v2-ckpt700"
PY="$RL/venv/bin/python"

[ -d "$ADAPTER" ] || { echo "missing $ADAPTER"; exit 1; }
echo "adapter md5: $(md5sum "$ADAPTER/adapter_model.safetensors")"
df -h "$E" | tail -1

if [ -f "$OUT/config.json" ] && [ -f "$OUT/model.safetensors.index.json" ]; then
  echo "merged model already present at $OUT"
else
  rm -rf "$OUT"; mkdir -p "$OUT"
  "$PY" - "$BASE" "$ADAPTER" "$OUT" <<'PY'
import sys, torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
base_path, adapter, out = sys.argv[1], sys.argv[2], sys.argv[3]
base = AutoModelForCausalLM.from_pretrained(base_path, torch_dtype=torch.bfloat16, device_map="cpu")
merged = PeftModel.from_pretrained(base, adapter).merge_and_unload()
merged.save_pretrained(out, safe_serialization=True)
AutoTokenizer.from_pretrained(base_path).save_pretrained(out)
print("merged ->", out)
PY
  rc=$?
  [ "$rc" = "0" ] || { echo "merge failed rc=$rc"; exit 1; }
fi

# A merge that silently produced fp32 would serve fine and quietly double the
# memory, so assert the dtype rather than trust it.
"$PY" - "$OUT" <<'PY'
import json, sys, pathlib
out = pathlib.Path(sys.argv[1])
cfg = json.loads((out / "config.json").read_text())
print("torch_dtype:", cfg.get("torch_dtype") or cfg.get("dtype"))
idx = json.loads((out / "model.safetensors.index.json").read_text())
total = idx["metadata"]["total_size"]
print(f"total_size: {total/1e9:.2f} GB across {len(set(idx['weight_map'].values()))} shards")
assert 14e9 < total < 19e9, f"unexpected size {total}, bf16 8B should be ~16 GB"
print("MERGE_OK")
PY
du -sh "$OUT"
df -h "$E" | tail -1

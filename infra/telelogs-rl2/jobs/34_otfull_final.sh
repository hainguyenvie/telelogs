#!/bin/bash
# Job: score the FINAL GRPO v2 checkpoint (1048) and a mid one (700) on
# official-864, two lanes in parallel on the pod's two cards. checkpoint-300 was
# scored by job 32 at 809/864.
#
# Merging here rather than using models/qwen3-8b-grpo-v2 that the trainer saved
# on its own, for two reasons. That directory is 31 GB where a bf16 8B is ~16 GB
# — TRL keeps fp32 master weights under mixed precision — and "merged model
# saved" was printed twice, once per DDP rank, so two processes wrote the same
# directory concurrently. Re-merging from the archived adapter in a single
# process at bf16 avoids both, and keeps the method byte-identical to how
# checkpoint-300 was merged, which is what makes the two scores comparable.
#
# Evaluating directly on ot-full, no dev-96 stage: dev-96 called base and
# checkpoint-300 a dead tie (89/96 each, McNemar p=1.0) with only 5 errors left
# per arm, and this repo has already recorded dev-96 returning a false negative
# on exactly this comparison — GRPO v1 tied there (3:4, p=1.0) and then took
# +2.55 points on official-864 (37:15, p=0.0032).
#
# Disk is at 97%: each merged model is ~16 GB, so every lane deletes its merge as
# soon as the eval finishes. Peak is two merges, not four.
set -u

E=/workspace/eval
RL=/workspace/telelogs-rl
BENCH4=/workspace/telelogs-bench4
PY="$BENCH4/dspy/.venv/bin/python"
VLLM_PY="$RL/venv/bin/python"
BASE=/workspace/models/Qwen3-8B
PROGRAM="$BENCH4/dspy-tools/results/optimized/bootstrap26_seeded11_b3/program.json"
PARQUET="$E/data/ot_full_telelogs_test.parquet"

export HF_HOME="$E/cache/hf"
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false

# job 32's merge is no longer needed; its results.jsonl is already written.
rm -rf "$E/models/grpo-v2-ckpt300"
df -h /workspace/eval | tail -1

score_one() {  # gpu port step
  local gpu=$1 port=$2 step=$3
  local adapter="$RL/results/ckpt_archive/checkpoint-$step"
  local merged="$E/models/merge-gpu$gpu"
  local out="$E/results/otfull_grpo_v2_ckpt$step"
  local vlog="$E/results/vllm_ckpt$step.log"

  [ -d "$adapter" ] || { echo "[$step] adapter missing, skipping"; return 0; }
  echo "[$step] === merge $(date -u +%H:%M:%S) ==="
  rm -rf "$merged"; mkdir -p "$merged" "$out"
  "$VLLM_PY" - <<PY || { echo "[$step] merge failed"; return 1; }
import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
base = AutoModelForCausalLM.from_pretrained("$BASE", torch_dtype=torch.bfloat16, device_map="cpu")
merged = PeftModel.from_pretrained(base, "$adapter").merge_and_unload()
merged.save_pretrained("$merged", safe_serialization=True)
AutoTokenizer.from_pretrained("$BASE").save_pretrained("$merged")
PY

  echo "[$step] === serve on GPU$gpu:$port $(date -u +%H:%M:%S) ==="
  CUDA_VISIBLE_DEVICES=$gpu "$VLLM_PY" -m vllm.entrypoints.openai.api_server \
    --model "$merged" --served-model-name "grpo-v2-ckpt$step" \
    --host 127.0.0.1 --port "$port" \
    --dtype bfloat16 --tensor-parallel-size 1 \
    --max-model-len 40960 --gpu-memory-utilization 0.90 \
    --max-num-seqs 64 --enable-prefix-caching --reasoning-parser qwen3 \
    > "$vlog" 2>&1 &
  local pid=$! ready=0
  for i in $(seq 1 120); do
    if "$VLLM_PY" - <<PY 2>/dev/null
import sys, urllib.request
try: urllib.request.urlopen("http://127.0.0.1:$port/health", timeout=3)
except Exception: sys.exit(1)
PY
    then ready=1; break; fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 10
  done
  if [ "$ready" != "1" ]; then
    echo "[$step] server never healthy"; tail -40 "$vlog"
    kill -9 "$pid" 2>/dev/null; rm -rf "$merged"; return 1
  fi
  echo "[$step] healthy, scoring official-864"

  ( cd "$E/code" && DSPY_API_BASE="http://127.0.0.1:$port/v1" DSPY_MODEL="openai/grpo-v2-ckpt$step" \
    "$PY" "$E/champion_gsma_full.py" \
      --parquet "$PARQUET" --program "$PROGRAM" --method b3_react_specialist \
      --workers 8 --max-tokens 1000 --out "$out" ) > "$E/results/otfull_ckpt$step.out" 2>&1
  local rc=$?

  kill "$pid" 2>/dev/null; sleep 5; kill -9 "$pid" 2>/dev/null
  rm -rf "$merged"          # 16 GB back, immediately
  echo "[$step] done rc=$rc $(date -u +%H:%M:%S)"
  return $rc
}

# Lane A takes two, lane B one, so both cards stay busy for most of the sweep.
( score_one 0 8020 1048 ) & LA=$!
( score_one 1 8021 700 )  & LB=$!
wait $LA; RA=$?
wait $LB; RB=$?

echo "=== sweep finished, lane rc: A=$RA B=$RB ==="
for s in 300 700 1048; do
  f="$E/results/otfull_grpo_v2_ckpt$s/summary.json"
  [ -f "$f" ] && "$VLLM_PY" -c "
import json; d=json.load(open('$f'))
print(f\"checkpoint-$s  {d['correct']}/{d['total']} = {d['accuracy']*100:.2f}%\")"
done
df -h /workspace/eval | tail -1
exit $(( RA + RB ))

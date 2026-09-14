#!/bin/bash
# Job: rerun ckpt-700 through the shipped pipeline at TENSOR-PARALLEL 1.
#
# Why. Job 34 scored this adapter at 814/864 serving TP=1; job 51 run A scored
# the same adapter at 811/864 serving TP=2. Paired by sample_index that is
# 17:14, p = 0.72 -- indistinguishable from noise -- but TWO things changed
# between those runs at once: the tensor-parallel degree, and the simple fact of
# being a different run. One comparison cannot separate them.
#
# This run holds TP at 1, matching job 34 exactly, so:
#   this vs job 34  (TP=1 vs TP=1)  = pure rerun spread
#   this vs job 51A (TP=1 vs TP=2)  = what the TP degree costs, if anything
#
# The answer decides what serving configuration the submission should publish.
# If the spread is the same either way, TP is irrelevant and the number simply
# carries a rerun band; if TP=2 is systematically worse, the endpoint must ship
# TP=1 and say so.
#
# One card, host GPU 4 (0/1 off limits, 2/3 busy with job 52, 6/7 other people).
set -u

E=/workspace/eval
RL=/workspace/telelogs-rl
BENCH4=/workspace/telelogs-bench4
MODEL="$E/models/qwen3-8b-grpo-v2-ckpt700"
SERVED=telelogs-grpo-v2-ckpt700-tp1
PORT=8500
GPUS=4
VLLM_PY="$RL/venv/bin/python"
DSPY_PY="$BENCH4/dspy/.venv/bin/python"
PROGRAM="$BENCH4/dspy-tools/results/optimized/bootstrap26_seeded11_b3/program.json"
OUT="$E/results/tp1_control_ckpt700"

export HF_HOME="$E/cache/hf"
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export VLLM_WORKER_MULTIPROC_METHOD=spawn
export OMP_NUM_THREADS=8

[ -f "$MODEL/config.json" ] || { echo "no merged model"; exit 1; }
nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv,noheader
echo "pinning to host GPU $GPUS"

CUDA_VISIBLE_DEVICES=$GPUS "$VLLM_PY" -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" --served-model-name "$SERVED" \
  --host 127.0.0.1 --port "$PORT" \
  --dtype bfloat16 --tensor-parallel-size 1 \
  --max-model-len 40960 --gpu-memory-utilization 0.90 \
  --max-num-seqs 64 --enable-prefix-caching --reasoning-parser qwen3 \
  > "$E/results/tp1_control.vllm.log" 2>&1 &
PID=$!

ready=0
for i in $(seq 1 150); do
  if "$VLLM_PY" - <<PY 2>/dev/null
import sys, urllib.request
try: urllib.request.urlopen("http://127.0.0.1:$PORT/health", timeout=3)
except Exception: sys.exit(1)
PY
  then ready=1; echo "healthy after ~$((i*10))s"; break; fi
  kill -0 "$PID" 2>/dev/null || { echo "vllm died"; break; }
  sleep 10
done
[ "$ready" = "1" ] || { tail -40 "$E/results/tp1_control.vllm.log"; kill -9 "$PID" 2>/dev/null; exit 1; }

echo "=== champion on ot-full, TP=1 $(date -u) ==="
( cd "$E/code" && DSPY_API_BASE="http://127.0.0.1:$PORT/v1" DSPY_MODEL="openai/$SERVED" \
  "$DSPY_PY" "$E/champion_gsma_full.py" \
    --parquet "$E/data/ot_full_telelogs_test.parquet" \
    --program "$PROGRAM" --method b3_react_specialist \
    --workers 8 --max-tokens 1000 --out "$OUT" ) > "$E/results/tp1_control.out" 2>&1
RC=$?

kill "$PID" 2>/dev/null; sleep 8; kill -9 "$PID" 2>/dev/null
echo "tp1 control rc=$RC $(date -u)"
"$DSPY_PY" - <<'PY'
import json, pathlib
p = pathlib.Path("/workspace/eval/results/tp1_control_ckpt700/summary.json")
if p.is_file():
    d = json.load(p.open())
    print(f"TP=1 control: {d['correct']}/{d['total']} = {d['accuracy']*100:.2f}%  errors {d['errors']} unboxed {d['unboxed']}")
    print("  job 34 (TP=1) was 814/864 = 94.21%; job 51A (TP=2) was 811/864 = 93.87%")
else:
    print("no summary.json")
PY
exit $RC

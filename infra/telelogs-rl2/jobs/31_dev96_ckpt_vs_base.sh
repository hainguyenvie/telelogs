#!/bin/bash
# Job: paired dev-96 comparison, GRPO v2 checkpoint-300 against the base model,
# through the deployed b3_react_specialist pipeline.
#
# Why dev and not official-864: selecting a checkpoint on the test set would make
# the reported number meaningless. Select on dev, confirm once on holdout, then
# score official — the discipline the rest of this repo already follows.
#
# Both endpoints are served under DISTINCT names. v1's provenance became
# unrecoverable precisely because serve_grpo_model.sh published GRPO weights as
# "Qwen/Qwen3-8B"; every log from this job says which weights answered it.
set -u

E=/workspace/eval
RL=/workspace/telelogs-rl
BENCH4=/workspace/telelogs-bench4
PY="$BENCH4/dspy/.venv/bin/python"
TRAIN_PY="$RL/venv/bin/python"
BASE=/workspace/models/Qwen3-8B
ADAPTER="$RL/results/ckpt_archive/checkpoint-300"
MERGED="$E/models/grpo-v2-ckpt300"
PROGRAM="$BENCH4/dspy-tools/results/optimized/bootstrap26_seeded11_b3/program.json"

export HF_HOME="$E/cache/hf"
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export DSPY_TOOL_ROOT="$E"
export TELELOGS_RAW_DATA="$RL/data/train.json"

nvidia-smi --query-gpu=index,name,uuid --format=csv || true

if [ ! -f "$MERGED/config.json" ]; then
  echo "=== merging $ADAPTER into base (bf16, on CPU) $(date) ==="
  "$TRAIN_PY" - <<PY
import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
base = AutoModelForCausalLM.from_pretrained("$BASE", torch_dtype=torch.bfloat16, device_map="cpu")
model = PeftModel.from_pretrained(base, "$ADAPTER")
merged = model.merge_and_unload()
merged.save_pretrained("$MERGED", safe_serialization=True)
AutoTokenizer.from_pretrained("$BASE").save_pretrained("$MERGED")
print("merged ->", "$MERGED")
PY
  rc=$?
  [ "$rc" = "0" ] || { echo "merge failed rc=$rc"; exit 1; }
else
  echo "merged model already present, skipping merge"
fi

serve() {  # gpu port model_dir served_name logfile
  CUDA_VISIBLE_DEVICES=$1 "$RL/venv/bin/python" -m vllm.entrypoints.openai.api_server \
    --model "$3" --served-model-name "$4" \
    --host 127.0.0.1 --port "$2" \
    --dtype bfloat16 --tensor-parallel-size 1 \
    --max-model-len 40960 --gpu-memory-utilization 0.90 \
    --max-num-seqs 64 --enable-prefix-caching --reasoning-parser qwen3 \
    > "$5" 2>&1 &
  echo $!
}

wait_health() {  # port pid logfile
  for i in $(seq 1 120); do
    if "$TRAIN_PY" - <<PY 2>/dev/null
import sys, urllib.request
try: urllib.request.urlopen("http://127.0.0.1:$1/health", timeout=3)
except Exception: sys.exit(1)
PY
    then echo "port $1 healthy after ~$((i*10))s"; return 0; fi
    kill -0 "$2" 2>/dev/null || { echo "server on port $1 died; last 40 lines:"; tail -40 "$3"; return 1; }
    sleep 10
  done
  echo "port $1 never became healthy"; tail -40 "$3"; return 1
}

echo "=== serving base on GPU0:8000 and ckpt300 on GPU1:8001 $(date) ==="
BASE_PID=$(serve 0 8000 "$BASE"   base-qwen3-8b     "$E/results/vllm_base.log")
CKPT_PID=$(serve 1 8001 "$MERGED" grpo-v2-ckpt300   "$E/results/vllm_ckpt300.log")
echo "base pid=$BASE_PID ckpt pid=$CKPT_PID"

wait_health 8000 "$BASE_PID" "$E/results/vllm_base.log" || { kill $BASE_PID $CKPT_PID 2>/dev/null; exit 1; }
wait_health 8001 "$CKPT_PID" "$E/results/vllm_ckpt300.log" || { kill $BASE_PID $CKPT_PID 2>/dev/null; exit 1; }

run_eval() {  # port served_name run_name
  DSPY_API_BASE="http://127.0.0.1:$1/v1" DSPY_MODEL="openai/$2" \
  "$PY" "$BENCH4/dspy-tools/code/run_tool_experiment.py" \
    --methods b3_react_specialist \
    --compiled "b3_react_specialist=$PROGRAM" \
    --eval-split dev --eval-per-label 12 \
    --workers 8 --max-tokens 1000 \
    --run-name "$3" \
    --dashboard-output "$E/results/$3_dashboard.json"
}

echo "=== dev-96, both arms in parallel $(date) ==="
run_eval 8000 base-qwen3-8b   dev96_base    > "$E/results/dev96_base.out"    2>&1 &
P1=$!
run_eval 8001 grpo-v2-ckpt300 dev96_ckpt300 > "$E/results/dev96_ckpt300.out" 2>&1 &
P2=$!
wait $P1; RC1=$?
wait $P2; RC2=$?

kill "$BASE_PID" "$CKPT_PID" 2>/dev/null || true
sleep 5
kill -9 "$BASE_PID" "$CKPT_PID" 2>/dev/null || true

echo "base rc=$RC1  ckpt300 rc=$RC2"
tail -5 "$E/results/dev96_base.out"    || true
tail -5 "$E/results/dev96_ckpt300.out" || true
exit $(( RC1 + RC2 ))

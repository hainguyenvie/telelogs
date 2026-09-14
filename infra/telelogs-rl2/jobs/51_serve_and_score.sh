#!/bin/bash
# Job: serve the merged ckpt-700 and score it two ways against the SAME endpoint.
#
#   A. champion_gsma_full.py on ot-full  -- the shipped DSPy ReAct + tools
#      pipeline. This is where 814/864 = 94.21% came from. Re-running it against
#      a freshly merged copy of the adapter is the only thing that proves the
#      artefact being handed over is the object that number describes.
#
#   B. full4_eval.py on the telelogs track -- the OFFICIAL harness, driven
#      through official_telelogs_eval.py. Raw question as a single user message,
#      no system prompt, thinking on, temperature 0.6 / top_p 0.95 / top_k 20 /
#      seed 42, max_tokens 38000.
#
# A and B are NOT two measurements of the same thing and must not be compared as
# if they were. Both apply the identical scorer -- last \boxed{...}, first
# integer -- but B asks the bare model to answer alone, while A wraps it in the
# tool pipeline. Worse for B here, the GRPO training prompts all ended in
# /no_think and were single-turn plain text, so B evaluates this model in a mode
# it was explicitly trained out of. Expect B to land far below A. That is the
# scaffold's contribution, not a defect in the weights.
#
# Serving flags are copied verbatim from serve_qwen3_8b.sh, the server the
# official base numbers were produced against -- including --max-model-len 40960,
# which is what makes max_tokens 38000 fit. The one deliberate difference is
# --served-model-name: the base server publishes "Qwen/Qwen3-8B" and v1 was
# served under that same disguise, which is precisely why nobody could later
# tell which run had used GRPO weights. This one says what it is.
set -u

E=/workspace/eval
RL=/workspace/telelogs-rl
BENCH4=/workspace/telelogs-bench4
MODEL="$E/models/qwen3-8b-grpo-v2-ckpt700"
SERVED=telelogs-grpo-v2-ckpt700
PORT=8300
GPUS=2,3                     # HOST indices. 0/1 off limits, 6/7 in use by others.
VLLM_PY="$RL/venv/bin/python"
DSPY_PY="$BENCH4/dspy/.venv/bin/python"
PROGRAM="$BENCH4/dspy-tools/results/optimized/bootstrap26_seeded11_b3/program.json"
OT_FULL_ROOT=/workspace/hf-cache/hub/datasets--GSMA--ot-full/snapshots/6319806f04783eafe04d9facf755d379c66b7664

export HF_HOME="$E/cache/hf"
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export VLLM_WORKER_MULTIPROC_METHOD=spawn
export OMP_NUM_THREADS=8

[ -f "$MODEL/config.json" ] || { echo "no merged model at $MODEL - run job 50 first"; exit 1; }
[ -f "$PROGRAM" ] || { echo "missing $PROGRAM"; exit 1; }
[ -d "$OT_FULL_ROOT/telelogs" ] || { echo "missing $OT_FULL_ROOT/telelogs"; exit 1; }

nvidia-smi --query-gpu=index,uuid,memory.used,utilization.gpu --format=csv
echo "pinning to host GPUs $GPUS"

echo "=== serve $SERVED on GPU $GPUS $(date -u) ==="
CUDA_VISIBLE_DEVICES=$GPUS "$VLLM_PY" -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --served-model-name "$SERVED" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --dtype bfloat16 \
  --tensor-parallel-size 2 \
  --max-model-len 40960 \
  --gpu-memory-utilization 0.90 \
  --max-num-seqs 64 \
  --enable-prefix-caching \
  --reasoning-parser qwen3 \
  > "$E/results/serve700.vllm.log" 2>&1 &
VLLM_PID=$!
echo "vllm pid=$VLLM_PID"

ready=0
for i in $(seq 1 150); do
  if "$VLLM_PY" - <<PY 2>/dev/null
import sys, urllib.request
try: urllib.request.urlopen("http://127.0.0.1:$PORT/health", timeout=3)
except Exception: sys.exit(1)
PY
  then ready=1; echo "healthy after ~$((i*10))s"; break; fi
  kill -0 "$VLLM_PID" 2>/dev/null || { echo "vllm died during startup"; break; }
  sleep 10
done
if [ "$ready" != "1" ]; then
  echo "server never became healthy; last 60 lines:"; tail -60 "$E/results/serve700.vllm.log"
  kill -9 "$VLLM_PID" 2>/dev/null; exit 1
fi

# Prove the endpoint answers, and under which name, before an hour is spent on it.
"$VLLM_PY" - <<PY
import json, urllib.request
models = json.load(urllib.request.urlopen("http://127.0.0.1:$PORT/v1/models", timeout=10))
print("served models:", [m["id"] for m in models["data"]])
req = urllib.request.Request("http://127.0.0.1:$PORT/v1/chat/completions",
    data=json.dumps({"model": "$SERVED",
                     "messages": [{"role": "user", "content": "Reply with exactly: \\\\boxed{7}"}],
                     "max_tokens": 64, "temperature": 0.0,
                     "chat_template_kwargs": {"enable_thinking": False}}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
out = json.load(urllib.request.urlopen(req, timeout=120))
print("smoke reply:", repr(out["choices"][0]["message"]["content"])[:200])
PY

stop_server() {
  kill "$VLLM_PID" 2>/dev/null || true
  sleep 8
  kill -9 "$VLLM_PID" 2>/dev/null || true
}

# ---- A: the shipped pipeline on ot-full, must reproduce 814/864 -------------
echo "=== A: champion_gsma_full on ot-full $(date -u) ==="
OUT_A="$E/results/serve700_otfull_champion"
( cd "$E/code" && DSPY_API_BASE="http://127.0.0.1:$PORT/v1" DSPY_MODEL="openai/$SERVED" \
  "$DSPY_PY" "$E/champion_gsma_full.py" \
    --parquet "$E/data/ot_full_telelogs_test.parquet" \
    --program "$PROGRAM" --method b3_react_specialist \
    --workers 8 --max-tokens 1000 --out "$OUT_A" ) > "$E/results/serve700_A.out" 2>&1
RC_A=$?
echo "A rc=$RC_A $(date -u)"
tail -3 "$E/results/serve700_A.out"

# ---- B: the official harness, telelogs track --------------------------------
echo "=== B: official full4_eval telelogs $(date -u) ==="
OUT_B="$E/results/serve700_otfull_official"
BENCH4_SCRIPTS="$BENCH4/scripts" \
OFFICIAL_RUN_DIR="$OUT_B" \
OFFICIAL_MODEL="$SERVED" \
OT_FULL_ROOT="$OT_FULL_ROOT" \
VLLM_CHAT_URL="http://127.0.0.1:$PORT/v1/chat/completions" \
EVAL_WORKERS=32 \
  "$VLLM_PY" "$E/official_telelogs_eval.py" > "$E/results/serve700_B.out" 2>&1
RC_B=$?
echo "B rc=$RC_B $(date -u)"
tail -5 "$E/results/serve700_B.out"

stop_server

echo "=== summary ==="
"$VLLM_PY" - <<'PY'
import json, pathlib
a = pathlib.Path("/workspace/eval/results/serve700_otfull_champion/summary.json")
if a.is_file():
    d = json.load(a.open())
    print(f"A  shipped DSPy pipeline, ot-full : {d['correct']}/{d['total']} = {d['accuracy']*100:.2f}%   (expected 814/864 = 94.21%)")
else:
    print("A  no summary.json")
b = pathlib.Path("/workspace/eval/results/serve700_otfull_official/results.jsonl")
if b.is_file():
    rows = [json.loads(l) for l in b.open() if l.strip()]
    rows = {r["sample_id"]: r for r in rows}
    ok = sum(bool(r["correct"]) for r in rows.values())
    err = sum(bool(r.get("error")) for r in rows.values())
    print(f"B  official raw harness, telelogs : {ok}/{len(rows)} = {ok/len(rows)*100:.2f}%  (errors {err})")
else:
    print("B  no results.jsonl")
PY
df -h "$E" | tail -1
exit $(( RC_A + RC_B ))

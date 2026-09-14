#!/bin/bash
# Job: score the DEPLOYED system through the OFFICIAL harness.
#
# The submitted configuration is not a bare model, it is GRPO ckpt-700 plus the
# DSPy tool pipeline, and gsma-labs/satellite scores whatever answers an OpenAI
# chat-completions URL -- its `open-local` provider is configured by nothing but
# VLLM_BASE_URL and nothing inspects what is behind it. So the pipeline goes
# behind the URL and the official telelogs task runs against that.
#
# Three processes, inside out:
#   vLLM on host GPU 2,3          serves the merged ckpt-700 weights   :8300
#   pipeline_openai_shim.py       runs ReAct + 6 tools per request     :8400
#   official_telelogs_eval.py     full4_eval.py, telelogs track only
#
# The harness sends the raw question and reads the last \boxed{}; between those
# two points sits the whole shipped pipeline instead of one forward pass. Its
# temperature/top_p/max_tokens/enable_thinking are accepted and ignored by the
# shim, which decodes at its own fixed settings -- honouring a caller's sampling
# knobs would make the endpoint behave differently per harness.
#
# Workers are 8, matching champion_gsma_full.py's --workers 8, so this run and
# job 51's run A differ in exactly one thing: who sends the question. The
# pipeline is deterministic at temperature 0, so the two should land on the same
# number, and a gap between them is a plumbing bug rather than a finding.
set -u

E=/workspace/eval
RL=/workspace/telelogs-rl
BENCH4=/workspace/telelogs-bench4
MODEL="$E/models/qwen3-8b-grpo-v2-ckpt700"
INNER_NAME=telelogs-grpo-v2-ckpt700
OUTER_NAME=telelogs-pipeline-ckpt700
INNER_PORT=8300
SHIM_PORT=8400
GPUS=2,3                     # HOST indices. 0/1 off limits, 6/7 in use by others.
VLLM_PY="$RL/venv/bin/python"
DSPY_PY="$BENCH4/dspy/.venv/bin/python"
PROGRAM="$BENCH4/dspy-tools/results/optimized/bootstrap26_seeded11_b3/program.json"
OT_FULL_ROOT=/workspace/hf-cache/hub/datasets--GSMA--ot-full/snapshots/6319806f04783eafe04d9facf755d379c66b7664
OUT="$E/results/official_pipeline_ckpt700"

export HF_HOME="$E/cache/hf"
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export VLLM_WORKER_MULTIPROC_METHOD=spawn
export OMP_NUM_THREADS=8

[ -f "$MODEL/config.json" ] || { echo "no merged model at $MODEL"; exit 1; }
[ -f "$E/pipeline_openai_shim.py" ] || { echo "missing shim"; exit 1; }
[ -d "$OT_FULL_ROOT/telelogs" ] || { echo "missing dataset"; exit 1; }

nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv,noheader
echo "pinning to host GPUs $GPUS"

echo "=== inner vLLM on GPU $GPUS $(date -u) ==="
CUDA_VISIBLE_DEVICES=$GPUS "$VLLM_PY" -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" --served-model-name "$INNER_NAME" \
  --host 127.0.0.1 --port "$INNER_PORT" \
  --dtype bfloat16 --tensor-parallel-size 2 \
  --max-model-len 40960 --gpu-memory-utilization 0.90 \
  --max-num-seqs 64 --enable-prefix-caching --reasoning-parser qwen3 \
  > "$E/results/official_pipeline.vllm.log" 2>&1 &
VLLM_PID=$!

wait_url() {  # url pid label
  local i
  for i in $(seq 1 150); do
    if "$VLLM_PY" - <<PY 2>/dev/null
import sys, urllib.request
try: urllib.request.urlopen("$1", timeout=3)
except Exception: sys.exit(1)
PY
    then echo "$3 healthy after ~$((i*10))s"; return 0; fi
    kill -0 "$2" 2>/dev/null || { echo "$3 process died"; return 1; }
    sleep 10
  done
  echo "$3 never became healthy"; return 1
}

cleanup() {
  kill "${SHIM_PID:-}" 2>/dev/null || true
  kill "$VLLM_PID" 2>/dev/null || true
  sleep 8
  kill -9 "${SHIM_PID:-}" 2>/dev/null || true
  kill -9 "$VLLM_PID" 2>/dev/null || true
}

wait_url "http://127.0.0.1:$INNER_PORT/health" "$VLLM_PID" "inner vLLM" || {
  tail -40 "$E/results/official_pipeline.vllm.log"; cleanup; exit 1; }

echo "=== pipeline shim on :$SHIM_PORT $(date -u) ==="
PIPELINE_CODE_DIR="$E/code" \
PIPELINE_SERVED_NAME="$OUTER_NAME" \
PIPELINE_INNER_BASE="http://127.0.0.1:$INNER_PORT/v1" \
PIPELINE_INNER_MODEL="$INNER_NAME" \
PIPELINE_PROGRAM="$PROGRAM" \
PIPELINE_METHOD=b3_react_specialist \
PIPELINE_MAX_TOKENS=1000 \
PIPELINE_PORT="$SHIM_PORT" \
  "$DSPY_PY" "$E/pipeline_openai_shim.py" > "$E/results/official_pipeline.shim.log" 2>&1 &
SHIM_PID=$!

wait_url "http://127.0.0.1:$SHIM_PORT/health" "$SHIM_PID" "shim" || {
  tail -40 "$E/results/official_pipeline.shim.log"; cleanup; exit 1; }

# End-to-end smoke on one real question before committing an hour: this is the
# exact shape satellite sends, so if the wiring is wrong it fails here.
"$DSPY_PY" - <<PY
import json, urllib.request
import pyarrow.parquet as pq
rows = pq.read_table("$OT_FULL_ROOT/telelogs/test-00000-of-00001.parquet").to_pylist()
print("models:", json.load(urllib.request.urlopen("http://127.0.0.1:$SHIM_PORT/v1/models"))["data"])
req = urllib.request.Request("http://127.0.0.1:$SHIM_PORT/v1/chat/completions",
    data=json.dumps({"model": "$OUTER_NAME",
                     "messages": [{"role": "user", "content": rows[0]["question"]}],
                     "temperature": 0.6, "top_p": 0.95, "max_tokens": 38000,
                     "chat_template_kwargs": {"enable_thinking": True}}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
out = json.load(urllib.request.urlopen(req, timeout=600))
body = out["choices"][0]["message"]["content"]
print("x_pipeline:", json.dumps(out["x_pipeline"])[:400])
print("tail of completion:", repr(body[-120:]))
print("gold:", rows[0]["answer"])
assert "\\\\boxed{" in body, "no boxed answer in shim completion"
print("SHIM_SMOKE_OK")
PY
rc=$?
[ "$rc" = "0" ] || { echo "shim smoke failed rc=$rc"; tail -40 "$E/results/official_pipeline.shim.log"; cleanup; exit 1; }

echo "=== official harness through the pipeline $(date -u) ==="
BENCH4_SCRIPTS="$BENCH4/scripts" \
OFFICIAL_RUN_DIR="$OUT" \
OFFICIAL_MODEL="$OUTER_NAME" \
OT_FULL_ROOT="$OT_FULL_ROOT" \
VLLM_CHAT_URL="http://127.0.0.1:$SHIM_PORT/v1/chat/completions" \
EVAL_WORKERS=8 \
  "$DSPY_PY" "$E/official_telelogs_eval.py" > "$E/results/official_pipeline.out" 2>&1
RC=$?
echo "official rc=$RC $(date -u)"
tail -6 "$E/results/official_pipeline.out"

cleanup

echo "=== summary ==="
"$DSPY_PY" - <<'PY'
import json, pathlib
p = pathlib.Path("/workspace/eval/results/official_pipeline_ckpt700/results.jsonl")
if not p.is_file():
    print("no results.jsonl"); raise SystemExit(0)
rows = {json.loads(l)["sample_id"]: json.loads(l) for l in p.open() if l.strip()}
ok = sum(bool(r["correct"]) for r in rows.values())
err = sum(bool(r.get("error")) for r in rows.values())
unboxed = sum(1 for r in rows.values() if not r.get("parsed_answer"))
print(f"OFFICIAL harness through the shipped pipeline: {ok}/{len(rows)} = {ok/len(rows)*100:.2f}%")
print(f"  errors {err}   no-boxed {unboxed}")
print("  compare with pair_runs.py against the champion runs of the same adapter;")
print("  do NOT hardcode an expected number here -- an earlier version of this line")
print("  claimed run A had scored 814 when it had in fact scored 811.")
PY
exit $RC

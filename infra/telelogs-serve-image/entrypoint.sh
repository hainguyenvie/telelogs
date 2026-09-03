#!/bin/bash
# Bring up the two processes that make one endpoint: the vLLM engine on a
# loopback port, and the pipeline shim on the published one.
#
#   serve    stay up so satellite (or anything speaking OpenAI chat-completions)
#            can score the system. This is the default.
#   verify   boot the same stack, run the OFFICIAL harness over official-864
#            against it, print the score, exit. Accuracy is something the
#            receiving machine measures, not something the image asserts.
#
# The container is not ready when it starts; it is ready when the shim answers a
# real diagnosis. Everything below exists so that a caller who gets a 200 from
# /v1/models is talking to a warmed, working pipeline -- not to a shim whose
# engine is still loading 16 GB, and not to one whose compiled program failed to
# load and will quietly answer every case wrong.
set -u

MODE=${1:-serve}
case "$MODE" in
  serve|verify) ;;
  *) echo "usage: [serve|verify]"; exit 2 ;;
esac

MODEL=${MODEL_DIR:-/opt/model}
PIPE=/opt/pipeline
INNER_NAME=telelogs-engine

echo "=== telelogs serving image  (mode: $MODE) ==="
echo "model       : $MODEL"
echo "served name : $PIPELINE_SERVED_NAME"
echo "shim        : $PIPELINE_HOST:$PIPELINE_PORT   <- satellite's VLLM_BASE_URL, plus /v1"
echo "engine      : 127.0.0.1:$VLLM_PORT  tp=$VLLM_TP  max_len=$VLLM_MAX_MODEL_LEN  util=$VLLM_GPU_MEM_UTIL"

nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader 2>/dev/null || {
  echo "!! no GPU visible. Run with --gpus all and the NVIDIA Container Toolkit installed."
  exit 1
}

python3 -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --served-model-name "$INNER_NAME" \
  --host 127.0.0.1 --port "$VLLM_PORT" \
  --dtype bfloat16 \
  --tensor-parallel-size "$VLLM_TP" \
  --max-model-len "$VLLM_MAX_MODEL_LEN" \
  --gpu-memory-utilization "$VLLM_GPU_MEM_UTIL" \
  --max-num-seqs "$VLLM_MAX_NUM_SEQS" \
  --enable-prefix-caching \
  --reasoning-parser qwen3 &
VLLM_PID=$!
SHIM_PID=""

# Forward a stop signal to both children rather than letting `docker stop` return
# while vLLM still holds the GPU.
shutdown() {
  echo "shutting down"
  [ -n "$SHIM_PID" ] && kill "$SHIM_PID" 2>/dev/null
  kill "$VLLM_PID" 2>/dev/null
  wait "$VLLM_PID" 2>/dev/null
  exit 0
}
trap shutdown TERM INT

wait_http() {   # url  pid  label  attempts  gap
  local i
  for i in $(seq 1 "$4"); do
    if python3 - "$1" <<'PY' 2>/dev/null
import sys, urllib.request
try:
    urllib.request.urlopen(sys.argv[1], timeout=3)
except Exception:
    sys.exit(1)
PY
    then echo "$3 ready after ~$((i * $5))s"; return 0; fi
    kill -0 "$2" 2>/dev/null || { echo "!! $3 process died during startup"; return 1; }
    sleep "$5"
  done
  echo "!! $3 never became healthy"
  return 1
}

echo "loading weights (typically 60-180s)"
wait_http "http://127.0.0.1:$VLLM_PORT/health" "$VLLM_PID" "engine" 180 5 || {
  kill -9 "$VLLM_PID" 2>/dev/null; exit 1; }

PIPELINE_CODE_DIR="$PIPE/code" \
PIPELINE_INNER_BASE="http://127.0.0.1:$VLLM_PORT/v1" \
PIPELINE_INNER_MODEL="$INNER_NAME" \
PIPELINE_PROGRAM="$PIPE/program.json" \
  "$PIPE/venv/bin/python" "$PIPE/pipeline_openai_shim.py" &
SHIM_PID=$!

wait_http "http://127.0.0.1:$PIPELINE_PORT/health" "$SHIM_PID" "shim" 60 2 || {
  kill -9 "$VLLM_PID" "$SHIM_PID" 2>/dev/null; exit 1; }

# One real diagnosis through the whole stack before announcing anything, using a
# REAL question out of the bundled official set.
#
# The first version of this self-test invented a short synthetic question. That
# was worse than no test: parse_case requires the engineering-parameter table and
# its separator, so the synthetic question died in the parser before the model was
# ever reached, and the check reported a failure that said nothing about the
# pipeline. A guard that cannot pass on a healthy system is not a guard.
#
# It also used to print READY regardless of the outcome. It no longer does: this
# block sets SELFTEST_OK, and serve mode refuses to announce readiness without it.
SELFTEST_OK=0
if "$PIPE/venv/bin/python" - <<'PY'; then SELFTEST_OK=1; fi
import json, os, sys, urllib.request

port = os.environ["PIPELINE_PORT"]
name = os.environ["PIPELINE_SERVED_NAME"]
rows = json.load(open("/opt/data/official_test_864/test.json", encoding="utf-8"))
row = rows[0]

try:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps({"model": name,
                         "messages": [{"role": "user", "content": row["question"]}]}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    payload = json.load(urllib.request.urlopen(request, timeout=600))
except Exception as exc:
    print(f"!! self-test request failed: {exc}")
    sys.exit(1)

body = payload["choices"][0]["message"]["content"] or ""
meta = payload.get("x_pipeline", {})
if meta.get("error"):
    print(f"!! self-test: the pipeline raised {meta['error']}")
    sys.exit(1)
if "\\boxed{" not in body:
    print("!! self-test: the answer carries no \\boxed{} and would score wrong")
    sys.exit(1)
print(f"self-test: answer={meta.get('pipeline_answer')} gold={row['answer']} "
      f"lm_calls={meta.get('lm_calls')} chars={len(body)}")
sys.exit(0)
PY

if [ "$MODE" = "verify" ]; then
  echo
  echo "=== verify: the OFFICIAL harness through the pipeline, 864 cases ==="
  echo "roughly 2 hours on an A40, about 40 minutes on an H200"
  OUT=${VERIFY_OUT:-/tmp/verify_official_864}
  BENCH4_SCRIPTS="$PIPE/harness" \
  OFFICIAL_RUN_DIR="$OUT" \
  OFFICIAL_MODEL="$PIPELINE_SERVED_NAME" \
  OT_FULL_ROOT=/opt/data/ot-full \
  VLLM_CHAT_URL="http://127.0.0.1:$PIPELINE_PORT/v1/chat/completions" \
  EVAL_WORKERS="$VERIFY_WORKERS" \
    "$PIPE/venv/bin/python" "$PIPE/official_telelogs_eval.py"
  rc=$?
  "$PIPE/venv/bin/python" "$PIPE/verify_summary.py" "$OUT" || true
  kill "$SHIM_PID" "$VLLM_PID" 2>/dev/null || true
  wait "$SHIM_PID" 2>/dev/null || true
  wait "$VLLM_PID" 2>/dev/null || true
  exit "$rc"
fi

if [ "$SELFTEST_OK" != "1" ]; then
  cat <<MSG

=== NOT READY ===
The endpoint is listening but a real question did not come back with a gradable
answer. Serving it now would return wrong answers to every request, quietly, so
this exits instead. Check the log above for the pipeline's own error.

MSG
  kill "$SHIM_PID" "$VLLM_PID" 2>/dev/null || true
  wait "$SHIM_PID" 2>/dev/null || true
  wait "$VLLM_PID" 2>/dev/null || true
  exit 1
fi

cat <<MSG

=== READY ===
Point satellite at:   VLLM_BASE_URL=http://<host>:$PIPELINE_PORT/v1
Model id:             $PIPELINE_SERVED_NAME
Sanity check:         curl http://<host>:$PIPELINE_PORT/v1/models
Re-score officially:  docker run --gpus all <image> verify

MSG

wait "$SHIM_PID"
echo "shim exited; stopping engine"
kill "$VLLM_PID" 2>/dev/null || true
wait "$VLLM_PID" 2>/dev/null || true

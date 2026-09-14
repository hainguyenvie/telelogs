#!/bin/bash
# Reproduce the 94.21% official-864 run on BASE Qwen/Qwen3-8B.
#
# Why this run exists: h200_use.md §11 states the 94.21% result ran on the
# untouched base checkpoint, and used that to justify deleting 215 GB of merged
# weights. The run's own logs say the opposite ("GRPO weights", in all three
# magrule jobs). The GRPO weights are now gone, so the claim can only be settled
# by measuring the base model under exactly the shipped pipeline.
#
# Everything except the weights is held identical to the 2026-08-03 run:
# same compiled program.json, same magnitude-rule specialist source, same
# official boxed-int scorer, same parquet, same workers/max-tokens.
set -u

REPRO=/workspace/repro
BENCH4=/workspace/telelogs-bench4
PY="$BENCH4/dspy/.venv/bin/python"
VLLM_PY=/workspace/telelogs/venv/bin/python
MODEL=/workspace/models/Qwen3-8B
PROGRAM="$BENCH4/dspy-tools/results/optimized/bootstrap26_seeded11_b3/program.json"
OUT="$REPRO/results/champion_base_magrule"
VLLM_LOG="$REPRO/results/vllm_base.log"

export HF_HOME=/workspace/telelogs/cache/hf
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export VLLM_WORKER_MULTIPROC_METHOD=spawn

mkdir -p "$OUT"

echo "=== GPU in use ==="
nvidia-smi --query-gpu=index,name,uuid,memory.total --format=csv || true

echo "=== weights being served (BASE, no GRPO) ==="
echo "model=$MODEL"
md5sum "$MODEL/config.json" || true
"$VLLM_PY" -c 'import vllm; print("vllm=" + vllm.__version__)'
# litellm stopped exporting __version__; read it from package metadata instead.
"$PY" -c 'import dspy;from importlib.metadata import version;print("dspy="+dspy.__version__,"litellm="+version("litellm"),"openai="+version("openai"))'

echo "=== starting vLLM ==="
"$VLLM_PY" -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --served-model-name Qwen/Qwen3-8B \
  --host 127.0.0.1 \
  --port 8000 \
  --dtype bfloat16 \
  --tensor-parallel-size 1 \
  --max-model-len 40960 \
  --gpu-memory-utilization 0.90 \
  --max-num-seqs 64 \
  --enable-prefix-caching \
  --reasoning-parser qwen3 \
  > "$VLLM_LOG" 2>&1 &
VLLM_PID=$!
echo "vllm pid=$VLLM_PID"

# Poll /health with python: curl is not guaranteed in this image.
ready=0
for i in $(seq 1 120); do
  if "$VLLM_PY" - <<'PY' 2>/dev/null
import sys, urllib.request
try:
    urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3)
except Exception:
    sys.exit(1)
PY
  then ready=1; echo "vLLM healthy after ~$((i*10))s"; break; fi
  if ! kill -0 "$VLLM_PID" 2>/dev/null; then
    echo "vLLM exited before becoming healthy; last 60 lines:"
    tail -60 "$VLLM_LOG"
    exit 1
  fi
  sleep 10
done
if [ "$ready" != "1" ]; then
  echo "vLLM never became healthy; last 60 lines:"
  tail -60 "$VLLM_LOG"
  kill "$VLLM_PID" 2>/dev/null || true
  exit 1
fi

PARQUET=$(ls /workspace/telelogs/cache/hf/hub/datasets--GSMA--ot-full/snapshots/*/telelogs/test-*.parquet | head -1)
echo "parquet=$PARQUET"

export DSPY_API_BASE=http://127.0.0.1:8000/v1
export DSPY_MODEL=openai/Qwen/Qwen3-8B

echo "=== official-864, magnitude residual rule, BASE weights ==="
cd "$REPRO/code"
"$PY" "$REPRO/champion_gsma_full.py" \
  --parquet "$PARQUET" \
  --program "$PROGRAM" \
  --method b3_react_specialist \
  --workers 8 \
  --max-tokens 1000 \
  --out "$OUT"
rc=$?

# Kill by PID, never by pattern: pkill -f matches the ssh command line itself.
kill "$VLLM_PID" 2>/dev/null || true
sleep 5
kill -9 "$VLLM_PID" 2>/dev/null || true

echo "eval rc=$rc"
exit $rc

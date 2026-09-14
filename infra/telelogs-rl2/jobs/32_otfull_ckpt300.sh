#!/bin/bash
# Job: score GRPO v2 checkpoint-300 on the official 864-case test through the
# shipped pipeline, so it lands next to two numbers already measured the same way:
#
#   base Qwen3-8B          797/864 = 92.25%   (runs/repro-9421, 2026-08-24)
#   GRPO v1 (weights lost) 814/864 = 94.21%   (runs/bench4, 2026-08-03)
#
# This is measurement, not selection. Choosing among checkpoints by their
# official-864 score would make the reported number meaningless; that choice is
# made on dev-96 by job 31. Only one checkpoint is scored here.
set -u

E=/workspace/eval
RL=/workspace/telelogs-rl
BENCH4=/workspace/telelogs-bench4
PY="$BENCH4/dspy/.venv/bin/python"
VLLM_PY="$RL/venv/bin/python"
MERGED="$E/models/grpo-v2-ckpt300"
PROGRAM="$BENCH4/dspy-tools/results/optimized/bootstrap26_seeded11_b3/program.json"
PARQUET="$E/data/ot_full_telelogs_test.parquet"
OUT="$E/results/otfull_grpo_v2_ckpt300"

export HF_HOME="$E/cache/hf"
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false

[ -f "$MERGED/config.json" ] || { echo "merged model missing at $MERGED — run job 31 first"; exit 1; }
mkdir -p "$OUT"

nvidia-smi --query-gpu=index,name,uuid --format=csv || true
echo "weights=$MERGED"
md5sum "$MERGED/config.json" || true

# Served under its own name. v1's provenance was lost because GRPO weights were
# published as "Qwen/Qwen3-8B"; nothing here repeats that.
CUDA_VISIBLE_DEVICES=0 "$VLLM_PY" -m vllm.entrypoints.openai.api_server \
  --model "$MERGED" --served-model-name grpo-v2-ckpt300 \
  --host 127.0.0.1 --port 8010 \
  --dtype bfloat16 --tensor-parallel-size 1 \
  --max-model-len 40960 --gpu-memory-utilization 0.90 \
  --max-num-seqs 64 --enable-prefix-caching --reasoning-parser qwen3 \
  > "$E/results/vllm_otfull_ckpt300.log" 2>&1 &
VLLM_PID=$!
echo "vllm pid=$VLLM_PID"

ready=0
for i in $(seq 1 120); do
  if "$VLLM_PY" - <<'PY' 2>/dev/null
import sys, urllib.request
try: urllib.request.urlopen("http://127.0.0.1:8010/health", timeout=3)
except Exception: sys.exit(1)
PY
  then ready=1; echo "vLLM healthy after ~$((i*10))s"; break; fi
  kill -0 "$VLLM_PID" 2>/dev/null || { echo "vLLM died:"; tail -50 "$E/results/vllm_otfull_ckpt300.log"; exit 1; }
  sleep 10
done
[ "$ready" = "1" ] || { echo "never healthy"; tail -50 "$E/results/vllm_otfull_ckpt300.log"; kill $VLLM_PID 2>/dev/null; exit 1; }

export DSPY_API_BASE=http://127.0.0.1:8010/v1
export DSPY_MODEL=openai/grpo-v2-ckpt300

cd "$E/code"
"$PY" "$E/champion_gsma_full.py" \
  --parquet "$PARQUET" \
  --program "$PROGRAM" \
  --method b3_react_specialist \
  --workers 8 \
  --max-tokens 1000 \
  --out "$OUT"
rc=$?

kill "$VLLM_PID" 2>/dev/null || true
sleep 5
kill -9 "$VLLM_PID" 2>/dev/null || true
echo "otfull rc=$rc"
exit $rc

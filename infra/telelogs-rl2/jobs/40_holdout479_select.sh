#!/bin/bash
# Job: score the base model and all 16 archived GRPO v2 checkpoints on
# holdout-479, to pick a checkpoint WITHOUT looking at official-864.
#
# Why this run exists. The three checkpoints scored on ot-full so far (300/700/
# 1048 -> 809/814/808) do not differ significantly from each other (300 vs 700
# p = 0.50, 700 vs 1048 p = 0.39). Reporting 814 means reporting the maximum of
# three noisy draws taken on the very set being reported, which is optimistic by
# construction. Selecting here, on 479 questions that neither GRPO nor the DSPy
# compilation has ever seen, makes the ot-full figure an estimate again.
#
# THE SELECTION RULE IS FIXED BEFORE ANY RESULT IS READ:
#   winner = highest correct count on holdout-479;
#   ties broken by the SMALLEST step number (less training, same evidence).
# Whatever it names is what gets reported on ot-full. There is no second look.
#
# Same script, same compiled program, same official boxed-int parser as every
# ot-full number in this repo — only --parquet and --suite differ. If the two
# sets were scored by different code the comparison would measure the code.
#
# Concurrency: 6 lanes, one per card, pulling from a shared queue so a lane that
# finishes early takes the next item instead of idling. Merging is capped at 2 at
# a time by a two-slot flock semaphore -- each merge is ~16 GB of RAM and 16 GB
# of disk, and six at once would be 98 GB of both for no speed gain, since the
# 20-minute scoring stage is what the wall-clock is made of, not the 3-minute
# merge.
set -u

E=/workspace/eval
RL=/workspace/telelogs-rl
BENCH4=/workspace/telelogs-bench4
PY="$BENCH4/dspy/.venv/bin/python"
VLLM_PY="$RL/venv/bin/python"
BASE=/workspace/models/Qwen3-8B
PROGRAM="$BENCH4/dspy-tools/results/optimized/bootstrap26_seeded11_b3/program.json"
PARQUET="$E/data/holdout479.parquet"
SUITE="TeleLogs holdout-479 · selection set · official boxed-int scorer"
W="$E/results/hold479"

export HF_HOME="$E/cache/hf"
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false

[ -f "$PARQUET" ] || { echo "missing $PARQUET"; exit 1; }
[ -f "$PROGRAM" ] || { echo "missing $PROGRAM"; exit 1; }

# Preflight the selection set before six cards spend an hour on it. The parquet
# was written by a newer pyarrow off-cluster; a version mismatch has to fail here,
# loudly, and not sixteen merges later.
"$PY" - <<'PYEOF' || { echo "preflight failed"; exit 1; }
import sys
from collections import Counter
import pyarrow.parquet as pq
rows = pq.read_table("/workspace/eval/data/holdout479.parquet").to_pylist()
assert len(rows) == 479, f"expected 479 rows, got {len(rows)}"
assert set(rows[0]) == {"question", "answer"}, sorted(rows[0])
counts = Counter(r["answer"] for r in rows)
assert sorted(counts) == [f"C{i}" for i in range(1, 9)], sorted(counts)
print("preflight OK:", len(rows), "rows,", dict(sorted(counts.items())))
PYEOF

mkdir -p "$W"
nvidia-smi --query-gpu=index,name,uuid,memory.total --format=csv
df -h /workspace/eval | tail -1

# ---- work queue -------------------------------------------------------------
QUEUE="$W/queue.txt"
IDX="$W/queue.idx"
QLOCK="$W/queue.lock"
: > "$QUEUE"
echo base >> "$QUEUE"
for s in 100 200 300 400 500 600 700 800 875 900 925 950 975 1000 1025 1048; do
  echo "$s" >> "$QUEUE"
done
echo 0 > "$IDX"
: > "$QLOCK"
N_ITEMS=$(wc -l < "$QUEUE")
echo "queue: $N_ITEMS items"

take_next() {  # prints the next item, or nothing when the queue is drained
  flock -x "$QLOCK" bash -c '
    i=$(cat "'"$IDX"'")
    line=$(sed -n "$((i+1))p" "'"$QUEUE"'")
    [ -n "$line" ] && echo $((i+1)) > "'"$IDX"'"
    printf "%s" "$line"
  '
}

# ---- two-slot merge semaphore ----------------------------------------------
: > "$W/mlock.1"; : > "$W/mlock.2"

cat > "$W/merge.py" <<'PYEOF'
import sys, torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
base_path, adapter, out = sys.argv[1], sys.argv[2], sys.argv[3]
base = AutoModelForCausalLM.from_pretrained(base_path, torch_dtype=torch.bfloat16, device_map="cpu")
merged = PeftModel.from_pretrained(base, adapter).merge_and_unload()
merged.save_pretrained(out, safe_serialization=True)
AutoTokenizer.from_pretrained(base_path).save_pretrained(out)
print("merged ->", out)
PYEOF

merge_with_slot() {  # adapter merged
  local adapter=$1 merged=$2 rc k
  rm -rf "$merged"; mkdir -p "$merged"
  while true; do
    for k in 1 2; do
      # -E 99 separates "the slot was busy" from "the merge itself failed".
      flock -n -E 99 "$W/mlock.$k" \
        "$VLLM_PY" "$W/merge.py" "$BASE" "$adapter" "$merged" && return 0
      rc=$?
      [ "$rc" = "99" ] || return "$rc"
    done
    sleep 10
  done
}

# ---- one evaluation ---------------------------------------------------------
score_item() {  # gpu port item
  local gpu=$1 port=$2 item=$3
  local name model served merged=""
  if [ "$item" = "base" ]; then
    name="hold479_base"; model="$BASE"; served="base-qwen3-8b"
  else
    name="hold479_ckpt$item"
    merged="$E/models/hold-gpu$gpu"
    model="$merged"; served="grpo-v2-ckpt$item"
  fi
  local out="$W/$name" vlog="$W/$name.vllm.log"

  if [ -f "$out/summary.json" ]; then echo "[$item] already scored, skipping"; return 0; fi

  if [ -n "$merged" ]; then
    local adapter="$RL/results/ckpt_archive/checkpoint-$item"
    [ -d "$adapter" ] || { echo "[$item] adapter missing"; return 1; }
    echo "[$item] merge (waiting for a slot) $(date -u +%H:%M:%S)"
    merge_with_slot "$adapter" "$merged" || { echo "[$item] merge failed"; return 1; }
    echo "[$item] merged $(date -u +%H:%M:%S)"
  fi

  mkdir -p "$out"
  CUDA_VISIBLE_DEVICES=$gpu "$VLLM_PY" -m vllm.entrypoints.openai.api_server \
    --model "$model" --served-model-name "$served" \
    --host 127.0.0.1 --port "$port" \
    --dtype bfloat16 --tensor-parallel-size 1 \
    --max-model-len 40960 --gpu-memory-utilization 0.90 \
    --max-num-seqs 64 --enable-prefix-caching --reasoning-parser qwen3 \
    > "$vlog" 2>&1 &
  local pid=$! ready=0 i
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
    echo "[$item] server never healthy"; tail -30 "$vlog"
    kill -9 "$pid" 2>/dev/null; [ -n "$merged" ] && rm -rf "$merged"; return 1
  fi

  ( cd "$E/code" && DSPY_API_BASE="http://127.0.0.1:$port/v1" DSPY_MODEL="openai/$served" \
    "$PY" "$E/champion_gsma_full.py" \
      --parquet "$PARQUET" --program "$PROGRAM" --method b3_react_specialist \
      --suite "$SUITE" --workers 8 --max-tokens 1000 --out "$out" ) > "$W/$name.out" 2>&1
  local rc=$?

  kill "$pid" 2>/dev/null; sleep 5; kill -9 "$pid" 2>/dev/null
  [ -n "$merged" ] && rm -rf "$merged"
  echo "[$item] done rc=$rc $(date -u +%H:%M:%S)"
  return $rc
}

lane() {  # gpu port
  local gpu=$1 port=$2 item fails=0
  while true; do
    item=$(take_next)
    [ -n "$item" ] || break
    echo "== lane$gpu took '$item' $(date -u +%H:%M:%S)"
    score_item "$gpu" "$port" "$item" || fails=$((fails+1))
  done
  echo "== lane$gpu drained, failures=$fails"
  return $fails
}

echo "=== sweep start $(date -u) ==="
PIDS=()
for g in 0 1 2 3 4 5; do
  ( lane "$g" "$((8100 + g))" ) & PIDS+=($!)
done
FAILS=0
for p in "${PIDS[@]}"; do wait "$p" || FAILS=$((FAILS + $?)); done

echo "=== sweep finished $(date -u), lane failures=$FAILS ==="
"$VLLM_PY" - <<'PY'
import json, glob, os, re
rows = []
for f in sorted(glob.glob("/workspace/eval/results/hold479/*/summary.json")):
    name = os.path.basename(os.path.dirname(f))
    d = json.load(open(f))
    step = 0 if name.endswith("base") else int(re.search(r"ckpt(\d+)", name).group(1))
    rows.append((step, name, d["correct"], d["total"], d["accuracy"]))
rows.sort()
for step, name, c, t, a in rows:
    print(f"{name:22} {c:4}/{t} = {a*100:6.2f}%")
cands = [r for r in rows if r[0] > 0]
if cands:
    best = max(r[2] for r in cands)
    winner = min(r for r in cands if r[2] == best)   # tie -> smallest step
    print(f"\nSELECTED (highest holdout, ties -> smallest step): {winner[1]}  {winner[2]}/{winner[3]}")
PY
df -h /workspace/eval | tail -1
exit "$FAILS"

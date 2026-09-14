#!/bin/bash
# Job: build the v2 GRPO training set (magnitude ladder) with hard assertions.
#
# The count assertion is not paranoia — a silent parse regression in
# neutral_tools would produce a small, plausible-looking file and train a model
# on a fraction of the data without anyone noticing.
set -euo pipefail

ROOT=/workspace/telelogs-rl
PY="$ROOT/venv/bin/python"

"$PY" "$ROOT/make_grpo_dataset_v2.py" \
  --raw "$ROOT/data/train.json" \
  --out "$ROOT/data/grpo_train_v2.jsonl"

LINES=$(wc -l < "$ROOT/data/grpo_train_v2.jsonl")
echo "dataset lines: $LINES"
# v1 shape: 783 gated x1 + 658 residual x2 = 2099 prompts.
if [ "$LINES" -lt 2000 ]; then
  echo "dataset too small ($LINES < 2000) — aborting before training"
  exit 1
fi

echo "=== the ladder the prompts now state ==="
"$PY" - <<'PY'
import json
row = json.loads(open("/workspace/telelogs-rl/data/grpo_train_v2.jsonl").readline())
system = row["prompt"][0]["content"]
assert "equal_residue_pair_count of more than 2" in system, "magnitude ladder missing"
assert "142.5" not in system, "presence ladder still present"
print(system.split("Only when all four verdicts")[1][:700])
PY
echo DATASET_OK

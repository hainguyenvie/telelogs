#!/bin/bash
# The shipped champion (specialist pipeline on GRPO weights), run end-to-end
# under the GSMA ot-full harness contract and scored by the OFFICIAL parser
# (last \boxed{...}, first-int comparison) — the same scorer full4_eval.py
# applies to the base model. Dataset: the ot-full telelogs test parquet,
# verified identical (order, questions, answers) to data/test_official864.json.
# Completions carry narrative + audited evidence + boxed answer, so the run
# also yields the per-class diagnosis table and narrated output for the report.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
PY=/workspace/telelogs-bench4/dspy/.venv/bin/python
export DSPY_API_BASE=http://telelogs-rl-vllm:8000/v1

PARQUET=$(ls /workspace/telelogs/cache/hf/hub/datasets--GSMA--ot-full/snapshots/*/telelogs/test-*.parquet | head -1)
"$PY" -m pip install -q pyarrow 2>/dev/null || true

cd "$ROOT/code"
"$PY" "$ROOT/champion_gsma_full.py" \
  --parquet "$PARQUET" \
  --program "$ROOT/results/optimized/bootstrap26_seeded11_b3/program.json" \
  --workers 8 --max-tokens 1000 \
  --out "$ROOT/results/champion_gsma_full"

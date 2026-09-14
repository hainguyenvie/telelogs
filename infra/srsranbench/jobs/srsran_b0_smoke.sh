#!/bin/bash
# Smoke: 8 rows, no-thinking, to confirm the endpoint, the template and the
# Inspect-verbatim parser all work from inside the client pod before spending
# the full 1502.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/srsranbench
PY=/workspace/telelogs-bench4/dspy/.venv/bin/python
cd "$ROOT/code"
"$PY" run_baseline.py \
  --data "$ROOT/data/test.jsonl" \
  --out "$ROOT/results/b0_smoke8" \
  --limit 8 --workers 4 --max-tokens 32

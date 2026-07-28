#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy
VENV="$ROOT/.venv"
mkdir -p "$ROOT/data" "$ROOT/results"
if [ ! -x "$VENV/bin/python" ]; then
  /opt/conda/bin/python -m venv --system-site-packages "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade 'dspy==3.2.1'
"$VENV/bin/python" - <<'PY'
import dspy
print("dspy", dspy.__version__)
PY

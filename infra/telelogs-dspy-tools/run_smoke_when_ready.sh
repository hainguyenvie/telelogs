#!/bin/bash
set -euo pipefail

BASE=http://telelogs-bench4-vllm:8000
PYTHON=/workspace/telelogs-bench4/dspy/.venv/bin/python
for attempt in $(seq 1 240); do
  if "$PYTHON" -c "import urllib.request; urllib.request.urlopen('$BASE/health', timeout=3).read()" >/dev/null 2>&1; then
    echo "vLLM ready on attempt $attempt"
    exec /workspace/telelogs-bench4/dspy-tools/run_smoke.sh
  fi
  echo "waiting for vLLM ($attempt/240)"
  sleep 5
done
echo "vLLM did not become ready in 20 minutes" >&2
exit 1

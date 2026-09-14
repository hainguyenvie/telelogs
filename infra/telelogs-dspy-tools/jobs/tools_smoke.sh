#!/bin/bash
set -euo pipefail

/workspace/telelogs-bench4/dspy-tools/run_preflight.sh
exec /workspace/telelogs-bench4/dspy-tools/run_smoke_when_ready.sh

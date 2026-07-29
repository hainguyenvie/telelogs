#!/bin/bash
set -euo pipefail

ROOT=/workspace/telelogs-bench4/dspy-tools
PYTHON=/workspace/telelogs-bench4/dspy/.venv/bin/python
cd "$ROOT/code"
"$PYTHON" -c 'import dspy; print("dspy=" + dspy.__version__)'
"$PYTHON" -c 'from tool_program import PROGRAMS; programs = {name: cls() for name, cls in PROGRAMS.items()}; print("programs=" + ",".join(programs))'
"$PYTHON" audit_tools.py --data "$ROOT/data/train.json"

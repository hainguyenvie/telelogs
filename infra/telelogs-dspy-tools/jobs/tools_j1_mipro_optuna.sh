#!/bin/bash
# MIPROv2 retry: dspy needs the optional optuna dependency for its Bayesian search.
set -euo pipefail
ROOT=/workspace/telelogs-bench4/dspy-tools
PY="/workspace/telelogs-bench4/dspy/.venv/bin/python"
"$PY" -m pip install --quiet optuna
"$PY" -c "import optuna; print('optuna', optuna.__version__)"
"$ROOT/run_opt_mipro.sh" b3_react_tools "$ROOT/seed_b3_calibrated_v11.txt" mipro_seeded11_b3
MP=$ROOT/results/optimized/mipro_seeded11_b3/program.json
"$ROOT/run_dev96_compiled.sh" b3_react_tools  "$MP" tool_dev96_mipro11
"$ROOT/run_dev96_compiled.sh" b3_react_forced "$MP" tool_dev96_mipro11_forced

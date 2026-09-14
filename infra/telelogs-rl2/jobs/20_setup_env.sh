#!/bin/bash
# Job: create the training venv for telelogs-rl2.
#
# Pins are copied verbatim from the v1 run that produced the lost checkpoint.
# They are not cosmetic: unpinned resolution grabs transformers 5.x, whose
# removed HybridCache breaks peft 0.17 (hit 2026-07-30). Newer TRL was
# considered and rejected — 0.23.1 still has no chat_template_kwargs on
# GRPOConfig (only SFTTrainer), so it would buy nothing and change the stack.
set -euo pipefail

ROOT=/workspace/telelogs-rl
PYBIN=$(command -v python3 || echo /opt/conda/bin/python3)

"$PYBIN" -m venv "$ROOT/venv"
"$ROOT/venv/bin/pip" install --upgrade pip
"$ROOT/venv/bin/pip" install \
  "trl==0.21.0" \
  "peft==0.17.0" \
  "accelerate==1.9.0" \
  "datasets==4.0.0" \
  "vllm==0.10.0" \
  "transformers==4.55.2" \
  "huggingface_hub==0.34.4"

"$ROOT/venv/bin/python" - <<'EOF'
import torch, transformers, trl, peft, vllm
print("torch", torch.__version__, "cuda", torch.version.cuda, "gpus", torch.cuda.device_count())
print("transformers", transformers.__version__)
print("trl", trl.__version__)
print("peft", peft.__version__)
print("vllm", vllm.__version__)
EOF
echo SETUP_OK

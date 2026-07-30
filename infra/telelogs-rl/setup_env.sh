#!/bin/bash
# Job: create the self-contained training venv for telelogs-rl.
# The venv pulls its own torch (via vllm) and ignores the image's torch.
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
  "huggingface_hub[hf_transfer]"

export HF_HOME="$ROOT/cache/hf"
export HF_HUB_ENABLE_HF_TRANSFER=1
"$ROOT/venv/bin/python" -c "
from huggingface_hub import snapshot_download
path = snapshot_download('Qwen/Qwen3-8B')
print('weights at', path)
"

"$ROOT/venv/bin/python" - <<'EOF'
import torch, transformers, trl, peft, vllm
print("torch", torch.__version__, "cuda", torch.version.cuda, "gpus", torch.cuda.device_count())
print("transformers", transformers.__version__)
print("trl", trl.__version__)
print("peft", peft.__version__)
print("vllm", vllm.__version__)
EOF
echo SETUP_OK

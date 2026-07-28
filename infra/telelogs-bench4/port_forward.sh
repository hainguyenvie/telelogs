#!/bin/bash
set -euo pipefail

LOCAL_PORT=${1:-8000}

echo "Qwen3-8B will be available at http://127.0.0.1:${LOCAL_PORT}/v1"
exec ssh \
  -N \
  -L "${LOCAL_PORT}:telelogs-bench4-vllm.tensara.svc.cluster.local:8000" \
  H200_Tensara

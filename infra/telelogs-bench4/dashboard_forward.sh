#!/bin/bash
set -euo pipefail

LOCAL_PORT=${1:-18081}
echo "Dashboard: http://127.0.0.1:${LOCAL_PORT}"
exec ssh -N \
  -L "${LOCAL_PORT}:telelogs-bench4-web.tensara.svc.cluster.local:8080" \
  H200_Tensara

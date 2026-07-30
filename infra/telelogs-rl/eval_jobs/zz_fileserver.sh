#!/bin/bash
# bench4-client job: temporary HTTP fileserver so the telelogs-rl pod on hgx47
# can bootstrap code+data over the cluster network. Self-terminates after 90 min.
set -u
cd /workspace/telelogs-bench4/dspy-tools
echo "fileserver up on 8111 at $(date)"
timeout 5400 python3 -m http.server 8111 --bind 0.0.0.0 || true
echo "fileserver down at $(date)"

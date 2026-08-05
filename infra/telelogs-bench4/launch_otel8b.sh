#!/bin/bash
# Orchestrates the OTel-LLM-8.3B-IT arm end to end on the H200, unattended.
#
# It exists because three things have to line up and none of them are ready at
# the same moment: the 33 GB checkpoint is still downloading, hgx046's GPUs are
# all held by other teams' pods, and the eval cannot start until vLLM answers
# /health. Polling those by hand across a working day is not a good use of anyone.
#
# Runs on the H200 login host, not in a pod. Safe to re-run: every step is
# idempotent and it never touches a pod it did not create.
#
#   ssh H200_Tensara 'nohup bash ~/projects/telelogs/runs/bench4/launch_otel8b.sh \
#       > ~/projects/telelogs/runs/bench4/logs/launch_otel8b.log 2>&1 &'
set -u

ROOT=/home/tensara/projects/telelogs/runs/bench4
MODEL_DIR=$ROOT/models/OTel-LLM-8.3B-IT
NS=tensara
POD=telelogs-bench4

say() { echo "[$(date -u +%H:%M:%S)] $*"; }

# 1. the checkpoint --------------------------------------------------------
say "waiting for the checkpoint"
while ! grep -q "^DONE" "$ROOT/logs/dl_otel.log" 2>/dev/null; do
  if grep -q "^GAVE UP" "$ROOT/logs/dl_otel.log" 2>/dev/null; then
    say "download gave up; stopping"; exit 1
  fi
  sleep 60
done
say "checkpoint present: $(du -sh "$MODEL_DIR" | cut -f1)"

# 2. a GPU ----------------------------------------------------------------
# hgx046 has three and other teams hold all of them. A bare Pod that loses the
# admission race lands in UnexpectedAdmissionError and stays there, so each
# attempt has to clean up after itself before the next one.
say "waiting for a free GPU on hgx046"
while true; do
  kubectl apply -f "$ROOT/bench4_pod.yaml" -n "$NS" >/dev/null 2>&1
  sleep 20
  phase=$(kubectl get pod "$POD" -n "$NS" -o jsonpath='{.status.phase}' 2>/dev/null)
  reason=$(kubectl get pod "$POD" -n "$NS" -o jsonpath='{.status.reason}' 2>/dev/null)
  if [ "$phase" = "Running" ]; then say "pod running"; break; fi
  if [ "$reason" = "UnexpectedAdmissionError" ] || [ "$phase" = "Failed" ]; then
    kubectl delete pod "$POD" -n "$NS" --wait=false >/dev/null 2>&1
    sleep 160
  else
    sleep 40   # Pending is fine, the scheduler is still thinking
  fi
done

# 3. serve ----------------------------------------------------------------
say "submitting the serving job"
cp "$ROOT/serve_otel8b.sh" "$ROOT/jobs/serve_otel8b.sh"

say "waiting for /health"
for _ in $(seq 1 120); do
  code=$(kubectl run curl-probe-$RANDOM -n "$NS" --rm -i --restart=Never \
           --image=curlimages/curl:latest --command -- \
           curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
           http://telelogs-bench4-vllm:8000/health 2>/dev/null | tr -d '\r')
  [ "$code" = "200" ] && { say "vLLM healthy"; break; }
  sleep 30
done

# 4. eval -----------------------------------------------------------------
say "submitting the eval job"
cp "$ROOT/teleqna_o1_otel8b.sh" "$ROOT/client_jobs/teleqna_o1_otel8b.sh"
say "handed off; watch $ROOT/client_done/teleqna_o1_otel8b.log"

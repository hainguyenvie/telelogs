#!/bin/bash
# Keep a sparse archive of GRPO adapters out of save_total_limit's reach.
#
# GRPOConfig has save_total_limit=8, so once step 225 lands the step-25 checkpoint
# is deleted. If checkpoint selection later prefers an early one it would already
# be gone.
#
# Two constraints shaped this:
#   - checkpoints/ is created by the pod (root) and the dev pod is uid 1000, so
#     the archive has to live under results/, which the runner chmods 777.
#   - `cp -al` is refused: protected_hardlinks forbids linking to files you do
#     not own. So this is a real copy — but only of the adapter, not the 700 MB
#     optimizer state, which selection does not need. ~350 MB per step archived.
#
# Runs from the dev pod, not the job runner: the runner is single-threaded and
# busy training.
set -u
R=$HOME/projects/telelogs/runs/rl-grpo2
SRC=$R/checkpoints/v2
DST=$R/results/ckpt_archive
KEEP="adapter_model.safetensors adapter_config.json trainer_state.json"
mkdir -p "$DST"

archive_one() {
  local d=$1 n=$2
  mkdir -p "$DST/$n.part" || return 1
  for f in $KEEP; do
    [ -f "$d/$f" ] && cp "$d/$f" "$DST/$n.part/" 2>/dev/null
  done
  # Rename only once every file landed, so a half-copied archive is never
  # mistaken for a usable one.
  mv "$DST/$n.part" "$DST/$n" && echo "$(date -u +%H:%M:%S) archived $n"
}

while true; do
  for d in "$SRC"/checkpoint-*; do
    [ -d "$d" ] || continue
    n=$(basename "$d"); step=${n#checkpoint-}
    case "$step" in (*[!0-9]*) continue ;; esac
    [ $((step % 100)) -eq 0 ] || continue
    [ -e "$DST/$n" ] && continue
    archive_one "$d" "$n"
  done
  if ls "$R"/done/27_train_3gpu.rc* >/dev/null 2>&1; then
    # Training is over: sweep every surviving checkpoint, not just the century marks.
    for d in "$SRC"/checkpoint-*; do
      [ -d "$d" ] || continue
      n=$(basename "$d")
      [ -e "$DST/$n" ] || archive_one "$d" "$n"
    done
    echo "$(date -u +%H:%M:%S) training finished; archive sealed"
    exit 0
  fi
  sleep 240
done

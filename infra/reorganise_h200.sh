#!/bin/bash
# Consolidate the H200 project storage under one root named after the repo.
#
# Eight sibling directories accumulated on hgx046 as the project moved through
# fine-tuning tracks that were later abandoned in favour of the prompt/tool
# approach. This puts them under ~/projects/telelogs/runs/ and separates what is
# irreplaceable from what is merely large.
#
# NOTHING IS DELETED. Reconstructible checkpoints are moved to _trash_<date>/,
# which stays on the same filesystem — the move is instantaneous and frees no
# space until you remove it yourself:
#
#     rm -rf ~/projects/_trash_<date>
#
# What goes to trash and why: every "merged" model is base Qwen3-8B plus a LoRA
# adapter, and every one of them was verified to still have its adapter, so
# merge_checkpoint.sh rebuilds it. The 94.21% official-864 result does not use
# any of them — run_official864_compiled.sh leaves DSPY_MODEL at its default,
# openai/Qwen/Qwen3-8B, the stock checkpoint. What is kept: every adapter, every
# result, the HF cache holding the stock model, and the venvs.
#
# Idempotent: re-running after a partial run finishes the job.
set -u

cd ~/projects || exit 1
ROOT=$HOME/projects/telelogs
TRASH=$HOME/projects/_trash_$(date +%Y%m%d)
STAGE=$HOME/projects/_telelogs_staging

say() { echo "[$(date -u +%H:%M:%S)] $*"; }

# --- 1. reconstructible checkpoints out of the way -------------------------
mkdir -p "$TRASH"
for p in \
  telelogs/models/qwen3-8b-telelogs-merged-v2 \
  telelogs/models/qwen3-8b-telelogs-merged-v5pilot \
  telelogs/models/qwen3-8b-telelogs-merged-v6 \
  telelogs/models/qwen3-8b-telelogs-grpo-v7-merged \
  telelogs/models/qwen3-8b-telelogs-grpo-v7-275-merged \
  telelogs-v12-sft/models/qwen3-8b-telelogs-merged-v12-epoch1 \
  telelogs-v12-sft/models/qwen3-8b-telelogs-merged-v12-epoch2 \
  telelogs-v12-sft/models/qwen3-8b-telelogs-merged-v12-epoch3 \
  telelogs-v13-sft/models/qwen3-8b-telelogs-v13-merged-phase1 \
  telelogs-v13-sft/models/qwen3-8b-telelogs-v13-merged-phase2 \
  telelogs-v14-sft/models/qwen3-8b-telelogs-v14-merged-epoch1 \
  telelogs-canon/models/base-v14-epoch1 \
  telelogs-rl-hgx47/models/qwen3-8b-grpo-v1 \
  telelogs/cache/pip
do
  [ -e "$p" ] || continue
  dest="$TRASH/$(echo "$p" | tr / _)"
  [ -e "$dest" ] && continue
  mv "$p" "$dest" && say "trashed $(du -sh "$dest" | cut -f1)  $p"
done

# --- 2. one root, named after the repo -------------------------------------
# ~/projects/telelogs is both a source and the target name, so the last move has
# to be a rename of a fully-built staging tree rather than an in-place mkdir.
if [ ! -d "$ROOT/runs" ]; then
  mkdir -p "$STAGE/runs"
  for pair in \
    "telelogs-v12-sft:v12-sft" \
    "telelogs-v13-sft:v13-sft" \
    "telelogs-v14-sft:v14-sft" \
    "telelogs-canon:canon" \
    "telelogs-rl-hgx47:rl-grpo" \
    "telelogs-rl:rl-skeleton" \
    "telelogs-bench4:bench4"
  do
    src=${pair%%:*}; dst=${pair##*:}
    [ -d "$src" ] || continue
    mv "$src" "$STAGE/runs/$dst" && say "moved $src -> runs/$dst"
  done

  # the original run last: it is the directory the new root takes its name from
  if [ -d telelogs ]; then
    mv telelogs "$STAGE/runs/v0-lora" && say "moved telelogs -> runs/v0-lora"
  fi

  mv "$STAGE" "$ROOT" && say "staging promoted to $ROOT"
fi

# --- 3. shared things at the top, not buried in one run --------------------
mkdir -p "$ROOT/shared"
[ -d "$ROOT/runs/v0-lora/cache/hf" ] && [ ! -e "$ROOT/shared/hf-cache" ] && \
  mv "$ROOT/runs/v0-lora/cache/hf" "$ROOT/shared/hf-cache" && say "hf cache -> shared/hf-cache"

mkdir -p "$ROOT/venvs"
for v in venv venv-dl venv-train venv-grpo; do
  [ -d "$ROOT/runs/v0-lora/$v" ] || continue
  [ -e "$ROOT/venvs/$v" ] && continue
  mv "$ROOT/runs/v0-lora/$v" "$ROOT/venvs/$v" && say "$v -> venvs/$v"
done

# --- 4. leave the mountpoints behind ---------------------------------------
# The pods mount runs/v0-lora read-only at /workspace/telelogs and then mount the
# venv and the HF cache back inside it, so that no script has to learn a new
# path. containerd cannot mkdir a mountpoint inside a read-only mount, so the
# empty directories have to exist on the host or every pod dies with
# RunContainerError "read-only file system".
mkdir -p "$ROOT/runs/v0-lora/cache/hf" "$ROOT/runs/v0-lora/venv"

say "done"
echo
echo "layout:"
du -sh "$ROOT"/* "$ROOT"/runs/* 2>/dev/null | sort -h
echo
echo "trash (nothing freed until you remove it):"
du -sh "$TRASH" 2>/dev/null
echo "  rm -rf $TRASH"

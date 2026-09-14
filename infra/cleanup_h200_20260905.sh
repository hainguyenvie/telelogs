#!/bin/bash
# Disk cleanup on H200_Tensara, 2026-09-05 — everything here is DERIVED data whose
# originals (adapters, per-row results, clean corpora) stay on disk. Two phases,
# per H200_SERVER_GUIDE.md §9: move to a trash dir first, inspect, then rm.
#
#   ssh H200_Tensara 'bash -s' < infra/cleanup_h200_20260905.sh trash   # phase 1
#   ssh H200_Tensara 'bash -s' < infra/cleanup_h200_20260905.sh purge   # phase 2
set -euo pipefail
T=~/projects/_trash_20260905
R=~/projects/telelogs/runs

phase_trash() {
  mkdir -p "$T"
  # 1) full-weight CPT checkpoints (48G): ep1 = mid-epoch of run 3; run 1/2 are closed
  #    arms whose per-row results live in results/eg/otfull_{ep2,dose}.jsonl. KEEP ep2.
  mkdir -p "$T/teleqna-8b-models-eg"
  mv "$R"/teleqna-8b/models/eg/ep1 "$R"/teleqna-8b/models/eg/ep2_run1 \
     "$R"/teleqna-8b/models/eg/ep2_dose "$T/teleqna-8b-models-eg/"
  # 2) merges (31G float32 + 16G bf16); adapters in rl-grpo2/results/ckpt_archive/checkpoint-*
  mv "$R"/rl-grpo2/models/qwen3-8b-grpo-v2            "$T/rl-grpo2-merge-fp32"
  mv "$R"/eval-v2/models/qwen3-8b-grpo-v2-ckpt700     "$T/eval-v2-ckpt700-merge-bf16"
  # 3) merge-GH-* (11G) are merges of ctxdistill-armG + armH adapters, both kept; smoke run
  mkdir -p "$T/teleqna-sft-models"
  mv "$R"/teleqna-sft/models/merge-GH-0.3 "$R"/teleqna-sft/models/merge-GH-0.6 \
     "$R"/teleqna-sft/models/merge-GH-1.0 "$R"/teleqna-sft/models/teleqna-grpo1smoke \
     "$T/teleqna-sft-models/"
  # 4) trainer checkpoints with optimizer state (6G); adapter-only copies in ckpt_archive. KEEP final.
  mkdir -p "$T/rl-grpo2-ckpt-v2"
  mv "$R"/rl-grpo2/checkpoints/v2/checkpoint-* "$T/rl-grpo2-ckpt-v2/"
  # 5) g9b adapters: ep1/ep2 of closed arms; -ep3 is byte-identical to the unsuffixed dir
  #    (md5 verified for all four) -> symlink so both names keep working.
  mkdir -p "$T/teleqna-8b-g9b"
  ( cd "$R/teleqna-8b/models"
    for a in armGfull synth14k tr8489 v3; do
      mv "g9b_$a-ep1" "g9b_$a-ep2" "g9b_$a-ep3" "$T/teleqna-8b-g9b/"
      ln -s "g9b_$a" "g9b_$a-ep3"
    done )
  # 6) vLLM logs (1.8G) -> gzip in place, ~10x smaller
  ( cd "$R/eval-v2/results" && gzip -f ./*.vllm.log )
  echo "=== moved to $T ==="; du -sh "$T"/* | sort -h; du -sh "$T"
  echo "=== sanity: originals still present ==="
  ls "$R"/teleqna-8b/models/eg/ep2/model.safetensors \
     "$R"/rl-grpo2/results/ckpt_archive/checkpoint-700/adapter_model.safetensors \
     "$R"/rl-grpo2/checkpoints/v2/final/adapter_model.safetensors \
     "$R"/teleqna-sft/models/ctxdistill-armG-adapter/adapter_model.safetensors \
     "$R"/teleqna-8b/models/g9b_v3-ep3/adapter_model.safetensors
  df -h /home/tensara | tail -1
}

# Run only after backups/h200/teleqna-8b-20260905/*.tgz is verified locally (sha256 + tar tzf).
phase_purge() {
  # 7) hidden-state dumps (4.6G) and raw pre-clean synthetic shards (1.2G) — regenerable
  mkdir -p "$T/teleqna-8b-derived"
  mv "$R"/teleqna-8b/results/lens/full_hidden.npy "$R"/teleqna-8b/results/lens/synth_hidden.npy \
     "$R"/teleqna-8b/data/eg/synth_shard*.jsonl "$R"/teleqna-8b/data/eg/pack_ids.npy \
     "$R"/teleqna-8b/data/eg/pack_mask.npy "$R"/teleqna-8b/data/eg3/gen_g*.jsonl \
     "$T/teleqna-8b-derived/" 2>/dev/null || true
  rm -f ~/projects/telelogs/shared/models/Qwen3-Embedding-8B/.gitattributes 2>/dev/null || true
  echo "=== purging ==="; du -sh "$T"
  rm -rf "$T"
  df -h /home/tensara | tail -1
}

case "${1:-}" in
  trash) phase_trash ;;
  purge) phase_purge ;;
  *) echo "usage: $0 trash|purge"; exit 2 ;;
esac

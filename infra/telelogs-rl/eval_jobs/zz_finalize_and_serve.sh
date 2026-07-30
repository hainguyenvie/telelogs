#!/bin/bash
# Queued in the RL pod BEHIND the training job (the runner is sequential), so it
# starts the moment training ends — merged model if train finished cleanly,
# otherwise the newest checkpoint. Then serves it for the dev-96 eval.
set -u
ROOT=/workspace/telelogs-rl
MERGED=$ROOT/models/qwen3-8b-grpo-v1

if [ ! -f "$MERGED/config.json" ]; then
  echo "no merged model from the training job; merging the newest checkpoint"
  bash "$ROOT/merge_checkpoint.sh" latest "$MERGED"
else
  echo "training already merged $MERGED"
fi
ls -la "$MERGED" | head
exec bash "$ROOT/serve_grpo_model.sh"

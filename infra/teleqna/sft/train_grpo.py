#!/usr/bin/env python3
"""GRPO over teleqna thinking traces.

The difference that matters versus the earlier GRPO script on the tools track:
thinking is ON. Rewarding `ANSWER: X` with no reasoning would be the same
1-bit letter channel that SFT and four DPO recipes already exhausted (net
+4..6 rows, fix-rate flat against training-pair similarity). With thinking on,
the unit of credit is a whole sampled reasoning path, so the policy is trained
on *how it reaches* the letter — the pass@k -> pass@1 gap that the any-branch
oracle (83.85% vs 74.71% voted) says is worth ~9 points.

Reward = the benchmark's own scorer:
  correctness  1.0  parsed letter == gold, using run_baseline.py's exact
                    STRICT -> LOOSE -> BARE cascade, last match wins
  format       0.05 a well-formed 'ANSWER: X' line exists at all
Nothing else is rewarded. Length is not penalised directly — truncated
rollouts already score zero because no ANSWER line survives, which is the
honest signal.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from pathlib import Path

# venv-grpo carries trl/peft/transformers but no vllm; venv carries vllm 0.11
# against the same container torch. Append (never prepend) so this venv's own
# transformers/trl keep precedence and only vllm resolves from the other tree.
_EXTRA = os.environ.get("EXTRA_SITE")
if _EXTRA:
    sys.path.append(_EXTRA)

from datasets import Dataset
from peft import LoraConfig
from trl import GRPOConfig, GRPOTrainer

# ported verbatim from run_baseline.py / eval_dev.py
STRICT = re.compile(r"(?i)^ANSWER\s*:\s*([A-Za-z\d ,]+)\s*(?:$|\n|\.)", re.MULTILINE)
LOOSE = re.compile(r"(?i)ANSWER\s*:\s*([A-Za-z\d ,]+)(?:[^\w]|\n|$|\.)")
BARE = re.compile(r"^\s*([A-Ea-e])(?:[).:,\s]|$)")


def completion_text(completion) -> str:
    if isinstance(completion, list):
        return " ".join(str(m.get("content", "")) for m in completion)
    return str(completion)


def parse(text: str, n: int) -> str:
    m = STRICT.findall(text or "") or LOOSE.findall(text or "") or BARE.findall(text or "")
    if not m:
        return ""
    got = m[-1].strip().rstrip(".").upper()
    return got if got in {chr(65 + i) for i in range(n)} else ""


def reward_correct(completions, answer, n_choices, **kwargs) -> list[float]:
    return [1.0 if parse(completion_text(c), n) == gold else 0.0
            for c, gold, n in zip(completions, answer, n_choices)]


def reward_format(completions, n_choices, **kwargs) -> list[float]:
    return [0.05 if parse(completion_text(c), n) else 0.0
            for c, n in zip(completions, n_choices)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--model-path", required=True,
                    help="base weights to start from. To build on CPT, merge "
                         "that adapter first (merge_adapter.py) and point here "
                         "at the merged directory — GRPOTrainer attaches its "
                         "own fresh LoRA and will not stack onto a loaded one")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--max-steps", type=int, default=-1)
    # LoRA takes ~10x the full-finetune LR (Schulman et al., "LoRA Without
    # Regret"): the 1/r scaling makes the optimum roughly rank-independent,
    # and their RL reference runs use 1e-5 LoRA against 1e-6 full FT.
    ap.add_argument("--learning-rate", type=float, default=1e-5)
    # Reference GRPO recipes now run beta=0. We keep a whisper of KL because
    # this project has a measured drift failure (SFT erased 114-155 dev rows);
    # with LoRA the reference pass is free (disable the adapter), so the
    # insurance costs nothing. Raise it if dev-1000 shows drift, drop to 0 if
    # the policy stops exploring.
    ap.add_argument("--beta", type=float, default=0.01)
    ap.add_argument("--num-generations", type=int, default=8)
    ap.add_argument("--generation-batch-size", type=int, default=None)
    ap.add_argument("--per-device-batch", type=int, default=8)
    # LoRA tolerates large batches poorly (same source); keep the effective
    # batch under ~32 completions.
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--max-prompt-length", type=int, default=1024)
    ap.add_argument("--max-completion-length", type=int, default=1536,
                    help="thinking averages ~968 tokens on this benchmark")
    ap.add_argument("--temperature", type=float, default=1.0)
    # RL extracts ~1 bit per episode, so it needs far less adapter capacity
    # than SFT: the same source recommends rank 1-32 for RL against 256 for
    # post-training-scale SFT.
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--vllm-gpu-mem", type=float, default=0.3)
    ap.add_argument("--save-steps", type=int, default=20)
    args = ap.parse_args()

    try:
        import vllm  # noqa: F401
    except ImportError:
        raise SystemExit(
            "vllm is not importable. venv-grpo does not ship it; point "
            "EXTRA_SITE at a site-packages tree that has vllm (venv has "
            "0.11.0 against the same container torch). Refusing to fall back "
            "to HF generate silently — rollouts would be several times slower "
            "and the run would look merely slow rather than misconfigured.")

    records = [json.loads(l) for l in Path(args.dataset).read_text(encoding="utf-8").splitlines()]
    dataset = Dataset.from_list(records)
    print(f"dataset: {len(dataset)} prompts", flush=True)

    cfg = dict(
        output_dir=args.output_dir,
        num_train_epochs=args.epochs,
        learning_rate=args.learning_rate,
        beta=args.beta,
        num_generations=args.num_generations,
        generation_batch_size=args.generation_batch_size or args.num_generations,
        per_device_train_batch_size=args.per_device_batch,
        gradient_accumulation_steps=args.grad_accum,
        max_steps=args.max_steps,
        lr_scheduler_type="cosine",
        max_grad_norm=1.0,
        max_prompt_length=args.max_prompt_length,
        max_completion_length=args.max_completion_length,
        temperature=args.temperature,
        bf16=True,
        gradient_checkpointing=True,
        logging_steps=5,
        save_steps=args.save_steps,
        save_total_limit=50,
        use_vllm=True,
        vllm_mode="colocate",
        vllm_gpu_memory_utilization=args.vllm_gpu_mem,
        log_completions=True,
        report_to=[],
        seed=42,
    )
    valid = {f.name for f in dataclasses.fields(GRPOConfig)}
    # thinking ON — the whole point of this run
    if "chat_template_kwargs" in valid:
        cfg["chat_template_kwargs"] = {"enable_thinking": True}
    else:
        raise SystemExit("GRPOConfig has no chat_template_kwargs: this TRL cannot "
                         "guarantee thinking is enabled in rollouts — upgrade first")
    if "mask_truncated_completions" in valid:
        cfg["mask_truncated_completions"] = True
    for k in [k for k in cfg if k not in valid]:
        print(f"WARN: GRPOConfig lacks {k}; dropping", flush=True)
        cfg.pop(k)

    trainer = GRPOTrainer(
        model=args.model_path,
        reward_funcs=[reward_correct, reward_format],
        args=GRPOConfig(**cfg),
        train_dataset=dataset,
        peft_config=LoraConfig(
            r=args.lora_r, lora_alpha=2 * args.lora_r, lora_dropout=0.0,
            bias="none", task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                            "gate_proj", "up_proj", "down_proj"]),
    )
    trainer.train()
    trainer.save_model(args.output_dir + "/final")
    print(f"adapter saved -> {args.output_dir}/final", flush=True)


if __name__ == "__main__":
    main()

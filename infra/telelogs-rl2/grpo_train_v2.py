#!/usr/bin/env python3
"""GRPO (TRL) over the single-turn tool-observation prompts — v2.

The gold label lives only inside the reward function; prompts are label-free.
LoRA keeps the policy close to base Qwen3-8B; rollouts run on a separate
`trl vllm-serve` GPU in server mode.

v2 adds one reward term. v1's reward was outcome-only, so a rollout that wrote an
arithmetically false comparison and still landed the right class was reinforced
for the false comparison. That is not hypothetical: the v1 policy was measured
writing "100.98 > 160 -> not triggered" on the C8 criterion and scoring 2/12 on
native gate discipline. `reward_arithmetic` penalises exactly that, using the
same INEQ_RE / _holds code path the inference-time audit already trusts.

The penalty is deliberately narrow. Only arithmetic is checked — never whether a
line follows the rule ladder — because the ladder changed in v2 and verifier.py's
RESIDUAL_SPECS still encodes the superseded presence thresholds. Arithmetic is
ladder-independent and label-free, so this term cannot leak the answer.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from pathlib import Path

from datasets import Dataset
from peft import LoraConfig
from trl import GRPOConfig, GRPOTrainer

# Copied verbatim from infra/telelogs-dspy-tools/verifier.py rather than imported:
# verifier.py imports dspy, and the training venv deliberately has no dspy in it.
# Source of truth stays verifier.py — if the regex there changes, change it here.
INEQ_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)")


def _holds(left: float, op: str, right: float) -> bool:
    return {"<": left < right, ">": left > right, "<=": left <= right, ">=": left >= right}[op]

ANSWER_RE = re.compile(r"\bC([1-8])\b")

# What the policy actually writes, seen once log_completions was on:
#
#     Final answer: \boxed{C4}
#
# It reaches for \boxed{} because that is what the official TeleLogs scorer
# reads, and v1's pattern -- "Final answer:" followed immediately by the class --
# could never match it. rewards/reward_format/mean sat at exactly 0.0, std 0.0,
# for the whole of v1: the format term never fired once.
#
# So the reward now reads the answer the way full4_eval.py scores it: the LAST
# \boxed{...}, first digit. Train-time extraction and test-time scoring agree,
# which is the only version of this that cannot drift apart.
BOXED_RE = re.compile(r"\\boxed\{((?:[^{}]|\{[^{}]*\})*)\}")
DIGIT_RE = re.compile(r"\d")
FINAL_LINE_RE = re.compile(r"Final answer\s*:?\s*\**\s*(?:\\boxed\{)?\s*\**\s*C?([1-8])\b", re.I)


def boxed_answer(text: str) -> str:
    """The official scorer's rule: last \boxed{...}, first digit in it."""
    boxed = BOXED_RE.findall(text)
    if not boxed:
        return ""
    digit = DIGIT_RE.search(boxed[-1])
    return f"C{digit.group()}" if digit and digit.group() in "12345678" else ""


def extract_answer(text: str) -> str:
    """Boxed first, then the declared line, then v1's last-mention fallback.

    The fallback is kept so nothing that used to score stops scoring, but it is
    the fragile path -- it misreads "... C4, not C6" as C6 -- and every rung
    above it exists to keep the reward off it.
    """
    for candidate in (boxed_answer(text), ):
        if candidate:
            return candidate
    declared = FINAL_LINE_RE.findall(text)
    if declared:
        return f"C{declared[-1]}"
    matches = ANSWER_RE.findall(text)
    return f"C{matches[-1]}" if matches else ""


def completion_text(completion) -> str:
    if isinstance(completion, list):
        return " ".join(str(message.get("content", "")) for message in completion)
    return str(completion)


def reward_correct(completions, answer, **kwargs) -> list[float]:
    rewards = []
    for completion, gold in zip(completions, answer):
        predicted = extract_answer(completion_text(completion))
        rewards.append(1.0 if predicted == gold else 0.0)
    return rewards


def reward_format(completions, **kwargs) -> list[float]:
    """0.05 for ending in something the official scorer can parse."""
    return [0.05 if extract_answer(completion_text(c)) else 0.0 for c in completions]


ARITHMETIC_PENALTY = 0.05
ARITHMETIC_PENALTY_CAP = 0.15


def reward_arithmetic(completions, **kwargs) -> list[float]:
    """-0.05 per arithmetically false written inequality, capped at -0.15.

    Capped so the term shapes the trace without ever outweighing the 1.0
    correctness signal; a rollout cannot buy a wrong answer with tidy arithmetic.
    """
    rewards = []
    for completion in completions:
        text = completion_text(completion)
        false_count = sum(
            1 for m in INEQ_RE.finditer(text)
            if not _holds(float(m[1]), m[2], float(m[3]))
        )
        rewards.append(-min(false_count * ARITHMETIC_PENALTY, ARITHMETIC_PENALTY_CAP))
    return rewards


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="/workspace/telelogs-rl/data/grpo_train.jsonl")
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--output-dir", default="/workspace/telelogs-rl/checkpoints/v2")
    parser.add_argument("--merged-dir", default="/workspace/telelogs-rl/models/qwen3-8b-grpo-v2")
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--beta", type=float, default=0.04)
    parser.add_argument("--num-generations", type=int, default=8)
    parser.add_argument("--per-device-batch", type=int, default=8)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--max-prompt-length", type=int, default=8192)
    parser.add_argument("--max-completion-length", type=int, default=768)
    parser.add_argument("--temperature", type=float, default=0.9)
    parser.add_argument("--vllm-mode", choices=("server", "colocate"), default="colocate")
    parser.add_argument("--vllm-gpu-mem", type=float, default=0.25,
                        help="colocate mode: fraction of the GPU handed to the vLLM rollout engine")
    parser.add_argument("--vllm-host", default="127.0.0.1")
    parser.add_argument("--vllm-port", type=int, default=8700)
    args = parser.parse_args()

    records = [json.loads(line) for line in Path(args.dataset).read_text(encoding="utf-8").splitlines()]
    dataset = Dataset.from_list(records)
    print(f"dataset: {len(dataset)} prompts")

    config_kwargs = dict(
        output_dir=args.output_dir,
        num_train_epochs=args.epochs,
        learning_rate=args.learning_rate,
        beta=args.beta,
        num_generations=args.num_generations,
        per_device_train_batch_size=args.per_device_batch,
        gradient_accumulation_steps=args.grad_accum,
        max_prompt_length=args.max_prompt_length,
        max_completion_length=args.max_completion_length,
        temperature=args.temperature,
        bf16=True,
        gradient_checkpointing=True,
        # Required the moment the trainer runs under DDP: reentrant checkpointing
        # marks the same LoRA parameter ready twice per backward and DDP aborts
        # with "Expected to mark a variable ready only once". v1 never hit this
        # because it trained on a single card. Non-reentrant checkpointing also
        # handles inputs that do not require grad, which is why TRL only calls
        # enable_input_require_grads() on the reentrant path.
        gradient_checkpointing_kwargs={"use_reentrant": False},
        logging_steps=5,
        # Without this the run is blind: the step-5 log showed the format reward
        # sitting at exactly 0.0 and there was no way to see what the policy was
        # actually writing. Print a couple of completions per logging step.
        log_completions=True,
        num_completions_to_print=2,
        save_steps=25,
        save_total_limit=8,
        use_vllm=True,
        vllm_mode=args.vllm_mode,
        report_to=[],
    )
    if args.vllm_mode == "server":
        config_kwargs["vllm_server_host"] = args.vllm_host
        config_kwargs["vllm_server_port"] = args.vllm_port
    else:
        config_kwargs["vllm_gpu_memory_utilization"] = args.vllm_gpu_mem
    valid_fields = {field.name for field in dataclasses.fields(GRPOConfig)}
    if "chat_template_kwargs" in valid_fields:
        config_kwargs["chat_template_kwargs"] = {"enable_thinking": False}
    else:
        print("WARN: GRPOConfig lacks chat_template_kwargs; rollouts use the template default thinking mode")
    dropped = [key for key in list(config_kwargs) if key not in valid_fields]
    for key in dropped:
        print(f"WARN: GRPOConfig lacks field {key}; dropping it")
        config_kwargs.pop(key)

    peft_config = LoraConfig(
        r=32,
        lora_alpha=64,
        lora_dropout=0.0,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        task_type="CAUSAL_LM",
    )

    trainer = GRPOTrainer(
        model=args.model_path,
        reward_funcs=[reward_correct, reward_format, reward_arithmetic],
        args=GRPOConfig(**config_kwargs),
        train_dataset=dataset,
        peft_config=peft_config,
    )
    trainer.train()
    trainer.save_model(args.output_dir + "/final")

    merged = trainer.model.merge_and_unload()
    merged.save_pretrained(args.merged_dir, safe_serialization=True)
    trainer.processing_class.save_pretrained(args.merged_dir)
    print(f"merged model saved to {args.merged_dir}")


if __name__ == "__main__":
    main()

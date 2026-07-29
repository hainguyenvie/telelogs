#!/usr/bin/env python3
"""GRPO (TRL) over the single-turn tool-observation prompts.

The gold label lives only inside the reward function; prompts are label-free.
LoRA keeps the policy close to base Qwen3-8B; rollouts run on a separate
`trl vllm-serve` GPU in server mode.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
from pathlib import Path

from datasets import Dataset
from peft import LoraConfig
from trl import GRPOConfig, GRPOTrainer

ANSWER_RE = re.compile(r"\bC([1-8])\b")
FINAL_LINE_RE = re.compile(r"Final answer:\s*C[1-8]\b")


def completion_text(completion) -> str:
    if isinstance(completion, list):
        return " ".join(str(message.get("content", "")) for message in completion)
    return str(completion)


def reward_correct(completions, answer, **kwargs) -> list[float]:
    rewards = []
    for completion, gold in zip(completions, answer):
        text = completion_text(completion)
        matches = ANSWER_RE.findall(text)
        predicted = f"C{matches[-1]}" if matches else ""
        rewards.append(1.0 if predicted == gold else 0.0)
    return rewards


def reward_format(completions, **kwargs) -> list[float]:
    return [0.05 if FINAL_LINE_RE.search(completion_text(c)) else 0.0 for c in completions]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="/workspace/telelogs-rl/data/grpo_train.jsonl")
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--output-dir", default="/workspace/telelogs-rl/checkpoints/v1")
    parser.add_argument("--merged-dir", default="/workspace/telelogs-rl/models/qwen3-8b-grpo-v1")
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--beta", type=float, default=0.04)
    parser.add_argument("--num-generations", type=int, default=8)
    parser.add_argument("--per-device-batch", type=int, default=8)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--max-prompt-length", type=int, default=8192)
    parser.add_argument("--max-completion-length", type=int, default=768)
    parser.add_argument("--temperature", type=float, default=0.9)
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
        logging_steps=5,
        save_steps=50,
        save_total_limit=3,
        use_vllm=True,
        vllm_mode="server",
        vllm_server_host=args.vllm_host,
        vllm_server_port=args.vllm_port,
        report_to=[],
    )
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
        reward_funcs=[reward_correct, reward_format],
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

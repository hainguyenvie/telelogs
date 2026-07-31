#!/usr/bin/env python3
"""Score a served model under EXACTLY the GRPO training conditions.

The point of this script is to remove every confound between training and
evaluation. `make_grpo_dataset.py` trains on a single-turn prompt that carries
the raw question, the stage-1 observations (plus stage-2 only when no exact
criterion fires), the four gate criteria and the five ordered residual rules.
Anything that runs the model through the ReAct loop instead is measuring a
distribution the model was never trained on, so a bad number there would not
tell us whether RL worked.

So: same prompt builder, same stage gating, one LM call per case, no tools, no
verifier, no specialist. The gold label is used only to score.

    python3 eval_grpo_native.py --split dev --per-label 12 \
        --base http://telelogs-rl-vllm:8000/v1 --out results/grpo1_native_dev96.json

The number to compare against is the pre-RL model on the same information
state: 47.9% on the official residual zone with all six observations present
and the rules printed verbatim. That measurement is what motivated both the
residual specialist and this training run.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import urllib.request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, "/workspace/telelogs-bench4/dspy-tools/code")
sys.path.insert(0, "/workspace/telelogs-rl/code")

from make_grpo_dataset import (  # noqa: E402
    STAGE1_TOOLS,
    STAGE2_TOOLS,
    build_prompt,
    gate_fires,
    question_group,
    split_for,
)
from neutral_tools import TOOL_FUNCTIONS, assert_label_neutral, parse_case  # noqa: E402

LABELS = tuple(f"C{i}" for i in range(1, 9))
GATE_LABELS = {"C2", "C5", "C7", "C8"}
# "Final answer: C2", "Final answer: \\boxed{C2}", "**Final answer:** C2". The base model
# wraps the label in \\boxed{}, so the separator class must allow letters — an earlier
# [^A-Za-z0-9] version scored all 96 base completions as unparsed.
ANSWER_RE = re.compile(r"final\s*answer.{0,40}?(C[1-8])\b", re.IGNORECASE | re.DOTALL)


def balanced_slice(rows, split, per_label, offset):
    """Identical selection to run_tool_experiment.balanced_slice."""
    pools = defaultdict(list)
    for row in rows:
        if row["split"] == split:
            pools[row["label"]].append(row)
    chosen = []
    for rank in range(per_label):
        for label in LABELS:
            pool = sorted(pools[label], key=lambda item: item["source_index"])
            if len(pool) < offset + per_label:
                raise ValueError(f"{split}/{label} has {len(pool)} rows, need {offset + per_label}")
            chosen.append(pool[offset + rank])
    return chosen


def observations_for(question: str) -> tuple[dict, str]:
    case = parse_case(question)
    stage1 = {name: TOOL_FUNCTIONS[name](case) for name in STAGE1_TOOLS}
    assert_label_neutral(stage1)
    observations = dict(stage1)
    stage = "gated"
    if not gate_fires(stage1):
        stage = "residual"
        stage2 = {name: TOOL_FUNCTIONS[name](case) for name in STAGE2_TOOLS}
        assert_label_neutral(stage2)
        observations.update(stage2)
    return observations, stage


def complete(base: str, messages: list[dict], max_tokens: int, temperature: float) -> str:
    payload = json.dumps({
        "model": "Qwen/Qwen3-8B",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode()
    request = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer local"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        body = json.loads(response.read())
    message = body["choices"][0]["message"]
    # the server runs --reasoning-parser qwen3; with /no_think the answer is in
    # content, but fall back rather than score a parse failure as a wrong answer
    return (message.get("content") or "") + (message.get("reasoning_content") or "")


def run_one(row, base, max_tokens, temperature):
    started = time.time()
    observations, stage = observations_for(row["question"])
    messages = build_prompt(row["question"], observations)
    try:
        text = complete(base, messages, max_tokens, temperature)
        error = None
    except Exception as exc:  # noqa: BLE001 - reported per case, never fatal
        text, error = "", f"{type(exc).__name__}: {exc}"
    matches = ANSWER_RE.findall(text)
    answer = matches[-1].upper() if matches else ""
    return {
        "source_index": row["source_index"],
        "target": row["label"],
        "answer": answer,
        "correct": answer == row["label"],
        "stage": stage,
        "elapsed_seconds": round(time.time() - started, 3),
        "completion": text,
        "error": error,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default="/workspace/telelogs-rl/data/train.json")
    parser.add_argument("--split", default="dev")
    parser.add_argument("--per-label", type=int, default=12)
    parser.add_argument("--offset-per-label", type=int, default=0)
    parser.add_argument("--base", default="http://telelogs-rl-vllm:8000/v1")
    parser.add_argument("--max-tokens", type=int, default=1000)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    raw = json.loads(Path(args.raw).read_text(encoding="utf-8"))
    rows = [
        {
            "source_index": index,
            "label": row["answer"],
            "question": row["question"],
            "split": split_for(question_group(row["question"])),
        }
        for index, row in enumerate(raw)
    ]
    chosen = balanced_slice(rows, args.split, args.per_label, args.offset_per_label)
    print(json.dumps({"stage": "start", "cases": len(chosen), "split": args.split,
                      "base": args.base, "temperature": args.temperature}), flush=True)

    results = []
    with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = [pool.submit(run_one, row, args.base, args.max_tokens, args.temperature)
                   for row in chosen]
        for done, future in enumerate(futures.as_completed(pending), start=1):
            results.append(future.result())
            if done % 12 == 0:
                ok = sum(r["correct"] for r in results)
                print(json.dumps({"stage": "progress", "done": done,
                                  "running_accuracy": round(ok / done, 4)}), flush=True)

    results.sort(key=lambda r: r["source_index"])
    total = len(results)
    correct = sum(r["correct"] for r in results)
    unparsed = sum(1 for r in results if not r["answer"])
    errors = sum(1 for r in results if r["error"])

    by_label = {}
    for label in LABELS:
        sub = [r for r in results if r["target"] == label]
        if sub:
            by_label[label] = {"correct": sum(r["correct"] for r in sub), "total": len(sub)}
    by_stage = {}
    for stage in ("gated", "residual"):
        sub = [r for r in results if r["stage"] == stage]
        if sub:
            by_stage[stage] = {"correct": sum(r["correct"] for r in sub), "total": len(sub),
                               "accuracy": round(sum(r["correct"] for r in sub) / len(sub), 4)}
    # the residual zone as the report defines it: gold is a residual class
    residual_gold = [r for r in results if r["target"] not in GATE_LABELS]
    confusions = Counter((r["target"], r["answer"]) for r in results if not r["correct"])

    summary = {
        "split": args.split, "per_label": args.per_label, "base": args.base,
        "temperature": args.temperature, "total": total, "correct": correct,
        "accuracy": round(correct / total, 4), "unparsed": unparsed, "errors": errors,
        "mean_latency_seconds": round(sum(r["elapsed_seconds"] for r in results) / total, 2),
        "by_label": by_label, "by_stage": by_stage,
        "residual_gold": {"correct": sum(r["correct"] for r in residual_gold),
                          "total": len(residual_gold),
                          "accuracy": round(sum(r["correct"] for r in residual_gold)
                                            / max(1, len(residual_gold)), 4)},
        "top_confusions": [{"target": t, "prediction": p or "(unparsed)", "count": c}
                           for (t, p), c in confusions.most_common(10)],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with out.with_suffix(".jsonl").open("w", encoding="utf-8") as stream:
        for record in results:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()

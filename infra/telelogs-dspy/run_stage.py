#!/usr/bin/env python3
"""Run one sequential DSPy TeleLogs experiment stage."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

import dspy

from telelogs_program import (
    CalibratedHybridTeleLogsProgram,
    HybridTeleLogsProgram,
    LABELS,
    TeleLogsProgram,
    exact_metric,
    gepa_metric,
    normalize_answer,
)


ROOT = Path(os.environ.get("DSPY_ROOT", "/workspace/telelogs-bench4/dspy"))
DATA = Path(
    os.environ.get("DSPY_DATA", ROOT / "data/telelogs_train_splits.jsonl")
)
RESULTS = ROOT / "results"
MODEL = os.environ.get("DSPY_MODEL", "openai/Qwen/Qwen3-8B")
API_BASE = os.environ.get(
    "DSPY_API_BASE", "http://telelogs-bench4-vllm:8000/v1"
)


def load_rows() -> list[dict]:
    return [json.loads(line) for line in DATA.read_text().splitlines()]


def balanced(rows: list[dict], split: str, per_label: int) -> list[dict]:
    return balanced_slice(rows, split, per_label, offset_per_label=0)


def balanced_slice(
    rows: list[dict], split: str, per_label: int, offset_per_label: int
) -> list[dict]:
    pools: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["split"] == split:
            pools[row["label"]].append(row)
    selected_by_label: dict[str, list[dict]] = {}
    for label in LABELS:
        group = sorted(pools[label], key=lambda row: row["source_index"])
        needed = offset_per_label + per_label
        if len(group) < needed:
            raise ValueError(
                f"{split}/{label} only has {len(group)} rows, need {needed}"
            )
        selected_by_label[label] = group[offset_per_label:needed]
    # Round-robin ordering makes deterministic LabeledFewShot(k=8) receive one
    # example per class instead of eight examples from the first class.
    return [
        selected_by_label[label][rank]
        for rank in range(per_label)
        for label in LABELS
    ]


def evaluation_rows(rows: list[dict], split: str, per_label: int) -> list[dict]:
    if per_label > 0:
        return balanced(rows, split, per_label)
    return sorted(
        (row for row in rows if row["split"] == split),
        key=lambda row: row["source_index"],
    )


def example(row: dict, include_outputs: bool) -> dspy.Example:
    values = {"verified_facts": row["verified_facts"], "answer": row["label"]}
    if include_outputs:
        values["reasoning"] = row["gold_reasoning"]
    return dspy.Example(**values).with_inputs("verified_facts")


def focused_train(rows: list[dict]) -> list[dict]:
    """Give GEPA enough named residual failures to reflect on.

    Exact-gate classes remain represented, while C3 and its three main competing
    residual classes dominate the reflective trainset.
    """
    quotas = {
        "C1": 8,
        "C2": 2,
        "C3": 24,
        "C4": 8,
        "C5": 2,
        "C6": 8,
        "C7": 2,
        "C8": 2,
    }
    selected = []
    for label in LABELS:
        pool = sorted(
            (
                row
                for row in rows
                if row["split"] == "train"
                and row["label"] == label
                and row.get("demo_ready")
            ),
            key=lambda row: row["source_index"],
        )
        selected.extend(pool[: quotas[label]])
    return selected


def contrastive_train(rows: list[dict]) -> list[dict]:
    """Four reviewed residual demonstrations with complementary evidence.

    These rows come only from the template-separated train split and are marked
    solution-ready by the v14 evidence audit. Quarantined ambiguous C3 examples
    are deliberately excluded.
    """
    source_indices = (1, 54, 20, 35)  # C1, C3, C4, C6
    by_index = {row["source_index"]: row for row in rows}
    selected = [by_index[index] for index in source_indices]
    if any(row["split"] != "train" or not row.get("demo_ready") for row in selected):
        raise RuntimeError("contrastive demonstration audit invariant failed")
    return selected


def configure_lm(max_tokens: int) -> dspy.LM:
    lm = dspy.LM(
        MODEL,
        api_base=API_BASE,
        api_key="local",
        temperature=0.0,
        max_tokens=max_tokens,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        cache=False,
    )
    dspy.configure(lm=lm)
    return lm


def configure_reflection_lm(provider: str) -> dspy.LM:
    if provider == "qwen":
        return dspy.LM(
            MODEL,
            api_base=API_BASE,
            api_key="local",
            temperature=0.7,
            max_tokens=2400,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            cache=False,
        )

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required for DeepSeek reflection")
    model = {
        "deepseek-pro": "deepseek-v4-pro",
        "deepseek-flash": "deepseek-v4-flash",
    }[provider]
    return dspy.LM(
        f"openai/{model}",
        api_base="https://api.deepseek.com",
        api_key=api_key,
        temperature=0.7,
        max_tokens=3000,
        extra_body={"thinking": {"type": "disabled"}},
        cache=False,
    )


def reflection_usage(lm: dspy.LM | None) -> dict:
    if lm is None:
        return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    prompt_tokens = completion_tokens = 0
    for item in getattr(lm, "history", []):
        usage = item.get("usage") or {}
        prompt_tokens += int(usage.get("prompt_tokens") or 0)
        completion_tokens += int(usage.get("completion_tokens") or 0)
    return {
        "calls": len(getattr(lm, "history", [])),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }


def evaluate(program: dspy.Module, rows: list[dict], workers: int) -> tuple[dict, list[dict]]:
    def run_one(row: dict) -> dict:
        started = time.monotonic()
        error = None
        try:
            pred = program(verified_facts=row["verified_facts"])
            answer = normalize_answer(getattr(pred, "answer", ""))
            reasoning = str(getattr(pred, "reasoning", ""))
            decision_path = str(getattr(pred, "decision_path", "dspy_lm"))
        except Exception as exc:
            answer, reasoning = "", ""
            decision_path = "error"
            error = f"{type(exc).__name__}: {exc}"
        return {
            "source_index": row["source_index"],
            "split": row["split"],
            "target": row["label"],
            "answer": answer,
            "correct": answer == row["label"],
            "reasoning": reasoning,
            "decision_path": decision_path,
            "reasoning_words": len(reasoning.split()),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": error,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        output = list(pool.map(run_one, rows))
    confusions = Counter((row["target"], row["answer"] or "EMPTY") for row in output)
    by_label = {}
    for label in LABELS:
        label_rows = [row for row in output if row["target"] == label]
        correct = sum(row["correct"] for row in label_rows)
        by_label[label] = {
            "correct": correct,
            "total": len(label_rows),
            "accuracy": correct / len(label_rows) if label_rows else 0.0,
        }
    elapsed = [row["elapsed_seconds"] for row in output]
    words = [row["reasoning_words"] for row in output]
    summary = {
        "correct": sum(row["correct"] for row in output),
        "total": len(output),
        "accuracy": sum(row["correct"] for row in output) / len(output),
        "errors": sum(bool(row["error"]) for row in output),
        "by_label": by_label,
        "prediction_distribution": dict(Counter(row["answer"] or "EMPTY" for row in output)),
        "decision_paths": dict(Counter(row["decision_path"] for row in output)),
        "top_confusions": [
            {"target": target, "prediction": prediction, "count": count}
            for (target, prediction), count in confusions.most_common()
            if target != prediction
        ][:16],
        "latency_seconds": {
            "mean": statistics.mean(elapsed),
            "median": statistics.median(elapsed),
        },
        "reasoning_words": {
            "mean": statistics.mean(words),
            "median": statistics.median(words),
        },
    }
    return summary, output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "stage",
        choices=(
            "structured_zero",
            "hybrid",
            "labeled_fewshot",
            "bootstrap",
            "contrastive_fewshot",
            "calibrated_prompt",
            "gepa",
            "gepa_hybrid",
            "gepa_calibrated",
        ),
    )
    parser.add_argument("--eval-per-label", type=int, default=8)
    parser.add_argument("--eval-offset-per-label", type=int, default=0)
    parser.add_argument(
        "--eval-split", choices=("dev", "holdout", "official"), default="dev"
    )
    parser.add_argument("--train-per-label", type=int, default=4)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--gepa-max-metric-calls", type=int, default=192)
    parser.add_argument("--gepa-val-per-label", type=int, default=4)
    parser.add_argument("--c3-advantage-threshold-mbps", type=float, default=142.5)
    parser.add_argument(
        "--reflection-provider",
        choices=("qwen", "deepseek-pro", "deepseek-flash"),
        default="qwen",
    )
    parser.add_argument("--train-focus", choices=("balanced", "residual"), default="balanced")
    parser.add_argument("--run-name")
    args = parser.parse_args()

    configure_lm(args.max_tokens)
    rows = load_rows()
    needs_trainset = args.stage in {
        "labeled_fewshot",
        "bootstrap",
        "contrastive_fewshot",
        "gepa",
        "gepa_hybrid",
        "gepa_calibrated",
    }
    train_rows = []
    if needs_trainset:
        if args.stage == "contrastive_fewshot":
            train_rows = contrastive_train(rows)
        else:
            train_rows = (
                focused_train(rows)
                if args.train_focus == "residual"
                else balanced(
                    [row for row in rows if row.get("demo_ready")],
                    "train",
                    args.train_per_label,
                )
            )
    eval_rows = (
        balanced_slice(
            rows,
            args.eval_split,
            args.eval_per_label,
            args.eval_offset_per_label,
        )
        if args.eval_per_label > 0
        else evaluation_rows(rows, args.eval_split, args.eval_per_label)
    )
    trainset = [example(row, include_outputs=True) for row in train_rows]

    if args.stage in {"calibrated_prompt", "gepa_calibrated"}:
        program: dspy.Module = CalibratedHybridTeleLogsProgram(
            c3_advantage_threshold_mbps=args.c3_advantage_threshold_mbps
        )
    elif args.stage in {"hybrid", "gepa_hybrid", "contrastive_fewshot"}:
        program = HybridTeleLogsProgram()
    else:
        program = TeleLogsProgram()
    reflection_lm: dspy.LM | None = None
    if args.stage == "labeled_fewshot":
        program = dspy.LabeledFewShot(k=min(8, len(trainset))).compile(
            program, trainset=trainset, sample=False
        )
    elif args.stage == "contrastive_fewshot":
        program = dspy.LabeledFewShot(k=4).compile(
            program, trainset=trainset, sample=False
        )
    elif args.stage == "bootstrap":
        optimizer = dspy.BootstrapFewShot(
            metric=exact_metric,
            max_bootstrapped_demos=4,
            max_labeled_demos=8,
            max_rounds=1,
        )
        program = optimizer.compile(program, trainset=trainset)
    elif args.stage in {"gepa", "gepa_hybrid", "gepa_calibrated"}:
        reflection_lm = configure_reflection_lm(args.reflection_provider)
        optimizer = dspy.GEPA(
            metric=gepa_metric,
            reflection_lm=reflection_lm,
            max_metric_calls=args.gepa_max_metric_calls,
            num_threads=args.workers,
            track_stats=True,
        )
        val_rows = balanced(rows, "dev", args.gepa_val_per_label)
        if {row["source_index"] for row in val_rows} & {
            row["source_index"] for row in eval_rows
        }:
            raise RuntimeError("GEPA validation rows overlap final evaluation rows")
        valset = [example(row, include_outputs=False) for row in val_rows]
        program = optimizer.compile(program, trainset=trainset, valset=valset)

    run_dir = RESULTS / (args.run_name or args.stage)
    run_dir.mkdir(parents=True, exist_ok=True)
    program.save(run_dir / "program.json")
    summary, predictions = evaluate(program, eval_rows, args.workers)
    summary.update(
        {
            "stage": args.stage,
            "model": MODEL,
            "eval_split": args.eval_split,
            "eval_per_label": args.eval_per_label,
            "eval_offset_per_label": args.eval_offset_per_label,
            "train_per_label": args.train_per_label,
            "train_examples": len(train_rows),
            "train_focus": args.train_focus,
            "max_tokens": args.max_tokens,
            "native_thinking": False,
            "visible_reasoning_field": True,
            "c3_advantage_threshold_mbps": (
                args.c3_advantage_threshold_mbps
                if args.stage == "calibrated_prompt"
                else None
            ),
            "reflection_provider": (
                args.reflection_provider
                if args.stage in {"gepa", "gepa_hybrid", "gepa_calibrated"}
                else None
            ),
            "reflection_usage": reflection_usage(reflection_lm),
        }
    )
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "predictions.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in predictions),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

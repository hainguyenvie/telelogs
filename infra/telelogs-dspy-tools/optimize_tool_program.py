#!/usr/bin/env python3
"""Optimize the tool-calling TeleLogs programs with DSPy (bootstrap/MIPRO/GEPA).

The optimized artifact is a prompt state JSON that run_tool_experiment.py can
load back with --compiled, so optimized and base variants share one evaluation
and dashboard pipeline. Train and validation examples come only from the train
split; the dev split stays untouched for selection and holdout for confirmation.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import time
from pathlib import Path
from typing import Any

import dspy

from neutral_tools import parse_case
from run_tool_experiment import MODEL, API_BASE, RAW_DATA, ROOT, balanced_slice, load_rows
from tool_program import PROGRAMS, normalize_answer


EVIDENCE_HINTS = {
    "C1": "serving RSRP level and antenna vertical geometry (analyze_coverage_geometry)",
    "C2": "serving distance measurements (analyze_coverage_geometry)",
    "C3": "per-serving-segment throughput comparison (analyze_throughput_segments)",
    "C4": "non-colocated neighbor power on low-throughput rows (analyze_neighbor_overlap)",
    "C5": "serving-cell transition events (analyze_mobility)",
    "C6": "serving and neighbor PCI modulo-30 residues (analyze_pci_relations)",
    "C7": "vehicle speed measurements (analyze_mobility)",
    "C8": "scheduled resource-block measurements (analyze_radio_resources)",
}


def exact_metric(example: dspy.Example, prediction: dspy.Prediction, trace=None) -> bool:
    del trace
    return normalize_answer(getattr(prediction, "answer", "")) == example.answer


def tool_gepa_metric(
    example: dspy.Example,
    prediction: dspy.Prediction,
    trace=None,
    pred_name=None,
    pred_trace=None,
) -> dspy.Prediction:
    del trace, pred_name, pred_trace
    predicted = normalize_answer(getattr(prediction, "answer", ""))
    if predicted == example.answer:
        words = len(str(getattr(prediction, "reasoning", "")).split())
        feedback = (
            "Correct class."
            if words <= 180
            else "Correct class, but keep the final evidence trace under 180 words."
        )
        return dspy.Prediction(score=1.0, feedback=feedback)
    called = ", ".join(getattr(prediction, "selected_tools", [])) or "none"
    feedback = (
        f"Wrong class: predicted {predicted or 'unparseable'}, expected {example.answer}. "
        f"Tools called in order: {called}. "
        f"The decisive evidence for the expected class lives in {EVIDENCE_HINTS[example.answer]}. "
        "Improve the instructions so the agent requests the measurement scopes that separate "
        "the plausible causes of the specific case and interprets them against the eight cause "
        "definitions, instead of assuming unmeasured evidence. Do not introduce a fixed global "
        "priority ranking among C1, C3, C4, and C6; require per-case measured evidence instead."
    )
    return dspy.Prediction(score=0.0, feedback=feedback)


def build_examples(rows: list[dict[str, Any]]) -> list[dspy.Example]:
    return [
        dspy.Example(
            raw_question=row["question"],
            case=parse_case(row["question"]),
            answer=row["label"],
        ).with_inputs("raw_question", "case")
        for row in rows
    ]


def evaluate(program: dspy.Module, examples: list[dspy.Example], workers: int) -> dict[str, Any]:
    def run_one(example: dspy.Example) -> dict[str, Any]:
        started = time.monotonic()
        try:
            pred = program(raw_question=example.raw_question, case=example.case)
            answer = normalize_answer(getattr(pred, "answer", ""))
            error = None
        except Exception as exc:
            answer, error = "", f"{type(exc).__name__}: {exc}"
        return {
            "target": example.answer,
            "answer": answer,
            "correct": answer == example.answer,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": error,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_one, examples))
    correct = sum(row["correct"] for row in results)
    return {
        "total": len(results),
        "correct": correct,
        "accuracy": correct / len(results) if results else None,
        "errors": sum(bool(row["error"]) for row in results),
        "rows": results,
    }


def reflection_lm(provider: str) -> dspy.LM:
    if provider == "qwen":
        return dspy.LM(
            MODEL,
            api_base=API_BASE,
            api_key="local",
            temperature=0.7,
            max_tokens=3000,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            cache=False,
        )
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required for DeepSeek reflection")
    model = {"deepseek-pro": "deepseek-v4-pro", "deepseek-flash": "deepseek-v4-flash"}[provider]
    return dspy.LM(
        f"openai/{model}",
        api_base="https://api.deepseek.com",
        api_key=api_key,
        temperature=0.7,
        max_tokens=3000,
        extra_body={"thinking": {"type": "disabled"}},
        cache=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--optimizer",
        choices=("none", "bootstrap", "bootstrap_balanced", "mipro", "gepa"),
        required=True,
    )
    parser.add_argument("--method", default="b3_react_tools", choices=sorted(PROGRAMS))
    parser.add_argument("--seed-instructions", type=Path, default=None,
                        help="text file whose content replaces the diagnosis signature instructions")
    parser.add_argument("--train-per-label", type=int, default=8)
    parser.add_argument("--val-per-label", type=int, default=4)
    parser.add_argument("--val-offset-per-label", type=int, default=8,
                        help="train-split offset for the validation slice; must not overlap the trainset")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=1000)
    parser.add_argument("--max-bootstrapped-demos", type=int, default=2)
    parser.add_argument("--demo-priority-labels", default=None,
                        help="comma-separated labels moved to the front of the trainset so "
                             "BootstrapFewShot draws its demos from them first")
    parser.add_argument("--gepa-max-metric-calls", type=int, default=300)
    parser.add_argument(
        "--reflection-provider",
        choices=("qwen", "deepseek-pro", "deepseek-flash"),
        default="deepseek-flash",
    )
    parser.add_argument("--run-name", required=True)
    args = parser.parse_args()

    if args.val_offset_per_label < args.train_per_label:
        raise SystemExit("validation slice would overlap the trainset")

    lm = dspy.LM(
        MODEL,
        api_base=API_BASE,
        api_key="local",
        temperature=0.0,
        max_tokens=args.max_tokens,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        cache=False,
    )
    dspy.configure(lm=lm)

    rows = load_rows(RAW_DATA)
    train_rows = balanced_slice(rows, "train", args.train_per_label, 0)
    if args.demo_priority_labels:
        priority = [item.strip() for item in args.demo_priority_labels.split(",") if item.strip()]
        pools = {label: [row for row in train_rows if row["label"] == label] for label in priority}
        rest = [row for row in train_rows if row["label"] not in priority]
        interleaved = []
        for rank in range(max((len(pool) for pool in pools.values()), default=0)):
            for label in priority:
                if rank < len(pools[label]):
                    interleaved.append(pools[label][rank])
        train_rows = interleaved + rest
    val_rows = balanced_slice(rows, "train", args.val_per_label, args.val_offset_per_label)
    trainset = build_examples(train_rows)
    valset = build_examples(val_rows)

    kwargs = {}
    if args.seed_instructions is not None:
        if args.method not in ("b1_all_tools", "b3_react_tools"):
            raise SystemExit("--seed-instructions is only supported for b1_all_tools and b3_react_tools")
        kwargs["instructions"] = args.seed_instructions.read_text(encoding="utf-8").strip()
    program = PROGRAMS[args.method](**kwargs)
    baseline = evaluate(program, valset, args.workers)
    print(json.dumps({"stage": "baseline_val", "accuracy": baseline["accuracy"]}), flush=True)

    reflection_usage = None
    if args.optimizer == "none":
        pass
    elif args.optimizer == "bootstrap":
        optimizer = dspy.BootstrapFewShot(
            metric=exact_metric,
            max_bootstrapped_demos=args.max_bootstrapped_demos,
            max_labeled_demos=0,
        )
        program = optimizer.compile(program, trainset=trainset)
    elif args.optimizer == "bootstrap_balanced":
        if not args.demo_priority_labels:
            raise SystemExit("bootstrap_balanced requires --demo-priority-labels")
        labels = [item.strip() for item in args.demo_priority_labels.split(",") if item.strip()]
        demo_bank: dict[str, list] = {}
        for label in labels:
            subset = [example for example in trainset if example.answer == label]
            optimizer = dspy.BootstrapFewShot(
                metric=exact_metric, max_bootstrapped_demos=1, max_labeled_demos=0
            )
            compiled = optimizer.compile(PROGRAMS[args.method](**kwargs), trainset=subset)
            found = 0
            for name, predictor in compiled.named_predictors():
                demo_bank.setdefault(name, []).extend(predictor.demos)
                found += len(predictor.demos)
            print(json.dumps({"stage": "demo_label", "label": label, "demos": found}), flush=True)
        program = PROGRAMS[args.method](**kwargs)
        for name, predictor in program.named_predictors():
            if demo_bank.get(name):
                predictor.demos = list(demo_bank[name])
    elif args.optimizer == "mipro":
        optimizer = dspy.MIPROv2(
            metric=exact_metric,
            auto="light",
            num_threads=args.workers,
        )
        program = optimizer.compile(
            program,
            trainset=trainset,
            valset=valset,
            requires_permission_to_run=False,
        )
    else:
        reflection = reflection_lm(args.reflection_provider)
        optimizer = dspy.GEPA(
            metric=tool_gepa_metric,
            reflection_lm=reflection,
            max_metric_calls=args.gepa_max_metric_calls,
            num_threads=args.workers,
            track_stats=True,
        )
        program = optimizer.compile(program, trainset=trainset, valset=valset)
        history = getattr(reflection, "history", [])
        reflection_usage = {
            "calls": len(history),
            "prompt_tokens": sum(int((item.get("usage") or {}).get("prompt_tokens") or 0) for item in history),
            "completion_tokens": sum(int((item.get("usage") or {}).get("completion_tokens") or 0) for item in history),
        }

    optimized = evaluate(program, valset, args.workers)
    run_dir = ROOT / "results" / "optimized" / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    program_path = run_dir / "program.json"
    try:
        program.save(program_path)
    except Exception as exc:
        # MIPROv2's bootstrapped demos carry non-string dict keys, which orjson
        # refuses. Losing a finished 20-minute compile to a serialization detail
        # is not acceptable, so fall back to the pickle form dspy also reads.
        print(json.dumps({"stage": "save_fallback", "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        program_path = run_dir / "program.pkl"
        program.save(program_path)
    summary = {
        "run_name": args.run_name,
        "optimizer": args.optimizer,
        "method": args.method,
        "model": MODEL,
        "train_per_label": args.train_per_label,
        "val_per_label": args.val_per_label,
        "val_offset_per_label": args.val_offset_per_label,
        "reflection_provider": args.reflection_provider if args.optimizer == "gepa" else None,
        "reflection_usage": reflection_usage,
        "baseline_val": {key: baseline[key] for key in ("total", "correct", "accuracy", "errors")},
        "optimized_val": {key: optimized[key] for key in ("total", "correct", "accuracy", "errors")},
        "program_path": str(program_path),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "val_rows.json").write_text(
        json.dumps({"baseline": baseline["rows"], "optimized": optimized["rows"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

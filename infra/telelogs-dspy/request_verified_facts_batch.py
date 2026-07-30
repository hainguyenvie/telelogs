#!/usr/bin/env python3
"""Run a resumable concurrent batch of TeleLogs verified facts."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import time
from collections import Counter
from pathlib import Path

import dspy

from telelogs_program import (
    CalibratedReActHybridTeleLogsProgram,
    HybridTeleLogsProgram,
    MandatoryReActTeleLogsProgram,
    MechanismSinglePassCalibratedReActTeleLogsProgram,
    SinglePassCalibratedReActTeleLogsProgram,
    TeleLogsProgram,
    normalize_answer,
)


class ConciseTeleLogsDiagnosis(dspy.Signature):
    """Diagnose one TeleLogs case from calculator-verified facts.

    Follow exact triggered gates before softer evidence. Return exactly one
    class from C1 through C8. Keep reasoning under 40 words, cite only decisive
    verified fields, and do not restate the full fact sheet.
    """

    verified_facts: str = dspy.InputField(
        desc="Authoritative compact JSON calculated from the raw tables."
    )
    reasoning: str = dspy.OutputField(
        desc="Decisive evidence only, strictly fewer than 40 words."
    )
    answer: str = dspy.OutputField(desc="Exactly one of C1,C2,C3,C4,C5,C6,C7,C8.")


class ConciseTeleLogsProgram(dspy.Module):
    def __init__(self) -> None:
        super().__init__()
        self.diagnose = dspy.Predict(ConciseTeleLogsDiagnosis)

    def forward(self, verified_facts: str) -> dspy.Prediction:
        return self.diagnose(verified_facts=verified_facts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=200)
    parser.add_argument(
        "--c3-advantage-threshold-mbps",
        type=float,
        default=142.5,
        help="Train-derived C3 threshold used by react-calibrated.",
    )
    parser.add_argument(
        "--enable-thinking",
        action="store_true",
        help="Request native model thinking through chat_template_kwargs.",
    )
    parser.add_argument(
        "--prompt-mode",
        choices=(
            "baseline",
            "concise",
            "hybrid",
            "react-mandatory",
            "react-calibrated",
            "react-single-pass-calibrated",
            "react-single-pass-mechanism",
        ),
        default="concise",
    )
    parser.add_argument(
        "--program",
        type=Path,
        help="Optional saved DSPy JSON state to load into the selected program.",
    )
    parser.add_argument(
        "--teacher-demos",
        type=Path,
        help="Optional verified teacher-reasoning JSONL for residual few-shot.",
    )
    parser.add_argument(
        "--teacher-demos-per-label",
        type=int,
        default=1,
        help="Fixed demonstrations per residual label C1/C3/C4/C6.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.program and args.teacher_demos:
        parser.error("--program and --teacher-demos are mutually exclusive")
    if args.teacher_demos and args.prompt_mode != "hybrid":
        parser.error("--teacher-demos requires --prompt-mode hybrid")

    rows = [
        json.loads(line)
        for line in args.dataset.read_text(encoding="utf-8").splitlines()
    ][args.start : args.start + args.count]
    api_key = os.environ.get("DSPY_API_KEY")
    if not api_key:
        raise SystemExit("DSPY_API_KEY is required")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result_by_index: dict[int, dict] = {}
    if args.output.exists():
        for line in args.output.read_text(encoding="utf-8").splitlines():
            result = json.loads(line)
            result_by_index[result["source_index"]] = result
    pending = [
        row
        for row in rows
        if row["source_index"] not in result_by_index
        or result_by_index[row["source_index"]].get("error")
    ]

    lm = dspy.LM(
        os.environ.get("DSPY_MODEL", "openai/netLLMv1.0"),
        api_base=os.environ.get(
            "DSPY_API_BASE", "https://stream-netmind.viettel.vn/gateway/v1"
        ),
        api_key=api_key,
        temperature=0.0,
        max_tokens=args.max_tokens,
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": args.enable_thinking
            }
        },
        cache=False,
    )
    dspy.configure(lm=lm)
    if args.prompt_mode == "baseline":
        program = TeleLogsProgram()
    elif args.prompt_mode == "hybrid":
        program = HybridTeleLogsProgram()
    elif args.prompt_mode == "react-mandatory":
        program = MandatoryReActTeleLogsProgram()
    elif args.prompt_mode == "react-calibrated":
        program = CalibratedReActHybridTeleLogsProgram(
            c3_advantage_threshold_mbps=args.c3_advantage_threshold_mbps
        )
    elif args.prompt_mode == "react-single-pass-calibrated":
        program = SinglePassCalibratedReActTeleLogsProgram(
            c3_advantage_threshold_mbps=args.c3_advantage_threshold_mbps
        )
    elif args.prompt_mode == "react-single-pass-mechanism":
        program = MechanismSinglePassCalibratedReActTeleLogsProgram(
            c3_advantage_threshold_mbps=args.c3_advantage_threshold_mbps
        )
    else:
        program = ConciseTeleLogsProgram()
    teacher_demo_indices: list[int] = []
    if args.teacher_demos:
        if not args.teacher_demos.is_file():
            raise SystemExit(
                f"Teacher demo file does not exist: {args.teacher_demos}"
            )
        demo_rows = [
            json.loads(line)
            for line in args.teacher_demos.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        residual_labels = ("C1", "C3", "C4", "C6")
        selected_by_label: dict[str, list[dict]] = {}
        for label in residual_labels:
            pool = []
            for demo in demo_rows:
                if (
                    demo.get("label") != label
                    or not demo.get("demo_ready")
                    or not demo.get("gold_reasoning")
                ):
                    continue
                facts = json.loads(demo["verified_facts"])
                exact_active = (
                    facts["C2"]["distance_gate"] == "triggered"
                    or facts["C5"]["frequent_change_gate"]
                    or facts["C7"]["speed_gate"]
                    or facts["C8"]["affected_average_rb_gate"]
                    or facts["C1"]["affected_weak_rsrp_witness"]
                )
                if not exact_active:
                    pool.append(demo)
            pool.sort(key=lambda demo: demo["source_index"])
            if len(pool) < args.teacher_demos_per_label:
                raise SystemExit(
                    f"Only {len(pool)} residual teacher demos for {label}; "
                    f"need {args.teacher_demos_per_label}"
                )
            selected_by_label[label] = pool[
                : args.teacher_demos_per_label
            ]
        selected_demos = [
            selected_by_label[label][rank]
            for rank in range(args.teacher_demos_per_label)
            for label in residual_labels
        ]
        teacher_demo_indices = [
            demo["source_index"] for demo in selected_demos
        ]
        trainset = [
            dspy.Example(
                verified_facts=demo["verified_facts"],
                reasoning=demo["gold_reasoning"],
                answer=demo["label"],
            ).with_inputs("verified_facts")
            for demo in selected_demos
        ]
        program = dspy.LabeledFewShot(
            k=len(trainset)
        ).compile(program, trainset=trainset, sample=False)
        program.save(args.output.with_name("program.json"))
    if args.program:
        if not args.program.is_file():
            raise SystemExit(f"Saved program does not exist: {args.program}")
        program.load(args.program)

    def run_one(row: dict) -> dict:
        started = time.monotonic()
        try:
            prediction = program(verified_facts=row["verified_facts"])
            answer = normalize_answer(prediction.answer)
            reasoning = str(prediction.reasoning)
            return {
                "source_index": row["source_index"],
                "target": row["label"],
                "answer": answer,
                "correct": answer == row["label"],
                "reasoning": reasoning,
                "reasoning_words": len(reasoning.split()),
                "decision_path": getattr(
                    prediction, "decision_path", "dspy_predict"
                ),
                "tool_called": getattr(prediction, "tool_called", None),
                "tool_succeeded": getattr(
                    prediction, "tool_succeeded", None
                ),
                "tool_name": getattr(prediction, "tool_name", None),
                "tool_args": getattr(prediction, "tool_args", None),
                "tool_observation": getattr(
                    prediction, "tool_observation", None
                ),
                "tool_calls": getattr(prediction, "tool_calls", None),
                "residual_tool_required": getattr(
                    prediction, "residual_tool_required", None
                ),
                "residual_tool_called": getattr(
                    prediction, "residual_tool_called", None
                ),
                "residual_tool_succeeded": getattr(
                    prediction, "residual_tool_succeeded", None
                ),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": None,
            }
        except Exception as exc:
            return {
                "source_index": row["source_index"],
                "target": row["label"],
                "answer": "",
                "correct": False,
                "reasoning": "",
                "reasoning_words": 0,
                "decision_path": "error",
                "tool_called": False,
                "tool_succeeded": False,
                "tool_name": None,
                "tool_args": None,
                "tool_observation": None,
                "tool_calls": None,
                "residual_tool_required": None,
                "residual_tool_called": None,
                "residual_tool_succeeded": None,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }

    print(
        f"start mode={args.prompt_mode} total={len(rows)} "
        f"completed={len(rows) - len(pending)} pending={len(pending)} "
        f"workers={args.workers}",
        flush=True,
    )
    with args.output.open("a", encoding="utf-8", buffering=1) as output:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.workers
        ) as executor:
            futures = {
                executor.submit(run_one, row): row["source_index"]
                for row in pending
            }
            for completed_count, future in enumerate(
                concurrent.futures.as_completed(futures), start=1
            ):
                result = future.result()
                result_by_index[result["source_index"]] = result
                output.write(json.dumps(result, ensure_ascii=False) + "\n")
                output.flush()
                if completed_count % 10 == 0 or completed_count == len(pending):
                    successful = sum(
                        not row.get("error") for row in result_by_index.values()
                    )
                    print(
                        f"progress={successful}/{len(rows)} "
                        f"attempted={completed_count}/{len(pending)}",
                        flush=True,
                    )

    results = [
        result_by_index[row["source_index"]]
        for row in rows
        if row["source_index"] in result_by_index
    ]
    by_label = {}
    for label in (f"C{i}" for i in range(1, 9)):
        label_rows = [row for row in results if row["target"] == label]
        label_correct = sum(row["correct"] for row in label_rows)
        by_label[label] = {
            "correct": label_correct,
            "total": len(label_rows),
            "accuracy": (
                label_correct / len(label_rows) if label_rows else None
            ),
        }
    word_counts = [row["reasoning_words"] for row in results]
    correct = sum(row["correct"] for row in results)
    summary = {
        "model": os.environ.get("DSPY_MODEL", "openai/netLLMv1.0"),
        "evaluation": f"verified_facts_zero_shot_{args.prompt_mode}",
        "prompt_mode": args.prompt_mode,
        "workers": args.workers,
        "max_tokens": args.max_tokens,
        "enable_thinking": args.enable_thinking,
        "c3_advantage_threshold_mbps": (
            args.c3_advantage_threshold_mbps
            if args.prompt_mode
            in {
                "react-calibrated",
                "react-single-pass-calibrated",
                "react-single-pass-mechanism",
            }
            else None
        ),
        "tool_observation_label_hints": (
            False
            if args.prompt_mode == "react-single-pass-mechanism"
            else (
                True
                if args.prompt_mode == "react-single-pass-calibrated"
                else None
            )
        ),
        "loaded_program": (
            str(args.program.resolve()) if args.program else None
        ),
        "teacher_demos": (
            str(args.teacher_demos.resolve())
            if args.teacher_demos
            else None
        ),
        "teacher_demo_indices": teacher_demo_indices,
        "teacher_demos_per_label": (
            args.teacher_demos_per_label if args.teacher_demos else 0
        ),
        "completed": len(results),
        "correct": correct,
        "accuracy": correct / len(results) if results else None,
        "errors": sum(bool(row["error"]) for row in results),
        "by_label": by_label,
        "prediction_distribution": dict(Counter(row["answer"] for row in results)),
        "decision_path_distribution": dict(
            Counter(row.get("decision_path", "unknown") for row in results)
        ),
        "tool_call_compliance": {
            "called": sum(row.get("tool_called") is True for row in results),
            "succeeded": sum(
                row.get("tool_succeeded") is True for row in results
            ),
            "execution_errors": sum(
                row.get("decision_path") == "react_tool_error"
                for row in results
            ),
            "skipped": sum(
                row.get("decision_path") == "react_tool_skipped"
                for row in results
            ),
            "residual_required": sum(
                row.get("residual_tool_required") is True
                for row in results
            ),
            "residual_called": sum(
                row.get("residual_tool_called") is True
                for row in results
            ),
            "residual_succeeded": sum(
                row.get("residual_tool_succeeded") is True
                for row in results
            ),
            "invalid_sequence": sum(
                str(row.get("decision_path", "")).endswith(
                    "_invalid_sequence"
                )
                for row in results
            ),
        },
        "reasoning_words": {
            "minimum": min(word_counts) if word_counts else 0,
            "maximum": max(word_counts) if word_counts else 0,
            "mean": statistics.mean(word_counts) if word_counts else 0,
            "over_limit": sum(
                count
                >= (40 if args.prompt_mode == "concise" else 120)
                for count in word_counts
            ),
        },
        "output": str(args.output),
    }
    summary_path = args.output.with_name("summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )
    if summary["errors"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

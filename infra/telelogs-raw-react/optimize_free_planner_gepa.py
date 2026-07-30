#!/usr/bin/env python3
"""Compile the raw-table free tool planner with GEPA, then run a dev gate."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

import dspy

from raw_react_program import RawTableReActTeleLogsProgram, normalize_answer


LABEL_TO_TOOL = {
    "C1": "analyze_signal_coverage",
    "C2": "analyze_radio_geometry",
    "C3": "compare_segment_throughput",
    "C4": "analyze_neighbor_overlap",
    "C5": "analyze_serving_transitions",
    "C6": "analyze_pci_pattern",
    "C7": "analyze_mobility",
    "C8": "analyze_resource_usage",
}

FEEDBACK = {
    "C1": "Prioritize below-lobe geometry and weak serving-signal evidence.",
    "C2": "Prioritize serving distance joined with engineering coordinates.",
    "C3": "Prioritize minimum-throughput comparison across serving PCI segments.",
    "C4": "Prioritize strong non-colocated neighbor overlap in affected rows.",
    "C5": "Prioritize the count and destinations of serving-PCI transitions.",
    "C6": "Prioritize serving-neighbor modulo-30 collision in affected rows.",
    "C7": "Prioritize maximum GPS speed and the explicit 40 km/h mechanism.",
    "C8": "Prioritize average scheduled RBs only over affected rows.",
}


def selected_tools(value: object) -> list[str]:
    names: list[str] = []
    for name in re.findall(r"(?:analyze|compare)_[a-z_]+", str(value or "")):
        if name not in names:
            names.append(name)
    return names


def gepa_tool_metric(
    example: dspy.Example,
    prediction: dspy.Prediction,
    trace=None,
    pred_name=None,
    pred_trace=None,
) -> dspy.Prediction:
    del trace, pred_name, pred_trace
    expected_tool = example.expected_tool
    planned = list(getattr(prediction, "planned_tools", []))
    answer = normalize_answer(getattr(prediction, "answer", ""))
    plan_hit = expected_tool in planned
    answer_correct = answer == example.answer
    compliant = bool(getattr(prediction, "tool_succeeded", False))
    score = (
        (0.60 if plan_hit else 0.0)
        + (0.25 if answer_correct else 0.0)
        + (0.15 if compliant else 0.0)
    )
    if plan_hit and answer_correct and compliant:
        return dspy.Prediction(score=1.0, feedback=None)
    issues = []
    if not plan_hit:
        issues.append(
            f"The shortlist missed {expected_tool}. {FEEDBACK[example.answer]}"
        )
    if len(planned) > 2:
        issues.append(
            "Keep the shortlist focused: select the primary calculator and at "
            "most one genuine competitor."
        )
    if not compliant:
        issues.append("Ensure ReAct can execute every selected calculator.")
    if not answer_correct:
        issues.append(
            f"The final answer was {answer or 'unparseable'}, expected "
            f"{example.answer}; use verified observations rather than generic "
            "coverage or interference language."
        )
    return dspy.Prediction(score=score, feedback=" ".join(issues))


def balanced(
    split_rows: list[dict],
    raw_rows: list[dict],
    split: str,
    per_label: int,
    offset: int,
) -> list[dict]:
    pools: dict[str, list[dict]] = defaultdict(list)
    for row in split_rows:
        if row["split"] == split:
            pools[row["label"]].append(row)
    output = []
    for rank in range(offset, offset + per_label):
        for label in LABEL_TO_TOOL:
            group = sorted(pools[label], key=lambda item: item["source_index"])
            if rank >= len(group):
                raise ValueError(f"{split}/{label} lacks rank {rank}")
            meta = group[rank]
            raw = raw_rows[meta["source_index"]]
            if raw["answer"] != label:
                raise ValueError("Raw/split label mismatch")
            output.append(
                {
                    "source_index": meta["source_index"],
                    "raw_table": raw["question"],
                    "answer": label,
                    "expected_tool": LABEL_TO_TOOL[label],
                }
            )
    return output


def example(row: dict) -> dspy.Example:
    return dspy.Example(
        raw_table=row["raw_table"],
        answer=row["answer"],
        expected_tool=row["expected_tool"],
    ).with_inputs("raw_table")


def evaluate(
    program: dspy.Module,
    rows: list[dict],
    workers: int,
) -> tuple[dict, list[dict]]:
    def run_one(row: dict) -> dict:
        started = time.monotonic()
        try:
            prediction = program(raw_table=row["raw_table"])
            answer = normalize_answer(prediction.answer)
            planned = list(getattr(prediction, "planned_tools", []))
            return {
                "source_index": row["source_index"],
                "target": row["answer"],
                "answer": answer,
                "correct": answer == row["answer"],
                "expected_tool": row["expected_tool"],
                "planned_tools": planned,
                "plan_hit": row["expected_tool"] in planned,
                "selection_reasoning": getattr(
                    prediction, "selection_reasoning", None
                ),
                "specialized_tools": getattr(
                    prediction, "specialized_tools", None
                ),
                "tool_succeeded": getattr(
                    prediction, "tool_succeeded", False
                ),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": None,
            }
        except Exception as exc:
            return {
                "source_index": row["source_index"],
                "target": row["answer"],
                "answer": "",
                "correct": False,
                "expected_tool": row["expected_tool"],
                "planned_tools": [],
                "plan_hit": False,
                "selection_reasoning": None,
                "specialized_tools": None,
                "tool_succeeded": False,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        predictions = list(executor.map(run_one, rows))
    completed = len(predictions)
    correct = sum(row["correct"] for row in predictions)
    plan_hits = sum(row["plan_hit"] for row in predictions)
    by_label = {}
    for label in LABEL_TO_TOOL:
        label_rows = [row for row in predictions if row["target"] == label]
        by_label[label] = {
            "correct": sum(row["correct"] for row in label_rows),
            "plan_hits": sum(row["plan_hit"] for row in label_rows),
            "total": len(label_rows),
        }
    return {
        "completed": completed,
        "correct": correct,
        "accuracy": correct / completed,
        "plan_hits": plan_hits,
        "plan_hit_rate": plan_hits / completed,
        "valid_tool_sequence": sum(
            row["tool_succeeded"] for row in predictions
        ),
        "errors": sum(bool(row["error"]) for row in predictions),
        "by_label": by_label,
        "plan_size_distribution": dict(
            Counter(len(row["planned_tools"]) for row in predictions)
        ),
        "latency_seconds": {
            "mean": statistics.mean(
                row["elapsed_seconds"] for row in predictions
            ),
            "median": statistics.median(
                row["elapsed_seconds"] for row in predictions
            ),
        },
    }, predictions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--raw-train",
        type=Path,
        default=Path("data/raw_train_2400/train.json"),
    )
    parser.add_argument(
        "--splits",
        type=Path,
        default=Path(
            "artifacts/telelogs-dspy/data/telelogs_train_splits.jsonl"
        ),
    )
    parser.add_argument("--train-per-label", type=int, default=4)
    parser.add_argument("--val-per-label", type=int, default=2)
    parser.add_argument("--gate-per-label", type=int, default=4)
    parser.add_argument("--max-metric-calls", type=int, default=64)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=500)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    api_key = os.environ.get("DSPY_API_KEY")
    if not api_key:
        raise SystemExit("DSPY_API_KEY is required")
    model = os.environ.get("DSPY_MODEL", "openai/netLLMv1.0")
    api_base = os.environ.get(
        "DSPY_API_BASE",
        "https://stream-netmind.viettel.vn/gateway/v1",
    )
    task_lm = dspy.LM(
        model,
        api_base=api_base,
        api_key=api_key,
        temperature=0.0,
        max_tokens=args.max_tokens,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        cache=False,
    )
    reflection_lm = dspy.LM(
        model,
        api_base=api_base,
        api_key=api_key,
        temperature=0.7,
        max_tokens=1200,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        cache=False,
    )
    dspy.configure(lm=task_lm)

    raw_rows = json.loads(args.raw_train.read_text(encoding="utf-8"))
    split_rows = [
        json.loads(line)
        for line in args.splits.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    train_rows = balanced(
        split_rows, raw_rows, "train", args.train_per_label, 0
    )
    val_rows = balanced(
        split_rows, raw_rows, "dev", args.val_per_label, 0
    )
    gate_rows = balanced(
        split_rows,
        raw_rows,
        "dev",
        args.gate_per_label,
        args.val_per_label,
    )
    if {row["source_index"] for row in val_rows} & {
        row["source_index"] for row in gate_rows
    }:
        raise RuntimeError("GEPA validation and gate rows overlap")

    program = RawTableReActTeleLogsProgram(
        tool_selection="free",
        max_iters=3,
    )
    optimizer = dspy.GEPA(
        metric=gepa_tool_metric,
        reflection_lm=reflection_lm,
        max_metric_calls=args.max_metric_calls,
        num_threads=args.workers,
        track_stats=True,
    )
    print(
        f"GEPA train={len(train_rows)} val={len(val_rows)} "
        f"gate={len(gate_rows)} max_metric_calls={args.max_metric_calls}",
        flush=True,
    )
    started = time.monotonic()
    compiled = optimizer.compile(
        program,
        trainset=[example(row) for row in train_rows],
        valset=[example(row) for row in val_rows],
    )
    compile_seconds = round(time.monotonic() - started, 3)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    program_path = args.output_dir / "program.json"
    compiled.save(program_path)
    gate_summary, gate_predictions = evaluate(
        compiled, gate_rows, args.workers
    )
    with (args.output_dir / "gate_predictions.jsonl").open(
        "w", encoding="utf-8"
    ) as output:
        for row in gate_predictions:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        "model": model,
        "optimizer": "dspy.GEPA",
        "tool_selection": "free",
        "train_examples": len(train_rows),
        "val_examples": len(val_rows),
        "gate_examples": len(gate_rows),
        "max_metric_calls": args.max_metric_calls,
        "compile_seconds": compile_seconds,
        "program": str(program_path),
        "gate": gate_summary,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

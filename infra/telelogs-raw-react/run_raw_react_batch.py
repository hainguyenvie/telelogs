#!/usr/bin/env python3
"""Run a resumable NetMind batch from raw TeleLogs tables through DSPy ReAct."""

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

from raw_react_program import RawTableReActTeleLogsProgram, normalize_answer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=16)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=500)
    parser.add_argument("--max-iters", type=int, default=3)
    parser.add_argument(
        "--tool-selection",
        choices=("guided", "free", "commit_plan"),
        default="guided",
    )
    parser.add_argument(
        "--program",
        type=Path,
        help="Optional saved DSPy program, including a GEPA-compiled planner.",
    )
    parser.add_argument(
        "--c3-advantage-threshold-mbps",
        type=float,
        default=142.5,
    )
    parser.add_argument("--enable-thinking", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    sources = json.loads(args.dataset.read_text(encoding="utf-8"))
    selected = sources[args.start : args.start + args.count]
    rows = [
        {
            "source_index": args.start + offset,
            "raw_table": source["question"],
            "target": source["answer"],
        }
        for offset, source in enumerate(selected)
    ]
    if not rows:
        raise SystemExit("No rows selected")

    api_key = os.environ.get("DSPY_API_KEY")
    if not api_key:
        raise SystemExit("DSPY_API_KEY is required")
    lm = dspy.LM(
        os.environ.get("DSPY_MODEL", "openai/netLLMv1.0"),
        api_base=os.environ.get(
            "DSPY_API_BASE",
            "https://stream-netmind.viettel.vn/gateway/v1",
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
    program = RawTableReActTeleLogsProgram(
        c3_advantage_threshold_mbps=args.c3_advantage_threshold_mbps,
        max_iters=args.max_iters,
        tool_selection=args.tool_selection,
    )
    if args.program:
        if not args.program.is_file():
            raise SystemExit(f"Saved DSPy program not found: {args.program}")
        program.load(args.program)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    result_by_index: dict[int, dict] = {}
    if args.output.exists():
        for line in args.output.read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                result = json.loads(line)
                result_by_index[result["source_index"]] = result
    pending = [
        row
        for row in rows
        if row["source_index"] not in result_by_index
        or result_by_index[row["source_index"]].get("error")
    ]

    def run_one(row: dict) -> dict:
        started = time.monotonic()
        try:
            prediction = program(raw_table=row["raw_table"])
            answer = normalize_answer(prediction.answer)
            reasoning = str(prediction.reasoning)
            return {
                "source_index": row["source_index"],
                "target": row["target"],
                "answer": answer,
                "correct": answer == row["target"],
                "reasoning": reasoning,
                "reasoning_words": len(reasoning.split()),
                "decision_path": getattr(
                    prediction,
                    "decision_path",
                    "raw_react_unknown",
                ),
                "tool_calls": getattr(prediction, "tool_calls", None),
                "specialized_tools": getattr(
                    prediction,
                    "specialized_tools",
                    None,
                ),
                "candidate_tools": getattr(
                    prediction,
                    "candidate_tools",
                    None,
                ),
                "planned_tools": getattr(
                    prediction,
                    "planned_tools",
                    None,
                ),
                "selection_reasoning": getattr(
                    prediction,
                    "selection_reasoning",
                    None,
                ),
                "followed_candidates": getattr(
                    prediction,
                    "followed_candidates",
                    False,
                ),
                "tool_called": getattr(prediction, "tool_called", False),
                "inspected": getattr(prediction, "inspected", False),
                "plan_committed": getattr(
                    prediction,
                    "plan_committed",
                    False,
                ),
                "tool_succeeded": getattr(
                    prediction,
                    "tool_succeeded",
                    False,
                ),
                "raw_characters": len(row["raw_table"]),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": None,
            }
        except Exception as exc:
            return {
                "source_index": row["source_index"],
                "target": row["target"],
                "answer": "",
                "correct": False,
                "reasoning": "",
                "reasoning_words": 0,
                "decision_path": "error",
                "tool_calls": None,
                "specialized_tools": None,
                "candidate_tools": None,
                "planned_tools": None,
                "selection_reasoning": None,
                "followed_candidates": False,
                "tool_called": False,
                "inspected": False,
                "plan_committed": False,
                "tool_succeeded": False,
                "raw_characters": len(row["raw_table"]),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }

    print(
        f"start raw-react total={len(rows)} completed="
        f"{len(rows) - len(pending)} pending={len(pending)} "
        f"workers={args.workers} max_iters={args.max_iters}",
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
                concurrent.futures.as_completed(futures),
                start=1,
            ):
                result = future.result()
                result_by_index[result["source_index"]] = result
                output.write(json.dumps(result, ensure_ascii=False) + "\n")
                output.flush()
                if completed_count % 10 == 0 or completed_count == len(pending):
                    successful = sum(
                        not row.get("error")
                        for row in result_by_index.values()
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
    correct = sum(row["correct"] for row in results)
    word_counts = [row["reasoning_words"] for row in results]
    latencies = [row["elapsed_seconds"] for row in results]
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
    summary = {
        "model": os.environ.get("DSPY_MODEL", "openai/netLLMv1.0"),
        "evaluation": "raw_table_dspy_react",
        "workers": args.workers,
        "max_tokens": args.max_tokens,
        "max_iters": args.max_iters,
        "effective_max_iters": args.max_iters,
        "tool_selection": args.tool_selection,
        "program": str(args.program) if args.program else None,
        "enable_thinking": args.enable_thinking,
        "c3_advantage_threshold_mbps": (
            args.c3_advantage_threshold_mbps
        ),
        "completed": len(results),
        "correct": correct,
        "accuracy": correct / len(results) if results else None,
        "errors": sum(bool(row["error"]) for row in results),
        "by_label": by_label,
        "prediction_distribution": dict(
            Counter(row["answer"] for row in results)
        ),
        "decision_path_distribution": dict(
            Counter(row["decision_path"] for row in results)
        ),
        "specialized_tool_usage": dict(
            Counter(
                tool
                for row in results
                for tool in (row.get("specialized_tools") or [])
            )
        ),
        "tool_call_compliance": {
            "inspected": sum(
                row.get("inspected") is True for row in results
            ),
            "any_tool_called": sum(
                row.get("tool_called") is True for row in results
            ),
            "plan_committed": sum(
                row.get("plan_committed") is True for row in results
            ),
            "any_specialized_tool": sum(
                bool(row.get("specialized_tools")) for row in results
            ),
            "valid_sequence": sum(
                row.get("tool_succeeded") is True for row in results
            ),
            "followed_candidates": sum(
                row.get("followed_candidates") is True
                for row in results
            ),
            "invalid_sequence": sum(
                row.get("decision_path") == "raw_react_invalid_sequence"
                for row in results
            ),
        },
        "raw_characters": {
            "minimum": min(row["raw_characters"] for row in results),
            "maximum": max(row["raw_characters"] for row in results),
            "mean": statistics.mean(
                row["raw_characters"] for row in results
            ),
        },
        "reasoning_words": {
            "minimum": min(word_counts) if word_counts else 0,
            "maximum": max(word_counts) if word_counts else 0,
            "mean": statistics.mean(word_counts) if word_counts else 0,
        },
        "latency_seconds": {
            "mean": statistics.mean(latencies) if latencies else 0,
            "median": statistics.median(latencies) if latencies else 0,
        },
        "output": str(args.output),
    }
    summary_path = args.output.with_name("summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["errors"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

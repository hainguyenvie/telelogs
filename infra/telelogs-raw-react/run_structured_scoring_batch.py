#!/usr/bin/env python3
"""Run structured tool scoring plus shortlisted ReAct on a raw dataset."""

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

from structured_scoring_program import (
    StructuredScoringReActProgram,
    TOOL_ORDER,
)


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=700)
    parser.add_argument("--scorer-attempts", type=int, default=2)
    parser.add_argument(
        "--ensemble-size",
        type=int,
        choices=(1, 3),
        default=1,
    )
    parser.add_argument("--top2-min-score", type=float, default=0.5)
    parser.add_argument("--top2-max-gap", type=float, default=0.2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    sources = json.loads(args.dataset.read_text(encoding="utf-8"))
    selected_sources = sources[args.start : args.start + args.count]
    rows = [
        {
            "source_index": args.start + offset,
            "raw_table": source["question"],
            "target": source["answer"],
        }
        for offset, source in enumerate(selected_sources)
    ]
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
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        cache=False,
    )
    dspy.configure(lm=lm)
    program = StructuredScoringReActProgram(
        scorer_attempts=args.scorer_attempts,
        ensemble_size=args.ensemble_size,
        top2_min_score=args.top2_min_score,
        top2_max_gap=args.top2_max_gap,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    result_by_index = {}
    if args.output.exists():
        for line in args.output.read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                row = json.loads(line)
                result_by_index[row["source_index"]] = row
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
            gold_tool = LABEL_TO_TOOL[row["target"]]
            ranked = list(prediction.ranked_tools)
            return {
                "source_index": row["source_index"],
                "target": row["target"],
                "answer": prediction.answer,
                "correct": prediction.answer == row["target"],
                "gold_tool": gold_tool,
                "top1_tool": ranked[0],
                "top2_tools": ranked[:2],
                "top1_hit": ranked[0] == gold_tool,
                "top2_hit": gold_tool in ranked[:2],
                "ranked_tools": ranked,
                "ranked_scores": prediction.ranked_scores,
                "selected_tools": prediction.selected_tools,
                "scorecard": prediction.scorecard,
                "scorer_attempts": prediction.scorer_attempts,
                "ensemble_size": prediction.ensemble_size,
                "ensemble_member_attempts": (
                    prediction.ensemble_member_attempts
                ),
                "ensemble_scorecards": prediction.ensemble_scorecards,
                "structured_valid": prediction.structured_valid,
                "tool_calls": prediction.tool_calls,
                "specialized_tools": prediction.specialized_tools,
                "tool_succeeded": prediction.tool_succeeded,
                "decision_path": prediction.decision_path,
                "reasoning": str(prediction.reasoning),
                "reasoning_words": len(
                    str(prediction.reasoning).split()
                ),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": None,
            }
        except Exception as exc:
            return {
                "source_index": row["source_index"],
                "target": row["target"],
                "answer": "",
                "correct": False,
                "gold_tool": LABEL_TO_TOOL[row["target"]],
                "top1_tool": None,
                "top2_tools": [],
                "top1_hit": False,
                "top2_hit": False,
                "ranked_tools": [],
                "ranked_scores": [],
                "selected_tools": [],
                "scorecard": None,
                "scorer_attempts": args.scorer_attempts,
                "ensemble_size": args.ensemble_size,
                "ensemble_member_attempts": [],
                "ensemble_scorecards": None,
                "structured_valid": False,
                "tool_calls": None,
                "specialized_tools": None,
                "tool_succeeded": False,
                "decision_path": "error",
                "reasoning": "",
                "reasoning_words": 0,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }

    print(
        f"structured-scoring total={len(rows)} pending={len(pending)} "
        f"workers={args.workers}",
        flush=True,
    )
    with args.output.open("a", encoding="utf-8", buffering=1) as output:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.workers
        ) as executor:
            futures = [executor.submit(run_one, row) for row in pending]
            for count, future in enumerate(
                concurrent.futures.as_completed(futures), start=1
            ):
                result = future.result()
                result_by_index[result["source_index"]] = result
                output.write(json.dumps(result, ensure_ascii=False) + "\n")
                output.flush()
                if count % 10 == 0 or count == len(futures):
                    print(f"progress={count}/{len(futures)}", flush=True)

    results = [result_by_index[row["source_index"]] for row in rows]
    by_label = {}
    for label in LABEL_TO_TOOL:
        label_rows = [row for row in results if row["target"] == label]
        by_label[label] = {
            "correct": sum(row["correct"] for row in label_rows),
            "top1_hits": sum(row["top1_hit"] for row in label_rows),
            "top2_hits": sum(row["top2_hit"] for row in label_rows),
            "total": len(label_rows),
        }
    summary = {
        "model": os.environ.get("DSPY_MODEL", "openai/netLLMv1.0"),
        "evaluation": "structured_tool_scoring_react",
        "workers": args.workers,
        "max_tokens": args.max_tokens,
        "ensemble_size": args.ensemble_size,
        "completed": len(results),
        "correct": sum(row["correct"] for row in results),
        "accuracy": sum(row["correct"] for row in results) / len(results),
        "top1_tool_hits": sum(row["top1_hit"] for row in results),
        "top1_tool_accuracy": sum(
            row["top1_hit"] for row in results
        ) / len(results),
        "top2_tool_hits": sum(row["top2_hit"] for row in results),
        "top2_tool_recall": sum(
            row["top2_hit"] for row in results
        ) / len(results),
        "structured_valid": sum(
            row["structured_valid"] for row in results
        ),
        "tool_sequence_valid": sum(
            row["tool_succeeded"] for row in results
        ),
        "errors": sum(bool(row["error"]) for row in results),
        "by_label": by_label,
        "top1_distribution": dict(
            Counter(row["top1_tool"] or "INVALID" for row in results)
        ),
        "selected_tool_usage": dict(
            Counter(
                tool
                for row in results
                for tool in row["selected_tools"]
            )
        ),
        "scorer_attempt_distribution": dict(
            Counter(row["scorer_attempts"] for row in results)
        ),
        "reasoning_words": {
            "mean": statistics.mean(
                row["reasoning_words"] for row in results
            )
        },
        "latency_seconds": {
            "mean": statistics.mean(
                row["elapsed_seconds"] for row in results
            ),
            "median": statistics.median(
                row["elapsed_seconds"] for row in results
            ),
        },
        "tool_catalog": list(TOOL_ORDER),
        "output": str(args.output),
    }
    summary_path = args.output.with_name("summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run DSPy TeleLogs tool experiments with realtime static-dashboard output."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import statistics
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import dspy

from neutral_tools import TOOL_FUNCTIONS, assert_label_neutral, parse_case, run_tools
from tool_program import PROGRAMS, normalize_answer


MODEL = os.environ.get("DSPY_MODEL", "openai/Qwen/Qwen3-8B")
API_BASE = os.environ.get("DSPY_API_BASE", "http://telelogs-bench4-vllm:8000/v1")
RAW_DATA = Path(os.environ.get("TELELOGS_RAW_DATA", "/workspace/telelogs-bench4/dspy-tools/data/train.json"))
ROOT = Path(os.environ.get("DSPY_TOOL_ROOT", "/workspace/telelogs-bench4/dspy-tools"))
DEFAULT_DASHBOARD = Path("/workspace/telelogs-bench4/dashboard/data/tool_progress.json")
LABELS = tuple(f"C{i}" for i in range(1, 9))
DISPLAY_NAMES = {
    "b0_raw": "B0 · DSPy raw zero-shot",
    "b1_all_tools": "B1 · DSPy all neutral tools",
    "b2_planned_tools": "B2 · DSPy plan → tools → diagnose",
    "b3_react_tools": "B3 · DSPy ReAct agentic tool calling",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def question_group(question: str) -> str:
    skeleton = re.sub(r"-?\d+(?:\.\d+)?", "#", question)
    skeleton = re.sub(r"\s+", " ", skeleton).strip()
    return hashlib.sha256(skeleton.encode()).hexdigest()[:16]


def split_for(group: str) -> str:
    bucket = int(group[:8], 16) % 100
    if bucket < 60:
        return "train"
    if bucket < 80:
        return "dev"
    return "holdout"


def load_rows(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "source_index": index,
            "label": row["answer"],
            "question": row["question"],
            "split": split_for(question_group(row["question"])),
        }
        for index, row in enumerate(raw)
    ]


def balanced_slice(rows: list[dict[str, Any]], split: str, per_label: int, offset: int) -> list[dict[str, Any]]:
    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
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


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def summarize(results: list[dict[str, Any]], methods: list[str], total_rows: int) -> dict[str, Any]:
    by_method = {}
    for method in methods:
        rows = [row for row in results if row["method"] == method]
        correct = sum(row["correct"] for row in rows)
        labels = {}
        for label in LABELS:
            subset = [row for row in rows if row["target"] == label]
            label_correct = sum(row["correct"] for row in subset)
            labels[label] = {
                "completed": len(subset),
                "total": total_rows // len(LABELS),
                "correct": label_correct,
                "accuracy": label_correct / len(subset) if subset else None,
            }
        tool_usage = Counter(tool for row in rows for tool in row["selected_tools"])
        confusions = Counter((row["target"], row["answer"] or "EMPTY") for row in rows if not row["correct"])
        by_method[method] = {
            "display_name": DISPLAY_NAMES[method],
            "completed": len(rows),
            "total": total_rows,
            "correct": correct,
            "accuracy": correct / len(rows) if rows else None,
            "errors": sum(bool(row["error"]) for row in rows),
            "mean_latency_seconds": statistics.mean(row["elapsed_seconds"] for row in rows) if rows else None,
            "mean_tool_calls": statistics.mean(len(row["selected_tools"]) for row in rows) if rows else None,
            "tool_usage": dict(tool_usage),
            "by_label": labels,
            "top_confusions": [
                {"target": target, "prediction": prediction, "count": count}
                for (target, prediction), count in confusions.most_common(12)
            ],
        }
    return by_method


def dashboard_payload(
    *,
    args: argparse.Namespace,
    methods: list[str],
    eval_rows: list[dict[str, Any]],
    results: list[dict[str, Any]],
    started: float,
    started_at: str,
    status: str,
) -> dict[str, Any]:
    elapsed = max(time.monotonic() - started, 1e-6)
    completed = len(results)
    total = len(eval_rows) * len(methods)
    recent = []
    questions = {row["source_index"]: row["question"] for row in eval_rows}
    for row in results[-48:][::-1]:
        recent.append({**row, "question": questions[row["source_index"]]})
    return {
        "schema_version": 1,
        "status": status,
        "run_name": args.run_name,
        "model": MODEL,
        "native_thinking": False,
        "dspy_enabled": True,
        "eval_split": args.eval_split,
        "eval_per_label": args.eval_per_label,
        "eval_offset_per_label": args.eval_offset_per_label,
        "methods": methods,
        "completed": completed,
        "total": total,
        "progress": completed / total if total else 0.0,
        "samples_per_minute": completed / elapsed * 60,
        "started_at": started_at,
        "updated_at": now(),
        "by_method": summarize(results, methods, len(eval_rows)),
        "recent": recent,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--methods", default="b0_raw,b1_all_tools,b2_planned_tools")
    parser.add_argument("--eval-split", choices=("train", "dev", "holdout"), default="dev")
    parser.add_argument("--eval-per-label", type=int, default=4)
    parser.add_argument("--eval-offset-per-label", type=int, default=0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=1000)
    parser.add_argument("--run-name", default="tool_dev32")
    parser.add_argument("--dashboard-output", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument(
        "--compiled",
        action="append",
        default=[],
        metavar="METHOD=PROGRAM_JSON",
        help="load an optimized DSPy program state for a method before evaluating",
    )
    args = parser.parse_args()

    methods = [item.strip() for item in args.methods.split(",") if item.strip()]
    unknown = sorted(set(methods) - PROGRAMS.keys())
    if unknown:
        raise SystemExit(f"unknown methods: {unknown}")
    compiled_paths: dict[str, Path] = {}
    for item in args.compiled:
        method, _, path = item.partition("=")
        if method not in PROGRAMS or not path:
            raise SystemExit(f"bad --compiled value: {item}")
        compiled_paths[method] = Path(path)

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
    programs = {method: PROGRAMS[method]() for method in methods}
    for method, path in compiled_paths.items():
        if method in programs:
            programs[method].load(path)

    rows = load_rows(RAW_DATA)
    eval_rows = balanced_slice(rows, args.eval_split, args.eval_per_label, args.eval_offset_per_label)
    contexts = {row["source_index"]: parse_case(row["question"]) for row in eval_rows}
    # Audit all measurements before making any LM request.
    for case in contexts.values():
        assert_label_neutral(run_tools(case, list(TOOL_FUNCTIONS)))

    run_dir = ROOT / "results" / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = run_dir / "predictions.jsonl"
    predictions_path.write_text("", encoding="utf-8")
    results: list[dict[str, Any]] = []
    lock = threading.Lock()
    started, started_at = time.monotonic(), now()

    atomic_json(
        args.dashboard_output,
        dashboard_payload(
            args=args,
            methods=methods,
            eval_rows=eval_rows,
            results=results,
            started=started,
            started_at=started_at,
            status="running",
        ),
    )

    def run_one(row: dict[str, Any], method: str) -> dict[str, Any]:
        begin = time.monotonic()
        error = None
        try:
            pred = programs[method](raw_question=row["question"], case=contexts[row["source_index"]])
            answer = normalize_answer(getattr(pred, "answer", ""))
            reasoning = str(getattr(pred, "reasoning", ""))
            selected_tools = list(getattr(pred, "selected_tools", []))
            observations = dict(getattr(pred, "tool_observations", {}))
            planning_reason = str(getattr(pred, "planning_reason", ""))
            lm_calls = int(getattr(pred, "lm_calls", 1))
        except Exception as exc:
            answer, reasoning, selected_tools, observations, planning_reason, lm_calls = "", "", [], {}, "", 0
            error = f"{type(exc).__name__}: {exc}"
        return {
            "source_index": row["source_index"],
            "split": row["split"],
            "method": method,
            "method_name": DISPLAY_NAMES[method],
            "target": row["label"],
            "answer": answer,
            "correct": answer == row["label"],
            "planning_reason": planning_reason,
            "selected_tools": selected_tools,
            "tool_observations": observations,
            "reasoning": reasoning,
            "lm_calls": lm_calls,
            "elapsed_seconds": round(time.monotonic() - begin, 3),
            "error": error,
        }

    futures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for row in eval_rows:
            for method in methods:
                futures.append(pool.submit(run_one, row, method))
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            with lock:
                results.append(result)
                with predictions_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(result, ensure_ascii=False) + "\n")
                atomic_json(
                    args.dashboard_output,
                    dashboard_payload(
                        args=args,
                        methods=methods,
                        eval_rows=eval_rows,
                        results=results,
                        started=started,
                        started_at=started_at,
                        status="running",
                    ),
                )

    final_payload = dashboard_payload(
        args=args,
        methods=methods,
        eval_rows=eval_rows,
        results=results,
        started=started,
        started_at=started_at,
        status="completed_with_errors" if any(row["error"] for row in results) else "completed",
    )
    atomic_json(args.dashboard_output, final_payload)
    summary = {key: value for key, value in final_payload.items() if key != "recent"}
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

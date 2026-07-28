#!/usr/bin/env python3
"""Resumable full evaluation for TeleLogs, TeleMath, TeleTables, and 3GPP."""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


MODEL = "Qwen/Qwen3-8B"
ENDPOINT = os.environ.get(
    "VLLM_CHAT_URL",
    "http://telelogs-bench4-vllm:8000/v1/chat/completions",
)
WORKERS = int(os.environ.get("EVAL_WORKERS", "64"))
MAX_TOKENS = int(os.environ.get("EVAL_MAX_TOKENS", "38000"))
ROOT = Path(os.environ.get("BENCH4_ROOT", "/workspace/telelogs-bench4"))
RUN_DIR = ROOT / "results" / "full4-base-qwen3-8b"
DASHBOARD_DIR = ROOT / "dashboard"
DATA_DIR = DASHBOARD_DIR / "data"
ITEMS_DIR = DATA_DIR / "items"
RESULTS_PATH = RUN_DIR / "results.jsonl"
PROGRESS_PATH = DATA_DIR / "progress.json"
RECENT_PATH = DATA_DIR / "recent.json"
REPORT_PATH = RUN_DIR / "report.md"
DATASET_ROOT = Path(
    os.environ.get(
        "OT_FULL_ROOT",
        "/workspace/telelogs/cache/hf/hub/"
        "datasets--GSMA--ot-full/snapshots/"
        "6319806f04783eafe04d9facf755d379c66b7664",
    )
)

BENCHMARKS = {
    "telelogs": DATASET_ROOT / "telelogs/test-00000-of-00001.parquet",
    "telemath": DATASET_ROOT / "telemath/test-00000-of-00001.parquet",
    "teletables": DATASET_ROOT / "teletables/test-00000-of-00001.parquet",
    "three_gpp": DATASET_ROOT / "3gpp_tsg/test-00000-of-00001.parquet",
}

TELEMATH_SYSTEM_PROMPT = r"""You are an expert problem solver. Your task is to solve numerical exercises by following these guidelines:
1.  **Understand the Goal:** Clearly identify what the problem is asking you to find, paying close attention to the required units for the final answer.
2.  **Reason Step-by-Step:** Provide a clear, sequential reasoning process. Explain the formulas, principles, or logic used in each step. Show intermediate calculations if they clarify your thought process. The detailed structure of your sub-steps is up to you, as long as the reasoning is sound and easy to follow.
3.  **Unit Management:**
    *   Track units throughout your calculations.
    *   **Crucially, ensure your final numerical answer is converted to the specific units requested in the problem statement.** If intermediate calculations result in a different unit, perform a final conversion step.
    *   State the unit of the final answer clearly in your explanatory text *before* the boxed answer.
4.  **Final Numerical Answer Format:**
    *   The final answer must be a single numerical value (integer or float).
    *   Present this numerical value exclusively within the `\$\boxed{{...}}\$` format.
    *   **CRITICAL:** The `\$\boxed{{...}}\$` block must contain *only* the number. No text, no units, no labels (e.g., NOT `\$\boxed{{Result: 50}}\$` or `\$\boxed{{50 \text{{ mA}}}}\$`, but `\$\boxed{{50}}\$`)."""

MC_TEMPLATE = r"""Answer the following multiple choice question. The entire content of your response should be of the following format: 'ANSWER: $LETTER' (without quotes) where LETTER is one of {letters}.

{question}

{choices}"""

BOXED_PATTERN = re.compile(r"\\boxed\{((?:[^{}]|\{[^{}]*\})*)\}")
DIGIT_PATTERN = re.compile(r"\d+")
WG_PATTERN = re.compile(r"([A-Z]+\d+(?:-[A-Z]+)?)", re.IGNORECASE)
PRINT_LOCK = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def parse_boxed(response: str) -> str:
    matches = BOXED_PATTERN.findall(response or "")
    if not matches:
        return ""
    answer = re.sub(r"\n\s*", "", matches[-1].strip())
    return answer.lstrip(":").rstrip("./")


def first_int(text: str) -> int | None:
    match = DIGIT_PATTERN.search(text or "")
    return int(match.group()) if match else None


def parse_choice(response: str, choice_count: int) -> str:
    matches = re.findall(
        r"(?i)^ANSWER\s*:\s*([A-Za-z\d ,]+)\s*(?:$|\n|\.)",
        response or "",
        flags=re.MULTILINE,
    )
    if not matches:
        matches = re.findall(
            r"(?i)ANSWER\s*:\s*([A-Za-z\d ,]+)(?:[^\w]|\n|$|\.)",
            response or "",
        )
    if not matches:
        return ""
    matched = matches[-1].strip().rstrip(".").upper()
    allowed = {chr(65 + index) for index in range(choice_count)}
    return matched if matched in allowed else ""


def load_samples() -> dict[str, list[dict[str, Any]]]:
    loaded: dict[str, list[dict[str, Any]]] = {}
    for benchmark, path in BENCHMARKS.items():
        rows = pq.read_table(path).to_pylist()
        samples = []
        for index, row in enumerate(rows):
            item = dict(row)
            item.update(
                benchmark=benchmark,
                sample_index=index,
                sample_id=f"{benchmark}-{index:05d}",
            )
            samples.append(item)
        loaded[benchmark] = samples
    return loaded


def build_messages(sample: dict[str, Any]) -> list[dict[str, str]]:
    benchmark = sample["benchmark"]
    if benchmark == "telemath":
        return [
            {"role": "system", "content": TELEMATH_SYSTEM_PROMPT},
            {"role": "user", "content": sample["question"]},
        ]
    if benchmark == "teletables":
        choices = sample["choices"]
        choices_text = "\n".join(
            f"{chr(65 + index)}) {choice}" for index, choice in enumerate(choices)
        )
        letters = ",".join(chr(65 + index) for index in range(len(choices)))
        prompt = MC_TEMPLATE.format(
            letters=letters,
            question=sample["question"],
            choices=choices_text,
        )
        return [{"role": "user", "content": prompt}]
    return [{"role": "user", "content": sample["question"]}]


def target_for(sample: dict[str, Any]) -> str:
    if sample["benchmark"] == "teletables":
        return chr(65 + int(sample["answer"]))
    return str(sample["answer"])


def score(benchmark: str, completion: str, target: str, sample: dict[str, Any]) -> tuple[str, bool]:
    if benchmark == "telelogs":
        parsed = parse_boxed(completion)
        predicted = first_int(parsed)
        expected = first_int(target)
        return parsed, predicted is not None and predicted == expected
    if benchmark == "telemath":
        parsed = parse_boxed(completion)
        try:
            correct = math.isclose(
                float(parsed), float(target), rel_tol=0.01, abs_tol=0.01
            )
        except (ValueError, TypeError):
            correct = parsed == target
        return parsed, correct
    if benchmark == "teletables":
        parsed = parse_choice(completion, len(sample["choices"]))
        return parsed, parsed == target
    match = WG_PATTERN.search(completion or "")
    parsed = match.group(1) if match else ""
    return parsed, bool(parsed) and parsed.lower() == target.lower()


def request_one(sample: dict[str, Any]) -> dict[str, Any]:
    messages = build_messages(sample)
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.6,
        "top_p": 0.95,
        "top_k": 20,
        "seed": 42,
        "max_tokens": MAX_TOKENS,
        "chat_template_kwargs": {"enable_thinking": True},
    }
    started = time.monotonic()
    last_error = ""
    for attempt in range(1, 4):
        try:
            request = urllib.request.Request(
                ENDPOINT,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=1800) as response:
                raw = json.load(response)
            message = raw["choices"][0]["message"]
            reasoning = message.get("reasoning_content") or ""
            completion = message.get("content") or ""
            target = target_for(sample)
            parsed, correct = score(
                sample["benchmark"], completion, target, sample
            )
            return {
                "sample_id": sample["sample_id"],
                "benchmark": sample["benchmark"],
                "sample_index": sample["sample_index"],
                "target": target,
                "parsed_answer": parsed,
                "correct": correct,
                "error": None,
                "question": sample["question"],
                "request_messages": messages,
                "thinking_enabled": True,
                "reasoning": reasoning,
                "completion": completion,
                "finish_reason": raw["choices"][0].get("finish_reason"),
                "usage": raw.get("usage", {}),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "completed_at": now_iso(),
            }
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    body = ""
                last_error = f"HTTP {exc.code}: {body[:1000]}"
            else:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < 3:
                time.sleep(2**attempt)
    return {
        "sample_id": sample["sample_id"],
        "benchmark": sample["benchmark"],
        "sample_index": sample["sample_index"],
        "target": target_for(sample),
        "parsed_answer": "",
        "correct": False,
        "error": last_error,
        "question": sample["question"],
        "request_messages": messages,
        "thinking_enabled": True,
        "reasoning": "",
        "completion": "",
        "finish_reason": "error",
        "usage": {},
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "completed_at": now_iso(),
    }


def read_existing() -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    if not RESULTS_PATH.exists():
        return results
    with RESULTS_PATH.open(encoding="utf-8") as source:
        for line in source:
            try:
                result = json.loads(line)
                results[result["sample_id"]] = result
            except (json.JSONDecodeError, KeyError):
                continue
    return results


def build_progress(
    samples: dict[str, list[dict[str, Any]]],
    results: dict[str, dict[str, Any]],
    started_at: str,
    started_monotonic: float,
    status: str,
) -> dict[str, Any]:
    try:
        started_datetime = datetime.fromisoformat(started_at)
        if started_datetime.tzinfo is None:
            started_datetime = started_datetime.replace(tzinfo=timezone.utc)
        elapsed = max(
            (datetime.now(timezone.utc) - started_datetime).total_seconds(), 0.001
        )
    except (TypeError, ValueError):
        elapsed = max(time.monotonic() - started_monotonic, 0.001)
    totals = {name: len(rows) for name, rows in samples.items()}
    stats: dict[str, Any] = {}
    for name, total in totals.items():
        rows = [row for row in results.values() if row["benchmark"] == name]
        completed = len(rows)
        errors = sum(bool(row.get("error")) for row in rows)
        scored = completed - errors
        correct = sum(bool(row.get("correct")) for row in rows)
        stats[name] = {
            "total": total,
            "completed": completed,
            "correct": correct,
            "errors": errors,
            "accuracy": correct / scored if scored else None,
            "progress": completed / total if total else 0,
            "reasoning_present": sum(bool(row.get("reasoning")) for row in rows),
            "completion_tokens": sum(
                int(row.get("usage", {}).get("completion_tokens", 0) or 0)
                for row in rows
            ),
        }
    completed = len(results)
    total = sum(totals.values())
    rate = completed / elapsed
    remaining = max(total - completed, 0)
    accuracies = [
        value["accuracy"] for value in stats.values() if value["accuracy"] is not None
    ]
    return {
        "status": status,
        "model": MODEL,
        "suite": "GSMA ot-full: TeleLogs + TeleMath + TeleTables + 3GPP",
        "started_at": started_at,
        "updated_at": now_iso(),
        "workers": WORKERS,
        "thinking_enabled": True,
        "sampling": {
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "seed": 42,
            "max_tokens": MAX_TOKENS,
        },
        "total": total,
        "completed": completed,
        "correct": sum(value["correct"] for value in stats.values()),
        "errors": sum(value["errors"] for value in stats.values()),
        "progress": completed / total if total else 0,
        "macro_accuracy": sum(accuracies) / len(accuracies) if accuracies else None,
        "elapsed_seconds": round(elapsed, 1),
        "samples_per_minute": round(rate * 60, 2),
        "eta_seconds": round(remaining / rate, 1) if rate else None,
        "completion_tokens": sum(
            value["completion_tokens"] for value in stats.values()
        ),
        "benchmarks": stats,
    }


def write_item(result: dict[str, Any]) -> None:
    atomic_json(ITEMS_DIR / f"{result['sample_id']}.json", result)


def recent_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": result["sample_id"],
        "benchmark": result["benchmark"],
        "sample_index": result["sample_index"],
        "target": result["target"],
        "parsed_answer": result["parsed_answer"],
        "correct": result["correct"],
        "error": result.get("error"),
        "question_preview": result["question"][:500],
        "reasoning_preview": result.get("reasoning", "")[:1200],
        "completion": result.get("completion", ""),
        "elapsed_seconds": result["elapsed_seconds"],
        "completed_at": result["completed_at"],
        "item_url": f"data/items/{result['sample_id']}.json",
    }


def write_report(progress: dict[str, Any]) -> None:
    lines = [
        "# Qwen3-8B base — GSMA full four-benchmark evaluation",
        "",
        f"Status: **{progress['status']}**",
        f"Completed: **{progress['completed']}/{progress['total']}**",
        f"Errors: **{progress['errors']}**",
        f"Macro accuracy: **{(progress['macro_accuracy'] or 0) * 100:.2f}%**",
        "",
        "| Benchmark | Correct | Completed | Total | Accuracy |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, stats in progress["benchmarks"].items():
        accuracy = stats["accuracy"]
        accuracy_text = f"{accuracy * 100:.2f}%" if accuracy is not None else "—"
        lines.append(
            f"| {name} | {stats['correct']} | {stats['completed']} | "
            f"{stats['total']} | {accuracy_text} |"
        )
    lines.extend(
        [
            "",
            "Configuration: zero-shot; Qwen3 thinking enabled; no fine-tuning.",
            "TeleMath uses its official system prompt and TeleTables uses its official multiple-choice template.",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    ITEMS_DIR.mkdir(parents=True, exist_ok=True)
    samples = load_samples()
    results = read_existing()

    # Rebuild dashboard item files from an interrupted previous run.
    for result in results.values():
        item_path = ITEMS_DIR / f"{result['sample_id']}.json"
        if not item_path.exists():
            write_item(result)

    previous_progress = {}
    if PROGRESS_PATH.exists():
        try:
            previous_progress = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous_progress = {}
    started_at = previous_progress.get("started_at") or now_iso()
    started_monotonic = time.monotonic()

    # Round-robin scheduling gives early live signal for all four benchmarks.
    pending: list[dict[str, Any]] = []
    max_count = max(len(rows) for rows in samples.values())
    for index in range(max_count):
        for benchmark in ("telelogs", "telemath", "teletables", "three_gpp"):
            rows = samples[benchmark]
            if index >= len(rows):
                continue
            sample = rows[index]
            existing = results.get(sample["sample_id"])
            if existing and not existing.get("error"):
                continue
            pending.append(sample)

    recent: dict[str, deque[dict[str, Any]]] = {
        "all": deque(maxlen=40),
        **{name: deque(maxlen=40) for name in BENCHMARKS},
    }
    for result in sorted(results.values(), key=lambda row: row.get("completed_at", "")):
        summary = recent_summary(result)
        recent["all"].append(summary)
        recent[result["benchmark"]].append(summary)
    progress = build_progress(
        samples, results, started_at, started_monotonic, "running"
    )
    atomic_json(PROGRESS_PATH, progress)
    atomic_json(
        RECENT_PATH,
        {name: list(reversed(rows)) for name, rows in recent.items()},
    )
    write_report(progress)

    print(
        f"starting full4: pending={len(pending)} existing={len(results)} "
        f"workers={WORKERS} endpoint={ENDPOINT}",
        flush=True,
    )
    with RESULTS_PATH.open("a", encoding="utf-8", buffering=1) as output:
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
            future_to_sample = {
                executor.submit(request_one, sample): sample for sample in pending
            }
            for future in concurrent.futures.as_completed(future_to_sample):
                result = future.result()
                results[result["sample_id"]] = result
                output.write(json.dumps(result, ensure_ascii=False) + "\n")
                output.flush()
                write_item(result)
                summary = recent_summary(result)
                recent["all"].append(summary)
                recent[result["benchmark"]].append(summary)
                progress = build_progress(
                    samples, results, started_at, started_monotonic, "running"
                )
                atomic_json(PROGRESS_PATH, progress)
                atomic_json(
                    RECENT_PATH,
                    {name: list(reversed(rows)) for name, rows in recent.items()},
                )
                write_report(progress)
                with PRINT_LOCK:
                    stats = progress["benchmarks"][result["benchmark"]]
                    print(
                        f"[{progress['completed']}/{progress['total']}] "
                        f"{result['sample_id']} correct={result['correct']} "
                        f"error={bool(result.get('error'))} "
                        f"bench_acc={(stats['accuracy'] or 0) * 100:.2f}% "
                        f"eta={progress['eta_seconds']}",
                        flush=True,
                    )

    status = "completed" if not any(row.get("error") for row in results.values()) else "completed_with_errors"
    progress = build_progress(
        samples, results, started_at, started_monotonic, status
    )
    atomic_json(PROGRESS_PATH, progress)
    atomic_json(
        RECENT_PATH,
        {name: list(reversed(rows)) for name, rows in recent.items()},
    )
    write_report(progress)
    print(json.dumps(progress, ensure_ascii=False, indent=2), flush=True)
    if status != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

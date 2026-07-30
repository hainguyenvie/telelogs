#!/usr/bin/env python3
"""Run a resumable raw-question TeleLogs baseline against an OpenAI API."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path


BOXED_PATTERN = re.compile(r"\\boxed\{((?:[^{}]|\{[^{}]*\})*)\}")
LABEL_PATTERN = re.compile(r"\bC([1-8])\b", re.IGNORECASE)
PRINT_LOCK = threading.Lock()
CONCISE_SYSTEM_PROMPT = r"""Classify the TeleLogs case directly from the raw tables.
Return exactly one of \boxed{C1}, \boxed{C2}, \boxed{C3}, \boxed{C4},
\boxed{C5}, \boxed{C6}, \boxed{C7}, or \boxed{C8}. Output only the boxed label.
Do not provide reasoning, analysis, explanation, or any other text."""


def parse_boxed_label(response: str) -> str:
    """Match the strict boxed-answer scoring used by the pulled baseline."""
    matches = BOXED_PATTERN.findall(response or "")
    if not matches:
        return ""
    labels = LABEL_PATTERN.findall(matches[-1])
    if labels:
        return f"C{labels[-1]}"
    digits = re.findall(r"\b([1-8])\b", matches[-1])
    return f"C{digits[-1]}" if digits else ""


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_existing(path: Path) -> dict[int, dict]:
    if not path.exists():
        return {}
    rows: dict[int, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if not row.get("error"):
            row["parsed_answer"] = parse_boxed_label(row.get("completion", ""))
            row["correct"] = row["parsed_answer"] == row["target"]
        rows[int(row["sample_index"])] = row
    return rows


def summarize(
    results: dict[int, dict], total: int, model: str, concise_prompt: bool
) -> dict:
    completed = [row for row in results.values() if not row.get("error")]
    correct = sum(bool(row["correct"]) for row in completed)
    by_label = {}
    for label in (f"C{i}" for i in range(1, 9)):
        rows = [row for row in completed if row["target"] == label]
        label_correct = sum(bool(row["correct"]) for row in rows)
        by_label[label] = {
            "correct": label_correct,
            "total": len(rows),
            "accuracy": label_correct / len(rows) if rows else None,
        }
    return {
        "model": model,
        "evaluation": (
            "raw_question_zero_shot_concise_prompt"
            if concise_prompt
            else "raw_question_zero_shot_baseline"
        ),
        "scoring": "strict_last_boxed_C1_to_C8",
        "completed": len(completed),
        "total": total,
        "correct": correct,
        "accuracy": correct / len(completed) if completed else None,
        "errors": sum(bool(row.get("error")) for row in results.values()),
        "by_label": by_label,
        "prediction_distribution": dict(
            Counter(row.get("parsed_answer") or "EMPTY" for row in completed)
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--endpoint",
        default=os.environ.get(
            "NETMIND_CHAT_URL",
            "https://stream-netmind.viettel.vn/gateway/v1/chat/completions",
        ),
    )
    parser.add_argument(
        "--model", default=os.environ.get("NETMIND_MODEL", "netLLMv1.0")
    )
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--max-tokens", type=int, default=38000)
    parser.add_argument(
        "--enable-thinking",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--concise-prompt", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    api_key = os.environ.get("NETMIND_API_KEY")
    if not api_key:
        raise SystemExit("NETMIND_API_KEY is required")

    sources = json.loads(args.input.read_text(encoding="utf-8"))
    if args.limit > 0:
        sources = sources[: args.limit]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results_path = args.output_dir / "results.jsonl"
    summary_path = args.output_dir / "summary.json"
    results = load_existing(results_path)

    def request_one(sample_index: int, source: dict) -> dict:
        messages = []
        if args.concise_prompt:
            messages.append({"role": "system", "content": CONCISE_SYSTEM_PROMPT})
        messages.append({"role": "user", "content": source["question"]})
        payload = {
            "model": args.model,
            "messages": messages,
            "temperature": 0.6,
            "top_p": 0.95,
            "top_k": 20,
            "seed": 42,
            "max_tokens": args.max_tokens,
        }
        if args.enable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": True}
        started = time.monotonic()
        last_error = ""
        for attempt in range(1, 4):
            try:
                request = urllib.request.Request(
                    args.endpoint,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=1800) as response:
                    raw = json.load(response)
                message = raw["choices"][0]["message"]
                completion = message.get("content") or ""
                reasoning = message.get("reasoning_content") or ""
                parsed = parse_boxed_label(completion)
                return {
                    "sample_index": sample_index,
                    "target": source["answer"],
                    "parsed_answer": parsed,
                    "correct": parsed == source["answer"],
                    "error": None,
                    "question": source["question"],
                    "request_messages": messages,
                    "thinking_enabled": args.enable_thinking,
                    "reasoning": reasoning,
                    "completion": completion,
                    "finish_reason": raw["choices"][0].get("finish_reason"),
                    "usage": raw.get("usage", {}),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
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
            "sample_index": sample_index,
            "target": source["answer"],
            "parsed_answer": "",
            "correct": False,
            "error": last_error,
            "question": source["question"],
            "request_messages": messages,
            "thinking_enabled": args.enable_thinking,
            "reasoning": "",
            "completion": "",
            "finish_reason": None,
            "usage": {},
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }

    pending = [
        (index, source)
        for index, source in enumerate(sources)
        if index not in results or results[index].get("error")
    ]
    print(
        f"baseline start model={args.model} total={len(sources)} "
        f"pending={len(pending)} workers={args.workers}",
        flush=True,
    )
    with results_path.open("a", encoding="utf-8", buffering=1) as output:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.workers
        ) as executor:
            futures = {
                executor.submit(request_one, index, source): index
                for index, source in pending
            }
            for completed_count, future in enumerate(
                concurrent.futures.as_completed(futures), start=1
            ):
                result = future.result()
                results[result["sample_index"]] = result
                output.write(json.dumps(result, ensure_ascii=False) + "\n")
                output.flush()
                summary = summarize(
                    results, len(sources), args.model, args.concise_prompt
                )
                atomic_json(summary_path, summary)
                with PRINT_LOCK:
                    print(
                        f"attempt={completed_count}/{len(pending)} "
                        f"completed={summary['completed']}/{len(sources)} "
                        f"correct={summary['correct']} "
                        f"errors={summary['errors']}",
                        flush=True,
                    )

    summary = summarize(results, len(sources), args.model, args.concise_prompt)
    atomic_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if summary["errors"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run 10 zero-shot TeleLogs samples against the persistent Qwen3-8B API."""

from __future__ import annotations

import concurrent.futures
import html
import json
import os
import re
import time
import urllib.request
from pathlib import Path

import duckdb


DATASET = os.environ.get(
    "TELELOGS_PARQUET",
    "/home/tensara/projects/telelogs/cache/hf/hub/"
    "datasets--GSMA--ot-full/snapshots/"
    "6319806f04783eafe04d9facf755d379c66b7664/"
    "telelogs/test-00000-of-00001.parquet",
)
ENDPOINT = os.environ.get(
    "VLLM_CHAT_URL",
    "http://telelogs-bench4-vllm:8000/v1/chat/completions",
)
OUTPUT_DIR = Path(
    os.environ.get(
        "OUTPUT_DIR",
        "/home/tensara/projects/telelogs/runs/bench4/results/pure_telelogs10",
    )
)
MODEL = "Qwen/Qwen3-8B"
BOXED_PATTERN = re.compile(r"\\boxed\{((?:[^{}]|\{[^{}]*\})*)\}")
DIGIT_PATTERN = re.compile(r"\d+")


def parse_boxed_answer(response: str) -> str:
    matches = BOXED_PATTERN.findall(response or "")
    if not matches:
        return ""
    answer = re.sub(r"\n\s*", "", matches[-1].strip())
    return answer.lstrip(":").rstrip("./")


def first_int(text: str) -> int | None:
    match = DIGIT_PATTERN.search(text or "")
    return int(match.group()) if match else None


def run_one(sample_index: int, question: str, target: str) -> dict:
    # Deliberately one user message: no system prompt and no few-shot examples.
    messages = [{"role": "user", "content": question}]
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.6,
        "top_p": 0.95,
        "top_k": 20,
        "seed": 42,
        "max_tokens": 38000,
        "chat_template_kwargs": {"enable_thinking": True},
    }
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=900) as response:
        raw = json.load(response)
    elapsed = time.monotonic() - started
    message = raw["choices"][0]["message"]
    reasoning = message.get("reasoning_content") or ""
    completion = message.get("content") or ""
    parsed = parse_boxed_answer(completion)
    correct = first_int(parsed) is not None and first_int(parsed) == first_int(target)
    return {
        "sample_index": sample_index,
        "target": target,
        "parsed_answer": parsed,
        "correct": correct,
        "question": question,
        "request_messages": messages,
        "thinking_enabled": True,
        "reasoning": reasoning,
        "completion": completion,
        "finish_reason": raw["choices"][0].get("finish_reason"),
        "usage": raw.get("usage", {}),
        "elapsed_seconds": round(elapsed, 3),
    }


def main() -> None:
    rows = duckdb.sql(
        f"SELECT question, answer FROM read_parquet('{DATASET}') LIMIT 10"
    ).fetchall()
    indexed = [(i, question, target) for i, (question, target) in enumerate(rows)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(run_one, *row) for row in indexed]
        results = [future.result() for future in futures]
    results.sort(key=lambda item: item["sample_index"])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTPUT_DIR / "results.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as output:
        for result in results:
            output.write(json.dumps(result, ensure_ascii=False) + "\n")

    correct = sum(result["correct"] for result in results)
    reasoning_present = sum(bool(result["reasoning"].strip()) for result in results)
    total_reasoning_tokens = sum(
        result.get("usage", {}).get("completion_tokens", 0) for result in results
    )
    lines = [
        "# Pure Qwen3-8B — first 10 TeleLogs samples",
        "",
        "- Messages per request: one `user` message",
        "- System prompt: none",
        "- Few-shot examples: none",
        "- Fine-tuning/adapter: none",
        "- Thinking: explicitly enabled",
        "- Sampling: temperature 0.6, top_p 0.95, top_k 20, seed 42",
        f"- Accuracy: **{correct}/10 ({correct * 10:.1f}%)**",
        f"- Reasoning returned: **{reasoning_present}/10**",
        f"- Completion tokens reported: {total_reasoning_tokens}",
        "",
    ]
    for result in results:
        status = "CORRECT" if result["correct"] else "WRONG"
        lines.extend(
            [
                f"## Sample {result['sample_index']} — {status}",
                "",
                f"Target: `{result['target']}`  ",
                f"Parsed: `{result['parsed_answer'] or '(empty)'}`  ",
                f"Elapsed: `{result['elapsed_seconds']}s`  ",
                f"Tokens: `{json.dumps(result['usage'])}`",
                "",
                "<details><summary>Reasoning</summary><pre>",
                html.escape(result["reasoning"]),
                "</pre></details>",
                "",
                "<details><summary>Final completion</summary><pre>",
                html.escape(result["completion"]),
                "</pre></details>",
                "",
            ]
        )
    report_path = OUTPUT_DIR / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"accuracy={correct}/10")
    print(f"reasoning_present={reasoning_present}/10")
    print(f"completion_tokens={total_reasoning_tokens}")
    for result in results:
        print(
            f"sample={result['sample_index']} target={result['target']} "
            f"pred={result['parsed_answer'] or '-'} correct={result['correct']} "
            f"reasoning_chars={len(result['reasoning'])} "
            f"elapsed={result['elapsed_seconds']}s"
        )
    print(f"jsonl={jsonl_path}")
    print(f"report={report_path}")


if __name__ == "__main__":
    main()

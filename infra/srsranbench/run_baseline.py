#!/usr/bin/env python3
"""Closed-book srsranbench baseline, scored exactly as the GSMA harness scores it.

Faithfulness to `gsma-labs/evals` + Inspect AI, verified against the sources:

  prompt   Inspect's SINGLE_ANSWER_TEMPLATE (cot=False), choices rendered by
           `answer_options` as "A) <choice>" — byte-identical to full4_eval.py's
           MC_TEMPLATE path, which was already checked against Inspect.
  parse    `parse_answers` ported verbatim: strict MULTILINE '^ANSWER: X' first,
           non-anchored fallback second, LAST match wins, then membership in the
           allowed letters. A response with no parsable letter scores wrong.
  score    `choice()` — exact letter match against chr(65 + answer).
  input    only the message `content` is parsed, never `reasoning_content`;
           that mirrors Inspect reading `state.output.completion`.

Two arms beyond the plain run, both about the position prior documented in
README.md (gold is at index 0 in 75.83% of rows):

  --permute   deterministically rotates each row's choices so the gold position
              is uniform across A/B/C/D. Same questions, same content, prior
              destroyed. The gap between this and the plain run is how much of
              the plain score is the artefact.
  --always A  no model call at all; emits the constant answer. The plain-run
              reference floor.

Resumable: finished sample_ids in results.jsonl are skipped on restart.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODEL = os.environ.get("SRSRAN_MODEL", "Qwen/Qwen3-8B")
ENDPOINT = os.environ.get(
    "VLLM_CHAT_URL", "http://telelogs-bench4-vllm:8000/v1/chat/completions"
)

# Inspect AI: inspect_ai/solver/_multiple_choice.py SINGLE_ANSWER_TEMPLATE
SINGLE_ANSWER_TEMPLATE = (
    "Answer the following multiple choice question. The entire content of your "
    "response should be of the following format: 'ANSWER: $LETTER' (without "
    "quotes) where LETTER is one of {letters}.\n\n{question}\n\n{choices}"
)
SINGLE_ANSWER_TEMPLATE_COT = (
    "Answer the following multiple choice question. The last line of your "
    "response should be of the following format: 'ANSWER: $LETTER' (without "
    "quotes) where LETTER is one of {letters}. Think step by step before "
    "answering.\n\n{question}\n\n{choices}"
)

STRICT_ANSWER = re.compile(r"(?i)^ANSWER\s*:\s*([A-Za-z\d ,]+)\s*(?:$|\n|\.)", re.MULTILINE)
LOOSE_ANSWER = re.compile(r"(?i)ANSWER\s*:\s*([A-Za-z\d ,]+)(?:[^\w]|\n|$|\.)")
PRINT_LOCK = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_answer(completion: str, choice_count: int) -> str:
    """Verbatim port of Inspect's parse_answers() for multiple_correct=False."""
    matches = STRICT_ANSWER.findall(completion or "")
    if not matches:
        matches = LOOSE_ANSWER.findall(completion or "")
    if not matches:
        return ""
    matched = matches[-1].strip().rstrip(".").upper()
    allowed = {chr(65 + index) for index in range(choice_count)}
    return matched if matched in allowed else ""


def permutation_for(sample_id: str, count: int) -> list[int]:
    """Deterministic rotation: new_position i shows original choice order[i].

    A rotation keeps the relative order of the distractors and moves gold to a
    position driven only by the sample id, so the gold-position histogram
    flattens without introducing a new orderings artefact.
    """
    digest = hashlib.sha256(sample_id.encode("utf-8")).digest()
    shift = digest[0] % count
    return [(index + shift) % count for index in range(count)]


def build_sample(row: dict[str, Any], permute: bool) -> dict[str, Any]:
    choices = list(row["choices"])
    gold = int(row["answer"])
    if permute:
        order = permutation_for(row["sample_id"], len(choices))
        choices = [row["choices"][j] for j in order]
        gold = order.index(int(row["answer"]))
    return {
        "sample_id": row["sample_id"],
        "sample_index": row["sample_index"],
        "question": row["question"],
        "choices": choices,
        "gold_index": gold,
        "original_gold_index": int(row["answer"]),
        "target": chr(65 + gold),
    }


def build_prompt(sample: dict[str, Any], cot: bool) -> str:
    choices_text = "\n".join(
        f"{chr(65 + index)}) {choice}" for index, choice in enumerate(sample["choices"])
    )
    letters = ",".join(chr(65 + index) for index in range(len(sample["choices"])))
    template = SINGLE_ANSWER_TEMPLATE_COT if cot else SINGLE_ANSWER_TEMPLATE
    return template.format(
        letters=letters, question=sample["question"], choices=choices_text
    )


def request_one(sample: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    prompt = build_prompt(sample, args.cot)
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "seed": 42,
        "chat_template_kwargs": {"enable_thinking": args.thinking},
    }
    if args.temperature > 0:
        payload.update(top_p=0.95, top_k=20)
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
            completion = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""
            parsed = parse_answer(completion, len(sample["choices"]))
            return {
                "sample_id": sample["sample_id"],
                "sample_index": sample["sample_index"],
                "target": sample["target"],
                "gold_index": sample["gold_index"],
                "original_gold_index": sample["original_gold_index"],
                "parsed_answer": parsed,
                "correct": bool(parsed) and parsed == sample["target"],
                "parse_failed": not parsed,
                "finish_reason": raw["choices"][0].get("finish_reason"),
                "completion": completion,
                "reasoning_chars": len(reasoning),
                "usage": raw.get("usage", {}),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "completed_at": now_iso(),
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 - retried, then recorded
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    body = ""
                last_error = f"HTTP {exc.code}: {body[:500]}"
            else:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < 3:
                time.sleep(2 * attempt)
    return {
        "sample_id": sample["sample_id"],
        "sample_index": sample["sample_index"],
        "target": sample["target"],
        "gold_index": sample["gold_index"],
        "original_gold_index": sample["original_gold_index"],
        "parsed_answer": "",
        "correct": False,
        "parse_failed": True,
        "completion": "",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "completed_at": now_iso(),
        "error": last_error,
    }


def constant_one(sample: dict[str, Any], letter: str) -> dict[str, Any]:
    return {
        "sample_id": sample["sample_id"],
        "sample_index": sample["sample_index"],
        "target": sample["target"],
        "gold_index": sample["gold_index"],
        "original_gold_index": sample["original_gold_index"],
        "parsed_answer": letter,
        "correct": letter == sample["target"],
        "parse_failed": False,
        "completion": f"ANSWER: {letter}",
        "elapsed_seconds": 0.0,
        "completed_at": now_iso(),
        "error": None,
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    correct = sum(1 for r in records if r["correct"])
    by_gold: dict[str, list[int]] = {}
    for record in records:
        key = chr(65 + record["gold_index"])
        bucket = by_gold.setdefault(key, [0, 0])
        bucket[1] += 1
        bucket[0] += 1 if record["correct"] else 0
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "stderr": round(((correct / total) * (1 - correct / total) / total) ** 0.5, 4)
        if total
        else 0.0,
        "parse_failures": sum(1 for r in records if r["parse_failed"]),
        "request_errors": sum(1 for r in records if r.get("error")),
        "truncated": sum(1 for r in records if r.get("finish_reason") == "length"),
        "predicted_letter_distribution": dict(
            sorted(Counter(r["parsed_answer"] or "<none>" for r in records).items())
        ),
        "accuracy_by_gold_letter": {
            key: {"correct": v[0], "total": v[1], "accuracy": round(v[0] / v[1], 4)}
            for key, v in sorted(by_gold.items())
        },
        "mean_completion_tokens": round(
            sum(r.get("usage", {}).get("completion_tokens", 0) for r in records)
            / max(1, sum(1 for r in records if r.get("usage"))),
            1,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True, help="test.jsonl")
    parser.add_argument("--out", type=Path, required=True, help="run directory")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--thinking", action="store_true", help="Qwen native thinking")
    parser.add_argument("--cot", action="store_true", help="use the cot=True template")
    parser.add_argument("--permute", action="store_true", help="position-prior control")
    parser.add_argument("--always", default="", help="no model: always answer this letter")
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.data.open(encoding="utf-8")]
    if args.limit:
        rows = rows[: args.limit]
    samples = [build_sample(row, args.permute) for row in rows]

    args.out.mkdir(parents=True, exist_ok=True)
    results_path = args.out / "results.jsonl"
    done: dict[str, dict[str, Any]] = {}
    if results_path.exists():
        for line in results_path.open(encoding="utf-8"):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not record.get("error"):
                done[record["sample_id"]] = record
    pending = [s for s in samples if s["sample_id"] not in done]

    config = {
        "model": MODEL if not args.always else f"constant:{args.always}",
        "endpoint": ENDPOINT,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "thinking": args.thinking,
        "cot_template": args.cot,
        "permuted_choices": args.permute,
        "data": str(args.data),
    }
    print(json.dumps({"stage": "start", "total": len(samples), "pending": len(pending),
                      "resumed": len(done), "config": config}), flush=True)
    print(json.dumps({"stage": "prompt_example", "prompt": build_prompt(samples[0], args.cot)}),
          flush=True)

    completed = len(done)
    with results_path.open("a", encoding="utf-8") as handle:
        if args.always:
            for sample in pending:
                record = constant_one(sample, args.always.upper())
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                done[sample["sample_id"]] = record
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(request_one, s, args): s for s in pending}
                for future in concurrent.futures.as_completed(futures):
                    record = future.result()
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                    done[record["sample_id"]] = record
                    completed += 1
                    if completed % 50 == 0 or completed == len(samples):
                        running = summarize(list(done.values()))
                        with PRINT_LOCK:
                            print(json.dumps({"stage": "progress", "completed": completed,
                                              "accuracy": running["accuracy"]}), flush=True)

    records = [done[s["sample_id"]] for s in samples if s["sample_id"] in done]
    summary = {"config": config, **summarize(records)}
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"stage": "done", **summary}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Replay data/telelogs.jsonl at an endpoint exactly as the router would.

Sends each record's body verbatim -- same messages, same model field, nothing
added -- and then applies the OFFICIAL parser to whatever comes back: the last
\\boxed{...}, first integer. This answers the only question that matters for a
specialist sitting behind a router: does the thing the router receives carry a
gradable answer, and is that answer right.

One trap this script exists to avoid. data/telelogs.jsonl and
data/official_test_864/test.json hold the SAME 864 questions but not in the same
order -- 3 rows sit in different positions. Joining by line number therefore
mislabels three cases and quietly moves the score. Gold is joined by a hash of
the question text instead.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import hashlib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

BOXED = re.compile(r"\\boxed\{((?:[^{}]|\{[^{}]*\})*)\}")
DIGITS = re.compile(r"\d+")


def parse_boxed(text: str) -> str:
    matches = BOXED.findall(text or "")
    if not matches:
        return ""
    return re.sub(r"\n\s*", "", matches[-1].strip()).lstrip(":").rstrip("./")


def first_int(text: str) -> int | None:
    m = DIGITS.search(text or "")
    return int(m.group()) if m else None


def qhash(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", required=True, type=Path, help="data/telelogs.jsonl")
    ap.add_argument("--gold", type=Path, help="data/official_test_864/test.json (optional)")
    ap.add_argument("--url", required=True, help="e.g. http://127.0.0.1:8000/v1/chat/completions")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    records = [json.loads(l) for l in args.requests.open(encoding="utf-8") if l.strip()]
    if args.limit:
        records = records[: args.limit]

    gold: dict[str, str] = {}
    if args.gold:
        for row in json.loads(args.gold.read_text(encoding="utf-8")):
            gold[qhash(row["question"])] = str(row["answer"])

    def send(index: int, body: dict) -> dict:
        question = body["messages"][-1]["content"]
        started = time.monotonic()
        try:
            request = urllib.request.Request(
                args.url, data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                payload = json.load(response)
            content = payload["choices"][0]["message"]["content"] or ""
            error = None
        except Exception as exc:
            payload, content, error = {}, "", f"{type(exc).__name__}: {exc}"

        parsed = parse_boxed(content)
        predicted = first_int(parsed)
        target = gold.get(qhash(question))
        correct = (predicted is not None and target is not None
                   and predicted == first_int(target))
        return {
            "index": index,
            "model_echoed": payload.get("model"),
            "parsed": parsed,
            "predicted": predicted,
            "target": target,
            "correct": correct,
            "has_boxed": bool(parsed),
            "content_chars": len(content),
            "usage": payload.get("usage"),
            "error": error,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }

    results: list[dict] = []
    with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        pending = [pool.submit(send, i, r) for i, r in enumerate(records)]
        for n, future in enumerate(futures.as_completed(pending), 1):
            results.append(future.result())
            if n % 25 == 0:
                ok = sum(r["correct"] for r in results)
                boxed = sum(r["has_boxed"] for r in results)
                print(json.dumps({"done": n, "of": len(records),
                                  "boxed": boxed, "correct": ok}), flush=True)

    results.sort(key=lambda r: r["index"])
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as sink:
            for row in results:
                sink.write(json.dumps(row, ensure_ascii=False) + "\n")

    total = len(results)
    boxed = sum(r["has_boxed"] for r in results)
    errors = sum(bool(r["error"]) for r in results)
    echoed = {r["model_echoed"] for r in results}
    print()
    print(f"requests            : {total}")
    print(f"HTTP/transport fails: {errors}")
    print(f"carried a \\boxed{{}} : {boxed}/{total} = {boxed / total * 100:.2f}%   <- format contract")
    print(f"model field echoed  : {sorted(x for x in echoed if x)}")
    if gold:
        matched = sum(1 for r in results if r["target"] is not None)
        correct = sum(r["correct"] for r in results)
        print(f"joined to gold      : {matched}/{total} (by question hash, not line order)")
        print(f"correct             : {correct}/{total} = {correct / total * 100:.2f}%")
    if errors:
        for row in results:
            if row["error"]:
                print(f"  first error, row {row['index']}: {row['error']}")
                break
    if boxed < total:
        missing = [r["index"] for r in results if not r["has_boxed"]][:10]
        print(f"  rows with no boxed answer (first 10): {missing}")


if __name__ == "__main__":
    main()

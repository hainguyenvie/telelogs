#!/usr/bin/env python3
"""Profile the GSMA ot-full srsranbench split before any modelling decision.

Reads the parquet exactly as the GSMA harness does (pyarrow, row order preserved)
and reports the things that decide how a specialist is built: answer-index
balance, choice-count distribution, question and choice lengths, near-duplicate
questions, and whether the question text alone leaks the answer position.

Also writes a readable JSONL mirror next to the parquet so the rest of the
tooling never has to open a parquet file again.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PARQUET = ROOT / "data/srsranbench/test-00000-of-00001.parquet"
DEFAULT_JSONL = ROOT / "data/srsranbench/test.jsonl"

# choices arrive as "1. <text>" / "2. <text>" ...; strip that prefix for analysis
CHOICE_PREFIX = re.compile(r"^\s*(\d+)\s*[.)]\s*")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def percentiles(values: list[int], points=(0, 25, 50, 75, 90, 99, 100)) -> dict[str, int]:
    if not values:
        return {}
    ordered = sorted(values)
    out = {}
    for p in points:
        index = min(len(ordered) - 1, max(0, round((p / 100) * (len(ordered) - 1))))
        out[f"p{p}"] = ordered[index]
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parquet", type=Path, default=DEFAULT_PARQUET)
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--samples", type=int, default=0, help="print N full examples")
    args = parser.parse_args()

    digest = hashlib.sha256(args.parquet.read_bytes()).hexdigest()
    rows = pq.read_table(args.parquet).to_pylist()

    args.jsonl.parent.mkdir(parents=True, exist_ok=True)
    with args.jsonl.open("w", encoding="utf-8") as handle:
        for index, row in enumerate(rows):
            record = {
                "sample_id": f"srsranbench-{index:05d}",
                "sample_index": index,
                "question": row["question"],
                "choices": list(row["choices"]),
                "answer": int(row["answer"]),
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    answers = Counter(int(r["answer"]) for r in rows)
    choice_counts = Counter(len(r["choices"]) for r in rows)
    q_lengths = [len(r["question"]) for r in rows]
    c_lengths = [len(c) for r in rows for c in r["choices"]]

    # does the gold choice differ systematically in length? (a length-prior leak)
    gold_len, distractor_len = [], []
    for row in rows:
        for position, choice in enumerate(row["choices"]):
            body = CHOICE_PREFIX.sub("", choice)
            (gold_len if position == int(row["answer"]) else distractor_len).append(len(body))

    # near-duplicate questions and repeated stems
    seen: dict[str, int] = {}
    duplicates = 0
    for row in rows:
        key = normalize(row["question"])
        if key in seen:
            duplicates += 1
        else:
            seen[key] = 1

    # what the questions are actually about: srsRAN identifiers carry the topic
    identifiers = Counter()
    for row in rows:
        identifiers.update(set(IDENT.findall(row["question"])))

    # prefix-order sanity: is choice i always labelled "i+1."?
    misordered = sum(
        1
        for row in rows
        for position, choice in enumerate(row["choices"])
        if (CHOICE_PREFIX.match(choice) or [None]) and CHOICE_PREFIX.match(choice)
        and int(CHOICE_PREFIX.match(choice).group(1)) != position + 1
    )
    unlabelled = sum(
        1 for row in rows for choice in row["choices"] if not CHOICE_PREFIX.match(choice)
    )

    report = {
        "parquet": str(args.parquet),
        "sha256": digest,
        "rows": len(rows),
        "jsonl": str(args.jsonl),
        "answer_index_distribution": {str(k): answers[k] for k in sorted(answers)},
        "majority_class_baseline": round(max(answers.values()) / len(rows), 4),
        "random_baseline": round(
            sum(count / n for n, count in choice_counts.items()) / len(rows), 4
        ),
        "choice_count_distribution": {str(k): choice_counts[k] for k in sorted(choice_counts)},
        "question_chars": percentiles(q_lengths),
        "choice_chars": percentiles(c_lengths),
        "gold_choice_mean_chars": round(sum(gold_len) / len(gold_len), 1),
        "distractor_choice_mean_chars": round(sum(distractor_len) / len(distractor_len), 1),
        "duplicate_questions": duplicates,
        "unique_questions": len(seen),
        "choices_with_wrong_number_prefix": misordered,
        "choices_without_number_prefix": unlabelled,
        "top_identifiers": identifiers.most_common(40),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    for row in rows[: args.samples]:
        print("\n" + "=" * 70)
        print(row["question"])
        for position, choice in enumerate(row["choices"]):
            mark = " <== gold" if position == int(row["answer"]) else ""
            print(f"  [{position}] {choice}{mark}")


if __name__ == "__main__":
    main()

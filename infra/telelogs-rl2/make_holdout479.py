#!/usr/bin/env python3
"""Build the holdout-479 selection set as an ot-full-shaped parquet.

Checkpoint selection has to happen on data the reported number is not computed
on, or the reported number is the peak of a noise distribution rather than an
estimate of anything. This writes the 479 rows of raw_train_2400 that the repo's
standing split function assigns to `holdout` — the same function
run_tool_experiment.py uses, copied here so this file has no import dependency
on the eval venv.

The parquet carries exactly the two columns champion_gsma_full.py reads,
`question` and `answer`, so the selection runs through the identical pipeline
and the identical official boxed-int scorer as the ot-full numbers. The scorer
compares first integers, so gold written as "C4" and gold written as "4" score
the same; no relabelling is needed.

Holdout is NOT class-balanced (54/61/70/57/72/44/69/52 for C1..C8) where ot-full
is 108 per class. That is a known and accepted difference: it costs a little
correspondence between the two composites and buys 479 rows of power instead of
the 352 a balanced slice would leave.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--split", default="holdout")
    args = parser.parse_args()

    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    rows = [r for r in raw if split_for(question_group(r["question"])) == args.split]
    assert rows, f"no rows in split {args.split}"

    # Order is fixed by position in the source file, so sample_index is stable
    # across every run of every checkpoint and pairing by it is meaningful.
    table = pa.table({
        "question": pa.array([r["question"] for r in rows], pa.string()),
        "answer": pa.array([r["answer"] for r in rows], pa.string()),
    })
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, args.out)

    counts = Counter(r["answer"] for r in rows)
    print(f"{args.split}: {len(rows)} rows -> {args.out}")
    print("  " + "  ".join(f"{lab} {counts[lab]}" for lab in sorted(counts)))


if __name__ == "__main__":
    main()

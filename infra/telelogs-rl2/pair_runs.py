#!/usr/bin/env python3
"""Exact paired McNemar between any two 864-row TeleLogs runs, by sample_index.

Takes two results.jsonl paths, so it works across formats: champion_gsma_full.py
writes `pipeline_answer`/`parsed_answer`, full4_eval.py writes `parsed_answer`
only, and both write `sample_index`, `target` and `correct` -- which is all the
pairing needs.

Pairing is by `sample_index`, never by line order: both runners write rows as
worker threads finish, so two files of the same 864 cases are not in the same
order and zipping them silently compares different questions.
"""
from __future__ import annotations

import json
import sys
from math import comb
from pathlib import Path


def load(path: Path) -> dict[int, dict]:
    rows = {}
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        rows[row["sample_index"]] = row       # last write wins, matching a resume
    return rows


def exact_mcnemar(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def answer_of(row: dict) -> str:
    """The predicted class as a bare digit, comparable across both formats.

    champion_gsma_full.py records `pipeline_answer` as "C4" while full4_eval.py
    records `parsed_answer` as what was inside the box, "4". Comparing the raw
    strings makes every row look like a disagreement, which is a bug in the
    comparison and not a finding about the runs.
    """
    raw = str(row.get("pipeline_answer") or row.get("parsed_answer") or "")
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits[:1]


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit("usage: pair_runs.py NAME_A results_a.jsonl NAME_B results_b.jsonl")
    name_a, path_a, name_b, path_b = sys.argv[1], Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
    a, b = load(path_a), load(path_b)
    keys = sorted(set(a) & set(b))
    print(f"paired rows: {len(keys)}  ({name_a} {len(a)}, {name_b} {len(b)})")

    both = only_a = only_b = neither = 0
    changed = 0
    for k in keys:
        x, y = bool(a[k]["correct"]), bool(b[k]["correct"])
        both += x and y
        only_a += x and not y
        only_b += y and not x
        neither += (not x) and (not y)
        changed += answer_of(a[k]) != answer_of(b[k])

    n = len(keys)
    print(f"{name_a} {both + only_a}/{n} = {(both + only_a) / n * 100:.2f}%"
          f"   vs   {name_b} {both + only_b}/{n} = {(both + only_b) / n * 100:.2f}%")
    print(f"  both {both}  {name_a}-only {only_a}  {name_b}-only {only_b}  neither {neither}")
    print(f"  McNemar {only_a}:{only_b}, exact p = {exact_mcnemar(only_a, only_b):.4g}")
    print(f"  rows where the ANSWER differs at all: {changed}/{n}")

    per: dict[str, list[int]] = {}
    for k in keys:
        lab = str(a[k]["target"])
        per.setdefault(lab, [0, 0])
        per[lab][0] += bool(a[k]["correct"])
        per[lab][1] += bool(b[k]["correct"])
    deltas = "  ".join(f"{lab} {y - x:+d}" for lab, (x, y) in sorted(per.items()) if y != x)
    print(f"  per-class delta ({name_b} minus {name_a}): {deltas or 'none'}")

    for name, rows in ((name_a, a), (name_b, b)):
        errs = [k for k in keys if rows[k].get("error")]
        unboxed = [k for k in keys if not rows[k].get("parsed_answer")]
        print(f"  {name}: errors {len(errs)} {errs[:8]}   unboxed {len(unboxed)} {unboxed[:8]}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Paired McNemar between any two official-864 runs of the same pipeline.

Pairing is by `sample_index`, never by line order: the runner writes rows as
worker threads finish, so two files of the same 864 cases are not in the same
order. Exact binomial on the discordant pairs — the counts are small enough that
the chi-square approximation is not worth the assumption.
"""
from __future__ import annotations

import json
import sys
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "artifacts" / "telelogs-dspy-tools" / "results"

RUNS = {
    "base":   ROOT / "champion_base_magrule" / "results.jsonl",
    "v1":     ROOT / "champion_gsma_full_magrule" / "results.jsonl",
    "v2c300": ROOT / "otfull_grpo_v2_ckpt300" / "results.jsonl",
    "v2c700": ROOT / "otfull_grpo_v2_ckpt700" / "results.jsonl",
    "v2c1048": ROOT / "otfull_grpo_v2_ckpt1048" / "results.jsonl",
}


def load(path: Path) -> dict[int, dict]:
    return {json.loads(l)["sample_index"]: json.loads(l) for l in path.open(encoding="utf-8")}


def exact_mcnemar(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(b, c) + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def compare(name_a: str, name_b: str) -> None:
    a, b = load(RUNS[name_a]), load(RUNS[name_b])
    keys = sorted(set(a) & set(b))
    assert len(keys) == 864, f"expected 864 paired rows, got {len(keys)}"
    both = a_only = b_only = neither = 0
    for k in keys:
        x, y = a[k]["correct"], b[k]["correct"]
        both += x and y
        a_only += x and not y
        b_only += y and not x
        neither += not x and not y
    print(f"{name_a} {both + a_only}/864 = {(both + a_only) / 864 * 100:.2f}%   "
          f"vs   {name_b} {both + b_only}/864 = {(both + b_only) / 864 * 100:.2f}%")
    print(f"  both {both}  {name_a}-only {a_only}  {name_b}-only {b_only}  neither {neither}"
          f"  ->  McNemar {a_only}:{b_only}, p = {exact_mcnemar(a_only, b_only):.4g}")
    per: dict[str, list[int]] = {}
    for k in keys:
        lab = a[k]["target"]
        per.setdefault(lab, [0, 0])
        per[lab][0] += a[k]["correct"]
        per[lab][1] += b[k]["correct"]
    deltas = "  ".join(f"{lab} {y - x:+d}" for lab, (x, y) in sorted(per.items()) if y != x)
    print(f"  per-class delta ({name_b} minus {name_a}): {deltas or 'none'}")
    print()


if __name__ == "__main__":
    pairs = [("base", "v2c700"), ("v1", "v2c700"), ("v2c300", "v2c700"),
             ("v2c700", "v2c1048"), ("base", "v2c1048")]
    if len(sys.argv) == 3:
        pairs = [(sys.argv[1], sys.argv[2])]
    for x, y in pairs:
        compare(x, y)

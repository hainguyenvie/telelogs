#!/usr/bin/env python3
"""Paired comparison of the 2026-08-03 champion run against the base-weight rerun.

Both runs use the same compiled program, the same magnitude residual rule and the
same official boxed-int scorer, so the only variable is the served checkpoint —
which is exactly the claim under test. Pairing is by `sample_index`, never by
line order: the runner writes rows as threads finish, so the two files are not in
the same order.

Exact two-sided McNemar (binomial, p=0.5) — the sample is small enough that the
chi-square approximation is not worth the assumption.
"""
from __future__ import annotations

import json
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "artifacts" / "telelogs-dspy-tools" / "results"
ORIG = ROOT / "champion_gsma_full_magrule" / "results.jsonl"
BASE = ROOT / "champion_base_magrule" / "results.jsonl"


def load(path: Path) -> dict[int, dict]:
    rows = {}
    for line in path.open(encoding="utf-8"):
        r = json.loads(line)
        rows[r["sample_index"]] = r
    return rows


def exact_mcnemar(b: int, c: int) -> float:
    """Two-sided exact binomial test on the discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def main() -> None:
    orig, base = load(ORIG), load(BASE)
    shared = sorted(set(orig) & set(base))
    assert len(shared) == 864, f"expected 864 paired rows, got {len(shared)}"

    both = only_orig = only_base = neither = 0
    flips: list[tuple[int, str, str, str]] = []
    for i in shared:
        o, b = orig[i]["correct"], base[i]["correct"]
        if o and b:
            both += 1
        elif o and not b:
            only_orig += 1
            flips.append((i, orig[i]["target"], orig[i]["pipeline_answer"] or "unparsed",
                          base[i]["pipeline_answer"] or "unparsed"))
        elif b and not o:
            only_base += 1
            flips.append((i, orig[i]["target"], orig[i]["pipeline_answer"] or "unparsed",
                          base[i]["pipeline_answer"] or "unparsed"))
        else:
            neither += 1

    n = len(shared)
    print(f"paired rows            : {n}")
    print(f"original (2026-08-03)  : {both + only_orig}/{n} = {(both + only_orig) / n * 100:.2f}%")
    print(f"base rerun (2026-08-24): {both + only_base}/{n} = {(both + only_base) / n * 100:.2f}%")
    print()
    print(f"both correct           : {both}")
    print(f"original only          : {only_orig}")
    print(f"base only              : {only_base}")
    print(f"neither                : {neither}")
    print(f"discordant             : {only_orig + only_base}")
    print(f"exact McNemar p        : {exact_mcnemar(only_orig, only_base):.4g}")
    print()

    per_class: dict[str, list[int]] = {}
    for i in shared:
        lab = orig[i]["target"]
        per_class.setdefault(lab, [0, 0])
        per_class[lab][0] += orig[i]["correct"]
        per_class[lab][1] += base[i]["correct"]
    print("class   orig  base  delta")
    for lab, (o, b) in sorted(per_class.items()):
        print(f"{lab:6} {o:5} {b:5} {b - o:+6}")
    print()

    print("discordant cases (index, gold, original answer, base answer)")
    for i, gold, o_ans, b_ans in flips:
        mark = "orig" if orig[i]["correct"] else "base"
        print(f"  {i:4}  gold={gold}  orig={o_ans:8} base={b_ans:8}  won={mark}")


if __name__ == "__main__":
    main()

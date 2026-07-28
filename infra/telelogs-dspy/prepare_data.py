#!/usr/bin/env python3
"""Prepare leakage-resistant TeleLogs development splits for DSPy.

The official 864-example test set is deliberately not read here.  Split groups are
formed from the raw training-question skeleton after replacing numeric values, so
near-identical table templates never straddle train/dev/holdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw_train_2400/train.json"
LEDGER = ROOT / "data/reasoning_v14/canonical_v14.audit.jsonl"


def compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


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


def gold_reasoning(solve_answer: str | None, label: str) -> str:
    if not solve_answer:
        return f"{label}: selected from the calculator-verified evidence hierarchy."
    decision = solve_answer.split("\n\nDecision\n", 1)[-1]
    decision = re.sub(r"\n*\\boxed\{C[1-8]\}\s*$", "", decision).strip()
    return decision


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    raw = json.loads(RAW.read_text(encoding="utf-8"))
    ledger = [json.loads(line) for line in LEDGER.read_text().splitlines()]
    if len(raw) != 2400 or len(ledger) != 2400:
        raise SystemExit("expected 2,400 aligned TeleLogs training rows")

    output = []
    counts: Counter[tuple[str, str]] = Counter()
    groups_by_split: dict[str, set[str]] = {
        "train": set(),
        "dev": set(),
        "holdout": set(),
    }
    for index, (source, audit) in enumerate(zip(raw, ledger)):
        if audit["source_index"] != index or audit["label"] != source["answer"]:
            raise SystemExit(f"alignment failure at source_index={index}")
        group = question_group(source["question"])
        split = split_for(group)
        groups_by_split[split].add(group)
        counts[(split, source["answer"])] += 1
        output.append(
            {
                "source_index": index,
                "label": source["answer"],
                "split": split,
                "template_group": group,
                "verified_facts": compact_json(audit["compact_facts"]),
                "gold_reasoning": gold_reasoning(
                    audit["solve_answer"], source["answer"]
                ),
                "demo_ready": bool(audit["solution_ready"]),
                "evidence_tier": audit["evidence_tier"],
            }
        )

    if any(
        groups_by_split[left] & groups_by_split[right]
        for left, right in (("train", "dev"), ("train", "holdout"), ("dev", "holdout"))
    ):
        raise SystemExit("template-group leakage between splits")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in output),
        encoding="utf-8",
    )
    summary = {
        split: {
            "total": sum(counts[(split, f"C{i}")] for i in range(1, 9)),
            "by_label": {f"C{i}": counts[(split, f"C{i}")] for i in range(1, 9)},
            "template_groups": len(groups_by_split[split]),
        }
        for split in ("train", "dev", "holdout")
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

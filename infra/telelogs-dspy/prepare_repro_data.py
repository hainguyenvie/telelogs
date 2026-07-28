#!/usr/bin/env python3
"""Rebuild TeleLogs DSPy inputs directly from a locally obtained benchmark.

The TeleLogs dataset asks users not to redistribute it, so the Git repository
contains this deterministic reconstruction script instead of train/test data.
Gold labels are retained only as evaluation targets and are never passed to the
calculator or model.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "infra" / "telelogs-dspy"))

from exhaustive_review_v11 import independent_parse  # noqa: E402
from generate_reasoning_v13 import verified_facts  # noqa: E402
from generate_reasoning_v14 import compact_facts, compact_json  # noqa: E402
from prepare_data import question_group, split_for  # noqa: E402


LABELS = {f"C{index}" for index in range(1, 9)}


def load_sources(path: Path, expected: int) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or len(rows) != expected:
        raise SystemExit(f"expected {expected} records in {path}, got {len(rows)}")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SystemExit(f"record {index} in {path} is not an object")
        if not isinstance(row.get("question"), str) or not row["question"]:
            raise SystemExit(f"record {index} in {path} has no question")
        if row.get("answer") not in LABELS:
            raise SystemExit(f"record {index} in {path} has invalid answer")
    return rows


def calculated_facts(question: str) -> str:
    calculator_facts, row_evidence = independent_parse(
        question, include_details=True
    )
    return compact_json(compact_facts(verified_facts(calculator_facts, row_evidence)))


def prepare_train(path: Path) -> tuple[list[dict], dict]:
    sources = load_sources(path, expected=2400)
    output = []
    groups_by_split: dict[str, set[str]] = {
        "train": set(),
        "dev": set(),
        "holdout": set(),
    }
    counts: Counter[tuple[str, str]] = Counter()

    for index, source in enumerate(sources):
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
                "verified_facts": calculated_facts(source["question"]),
                "gold_reasoning": "",
                "demo_ready": False,
                "evidence_tier": "reconstructed_from_raw",
            }
        )

    for left, right in (
        ("train", "dev"),
        ("train", "holdout"),
        ("dev", "holdout"),
    ):
        overlap = groups_by_split[left] & groups_by_split[right]
        if overlap:
            raise SystemExit(
                f"template-group leakage between {left} and {right}: {len(overlap)}"
            )

    summary = {
        split: {
            "total": sum(counts[(split, label)] for label in sorted(LABELS)),
            "by_label": {
                label: counts[(split, label)] for label in sorted(LABELS)
            },
            "template_groups": len(groups_by_split[split]),
        }
        for split in ("train", "dev", "holdout")
    }
    return output, summary


def prepare_official(path: Path) -> tuple[list[dict], dict]:
    sources = load_sources(path, expected=864)
    output = [
        {
            "source_index": index,
            "label": source["answer"],
            "split": "official",
            "verified_facts": calculated_facts(source["question"]),
            "gold_reasoning": "",
            "demo_ready": False,
            "evidence_tier": "reconstructed_from_raw",
        }
        for index, source in enumerate(sources)
    ]
    return output, {
        "official": {
            "total": len(output),
            "by_label": dict(Counter(row["label"] for row in output)),
        }
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-json", type=Path)
    parser.add_argument("--train-output", type=Path)
    parser.add_argument("--official-json", type=Path)
    parser.add_argument("--official-output", type=Path)
    args = parser.parse_args()

    if bool(args.train_json) != bool(args.train_output):
        parser.error("--train-json and --train-output must be supplied together")
    if bool(args.official_json) != bool(args.official_output):
        parser.error("--official-json and --official-output must be supplied together")
    if not args.train_json and not args.official_json:
        parser.error("supply a train pair, an official pair, or both")

    report = {}
    if args.train_json:
        rows, summary = prepare_train(args.train_json)
        write_jsonl(args.train_output, rows)
        report["train_output"] = str(args.train_output)
        report.update(summary)

    if args.official_json:
        rows, summary = prepare_official(args.official_json)
        write_jsonl(args.official_output, rows)
        report["official_output"] = str(args.official_output)
        report.update(summary)

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

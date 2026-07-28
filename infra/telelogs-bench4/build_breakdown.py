#!/usr/bin/env python3
"""Build compact ground-truth/category accuracy breakdowns for the dashboard."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq


DISPLAY_NAMES = {
    "telelogs": "TeleLogs",
    "telemath": "TeleMath",
    "teletables": "TeleTables",
    "three_gpp": "3GPP",
}


def sort_key(benchmark: str, label: str) -> tuple[int, str]:
    if benchmark == "telelogs" and label.startswith("C"):
        return (int(label[1:]) if label[1:].isdigit() else 999, label)
    if benchmark == "teletables" and len(label) == 1:
        return (ord(label), label)
    group_order = {"CT": 0, "RAN": 1, "SA": 2}
    prefix = next((key for key in group_order if label.startswith(key)), "")
    return (group_order.get(prefix, 999), label)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--telemath-parquet", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    math_rows = pq.read_table(args.telemath_parquet, columns=["category"]).to_pylist()
    stats: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"total": 0, "correct": 0, "errors": 0})
    )

    with args.results.open(encoding="utf-8") as source:
        for line in source:
            result = json.loads(line)
            benchmark = result["benchmark"]
            if benchmark == "telemath":
                label = str(math_rows[int(result["sample_index"])]["category"])
            else:
                label = str(result["target"])
            row = stats[benchmark][label]
            row["total"] += 1
            row["correct"] += int(bool(result.get("correct")))
            row["errors"] += int(bool(result.get("error")))

    benchmarks = {}
    for benchmark in DISPLAY_NAMES:
        rows = []
        for label, values in sorted(
            stats[benchmark].items(), key=lambda item: sort_key(benchmark, item[0])
        ):
            scored = values["total"] - values["errors"]
            wrong = scored - values["correct"]
            rows.append(
                {
                    "label": label,
                    "total": values["total"],
                    "correct": values["correct"],
                    "wrong": wrong,
                    "errors": values["errors"],
                    "accuracy": values["correct"] / scored if scored else None,
                }
            )
        benchmarks[benchmark] = {
            "display_name": DISPLAY_NAMES[benchmark],
            "grouping": "category" if benchmark == "telemath" else "ground_truth",
            "rows": rows,
        }

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmarks": benchmarks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

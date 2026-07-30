#!/usr/bin/env python3
"""Extract incorrect predictions without modifying the original result files."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc
    return rows


def index_unique(rows: list[dict], path: Path) -> dict[int, dict]:
    indexed: dict[int, dict] = {}
    for row in rows:
        source_index = row.get("source_index")
        if not isinstance(source_index, int):
            raise ValueError(f"Missing integer source_index in {path}: {row!r}")
        if source_index in indexed:
            raise ValueError(f"Duplicate source_index={source_index} in {path}")
        indexed[source_index] = row
    return indexed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()

    predictions = read_jsonl(args.predictions)
    facts_by_index = index_unique(read_jsonl(args.facts), args.facts)
    index_unique(predictions, args.predictions)

    incorrect: list[dict] = []
    for prediction in predictions:
        if prediction.get("correct") is not False:
            continue
        source_index = prediction["source_index"]
        if source_index not in facts_by_index:
            raise ValueError(f"No verified facts for source_index={source_index}")
        fact = facts_by_index[source_index]
        verified_facts = fact.get("verified_facts", "")
        if isinstance(verified_facts, str):
            verified_facts_parsed = json.loads(verified_facts)
        else:
            verified_facts_parsed = verified_facts
        incorrect.append(
            {
                "source_index": source_index,
                "split": fact.get("split"),
                "gold_label": prediction.get("target"),
                "predicted_label": prediction.get("answer"),
                "model_reasoning": prediction.get("reasoning"),
                "decision_path": prediction.get("decision_path"),
                "tool_called": prediction.get("tool_called"),
                "tool_succeeded": prediction.get("tool_succeeded"),
                "tool_name": prediction.get("tool_name"),
                "tool_args": prediction.get("tool_args"),
                "tool_observation": prediction.get("tool_observation"),
                "verified_facts": verified_facts_parsed,
                "elapsed_seconds": prediction.get("elapsed_seconds"),
                "error": prediction.get("error"),
            }
        )

    incorrect.sort(key=lambda row: row["source_index"])
    args.output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    with args.output_jsonl.open("w", encoding="utf-8", newline="\n") as handle:
        for row in incorrect:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    gold_counts = Counter(row["gold_label"] for row in incorrect)
    confusion_counts = Counter(
        (row["gold_label"], row["predicted_label"]) for row in incorrect
    )
    markdown: list[str] = [
        "# Incorrect predictions report",
        "",
        f"- Original predictions: `{args.predictions}`",
        f"- Verified facts: `{args.facts}`",
        f"- Incorrect samples: **{len(incorrect)} / {len(predictions)}**",
        "- The original files were read only and were not modified.",
        "",
        "## Incorrect count by gold label",
        "",
        "| Gold label | Incorrect |",
        "|---|---:|",
    ]
    for label in sorted(gold_counts):
        markdown.append(f"| {label} | {gold_counts[label]} |")

    markdown.extend(
        [
            "",
            "## Gold-to-predicted confusion",
            "",
            "| Gold | Predicted | Count |",
            "|---|---|---:|",
        ]
    )
    for (gold, predicted), count in sorted(
        confusion_counts.items(), key=lambda item: (-item[1], item[0])
    ):
        markdown.append(f"| {gold} | {predicted or '(empty)'} | {count} |")

    markdown.extend(["", "## Samples", ""])
    for number, row in enumerate(incorrect, start=1):
        facts_pretty = json.dumps(
            row["verified_facts"], ensure_ascii=False, indent=2, sort_keys=True
        )
        markdown.extend(
            [
                f"### {number}. source_index={row['source_index']}",
                "",
                f"- Gold label: **{row['gold_label']}**",
                f"- Predicted label: **{row['predicted_label'] or '(empty)'}**",
                f"- Decision path: `{row['decision_path'] or '(unknown)'}`",
                f"- Elapsed: {row['elapsed_seconds']} seconds",
                "",
            ]
        )
        if row["tool_called"] is not None:
            tool_args = json.dumps(
                row["tool_args"], ensure_ascii=False, separators=(",", ":")
            )
            markdown.extend(
                [
                    f"- Tool called: **{row['tool_called']}**",
                    f"- Tool succeeded: **{row['tool_succeeded']}**",
                    f"- Tool name: `{row['tool_name'] or '(none)'}`",
                    f"- Tool args: `{tool_args}`",
                    "",
                    "Tool observation:",
                    "",
                    f"> {row['tool_observation'] or '(empty)'}",
                    "",
                ]
            )
        markdown.extend(
            [
                "Model reasoning:",
                "",
                f"> {row['model_reasoning'] or '(empty)'}",
                "",
                "Verified facts:",
                "",
                "```json",
                facts_pretty,
                "```",
                "",
            ]
        )

    args.output_markdown.write_text("\n".join(markdown), encoding="utf-8")
    print(
        json.dumps(
            {
                "total_predictions": len(predictions),
                "incorrect": len(incorrect),
                "output_jsonl": str(args.output_jsonl.resolve()),
                "output_markdown": str(args.output_markdown.resolve()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

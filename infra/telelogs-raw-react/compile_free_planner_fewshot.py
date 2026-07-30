#!/usr/bin/env python3
"""Attach eight compact, balanced tool-planning demos to the free planner."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import dspy

from raw_react_program import RawTableReActTeleLogsProgram
from raw_tools import RawCaseCalculator


LABEL_TO_TOOL = {
    "C1": "analyze_signal_coverage",
    "C2": "analyze_radio_geometry",
    "C3": "compare_segment_throughput",
    "C4": "analyze_neighbor_overlap",
    "C5": "analyze_serving_transitions",
    "C6": "analyze_pci_pattern",
    "C7": "analyze_mobility",
    "C8": "analyze_resource_usage",
}

TOOL_DESCRIPTIONS = {
    "analyze_radio_geometry": (
        "Calculate serving distances from raw drive and engineering rows."
    ),
    "analyze_signal_coverage": (
        "Calculate below-lobe geometry and weak-signal row evidence."
    ),
    "analyze_serving_transitions": (
        "Calculate serving-cell transitions and affected destinations."
    ),
    "analyze_mobility": (
        "Calculate maximum vehicle speed from raw drive rows."
    ),
    "analyze_resource_usage": (
        "Calculate scheduled resources over affected raw rows."
    ),
    "compare_segment_throughput": (
        "Compare minimum throughput across raw serving segments."
    ),
    "analyze_neighbor_overlap": (
        "Join raw neighbor BRSRP with engineering sites and assess overlap."
    ),
    "analyze_pci_pattern": (
        "Inspect affected raw rows for serving-neighbor PCI collisions."
    ),
}


def tool_catalog() -> str:
    return "\n".join(
        f"- {name}: {description}"
        for name, description in TOOL_DESCRIPTIONS.items()
    )


def compact_demo_input(observation: dict) -> str:
    return (
        "Compact planning example derived from raw affected rows. "
        f"Observation type: {observation.get('observation_type')}. "
        f"Measurements: {json.dumps(observation.get('measurements'), ensure_ascii=False)}. "
        f"Engineering interpretation: {observation.get('interpretation')}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--raw-train",
        type=Path,
        default=Path("data/raw_train_2400/train.json"),
    )
    parser.add_argument(
        "--splits",
        type=Path,
        default=Path(
            "artifacts/telelogs-dspy/data/telelogs_train_splits.jsonl"
        ),
    )
    parser.add_argument("--demos-per-label", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    raw_rows = json.loads(args.raw_train.read_text(encoding="utf-8"))
    split_rows = [
        json.loads(line)
        for line in args.splits.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    pools: dict[str, list[dict]] = defaultdict(list)
    for row in split_rows:
        if row["split"] == "train":
            pools[row["label"]].append(row)

    demos = []
    audit = []
    catalog = tool_catalog()
    selected_by_label = {
        label: sorted(
            pools[label], key=lambda row: row["source_index"]
        )[: args.demos_per_label]
        for label in LABEL_TO_TOOL
    }
    if any(
        len(rows) < args.demos_per_label
        for rows in selected_by_label.values()
    ):
        raise ValueError("Not enough balanced train demonstrations")
    for rank in range(args.demos_per_label):
        for label, tool_name in LABEL_TO_TOOL.items():
            meta = selected_by_label[label][rank]
            raw = raw_rows[meta["source_index"]]
            calculator = RawCaseCalculator(raw["question"])
            observation = json.loads(getattr(calculator, tool_name)())
            demo_input = compact_demo_input(observation)
            reasoning = (
                f"The affected-row pattern specifically requires {tool_name}: "
                f"{observation.get('interpretation')} Other calculators should "
                "only be added when the query contains a concrete competing "
                "witness."
            )
            demos.append(
                dspy.Example(
                    raw_table=demo_input,
                    tool_catalog=catalog,
                    selection_reasoning=reasoning,
                    selected_tools=tool_name,
                ).with_inputs("raw_table", "tool_catalog")
            )
            audit.append(
                {
                    "label": label,
                    "source_index": meta["source_index"],
                    "selected_tools": tool_name,
                    "compact_input": demo_input,
                    "selection_reasoning": reasoning,
                }
            )

    program = RawTableReActTeleLogsProgram(
        tool_selection="free",
        max_iters=3,
    )
    program.tool_planner.demos = demos
    args.output_dir.mkdir(parents=True, exist_ok=True)
    program_path = args.output_dir / "program.json"
    program.save(program_path)
    summary = {
        "method": "dspy_compact_labeled_fewshot",
        "demo_count": len(demos),
        "labels": list(LABEL_TO_TOOL),
        "program": str(program_path),
        "demos": audit,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Reproduce the C3 residual threshold using the train split only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def is_residual(facts: dict) -> bool:
    return not (
        facts["C2"]["distance_gate"] == "triggered"
        or facts["C5"]["frequent_change_gate"]
        or facts["C7"]["speed_gate"]
        or facts["C8"]["affected_average_rb_gate"]
        or facts["C1"]["affected_weak_rsrp_witness"]
    )


def predict(facts: dict, threshold: float) -> str:
    if facts["C3"]["minimum_advantage_mbps"] >= threshold:
        return "C3"
    if facts["C4"]["affected_overlap_gate"]:
        return "C4"
    if facts["C6"]["affected_modulo_30_collision"]:
        return "C6"
    if facts["C1"]["affected_below_lower_lobe"]:
        return "C1"
    return "C3"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--step", type=float, default=0.5)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.dataset.read_text().splitlines()]
    train = []
    for row in rows:
        facts = json.loads(row["verified_facts"])
        if row["split"] == "train" and is_residual(facts):
            train.append((row["label"], facts))

    maximum = max(facts["C3"]["minimum_advantage_mbps"] for _, facts in train)
    candidates = [i * args.step for i in range(int(maximum / args.step) + 2)]
    scored = [
        (sum(predict(facts, threshold) == label for label, facts in train), threshold)
        for threshold in candidates
    ]
    best_correct = max(correct for correct, _ in scored)
    best_thresholds = [threshold for correct, threshold in scored if correct == best_correct]
    selected = min(best_thresholds)
    print(
        json.dumps(
            {
                "calibration_split": "train",
                "residual_examples": len(train),
                "grid_step_mbps": args.step,
                "selected_threshold_mbps": selected,
                "correct": best_correct,
                "accuracy": best_correct / len(train),
                "tied_best_thresholds_mbps": best_thresholds,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

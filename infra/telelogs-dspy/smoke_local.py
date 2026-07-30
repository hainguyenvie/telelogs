#!/usr/bin/env python3
"""Validate deterministic hybrid routes without contacting a model API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from telelogs_program import CalibratedHybridTeleLogsProgram, normalize_answer


DIRECT_LABELS = {"C1", "C2", "C5", "C7", "C8"}


def direct_answer(facts: dict) -> str | None:
    """Mirror the program's ordered proof-level routing."""
    if facts["C2"]["distance_gate"] == "triggered":
        return "C2"
    if facts["C5"]["frequent_change_gate"]:
        return "C5"
    if facts["C7"]["speed_gate"]:
        return "C7"
    if facts["C8"]["affected_average_rb_gate"]:
        return "C8"
    if facts["C1"]["affected_weak_rsrp_witness"]:
        return "C1"
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()

    program = CalibratedHybridTeleLogsProgram()
    found: dict[str, dict] = {}
    for line in args.dataset.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["label"] not in DIRECT_LABELS or row["label"] in found:
            continue
        facts = json.loads(row["verified_facts"])
        if direct_answer(facts) != row["label"]:
            continue
        prediction = program(verified_facts=row["verified_facts"])
        answer = normalize_answer(prediction.answer)
        if (
            prediction.decision_path == "deterministic_gate"
            and answer == row["label"]
        ):
            found[row["label"]] = {
                "source_index": row["source_index"],
                "answer": answer,
                "decision_path": prediction.decision_path,
                "reasoning": prediction.reasoning,
            }

    missing = sorted(DIRECT_LABELS - found.keys())
    if missing:
        raise SystemExit(f"missing passing deterministic examples: {missing}")
    print(json.dumps(found, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Audit every raw training case for parser and label-neutral tool invariants."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from neutral_tools import TOOL_FUNCTIONS, assert_label_neutral, parse_case, run_tools


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    rows = json.loads(args.data.read_text(encoding="utf-8"))
    for index, row in enumerate(rows):
        case = parse_case(row["question"])
        results = run_tools(case, list(TOOL_FUNCTIONS))
        assert_label_neutral(results)
        if not case.observations:
            raise SystemExit(f"no observations at source_index={index}")
    print(json.dumps({"records": len(rows), "tools": list(TOOL_FUNCTIONS), "label_neutral": True}, indent=2))


if __name__ == "__main__":
    main()

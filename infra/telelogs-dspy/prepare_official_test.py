#!/usr/bin/env python3
"""Calculate DSPy input facts for the frozen official TeleLogs test split.

The answer is retained only as an evaluation target. It is never passed to the
calculator or model. This script must be run only after prompt selection is frozen.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from exhaustive_review_v11 import independent_parse  # noqa: E402
from generate_reasoning_v13 import verified_facts  # noqa: E402
from generate_reasoning_v14 import compact_facts, compact_json  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--test-json", type=Path, default=ROOT / "data/official_test_864/test.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    sources = json.loads(args.test_json.read_text(encoding="utf-8"))
    rows = []
    for index, source in enumerate(sources):
        calculator_facts, row_evidence = independent_parse(
            source["question"], include_details=True
        )
        facts = compact_facts(verified_facts(calculator_facts, row_evidence))
        rows.append(
            {
                "source_index": index,
                "label": source["answer"],
                "split": "official",
                "verified_facts": compact_json(facts),
                "gold_reasoning": "",
                "demo_ready": False,
            }
        )

    if len(rows) != 864:
        raise SystemExit(f"expected 864 official rows, got {len(rows)}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(json.dumps({"records": len(rows), "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()

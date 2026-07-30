#!/usr/bin/env python3
"""Send one prepared TeleLogs fact sheet through the zero-shot DSPy program."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import dspy

from telelogs_program import TeleLogsProgram, normalize_answer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--max-tokens", type=int, default=400)
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in args.dataset.read_text(encoding="utf-8").splitlines()
    ]
    row = rows[args.index]
    api_key = os.environ.get("DSPY_API_KEY")
    if not api_key:
        raise SystemExit("DSPY_API_KEY is required")

    lm = dspy.LM(
        os.environ.get("DSPY_MODEL", "openai/netLLMv1.0"),
        api_base=os.environ.get(
            "DSPY_API_BASE", "https://stream-netmind.viettel.vn/gateway/v1"
        ),
        api_key=api_key,
        temperature=0.0,
        max_tokens=args.max_tokens,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        cache=False,
    )
    dspy.configure(lm=lm)
    prediction = TeleLogsProgram()(verified_facts=row["verified_facts"])
    answer = normalize_answer(prediction.answer)
    print(
        json.dumps(
            {
                "source_index": row["source_index"],
                "target": row["label"],
                "answer": answer,
                "correct": answer == row["label"],
                "reasoning": str(prediction.reasoning),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

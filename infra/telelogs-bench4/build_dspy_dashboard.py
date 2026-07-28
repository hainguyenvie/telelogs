#!/usr/bin/env python3
"""Build the compact DSPy TeleLogs experiment payload for the static dashboard."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


RUNS = (
    ("structured_zero_dev256", "Structured zero-shot · dev", "baseline"),
    ("labeled_fewshot", "LabeledFewShot · pilot", "rejected"),
    ("bootstrap", "BootstrapFewShot · pilot", "rejected"),
    ("gepa", "GEPA · pilot", "no_change"),
    ("gepa_residual", "GEPA residual · pilot", "no_change"),
    ("hybrid_dev256", "Hybrid residual-only · dev", "rejected"),
    ("hybrid_original_dev256", "Hybrid validated prompt · dev", "selected"),
    ("hybrid_holdout", "Hybrid validated prompt · holdout", "holdout"),
    ("hybrid_official_frozen", "Hybrid frozen · official 864", "official"),
    ("gepa_deepseek_pro", "GEPA + DeepSeek V4 Pro · dev 224", "rejected"),
    ("gepa_deepseek_flash", "GEPA + DeepSeek V4 Flash · dev 224", "no_change"),
    (
        "gepa_hybrid_deepseek_flash",
        "Hybrid GEPA + DeepSeek Flash · dev 224",
        "no_change",
    ),
    (
        "contrastive_residual_dev224",
        "Contrastive few-shot · dev 224",
        "rejected",
    ),
    ("calibrated_prompt_dev224", "Calibrated numeric prompt · dev 224", "improved"),
    (
        "calibrated_prompt_boolean_dev224",
        "Calibrated boolean prompt · dev 224",
        "selected_latest",
    ),
    (
        "calibrated_prompt_boolean_holdout479",
        "Calibrated boolean prompt · holdout 479",
        "secondary_holdout",
    ),
    (
        "gepa_calibrated_deepseek_flash",
        "Calibrated + GEPA Flash · dev 224",
        "no_change",
    ),
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--official-dataset", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    facts = {
        row["source_index"]: row
        for row in (
            json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines()
        )
    }
    official_facts = (
        {
            row["source_index"]: row
            for row in (
                json.loads(line)
                for line in args.official_dataset.read_text(encoding="utf-8").splitlines()
            )
        }
        if args.official_dataset
        else {}
    )
    runs = []
    for key, display_name, verdict in RUNS:
        run_dir = args.results / key
        if not (run_dir / "summary.json").exists():
            continue
        summary = read_json(run_dir / "summary.json")
        source_facts = official_facts if summary["eval_split"] == "official" else facts
        predictions = [
            json.loads(line)
            for line in (run_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        errors = []
        for prediction in predictions:
            if prediction["correct"]:
                continue
            source = source_facts[prediction["source_index"]]
            errors.append(
                {
                    **prediction,
                    "verified_facts": json.loads(source["verified_facts"]),
                    "gold_reasoning": source["gold_reasoning"],
                }
            )
        runs.append(
            {
                "key": key,
                "display_name": display_name,
                "verdict": verdict,
                "summary": summary,
                "errors": errors,
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "model": "Qwen/Qwen3-8B",
                "official_test_used_for_tuning": False,
                "runs": runs,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

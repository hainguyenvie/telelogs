#!/usr/bin/env python3
"""Exercise the DSPy residual path with a synthetic, non-dataset fact sheet."""

from __future__ import annotations

import json
import os

import dspy

from telelogs_program import CalibratedHybridTeleLogsProgram, normalize_answer


SYNTHETIC_FACTS = {
    "affected": {"rows": 2, "minimum_throughput_mbps": 100.0},
    "C1": {
        "affected_below_lower_lobe": False,
        "affected_weak_rsrp_witness": False,
        "witness": None,
    },
    "C2": {"distance_gate": "not_triggered", "maximum_distance_km": 0.5},
    "C3": {
        "affected_pci": 1,
        "affected_min_mbps": 100.0,
        "alternative_pci": 2,
        "alternative_min_mbps": 300.0,
        "minimum_advantage_mbps": 200.0,
    },
    "C4": {"affected_overlap_gate": False, "witness": None},
    "C5": {"change_count": 1, "frequent_change_gate": False},
    "C6": {"affected_modulo_30_collision": False, "witness": None},
    "C7": {"maximum_speed_kmh": 20.0, "speed_gate": False},
    "C8": {
        "affected_average_scheduled_rbs": 200.0,
        "affected_average_rb_gate": False,
    },
}


def main() -> None:
    api_key = os.environ.get("DSPY_API_KEY")
    if not api_key:
        raise SystemExit("DSPY_API_KEY is required")
    lm = dspy.LM(
        os.environ.get("DSPY_MODEL", "openai/Qwen/Qwen3.5-9B"),
        api_base=os.environ.get(
            "DSPY_API_BASE", "https://stream-netmind.viettel.vn/gateway/v1"
        ),
        api_key=api_key,
        temperature=0.0,
        max_tokens=600,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        cache=False,
    )
    dspy.configure(lm=lm)
    prediction = CalibratedHybridTeleLogsProgram()(
        verified_facts=json.dumps(
            SYNTHETIC_FACTS, ensure_ascii=False, separators=(",", ":")
        )
    )
    print(
        json.dumps(
            {
                "answer": normalize_answer(prediction.answer),
                "reasoning": str(prediction.reasoning),
                "decision_path": str(prediction.decision_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

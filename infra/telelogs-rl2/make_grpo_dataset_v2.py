#!/usr/bin/env python3
"""Build the GRPO training set for the tool-calling track — v2, magnitude ladder.

Every prompt reproduces the information state of the deployed two-stage b3
program: the four stage-one observations always, the two stage-two
observations only when no exact criterion fires (decided symbolically from the
observations themselves — the label is never consulted). The gold label is
stored in a separate field consumed ONLY by the reward function.

v2 changes exactly one thing: the residual half of SYSTEM_PROMPT now states the
magnitude ladder the shipped specialist actually uses, not the superseded
presence ladder.

Why it matters, measured on this very split by
infra/telelogs-rl2/ladder_reward_alignment.py:

    residual prompts where obeying the stated ladder earns reward
      presence  ladder : 516/658 = 78.42%
      magnitude ladder : 598/658 = 90.88%

Under the presence ladder, 142 of 658 residual prompts reward the policy for
*disobeying* its own instructions — the reward and the instruction point in
opposite directions on more than a fifth of the residual training signal. That
is a defect in the data, not in GRPO, and it is fixed here. Everything else —
the split, the stage logic, the /no_think switch, the residual repeat — is held
identical so the change stays interpretable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/workspace/telelogs-rl/code")
sys.path.insert(0, "/workspace/telelogs-bench4/dspy-tools/code")
from neutral_tools import TOOL_FUNCTIONS, assert_label_neutral, parse_case  # noqa: E402

STAGE1_TOOLS = (
    "analyze_throughput_segments",
    "analyze_coverage_geometry",
    "analyze_mobility",
    "analyze_radio_resources",
)
STAGE2_TOOLS = ("analyze_pci_relations", "analyze_neighbor_overlap")

SYSTEM_PROMPT = """You are a telecom drive-test diagnostician. The measurement observations below were computed by deterministic tools from the raw tables; they never contain a diagnosis. Every number you write must come from an observation; comparisons must be written as signed inequalities between the two numbers, never as words like "worse" or "weaker"; for negative numbers -2.1 > -3 and -95 < -90.

First verify the four decisive criteria, one line per criterion, quoting the named observation field, the two numbers joined by < or >, and the verdict "triggered" or "not triggered":
- C2: triggered when maximum_distance_km is greater than 1;
- C5: triggered when transition_count is 3 or more; exactly 3 already triggers;
- C7: triggered when maximum_speed_kmh is greater than 40;
- C8: triggered when low_throughput_rows_mean_scheduled_rbs is below 160.
A triggered criterion IS the diagnosis: stop at the first triggered line and answer that class. Writing a comparison that makes a criterion true and then labelling it "not triggered" is an error.

Only when all four verdicts are "not triggered": decide the residual cause. Each of C6, C4 and C1 below is decided by HOW MUCH of its phenomenon is present, not merely whether any is present at all — a single occurrence is common and often coincidental; the dominant cause is the one that recurs. Check these rules one at a time, in this exact order, quoting the named observation field, and stop at the first satisfied rule without evaluating or mentioning later rules:
0. a row in rows_below_main_lobe_lower_edge whose throughput is below the stated criterion AND whose serving RSRP satisfies RSRP <= -90 proves C1 outright;
1. otherwise equal_residue_pair_count of more than 2 selects C6 (more than two serving/neighbour row-pairs sharing a PCI mod-30 residue, not merely one);
2. otherwise noncolocated_gap_at_or_above_neg3db_count of more than 2 selects C4 (more than two low-throughput rows where a non-colocated neighbour clears -3 dB relative to serving, not merely one);
3. otherwise deepest_below_lobe_deficit_deg of more than 2.5 selects C1 (the worst low-throughput row below the main lobe lower edge sits more than 2.5 degrees below it, not merely below it);
4. otherwise select C3.

End with exactly one line: Final answer: <one of C1..C8>"""


def question_group(question: str) -> str:
    skeleton = re.sub(r"-?\d+(?:\.\d+)?", "#", question)
    skeleton = re.sub(r"\s+", " ", skeleton).strip()
    return hashlib.sha256(skeleton.encode()).hexdigest()[:16]


def split_for(group: str) -> str:
    bucket = int(group[:8], 16) % 100
    return "train" if bucket < 60 else ("dev" if bucket < 80 else "holdout")


def gate_fires(observations: dict) -> bool:
    geometry = observations["analyze_coverage_geometry"]
    mobility = observations["analyze_mobility"]
    radio = observations["analyze_radio_resources"]
    distance = geometry.get("maximum_distance_km")
    mean_rbs = radio.get("low_throughput_rows_mean_scheduled_rbs")
    return bool(
        (distance is not None and distance > 1.0)
        or mobility["transition_count"] >= 3
        or mobility["maximum_speed_kmh"] > 40.0
        or (mean_rbs is not None and mean_rbs < 160.0)
    )


def build_prompt(question: str, observations: dict) -> list[dict[str, str]]:
    blocks = [
        f"### Observation from {name}\n{json.dumps(obs, ensure_ascii=False, separators=(',', ':'))}"
        for name, obs in observations.items()
    ]
    # Qwen3 soft switch: trl 0.21 cannot pass chat_template_kwargs, and default
    # thinking-mode rollouts truncate at max_completion_length (94% clipped at
    # 768 observed) which corrupts the reward. /no_think forces short answers.
    user = f"{question.strip()}\n\n" + "\n\n".join(blocks) + "\n\n/no_think"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default="/workspace/telelogs-rl/data/train.json")
    parser.add_argument("--out", default="/workspace/telelogs-rl/data/grpo_train.jsonl")
    parser.add_argument("--residual-repeat", type=int, default=2,
                        help="how many times residual-stage prompts are repeated (gated stay at 1)")
    args = parser.parse_args()

    raw = json.loads(Path(args.raw).read_text(encoding="utf-8"))
    rows = []
    stage_counts: Counter[str] = Counter()
    for index, row in enumerate(raw):
        if split_for(question_group(row["question"])) != "train":
            continue
        case = parse_case(row["question"])
        stage1 = {name: TOOL_FUNCTIONS[name](case) for name in STAGE1_TOOLS}
        assert_label_neutral(stage1)
        observations = dict(stage1)
        stage = "gated"
        if not gate_fires(stage1):
            stage = "residual"
            stage2 = {name: TOOL_FUNCTIONS[name](case) for name in STAGE2_TOOLS}
            assert_label_neutral(stage2)
            observations.update(stage2)
        stage_counts[stage] += 1
        record = {
            "prompt": build_prompt(row["question"], observations),
            "answer": row["answer"],
            "source_index": index,
            "stage": stage,
        }
        repeats = args.residual_repeat if stage == "residual" else 1
        rows.extend([record] * repeats)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as stream:
        for record in rows:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} prompts to {out}")
    print("stage mix (unique cases):", dict(stage_counts))
    print("label mix (unique cases): computed only for the audit below")
    labels = Counter()
    seen = set()
    for record in rows:
        if record["source_index"] in seen:
            continue
        seen.add(record["source_index"])
        labels[record["answer"]] += 1
    print(dict(sorted(labels.items())))


if __name__ == "__main__":
    main()

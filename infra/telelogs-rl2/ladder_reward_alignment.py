#!/usr/bin/env python3
"""How often does obeying the ladder in the GRPO prompt actually earn reward?

GRPO's reward is 1.0 for matching the gold label. The prompt tells the policy to
follow a fixed rule ladder. If that ladder is frequently wrong, the two signals
point in opposite directions: the policy is rewarded for *disobeying* its own
instructions. That is a defect in the training data, not in the algorithm, and it
is measurable without training anything.

This script evaluates both ladders — the presence ladder shipped in
make_grpo_dataset.py's SYSTEM_PROMPT, and the magnitude ladder in
specialist_program.py's ResidualDecision docstring — through the REAL tool
functions, on the same train split GRPO trains on.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "infra" / "telelogs-dspy-tools"))

from neutral_tools import (  # noqa: E402
    TOOL_FUNCTIONS,
    parse_case,
)

TRAIN = REPO / "data/raw_train_2400/train.json"

STAGE1 = ("analyze_throughput_segments", "analyze_coverage_geometry",
          "analyze_mobility", "analyze_radio_resources")
STAGE2 = ("analyze_pci_relations", "analyze_neighbor_overlap")


def question_group(question: str) -> str:
    skeleton = re.sub(r"-?\d+(?:\.\d+)?", "#", question)
    skeleton = re.sub(r"\s+", " ", skeleton).strip()
    return hashlib.sha256(skeleton.encode()).hexdigest()[:16]


def split_for(group: str) -> str:
    bucket = int(group[:8], 16) % 100
    return "train" if bucket < 60 else ("dev" if bucket < 80 else "holdout")


def gate_answer(obs: dict) -> str | None:
    """The four exact criteria, in the order the prompt states them."""
    geo, mob, rad = obs["analyze_coverage_geometry"], obs["analyze_mobility"], obs["analyze_radio_resources"]
    dist = geo.get("maximum_distance_km")
    rbs = rad.get("low_throughput_rows_mean_scheduled_rbs")
    if dist is not None and dist > 1.0:
        return "C2"
    if mob["transition_count"] >= 3:
        return "C5"
    if mob["maximum_speed_kmh"] > 40.0:
        return "C7"
    if rbs is not None and rbs < 160.0:
        return "C8"
    return None


def below_lobe_low_rows(obs: dict, threshold: float) -> list[dict]:
    """Below-lobe rows are NOT pre-filtered by throughput; both ladders filter them."""
    return [r for r in (obs["analyze_coverage_geometry"].get("rows_below_main_lobe_lower_edge") or [])
            if r.get("throughput_mbps") is not None and r["throughput_mbps"] < threshold]


def strong_c1(obs: dict, threshold: float) -> bool:
    """The sufficient witness both ladders share, stated before either rule list."""
    for row in below_lobe_low_rows(obs, threshold):
        rsrp = row.get("serving_rsrp_dbm")
        if rsrp is not None and rsrp <= -90:
            return True
    return False


def presence_ladder(obs: dict, threshold: float) -> str:
    """make_grpo_dataset.py SYSTEM_PROMPT: any occurrence counts."""
    if strong_c1(obs, threshold):
        return "C1"
    seg = obs["analyze_throughput_segments"].get("segment_minimum_comparison") or {}
    adv = seg.get("minimum_difference_mbps")
    if adv is not None and adv >= 142.5:
        return "C3"
    if obs["analyze_pci_relations"].get("equal_residue_pairs"):
        return "C6"
    gap = (obs["analyze_neighbor_overlap"].get("best_noncolocated_gap") or {}).get("neighbor_minus_serving_db")
    if gap is not None and gap >= -3.0:
        return "C4"
    if below_lobe_low_rows(obs, threshold):
        return "C1"
    return "C3"


def magnitude_ladder(obs: dict, threshold: float) -> str:
    """specialist_program.py ResidualDecision: how much of the phenomenon is present."""
    if strong_c1(obs, threshold):
        return "C1"
    if (obs["analyze_pci_relations"].get("equal_residue_pair_count") or 0) > 2:
        return "C6"
    if (obs["analyze_neighbor_overlap"].get("noncolocated_gap_at_or_above_neg3db_count") or 0) > 2:
        return "C4"
    deficit = obs["analyze_coverage_geometry"].get("deepest_below_lobe_deficit_deg")
    if deficit is not None and deficit > 2.5:
        return "C1"
    return "C3"


def main() -> None:
    raw = json.loads(TRAIN.read_text(encoding="utf-8"))
    gated = residual = 0
    gate_ok = 0
    pres_ok = magn_ok = 0
    pres_by_gold: Counter[str] = Counter()
    magn_by_gold: Counter[str] = Counter()
    gold_total: Counter[str] = Counter()

    for row in raw:
        if split_for(question_group(row["question"])) != "train":
            continue
        case = parse_case(row["question"])
        obs = {name: TOOL_FUNCTIONS[name](case) for name in STAGE1}
        gold = row["answer"]
        g = gate_answer(obs)
        if g is not None:
            gated += 1
            gate_ok += (g == gold)
            continue
        residual += 1
        obs.update({name: TOOL_FUNCTIONS[name](case) for name in STAGE2})
        gold_total[gold] += 1
        thr = case.throughput_threshold_mbps
        p, m = presence_ladder(obs, thr), magnitude_ladder(obs, thr)
        pres_ok += (p == gold)
        magn_ok += (m == gold)
        pres_by_gold[gold] += (p == gold)
        magn_by_gold[gold] += (m == gold)

    total = gated + residual
    print(f"train cases            : {total}  (gated {gated}, residual {residual})")
    print(f"gate ladder on gated   : {gate_ok}/{gated} = {gate_ok/gated*100:.2f}%")
    print()
    print("On the residual half — obeying the ladder earns reward this often:")
    print(f"  presence  (GRPO prompt today) : {pres_ok}/{residual} = {pres_ok/residual*100:.2f}%")
    print(f"  magnitude (shipped specialist): {magn_ok}/{residual} = {magn_ok/residual*100:.2f}%")
    print()
    print("class  n   presence  magnitude")
    for lab in sorted(gold_total):
        n = gold_total[lab]
        print(f"{lab:5} {n:4}  {pres_by_gold[lab]:8} {magn_by_gold[lab]:10}")
    print()
    whole_pres = (gate_ok + pres_ok) / total * 100
    whole_magn = (gate_ok + magn_ok) / total * 100
    print(f"whole train split, obeying the prompt end to end:")
    print(f"  presence  : {gate_ok + pres_ok}/{total} = {whole_pres:.2f}%")
    print(f"  magnitude : {gate_ok + magn_ok}/{total} = {whole_magn:.2f}%")


if __name__ == "__main__":
    main()

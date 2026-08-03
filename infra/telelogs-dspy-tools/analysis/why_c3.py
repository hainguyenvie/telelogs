#!/usr/bin/env python3
"""Why does the magnitude ladder lose C3, and what separates C3 from C1?

The b40c48e diagnosis localises the entire C3 loss to one rule: rule 3
(`deepest_below_lobe_deficit_deg > 2.5 -> C1`) fires 79 times on official and is
right only 50 of them — the other 29 are gold C3. Rules 1, 2 and 4 run at
100% / 99.1% / 95.1%. So the question is not "why is C3 hard" in general; it is
specifically: when the serving cell IS geometrically below its main lobe AND a
neighbouring segment carries more throughput, which of the two is the cause?

The magnitude ladder dropped `advantage` (minimum_difference_mbps) entirely,
because the train-split tree found the three count/depth features sufficient.
This script tests whether that was the mistake, by asking how well `advantage`
separates gold C1 from gold C3 *inside the population rule 3 claims*.

Everything is fitted on the TRAIN split; official is reported afterwards for
description only, exactly as the shipped thresholds were. Nothing here is
proposed for the answer path without a fresh paired run.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from neutral_tools import (  # noqa: E402
    analyze_coverage_geometry,
    analyze_mobility,
    analyze_neighbor_overlap,
    analyze_pci_relations,
    analyze_radio_resources,
    analyze_throughput_segments,
    parse_case,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TRAIN_PATH = REPO_ROOT / "data/raw_train_2400/train.json"
OFFICIAL_PATH = REPO_ROOT / "data/official_test_864/test.json"

RESIDUE_PAIRS_MIN = 2
GAP_ROWS_MIN = 2
LOBE_DEFICIT_MIN = 2.5


def question_group(question: str) -> str:
    skeleton = re.sub(r"-?\d+(?:\.\d+)?", "#", question)
    return hashlib.sha256(re.sub(r"\s+", " ", skeleton).strip().encode()).hexdigest()[:16]


def split_for(group: str) -> str:
    bucket = int(group[:8], 16) % 100
    return "train" if bucket < 60 else ("dev" if bucket < 80 else "holdout")


def measure(question: str) -> dict | None:
    case = parse_case(question)
    geo = analyze_coverage_geometry(case)
    mobility = analyze_mobility(case)
    radio = analyze_radio_resources(case)
    throughput = analyze_throughput_segments(case)
    overlap = analyze_neighbor_overlap(case)
    pci = analyze_pci_relations(case)

    strong_c1 = any(row["throughput_mbps"] < case.throughput_threshold_mbps
                    and row["serving_rsrp_dbm"] <= -90.0
                    for row in geo["rows_below_main_lobe_lower_edge"])
    if ((geo["maximum_distance_km"] is not None and geo["maximum_distance_km"] > 1.0)
            or mobility["transition_count"] >= 3
            or mobility["maximum_speed_kmh"] > 40.0
            or (radio["low_throughput_rows_mean_scheduled_rbs"] is not None
                and radio["low_throughput_rows_mean_scheduled_rbs"] < 160.0)
            or strong_c1):
        return None
    cmp_ = throughput["segment_minimum_comparison"]
    return {
        "advantage": cmp_["minimum_difference_mbps"],
        "n_segments": len(throughput["segment_statistics"]),
        "w6_count": pci["equal_residue_pair_count"],
        "w4_count": overlap["noncolocated_gap_at_or_above_neg3db_count"],
        "w1_deficit": geo["deepest_below_lobe_deficit_deg"],
    }


def load(path: Path, split: str | None) -> list[tuple[dict, str]]:
    pool = []
    for row in json.loads(path.read_text()):
        if split is not None and split_for(question_group(row["question"])) != split:
            continue
        f = measure(row["question"])
        if f is not None:
            pool.append((f, row["answer"]))
    return pool


def rule3_population(pool):
    """Cases the shipped ladder hands to rule 3 (C1 by depth)."""
    out = []
    for f, gold in pool:
        if f["w6_count"] > RESIDUE_PAIRS_MIN or f["w4_count"] > GAP_ROWS_MIN:
            continue
        if f["w1_deficit"] is not None and f["w1_deficit"] > LOBE_DEFICIT_MIN:
            out.append((f, gold))
    return out


def describe(values, label):
    if not values:
        print(f"    {label}: none")
        return
    values = sorted(values)
    mid = len(values) // 2
    q1, q3 = values[len(values) // 4], values[3 * len(values) // 4]
    print(f"    {label}: n={len(values)} min={values[0]:.1f} q1={q1:.1f} "
          f"median={values[mid]:.1f} q3={q3:.1f} max={values[-1]:.1f}")


def main() -> None:
    train = load(TRAIN_PATH, "train")
    dev = load(TRAIN_PATH, "dev")
    holdout = load(TRAIN_PATH, "holdout")
    official = load(OFFICIAL_PATH, None)

    print("=" * 78)
    print("1. WHO LIVES IN RULE 3's POPULATION (the C1-by-depth rule)")
    print("=" * 78)
    for name, pool in (("train", train), ("dev", dev), ("holdout", holdout), ("official", official)):
        pop = rule3_population(pool)
        mix = Counter(g for _, g in pop)
        print(f"  {name:9s} rule-3 population n={len(pop):4d}  gold mix: {dict(mix.most_common())}")

    print()
    print("=" * 78)
    print("2. DOES `advantage` SEPARATE C1 FROM C3 INSIDE THAT POPULATION?")
    print("=" * 78)
    for name, pool in (("train", train), ("official", official)):
        pop = rule3_population(pool)
        print(f"  {name}:")
        describe([f["advantage"] for f, g in pop if g == "C1" and f["advantage"] is not None],
                 "gold C1 advantage (Mbps)")
        describe([f["advantage"] for f, g in pop if g == "C3" and f["advantage"] is not None],
                 "gold C3 advantage (Mbps)")
        describe([f["w1_deficit"] for f, g in pop if g == "C1"], "gold C1 lobe deficit (deg)")
        describe([f["w1_deficit"] for f, g in pop if g == "C3"], "gold C3 lobe deficit (deg)")

    print()
    print("=" * 78)
    print("3. BEST advantage CUT INSIDE RULE 3's POPULATION — FITTED ON TRAIN ONLY")
    print("=" * 78)
    pop_train = rule3_population(train)
    values = sorted({f["advantage"] for f, _ in pop_train if f["advantage"] is not None})
    cuts = [(a + b) / 2 for a, b in zip(values, values[1:])] or [0.0]
    best = None
    for cut in cuts:
        # inside rule 3's population: advantage >= cut -> C3, else C1
        ok = sum(1 for f, g in pop_train
                 if g == ("C3" if (f["advantage"] is not None and f["advantage"] >= cut) else "C1"))
        if best is None or ok > best[0]:
            best = (ok, cut)
    ok, cut = best
    print(f"  best train cut: advantage >= {cut:.1f} -> C3 (else C1)")
    print(f"  train: {ok}/{len(pop_train)} = {ok/len(pop_train)*100:.2f}% inside rule 3's population")
    print(f"  (rule 3 as shipped, i.e. always C1, scores "
          f"{sum(1 for _, g in pop_train if g == 'C1')}/{len(pop_train)} = "
          f"{sum(1 for _, g in pop_train if g == 'C1')/len(pop_train)*100:.2f}%)")

    print()
    print("=" * 78)
    print("4. WHAT THAT CUT WOULD DO TO THE WHOLE RESIDUAL ZONE (train-fitted cut)")
    print("=" * 78)

    def shipped(f):
        if f["w6_count"] > RESIDUE_PAIRS_MIN:
            return "C6"
        if f["w4_count"] > GAP_ROWS_MIN:
            return "C4"
        if f["w1_deficit"] is not None and f["w1_deficit"] > LOBE_DEFICIT_MIN:
            return "C1"
        return "C3"

    def proposed(f):
        if f["w6_count"] > RESIDUE_PAIRS_MIN:
            return "C6"
        if f["w4_count"] > GAP_ROWS_MIN:
            return "C4"
        if f["w1_deficit"] is not None and f["w1_deficit"] > LOBE_DEFICIT_MIN:
            return "C3" if (f["advantage"] is not None and f["advantage"] >= cut) else "C1"
        return "C3"

    for name, pool in (("train", train), ("dev", dev), ("holdout", holdout), ("official", official)):
        s = sum(1 for f, g in pool if shipped(f) == g)
        p = sum(1 for f, g in pool if proposed(f) == g)
        print(f"  {name:9s} shipped {s}/{len(pool)} = {s/len(pool)*100:.2f}%   "
              f"proposed {p}/{len(pool)} = {p/len(pool)*100:.2f}%   delta {p-s:+d}")

    print()
    print("  per-class on official (residual zone only):")
    pool = official
    for label in ("C1", "C3", "C4", "C6"):
        n = sum(1 for _, g in pool if g == label)
        s = sum(1 for f, g in pool if g == label and shipped(f) == g)
        p = sum(1 for f, g in pool if g == label and proposed(f) == g)
        print(f"    {label}: n={n:3d}  shipped {s:3d}  proposed {p:3d}  delta {p-s:+d}")


if __name__ == "__main__":
    main()

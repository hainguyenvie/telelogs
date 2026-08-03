#!/usr/bin/env python3
"""Fit the magnitude-based residual tie-break through the REAL tool functions.

`probe_headroom.py` showed the shipped presence-based ladder (289/376 = 76.86%
on the official residual pool) can reach far higher by replacing each yes/no
witness with the count/magnitude already latent in the same measurement,
fit on the train split only, using a small Gini-impurity decision tree (its
`build_tree`) over hand-reimplemented features. That probe bypassed
`neutral_tools.py`'s actual JSON output, so it is a research result about the
underlying data, not proof the shipped tools carry the distinction faithfully.

This script re-derives the same class of rule by calling the real, updated
tool functions (`analyze_pci_relations`, `analyze_neighbor_overlap`,
`analyze_coverage_geometry` — now exposing `equal_residue_pair_count`,
`noncolocated_gap_at_or_above_neg3db_count`, `deepest_below_lobe_deficit_deg`)
so a fitted threshold is guaranteed to mean exactly what the model will see.

An earlier version of this script tried a hand-fixed-order greedy sequential
threshold fit (pick each rule's own cut, in a chosen label order, to maximize
raw correct count among cases not yet claimed). That converged to 54-60% —
*worse* than the shipped presence rule — because maximizing one rule's raw
correct count in isolation ignores how the cut changes what is left over for
later rules; it is not the technique that produced the probe's numbers. This
version instead ports `probe_headroom.py`'s actual method: a small recursive
decision tree over the 4 numeric residual features, split by Gini impurity,
depth capped low enough to describe as a handful of if/else rules for the
specialist prompt. Fit on TRAIN only; dev/holdout/official are computed
afterwards for whichever depth is selected and never used to pick among them.

Sanity check built in: the shipped rule (C3 precheck @ 142.5, then presence
order C6>C4>C1, i.e. threshold "count/deficit > 0") is evaluated through this
same measure() pipeline and must reproduce 289/376 on the official residual
pool — if it does not, the new tool fields or this script's gate/measure
logic have a bug, and nothing below should be trusted until that is fixed.

Usage: python3 fit_magnitude_rule.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
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

NUMERIC = ["advantage", "w6_count", "w4_count", "w1_deficit"]
MIN_LEAF = 12
CAP_CUTS = 60


def question_group(question: str) -> str:
    skeleton = re.sub(r"-?\d+(?:\.\d+)?", "#", question)
    skeleton = re.sub(r"\s+", " ", skeleton).strip()
    return hashlib.sha256(skeleton.encode()).hexdigest()[:16]


def split_for(group: str) -> str:
    bucket = int(group[:8], 16) % 100
    return "train" if bucket < 60 else ("dev" if bucket < 80 else "holdout")


def measure(question: str) -> dict | None:
    """Run the real tools; return None for a gate case, else residual features."""
    case = parse_case(question)
    geo = analyze_coverage_geometry(case)
    mobility = analyze_mobility(case)
    radio = analyze_radio_resources(case)
    throughput = analyze_throughput_segments(case)
    overlap = analyze_neighbor_overlap(case)
    pci = analyze_pci_relations(case)

    strong_c1 = any(
        row["throughput_mbps"] < case.throughput_threshold_mbps and row["serving_rsrp_dbm"] <= -90.0
        for row in geo["rows_below_main_lobe_lower_edge"]
    )
    gate = (
        (geo["maximum_distance_km"] is not None and geo["maximum_distance_km"] > 1.0)
        or mobility["transition_count"] >= 3
        or mobility["maximum_speed_kmh"] > 40.0
        or (
            radio["low_throughput_rows_mean_scheduled_rbs"] is not None
            and radio["low_throughput_rows_mean_scheduled_rbs"] < 160.0
        )
        or strong_c1
    )
    if gate:
        return None
    advantage = throughput["segment_minimum_comparison"]["minimum_difference_mbps"]
    deficit = geo["deepest_below_lobe_deficit_deg"]
    return {
        "advantage": advantage if advantage is not None else -1.0,
        "w6_count": pci["equal_residue_pair_count"],
        "w4_count": overlap["noncolocated_gap_at_or_above_neg3db_count"],
        "w1_deficit": deficit if deficit is not None else -1.0,
    }


def load_pool(path: Path, split: str | None) -> list[tuple[dict, str]]:
    raw = json.loads(path.read_text())
    pool = []
    for row in raw:
        if split is not None and split_for(question_group(row["question"])) != split:
            continue
        f = measure(row["question"])
        if f is not None:
            pool.append((f, row["answer"]))
    return pool


# --- shipped-rule sanity check (presence order C3>C6>C4>C1) -----------------

def shipped_predict(f: dict) -> str:
    if f["advantage"] >= 142.5:
        return "C3"
    if f["w6_count"] > 0:
        return "C6"
    if f["w4_count"] > 0:
        return "C4"
    if f["w1_deficit"] > 0:
        return "C1"
    return "C3"


# --- Gini decision tree, ported from probe_headroom.py's build_tree ---------

def gini(labels: list[str]) -> float:
    n = len(labels)
    if not n:
        return 0.0
    counts = Counter(labels)
    return 1.0 - sum((c / n) ** 2 for c in counts.values())


def build_tree(rows: list[tuple[dict, str]], depth: int, min_leaf: int = MIN_LEAF):
    labels = [g for _, g in rows]
    node = {"pred": Counter(labels).most_common(1)[0][0], "n": len(rows)}
    if depth == 0 or len(set(labels)) == 1 or len(rows) < 2 * min_leaf:
        return node
    best = None
    for feat in NUMERIC:
        values = sorted({f[feat] for f, _ in rows})
        if len(values) < 2:
            continue
        cuts = [(a + b) / 2 for a, b in zip(values, values[1:])]
        if len(cuts) > CAP_CUTS:
            step = max(1, len(cuts) // CAP_CUTS)
            cuts = cuts[::step]
        for cut in cuts:
            left = [(f, g) for f, g in rows if f[feat] <= cut]
            right = [(f, g) for f, g in rows if f[feat] > cut]
            if len(left) < min_leaf or len(right) < min_leaf:
                continue
            score = (len(left) * gini([g for _, g in left])
                     + len(right) * gini([g for _, g in right])) / len(rows)
            if best is None or score < best[0]:
                best = (score, feat, cut, left, right)
    if best is None:
        return node
    _, feat, cut, left, right = best
    node["feat"], node["cut"] = feat, cut
    node["left"] = build_tree(left, depth - 1, min_leaf)
    node["right"] = build_tree(right, depth - 1, min_leaf)
    return node


def tree_pred(node, f: dict) -> str:
    while "feat" in node:
        node = node["left"] if f[node["feat"]] <= node["cut"] else node["right"]
    return node["pred"]


def describe(node, indent: str = "") -> list[str]:
    if "feat" not in node:
        return [f"{indent}-> {node['pred']}  (n={node['n']})"]
    out = [f"{indent}{node['feat']} <= {node['cut']:.4g} ?"]
    out += describe(node["left"], indent + "  ")
    out += [f"{indent}else:"]
    out += describe(node["right"], indent + "  ")
    return out


def accuracy(pool: list[tuple[dict, str]], predict) -> tuple[int, int]:
    correct = sum(1 for f, g in pool if predict(f) == g)
    return correct, len(pool)


def main() -> None:
    train_pool = load_pool(TRAIN_PATH, "train")
    dev_pool = load_pool(TRAIN_PATH, "dev")
    holdout_pool = load_pool(TRAIN_PATH, "holdout")
    official_pool = load_pool(OFFICIAL_PATH, None)
    print(f"train residual pool: {len(train_pool)}   dev: {len(dev_pool)}   "
          f"holdout: {len(holdout_pool)}   official: {len(official_pool)}")

    ok, n = accuracy(official_pool, shipped_predict)
    print(f"\nsanity check — shipped rule reproduced via real tools: {ok}/{n} = {ok/n*100:.2f}% "
          f"(report value: 289/376 = 76.86%)")
    if (ok, n) != (289, 376):
        print("  MISMATCH — investigate before trusting anything below.")
        return

    print("\ndecision tree fit on TRAIN residual pool only, evaluated per depth:")
    for depth in (2, 3, 4, 5):
        tree = build_tree(train_pool, depth)
        rows = []
        for name, pool in (("train", train_pool), ("dev", dev_pool),
                           ("holdout", holdout_pool), ("official", official_pool)):
            c, n = accuracy(pool, lambda f: tree_pred(tree, f))
            rows.append(f"{name}={c}/{n}={c/n*100:.2f}%")
        print(f"\n  depth {depth}: " + "  ".join(rows))
        print("  tree:")
        print("\n".join("    " + line for line in describe(tree)))


if __name__ == "__main__":
    main()

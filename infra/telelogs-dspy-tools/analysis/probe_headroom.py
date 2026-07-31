#!/usr/bin/env python3
"""Is the residual ceiling a property of the RULE FAMILY, or of the measurements?

Three probes, all fitted on the TRAIN split only and evaluated on the official
test residual zone, so the numbers are comparable to the shipped 289/376:

  1. ordered presence tests  — the family in use (baseline)
  2. full lookup table       — the best possible function of the four BINARY
                               witnesses; beats an order only where the majority
                               inside a witness pattern differs from the order's pick
  3. shallow decision tree   — allowed to use the MAGNITUDES the tools already
                               return (how large the gap is, how many mod-30 pairs,
                               how far below the lobe, ...) instead of a yes/no

If (3) clears (2) by a real margin, the information needed to separate the
co-occurring causes is present in the existing measurements and the fix is a new
exposed quantity. If it does not, the six tools do not carry the distinction and
no decision procedure over them can.

Nothing here is proposed for the answer path: it is a feature-discovery probe.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(os.environ.get("TL_ROOT", "/workspace/telelogs-bench4/dspy-tools"))
sys.path.insert(0, str(ROOT / "code"))
from neutral_tools import parse_case  # noqa: E402

THRESHOLD = 142.5


def question_group(question: str) -> str:
    skeleton = re.sub(r"\s+", " ", re.sub(r"-?\d+(?:\.\d+)?", "#", question)).strip()
    return hashlib.sha256(skeleton.encode()).hexdigest()[:16]


def split_for(question: str) -> str:
    bucket = int(question_group(question)[:8], 16) % 100
    return "train" if bucket < 60 else ("dev" if bucket < 80 else "holdout")


def rich(question: str) -> dict | None:
    """Every quantity the six tools already compute, kept as a magnitude."""
    case = parse_case(question)
    obs = case.observations
    affected = [r for r in obs if r["throughput_mbps"] < case.throughput_threshold_mbps]
    if not affected:
        affected = obs
    distances = [r["distance_km"] for r in obs if r["distance_km"] is not None]
    transitions = sum(1 for p, c in zip(obs, obs[1:]) if p["serving_pci"] != c["serving_pci"])
    by_pci = defaultdict(list)
    for r in obs:
        by_pci[r["serving_pci"]].append(r["throughput_mbps"])
    minima = {p: min(v) for p, v in by_pci.items()}
    lowest = min(minima, key=lambda p: minima[p])
    others = {p: v for p, v in minima.items() if p != lowest}
    advantage = (max(others.values()) - minima[lowest]) if others else None

    gaps = [n["brsrp_minus_serving_rsrp_db"] for r in affected for n in r["neighbors"]
            if not n["same_gnodeb"] and n["brsrp_minus_serving_rsrp_db"] is not None]
    residue_rows = [r for r in affected
                    if any(n["serving_mod30"] == n["neighbor_mod30"] for n in r["neighbors"])]
    residue_pairs = sum(1 for r in affected for n in r["neighbors"]
                        if n["serving_mod30"] == n["neighbor_mod30"])
    below = [r for r in affected
             if r["elevation_deg"] is not None and r["elevation_deg"] < r["main_lobe_lower_deg"]]
    deficits = [r["main_lobe_lower_deg"] - r["elevation_deg"] for r in below]
    rsrps = [r["serving_rsrp_dbm"] for r in affected]

    gate = (bool(distances and max(distances) > 1.0) or transitions >= 3
            or max(r["speed_kmh"] for r in obs) > 40.0
            or (bool(affected) and sum(r["scheduled_rbs"] for r in affected) / len(affected) < 160.0)
            or any(r["elevation_deg"] is not None and r["elevation_deg"] < r["main_lobe_lower_deg"]
                   and r["serving_rsrp_dbm"] <= -90.0 for r in affected))
    if gate:
        return None
    return {
        # the four binary witnesses the shipped ladder uses
        "w3": advantage is not None and advantage >= THRESHOLD,
        "w6": residue_pairs > 0,
        "w4": bool(gaps) and max(gaps) >= -3.0,
        "w1": bool(below),
        # magnitudes behind them
        "advantage": advantage if advantage is not None else -1.0,
        "best_gap": max(gaps) if gaps else -99.0,
        "mean_gap": (sum(gaps) / len(gaps)) if gaps else -99.0,
        "n_gap_strong": sum(1 for g in gaps if g >= -3.0),
        "residue_pairs": residue_pairs,
        "residue_row_frac": len(residue_rows) / len(affected),
        "below_frac": len(below) / len(affected),
        "max_deficit": max(deficits) if deficits else 0.0,
        "min_rsrp": min(rsrps),
        "mean_rsrp": sum(rsrps) / len(rsrps),
        "n_segments": len(by_pci),
        "n_affected": len(affected),
        "affected_frac": len(affected) / len(obs),
        "max_distance": max(distances) if distances else 0.0,
        "worst_throughput": min(r["throughput_mbps"] for r in affected),
    }


def ladder(f: dict) -> str:
    if f["w3"]:
        return "C3"
    if f["w6"]:
        return "C6"
    if f["w4"]:
        return "C4"
    if f["w1"]:
        return "C1"
    return "C3"


def pattern(f: dict) -> tuple:
    return (f["w3"], f["w6"], f["w4"], f["w1"])


NUMERIC = ["advantage", "best_gap", "mean_gap", "n_gap_strong", "residue_pairs",
           "residue_row_frac", "below_frac", "max_deficit", "min_rsrp", "mean_rsrp",
           "n_segments", "n_affected", "affected_frac", "max_distance", "worst_throughput"]
BOOLS = ["w3", "w6", "w4", "w1"]


def gini(labels: list[str]) -> float:
    n = len(labels)
    if not n:
        return 0.0
    counts = Counter(labels)
    return 1.0 - sum((c / n) ** 2 for c in counts.values())


def build_tree(rows: list[tuple[dict, str]], depth: int, min_leaf: int = 12):
    labels = [g for _, g in rows]
    node = {"pred": Counter(labels).most_common(1)[0][0], "n": len(rows)}
    if depth == 0 or len(set(labels)) == 1 or len(rows) < 2 * min_leaf:
        return node
    best = None
    for feat in NUMERIC + BOOLS:
        values = sorted({float(f[feat]) for f, _ in rows})
        if len(values) < 2:
            continue
        cuts = [(a + b) / 2 for a, b in zip(values, values[1:])]
        if len(cuts) > 60:  # keep the search cheap and the tree coarse
            step = len(cuts) // 60
            cuts = cuts[::step]
        for cut in cuts:
            left = [(f, g) for f, g in rows if float(f[feat]) <= cut]
            right = [(f, g) for f, g in rows if float(f[feat]) > cut]
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


def tree_pred(node, f) -> str:
    while "feat" in node:
        node = node["left"] if float(f[node["feat"]]) <= node["cut"] else node["right"]
    return node["pred"]


def describe(node, indent: str = "") -> list[str]:
    if "feat" not in node:
        return [f"{indent}→ {node['pred']}  (n={node['n']})"]
    out = [f"{indent}{node['feat']} <= {node['cut']:.4g} ?"]
    out += describe(node["left"], indent + "  ")
    out += [f"{indent}else:"]
    out += describe(node["right"], indent + "  ")
    return out


def main() -> None:
    import duckdb

    train_raw = json.loads((ROOT / "data/train.json").read_text())
    test_path = glob.glob(os.environ.get(
        "TL_PARQUET_GLOB",
        "/workspace/telelogs/cache/hf/hub/datasets--GSMA--ot-full/snapshots/*/telelogs/test-*.parquet"))[0]
    test_raw = [{"question": q, "answer": a} for q, a in duckdb.sql(
        f"SELECT question, answer FROM read_parquet('{test_path}')").fetchall()]

    train = []
    for r in train_raw:
        if split_for(r["question"]) != "train":
            continue
        f = rich(r["question"])
        if f is not None:
            train.append((f, r["answer"]))
    test = []
    for r in test_raw:
        f = rich(r["question"])
        if f is not None:
            test.append((f, str(r["answer"])))
    print(f"train residual pool: {len(train)}   official residual pool: {len(test)}")

    base = sum(ladder(f) == g for f, g in test)
    print(f"\n1. ordered presence tests (shipped)      {base}/{len(test)} = {base/len(test)*100:.2f}%")

    table = {}
    for pat, group in ((p, [g for f, g in train if pattern(f) == p])
                       for p in {pattern(f) for f, _ in train}):
        table[pat] = Counter(group).most_common(1)[0][0]
    look = sum(table.get(pattern(f), ladder(f)) == g for f, g in test)
    print(f"2. full lookup over the 4 binary witnesses {look}/{len(test)} = {look/len(test)*100:.2f}%")

    for depth in (2, 3, 4, 5):
        tree = build_tree(train, depth)
        ok = sum(tree_pred(tree, f) == g for f, g in test)
        print(f"3. decision tree on magnitudes, depth {depth}  {ok}/{len(test)} = {ok/len(test)*100:.2f}%")
        if depth == 4:
            print("\n   the depth-4 tree it learned:")
            print("\n".join("   " + line for line in describe(tree)))


if __name__ == "__main__":
    main()

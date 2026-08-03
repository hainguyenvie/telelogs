#!/usr/bin/env python3
"""How separable are C1 and C3 really, inside rule 3's population?

A single `advantage >= 106.7` cut takes that population from 63.3% to 78.5% on
official — real, but it still leaves 17 of 79 wrong, so the obvious question is
whether the residue is irreducible or whether a better-posed measurement exists.

This script tests that directly. It builds a wider feature set out of quantities
the six tools already compute (or trivially could), and asks, on the TRAIN
population only:

  1. how well each single feature separates C1 from C3 (best achievable
     single-threshold accuracy, plus AUC so the ranking does not depend on
     where the cut lands);
  2. what a small 2-feature rule buys over the best single one;
  3. whether the residue that survives is genuinely ambiguous — i.e. do C1 and
     C3 cases exist that are identical on every measured feature.

Nothing is fitted on dev/holdout/official; those are printed afterwards for the
train-selected rule only, exactly as the shipped thresholds were.
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

RESIDUE_PAIRS_MIN = 2
GAP_ROWS_MIN = 2
LOBE_DEFICIT_MIN = 2.5


def question_group(q: str) -> str:
    s = re.sub(r"-?\d+(?:\.\d+)?", "#", q)
    return hashlib.sha256(re.sub(r"\s+", " ", s).strip().encode()).hexdigest()[:16]


def split_for(g: str) -> str:
    b = int(g[:8], 16) % 100
    return "train" if b < 60 else ("dev" if b < 80 else "holdout")


def measure(question: str) -> dict | None:
    case = parse_case(question)
    geo = analyze_coverage_geometry(case)
    mob = analyze_mobility(case)
    rad = analyze_radio_resources(case)
    thr = analyze_throughput_segments(case)
    ovl = analyze_neighbor_overlap(case)
    pci = analyze_pci_relations(case)
    thresh = case.throughput_threshold_mbps

    below = geo["rows_below_main_lobe_lower_edge"]
    below_affected = [r for r in below if r["throughput_mbps"] < thresh]
    strong_c1 = any(r["serving_rsrp_dbm"] <= -90.0 for r in below_affected)
    if ((geo["maximum_distance_km"] is not None and geo["maximum_distance_km"] > 1.0)
            or mob["transition_count"] >= 3 or mob["maximum_speed_kmh"] > 40.0
            or (rad["low_throughput_rows_mean_scheduled_rbs"] is not None
                and rad["low_throughput_rows_mean_scheduled_rbs"] < 160.0)
            or strong_c1):
        return None

    cmp_ = thr["segment_minimum_comparison"]
    adv = cmp_["minimum_difference_mbps"]
    lowest_min = cmp_["lowest_minimum_mbps"]
    best_other = cmp_["best_other_minimum_mbps"]
    affected = thr["low_throughput_rows"]
    all_rows = geo["rows"]
    rsrps_below = [r["serving_rsrp_dbm"] for r in below_affected]

    return {
        # the discriminator currently proposed
        "advantage": adv if adv is not None else -1.0,
        # relative forms of the same thing
        "advantage_ratio": (best_other / lowest_min) if (best_other and lowest_min) else 1.0,
        "best_other_min": best_other if best_other is not None else -1.0,
        "lowest_min": lowest_min if lowest_min is not None else -1.0,
        # does the alternative actually clear the criterion the question asks about?
        "best_other_clears_criterion": 1.0 if (best_other is not None and best_other >= thresh) else 0.0,
        "n_segments": float(len(thr["segment_statistics"])),
        # geometry depth / extent
        "lobe_deficit": geo["deepest_below_lobe_deficit_deg"] or 0.0,
        "below_frac": len(below_affected) / max(1, len(affected)),
        "n_below": float(len(below_affected)),
        # signal level on the below-lobe rows (C1's physical signature)
        "min_rsrp_below": min(rsrps_below) if rsrps_below else 0.0,
        "mean_rsrp_below": (sum(rsrps_below) / len(rsrps_below)) if rsrps_below else 0.0,
        # severity of the degradation itself
        "worst_throughput": min((r["throughput_mbps"] for r in affected), default=thresh),
        "affected_frac": len(affected) / max(1, len(all_rows)),
        "criterion": thresh,
        # leftover competing witnesses, below their own thresholds
        "w6_count": float(pci["equal_residue_pair_count"]),
        "w4_count": float(ovl["noncolocated_gap_at_or_above_neg3db_count"]),
    }


def load_pop(path: Path, split: str | None) -> list[tuple[dict, str]]:
    pop = []
    for row in json.loads(path.read_text()):
        if split is not None and split_for(question_group(row["question"])) != split:
            continue
        f = measure(row["question"])
        if f is None:
            continue
        if f["w6_count"] > RESIDUE_PAIRS_MIN or f["w4_count"] > GAP_ROWS_MIN:
            continue
        if f["lobe_deficit"] <= LOBE_DEFICIT_MIN:
            continue
        if row["answer"] in ("C1", "C3"):
            pop.append((f, row["answer"]))
    return pop


FEATURES = ["advantage", "advantage_ratio", "best_other_min", "lowest_min",
            "best_other_clears_criterion", "n_segments", "lobe_deficit", "below_frac",
            "n_below", "min_rsrp_below", "mean_rsrp_below", "worst_throughput",
            "affected_frac", "w6_count", "w4_count"]


def best_cut(pop, feat):
    """Best single threshold: above -> C3, below -> C1 (or the flipped polarity)."""
    vals = sorted({f[feat] for f, _ in pop})
    cuts = [(a + b) / 2 for a, b in zip(vals, vals[1:])]
    best = (0, None, None)
    for cut in cuts:
        for hi, lo in (("C3", "C1"), ("C1", "C3")):
            ok = sum(1 for f, g in pop if g == (hi if f[feat] > cut else lo))
            if ok > best[0]:
                best = (ok, cut, hi)
    return best


def auc(pop, feat):
    """P(random C3 ranks above random C1) — threshold-free separability."""
    c3 = [f[feat] for f, g in pop if g == "C3"]
    c1 = [f[feat] for f, g in pop if g == "C1"]
    if not c3 or not c1:
        return 0.5
    wins = sum((a > b) + 0.5 * (a == b) for a in c3 for b in c1)
    return wins / (len(c3) * len(c1))


def main() -> None:
    train = load_pop(TRAIN_PATH, "train")
    dev = load_pop(TRAIN_PATH, "dev")
    holdout = load_pop(TRAIN_PATH, "holdout")
    official = load_pop(OFFICIAL_PATH, None)
    base = Counter(g for _, g in train)
    print(f"rule-3 population (C1/C3 only) — train {len(train)} {dict(base)}, "
          f"dev {len(dev)}, holdout {len(holdout)}, official {len(official)}")
    majority = max(base.values()) / len(train)
    print(f"train majority baseline ('always C1'): {majority*100:.1f}%\n")

    print("=" * 76)
    print("1. SINGLE FEATURES, RANKED BY TRAIN SEPARABILITY (AUC, threshold-free)")
    print("=" * 76)
    rows = []
    for feat in FEATURES:
        ok, cut, hi = best_cut(train, feat)
        a = auc(train, feat)
        rows.append((abs(a - 0.5), a, ok / len(train), feat, cut, hi))
    rows.sort(reverse=True)
    print(f"  {'feature':<30}{'AUC':>7}{'best train acc':>16}   rule")
    for _, a, acc, feat, cut, hi in rows:
        if cut is None:   # constant inside this population — carries no information
            print(f"  {feat:<30}{a:>7.3f}{acc*100:>15.1f}%   (constant here — no usable cut)")
            continue
        lo = "C1" if hi == "C3" else "C3"
        print(f"  {feat:<30}{a:>7.3f}{acc*100:>15.1f}%   {feat} > {cut:.4g} -> {hi}, else {lo}")

    print()
    print("=" * 76)
    print("2. BEST 2-FEATURE RULE ON TRAIN (exhaustive over pairs and both cuts)")
    print("=" * 76)
    best2 = None
    for i, fa in enumerate(FEATURES):
        va = sorted({f[fa] for f, _ in train})
        ca = [(x + y) / 2 for x, y in zip(va, va[1:])]
        if len(ca) > 40:
            ca = ca[:: max(1, len(ca) // 40)]
        for fb in FEATURES[i + 1:]:
            vb = sorted({f[fb] for f, _ in train})
            cb = [(x + y) / 2 for x, y in zip(vb, vb[1:])]
            if len(cb) > 40:
                cb = cb[:: max(1, len(cb) // 40)]
            for x in ca:
                for y in cb:
                    # C3 iff (fa > x) OR (fb > y), and the AND variant
                    for mode in ("or", "and"):
                        ok = 0
                        for f, g in train:
                            a_, b_ = f[fa] > x, f[fb] > y
                            hit = (a_ or b_) if mode == "or" else (a_ and b_)
                            ok += g == ("C3" if hit else "C1")
                        if best2 is None or ok > best2[0]:
                            best2 = (ok, fa, x, fb, y, mode)
    ok, fa, x, fb, y, mode = best2
    print(f"  best pair: C3 iff ({fa} > {x:.4g}) {mode.upper()} ({fb} > {y:.4g})")
    print(f"  train {ok}/{len(train)} = {ok/len(train)*100:.1f}%")

    single_ok, single_cut, single_hi = best_cut(train, "advantage")
    print(f"  (single advantage cut on train: {single_ok}/{len(train)} = "
          f"{single_ok/len(train)*100:.1f}% at advantage > {single_cut:.4g})")

    print()
    print("=" * 76)
    print("3. DOES EITHER RULE TRANSFER? (train-fitted, evaluated everywhere)")
    print("=" * 76)

    def apply_single(f):
        return "C3" if f["advantage"] > single_cut else "C1"

    def apply_pair(f):
        a_, b_ = f[fa] > x, f[fb] > y
        return "C3" if ((a_ or b_) if mode == "or" else (a_ and b_)) else "C1"

    print(f"  {'split':<10}{'n':>5}{'always C1':>12}{'advantage cut':>16}{'2-feature':>12}")
    for name, pool in (("train", train), ("dev", dev), ("holdout", holdout), ("official", official)):
        if not pool:
            continue
        n = len(pool)
        maj = sum(1 for _, g in pool if g == "C1")
        s = sum(1 for f, g in pool if apply_single(f) == g)
        p = sum(1 for f, g in pool if apply_pair(f) == g)
        print(f"  {name:<10}{n:>5}{maj/n*100:>11.1f}%{s/n*100:>15.1f}%{p/n*100:>11.1f}%")

    print()
    print("=" * 76)
    print("4. IS THE RESIDUE IRREDUCIBLE? nearest-neighbour check on train")
    print("=" * 76)
    print("  For each case, find the closest case of the OPPOSITE class in the")
    print("  normalised feature space, and report how far it is. Near-zero distance")
    print("  means two cases look identical to every tool and carry different labels.")
    lo = {f: min(x[f] for x, _ in train) for f in FEATURES}
    hi_ = {f: max(x[f] for x, _ in train) for f in FEATURES}
    rng = {f: (hi_[f] - lo[f]) or 1.0 for f in FEATURES}

    def dist(a, b):
        return sum(((a[f] - b[f]) / rng[f]) ** 2 for f in FEATURES) ** 0.5

    collisions = 0
    dists = []
    for f_, g_ in train:
        d = min(dist(f_, o) for o, og in train if og != g_)
        dists.append(d)
        collisions += d < 0.05
    dists.sort()
    print(f"  median distance to nearest opposite-class case: {dists[len(dists)//2]:.3f}")
    print(f"  cases with an opposite-class twin closer than 0.05: {collisions}/{len(train)}")
    print(f"  10th percentile distance: {dists[len(dists)//10]:.3f}")


if __name__ == "__main__":
    main()

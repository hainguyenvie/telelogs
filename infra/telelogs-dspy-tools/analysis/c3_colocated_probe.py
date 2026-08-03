#!/usr/bin/env python3
"""Does a CO-LOCATED measurement separate C1 from C3 where `advantage` cannot?

c3_confounding.py established the defect: `advantage` compares the worst
segment's minimum throughput against the best other segment's, and in 100% of
cases those segments cover disjoint stretches of road. A higher minimum
elsewhere is therefore consistent with both "that cell is genuinely better"
(C3) and "that stretch is simply easier" (C1 untouched).

The well-posed replacement is a question asked AT the affected rows themselves:

    the PCI that achieved the higher minimum on the other stretch — is it
    visible, and is it stronger than the serving cell, HERE?

If that specific cell is present and stronger at the exact rows where
throughput collapsed, it genuinely could have served them, and "a neighbouring
cell provides higher throughput" is a claim about this location rather than a
different one. If it is absent or weak here, the segment comparison was
confounded and the serving cell's own geometry is the remaining explanation.

Every quantity below is per-row at the affected rows, computed from fields the
parser already exposes (`neighbors[].brsrp_minus_serving_rsrp_db`), and none of
them filters on same_gnodeb: for C3 a co-located sector is a perfectly good
alternative server, unlike C4 where non-colocation is part of the definition.

Fitted and ranked on TRAIN only; dev/holdout/official follow for the
train-selected rule. If nothing here separates the two classes materially
better than `advantage` does, that is itself the finding: the drive-test data
does not contain the comparison this diagnosis requires, and no rule, prompt or
model can recover it.
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
    thresh = case.throughput_threshold_mbps
    geo = analyze_coverage_geometry(case)
    mob = analyze_mobility(case)
    rad = analyze_radio_resources(case)
    seg = analyze_throughput_segments(case)
    ovl = analyze_neighbor_overlap(case)
    pci = analyze_pci_relations(case)

    below = [r for r in geo["rows_below_main_lobe_lower_edge"] if r["throughput_mbps"] < thresh]
    if ((geo["maximum_distance_km"] is not None and geo["maximum_distance_km"] > 1.0)
            or mob["transition_count"] >= 3 or mob["maximum_speed_kmh"] > 40.0
            or (rad["low_throughput_rows_mean_scheduled_rbs"] is not None
                and rad["low_throughput_rows_mean_scheduled_rbs"] < 160.0)
            or any(r["serving_rsrp_dbm"] <= -90.0 for r in below)):
        return None
    if pci["equal_residue_pair_count"] > RESIDUE_PAIRS_MIN:
        return None
    if ovl["noncolocated_gap_at_or_above_neg3db_count"] > GAP_ROWS_MIN:
        return None
    deficit = geo["deepest_below_lobe_deficit_deg"]
    if deficit is None or deficit <= LOBE_DEFICIT_MIN:
        return None

    cmp_ = seg["segment_minimum_comparison"]
    better_pci = cmp_["best_other_minimum_pci"]
    affected = [r for r in case.observations if r["throughput_mbps"] < thresh]
    if not affected or better_pci is None:
        return None

    # --- the co-located question, asked at the affected rows only ---
    # gaps of the specific PCI that did better on the other stretch
    better_gaps = []
    rows_with_better_visible = 0
    for row in affected:
        hit = next((n for n in row["neighbors"] if n["pci"] == better_pci
                    and n["brsrp_minus_serving_rsrp_db"] is not None), None)
        if hit is not None:
            rows_with_better_visible += 1
            better_gaps.append(hit["brsrp_minus_serving_rsrp_db"])
    # gaps of ANY neighbour (co-located or not) at the affected rows
    any_gaps = [n["brsrp_minus_serving_rsrp_db"] for row in affected for n in row["neighbors"]
                if n["brsrp_minus_serving_rsrp_db"] is not None]
    rows_with_stronger_any = sum(
        1 for row in affected
        if any(n["brsrp_minus_serving_rsrp_db"] is not None and n["brsrp_minus_serving_rsrp_db"] > 0
               for n in row["neighbors"]))

    return {
        # --- co-located candidates (new) ---
        "better_pci_visible_frac": rows_with_better_visible / len(affected),
        "better_pci_gap_max": max(better_gaps) if better_gaps else -99.0,
        "better_pci_gap_mean": (sum(better_gaps) / len(better_gaps)) if better_gaps else -99.0,
        "better_pci_ever_stronger": 1.0 if any(g > 0 for g in better_gaps) else 0.0,
        "any_neighbor_gap_max": max(any_gaps) if any_gaps else -99.0,
        "any_neighbor_gap_mean": (sum(any_gaps) / len(any_gaps)) if any_gaps else -99.0,
        "rows_with_stronger_neighbor": float(rows_with_stronger_any),
        "frac_rows_stronger_neighbor": rows_with_stronger_any / len(affected),
        # --- the incumbent, for comparison ---
        "advantage": cmp_["minimum_difference_mbps"] if cmp_["minimum_difference_mbps"] is not None else -1.0,
    }


CO_LOCATED = ["better_pci_visible_frac", "better_pci_gap_max", "better_pci_gap_mean",
              "better_pci_ever_stronger", "any_neighbor_gap_max", "any_neighbor_gap_mean",
              "rows_with_stronger_neighbor", "frac_rows_stronger_neighbor"]
ALL_FEATURES = CO_LOCATED + ["advantage"]


def load_pop(path: Path, split: str | None) -> list[tuple[dict, str]]:
    pop = []
    for row in json.loads(path.read_text()):
        if split is not None and split_for(question_group(row["question"])) != split:
            continue
        if row["answer"] not in ("C1", "C3"):
            continue
        f = measure(row["question"])
        if f is not None:
            pop.append((f, row["answer"]))
    return pop


def best_cut(pop, feat):
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
    c3 = [f[feat] for f, g in pop if g == "C3"]
    c1 = [f[feat] for f, g in pop if g == "C1"]
    if not c3 or not c1:
        return 0.5
    return sum((a > b) + 0.5 * (a == b) for a in c3 for b in c1) / (len(c3) * len(c1))


def main() -> None:
    train = load_pop(TRAIN_PATH, "train")
    dev = load_pop(TRAIN_PATH, "dev")
    holdout = load_pop(TRAIN_PATH, "holdout")
    official = load_pop(OFFICIAL_PATH, None)
    mix = Counter(g for _, g in train)
    print(f"rule-3 population: train {len(train)} {dict(mix)}, dev {len(dev)}, "
          f"holdout {len(holdout)}, official {len(official)}")
    print(f"train majority ('always C1'): {max(mix.values())/len(train)*100:.1f}%\n")

    print("=" * 78)
    print("1. IS THE BETTER-PERFORMING CELL EVEN VISIBLE AT THE AFFECTED ROWS?")
    print("=" * 78)
    for name, pool in (("train", train), ("official", official)):
        for label in ("C1", "C3"):
            grp = [f for f, g in pool if g == label]
            if not grp:
                continue
            seen = sum(1 for f in grp if f["better_pci_visible_frac"] > 0)
            allrows = sum(1 for f in grp if f["better_pci_visible_frac"] >= 0.999)
            stronger = sum(1 for f in grp if f["better_pci_ever_stronger"] > 0)
            print(f"  {name:9s} {label}: n={len(grp):3d}  visible on >=1 affected row: "
                  f"{seen:3d} ({seen/len(grp)*100:5.1f}%)  on ALL rows: {allrows:3d}  "
                  f"ever stronger than serving: {stronger:3d} ({stronger/len(grp)*100:5.1f}%)")

    print()
    print("=" * 78)
    print("2. SEPARABILITY OF EVERY CO-LOCATED CANDIDATE (train-fitted)")
    print("=" * 78)
    rows = []
    for feat in ALL_FEATURES:
        ok, cut, hi = best_cut(train, feat)
        a = auc(train, feat)
        rows.append((abs(a - 0.5), a, ok / len(train), feat, cut, hi))
    rows.sort(reverse=True)
    print(f"  {'feature':<32}{'AUC':>7}{'best train acc':>16}   rule")
    for _, a, acc, feat, cut, hi in rows:
        mark = "  <-- incumbent" if feat == "advantage" else ""
        if cut is None:
            print(f"  {feat:<32}{a:>7.3f}{acc*100:>15.1f}%   (constant){mark}")
            continue
        lo = "C1" if hi == "C3" else "C3"
        print(f"  {feat:<32}{a:>7.3f}{acc*100:>15.1f}%   > {cut:.4g} -> {hi}, else {lo}{mark}")

    print()
    print("=" * 78)
    print("3. DOES THE BEST CO-LOCATED FEATURE TRANSFER?")
    print("=" * 78)
    best_co = max(CO_LOCATED, key=lambda f: abs(auc(train, f) - 0.5))
    ok_co, cut_co, hi_co = best_cut(train, best_co)
    ok_ad, cut_ad, hi_ad = best_cut(train, "advantage")
    print(f"  best co-located feature on train: {best_co} > {cut_co:.4g} -> {hi_co}")
    print(f"  incumbent:                        advantage > {cut_ad:.4g} -> {hi_ad}\n")
    print(f"  {'split':<10}{'n':>5}{'always C1':>12}{'advantage':>12}{best_co:>30}")
    for name, pool in (("train", train), ("dev", dev), ("holdout", holdout), ("official", official)):
        if not pool:
            continue
        n = len(pool)
        maj = sum(1 for _, g in pool if g == "C1") / n
        a = sum(1 for f, g in pool if g == (hi_ad if f["advantage"] > cut_ad
                                            else ("C1" if hi_ad == "C3" else "C3"))) / n
        c = sum(1 for f, g in pool if g == (hi_co if f[best_co] > cut_co
                                            else ("C1" if hi_co == "C3" else "C3"))) / n
        print(f"  {name:<10}{n:>5}{maj*100:>11.1f}%{a*100:>11.1f}%{c*100:>29.1f}%")

    print()
    print("=" * 78)
    print("4. DOES ANY CO-LOCATED FEATURE ADD TO `advantage`? (train-fitted pairs)")
    print("=" * 78)
    base_ok = ok_ad
    print(f"  advantage alone on train: {base_ok}/{len(train)} = {base_ok/len(train)*100:.1f}%")
    best_pair = None
    for feat in CO_LOCATED:
        vals = sorted({f[feat] for f, _ in train})
        cuts = [(a + b) / 2 for a, b in zip(vals, vals[1:])] or [0.0]
        if len(cuts) > 50:
            cuts = cuts[:: max(1, len(cuts) // 50)]
        for cut in cuts:
            for mode in ("or", "and"):
                for polarity in (True, False):
                    ok = 0
                    for f, g in train:
                        a_ = f["advantage"] > cut_ad
                        b_ = (f[feat] > cut) if polarity else (f[feat] <= cut)
                        hit = (a_ or b_) if mode == "or" else (a_ and b_)
                        ok += g == ("C3" if hit else "C1")
                    if best_pair is None or ok > best_pair[0]:
                        best_pair = (ok, feat, cut, mode, polarity)
    ok, feat, cut, mode, polarity = best_pair
    op = ">" if polarity else "<="
    print(f"  best combination: C3 iff (advantage > {cut_ad:.4g}) {mode.upper()} ({feat} {op} {cut:.4g})")
    print(f"  train {ok}/{len(train)} = {ok/len(train)*100:.1f}%  "
          f"(gain over advantage alone: {ok - base_ok:+d} cases)")
    print("\n  transfer of that combination:")
    print(f"  {'split':<10}{'n':>5}{'advantage':>12}{'combined':>12}")
    for name, pool in (("train", train), ("dev", dev), ("holdout", holdout), ("official", official)):
        if not pool:
            continue
        n = len(pool)
        a = sum(1 for f, g in pool if g == ("C3" if f["advantage"] > cut_ad else "C1")) / n
        c = 0
        for f, g in pool:
            a_ = f["advantage"] > cut_ad
            b_ = (f[feat] > cut) if polarity else (f[feat] <= cut)
            hit = (a_ or b_) if mode == "or" else (a_ and b_)
            c += g == ("C3" if hit else "C1")
        print(f"  {name:<10}{n:>5}{a*100:>11.1f}%{c/n*100:>11.1f}%")


if __name__ == "__main__":
    main()

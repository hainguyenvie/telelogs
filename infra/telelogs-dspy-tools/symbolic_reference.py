"""Symbolic reference classifier for the tools track.

Runs the old hybrid's decision flow (exact gates -> strong-C1 witness ->
calibrated residual tie-break) in pure Python over neutral_tools, and refits
the residual order/threshold on the hash-split TRAIN population only. Used as
an audit/ceiling reference, never as an answer path. 2026-07-29 result: gates
874/874 on train; best residual config C6>C4>C1 at 142.5 (74.96% train
residual); the resulting classifier scores 93/96 = 96.88% on dev-96.

Usage: python3 symbolic_reference.py  (expects the repo's raw train.json)
"""
import hashlib
import itertools
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from neutral_tools import parse_case

REPO_ROOT = Path(__file__).resolve().parents[2]

LABELS = tuple(f"C{i}" for i in range(1, 9))


def question_group(question: str) -> str:
    skeleton = re.sub(r"-?\d+(?:\.\d+)?", "#", question)
    skeleton = re.sub(r"\s+", " ", skeleton).strip()
    return hashlib.sha256(skeleton.encode()).hexdigest()[:16]


def split_for(group: str) -> str:
    bucket = int(group[:8], 16) % 100
    return "train" if bucket < 60 else ("dev" if bucket < 80 else "holdout")


def features(question: str) -> dict:
    case = parse_case(question)
    obs = case.observations
    affected = [r for r in obs if r["throughput_mbps"] < case.throughput_threshold_mbps]
    distances = [r["distance_km"] for r in obs if r["distance_km"] is not None]
    transitions = sum(1 for p, c in zip(obs, obs[1:]) if p["serving_pci"] != c["serving_pci"])
    by_pci = defaultdict(list)
    for r in obs:
        by_pci[r["serving_pci"]].append(r["throughput_mbps"])
    minima = {pci: min(v) for pci, v in by_pci.items()}
    lowest = min(minima, key=lambda p: minima[p])
    others = {p: v for p, v in minima.items() if p != lowest}
    return {
        "gate_c2": bool(distances and max(distances) > 1.0),
        "gate_c5": transitions >= 3,
        "gate_c7": max(r["speed_kmh"] for r in obs) > 40.0,
        "gate_c8": bool(affected) and sum(r["scheduled_rbs"] for r in affected) / len(affected) < 160.0,
        "strong_c1": any(
            r["elevation_deg"] is not None
            and r["elevation_deg"] < r["main_lobe_lower_deg"]
            and r["serving_rsrp_dbm"] <= -90.0
            for r in affected
        ),
        "advantage": (max(others.values()) - minima[lowest]) if others else None,
        "w4": any(
            not n["same_gnodeb"] and n["brsrp_minus_serving_rsrp_db"] is not None and n["brsrp_minus_serving_rsrp_db"] >= -3.0
            for r in affected
            for n in r["neighbors"]
        ),
        "w6": any(n["serving_mod30"] == n["neighbor_mod30"] for r in affected for n in r["neighbors"]),
        "w1": any(
            r["elevation_deg"] is not None and r["elevation_deg"] < r["main_lobe_lower_deg"] for r in affected
        ),
    }


def gate_label(f: dict) -> str | None:
    if f["gate_c2"]:
        return "C2"
    if f["gate_c5"]:
        return "C5"
    if f["gate_c7"]:
        return "C7"
    if f["gate_c8"]:
        return "C8"
    if f["strong_c1"]:
        return "C1"
    return None


def residual_pred(f: dict, order: tuple[str, ...], threshold: float | None) -> str:
    if threshold is not None and f["advantage"] is not None and f["advantage"] >= threshold:
        return "C3"
    witness = {"C4": f["w4"], "C6": f["w6"], "C1": f["w1"]}
    for label in order:
        if witness[label]:
            return label
    return "C3"


raw = json.loads((REPO_ROOT / "data/raw_train_2400/train.json").read_text())
rows = [
    {"i": i, "label": r["answer"], "q": r["question"], "split": split_for(question_group(r["question"]))}
    for i, r in enumerate(raw)
]
train = [r for r in rows if r["split"] == "train"]
print(f"train rows: {len(train)}")
feats = {r["i"]: features(r["q"]) for r in train}
residual = [r for r in train if gate_label(feats[r["i"]]) is None]
gated = [r for r in train if gate_label(feats[r["i"]]) is not None]
gate_acc = sum(1 for r in gated if gate_label(feats[r["i"]]) == r["label"])
print(f"gated: {gate_acc}/{len(gated)} correct; residual pool: {len(residual)}")
print("residual gold mix:", Counter(r["label"] for r in residual))

best = []
for order in itertools.permutations(["C4", "C6", "C1"]):
    for threshold in [None] + [t / 2 for t in range(0, 601, 5)]:
        acc = sum(1 for r in residual if residual_pred(feats[r["i"]], order, threshold) == r["label"])
        best.append((acc, order, threshold))
best.sort(key=lambda x: (-x[0], x[2] is None, x[2] if x[2] is not None else 0))
for acc, order, threshold in best[:8]:
    print(f"order={order} threshold={threshold}: {acc}/{len(residual)} = {acc / len(residual) * 100:.2f}%")

acc_old = sum(1 for r in residual if residual_pred(feats[r["i"]], ("C4", "C6", "C1"), 142.5) == r["label"])
print(f"old config (C4,C6,C1 @142.5): {acc_old}/{len(residual)} = {acc_old / len(residual) * 100:.2f}%")

pools = defaultdict(list)
for r in rows:
    if r["split"] == "dev":
        pools[r["label"]].append(r)
dev96 = [sorted(pools[label], key=lambda x: x["i"])[rank] for rank in range(12) for label in LABELS]
per = Counter()
conf = Counter()
for r in dev96:
    f = features(r["q"])
    pred = gate_label(f) or residual_pred(f, ("C6", "C4", "C1"), 142.5)
    if pred == r["label"]:
        per[r["label"]] += 1
    else:
        conf[(r["label"], pred)] += 1
total = sum(per.values())
print(f"refit config (C6,C4,C1 @142.5) on dev-96: {total}/96 = {total / 96 * 100:.2f}%")
print("per-label:", {label: per.get(label, 0) for label in LABELS})
print("confusion:", sorted(conf.items(), key=lambda x: -x[1]))

#!/usr/bin/env python3
"""Attribute every error of the GSMA-harness run to the layer that produced it.

The run stores the audited evidence and the final answer per case; the six tools
are deterministic, so every witness the policy depends on can be recomputed in
pure Python and compared against what the model actually concluded. That turns
"the model got it wrong" into one of a few mechanically-decidable statements:

  gate stage      — an exact criterion fired and the answer did not follow it
  routing         — a gate class was answered while no gate criterion fires
  unsupported     — the answered residual class has NO witness in the measurements
  rule order      — the witness exists, but an earlier rule in the fixed order also
                    fired and the policy's answer (the earlier one) is the gold label
  policy limit    — the model executed the published rules faithfully and the rules
                    themselves are wrong on this case

Emits one JSON blob: layer attribution, per-rule precision, the 8x8 confusion
matrix, and a set of representative cases (narrative + evidence) chosen to cover
every bucket, for the report's samples section.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import os

ROOT = Path(os.environ.get("TL_ROOT", "/workspace/telelogs-bench4/dspy-tools"))
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT))
from neutral_tools import parse_case  # noqa: E402

RUN = ROOT / "results/champion_gsma_full/results.jsonl"
PARQUET_GLOB = os.environ.get(
    "TL_PARQUET_GLOB",
    "/workspace/telelogs/cache/hf/hub/datasets--GSMA--ot-full/snapshots/*/telelogs/test-*.parquet")
GATES = ("C2", "C5", "C7", "C8")
RESIDUAL = ("C1", "C3", "C4", "C6")
THRESHOLD = 142.5


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
        "strong_c1": any(r["elevation_deg"] is not None and r["elevation_deg"] < r["main_lobe_lower_deg"]
                         and r["serving_rsrp_dbm"] <= -90.0 for r in affected),
        "advantage": (max(others.values()) - minima[lowest]) if others else None,
        "w4": any(not n["same_gnodeb"] and n["brsrp_minus_serving_rsrp_db"] is not None
                  and n["brsrp_minus_serving_rsrp_db"] >= -3.0
                  for r in affected for n in r["neighbors"]),
        "w6": any(n["serving_mod30"] == n["neighbor_mod30"] for r in affected for n in r["neighbors"]),
        "w1": any(r["elevation_deg"] is not None and r["elevation_deg"] < r["main_lobe_lower_deg"]
                  for r in affected),
    }


def gate_label(f: dict) -> str | None:
    for flag, label in (("gate_c2", "C2"), ("gate_c5", "C5"), ("gate_c7", "C7"), ("gate_c8", "C8")):
        if f[flag]:
            return label
    return "C1" if f["strong_c1"] else None


def residual_rule(f: dict) -> tuple[int, str]:
    """(rule index actually satisfied first, the class it selects)."""
    if f["advantage"] is not None and f["advantage"] >= THRESHOLD:
        return 1, "C3"
    if f["w6"]:
        return 2, "C6"
    if f["w4"]:
        return 3, "C4"
    if f["w1"]:
        return 4, "C1"
    return 5, "C3"


WITNESS = {"C3": lambda f: f["advantage"] is not None and f["advantage"] >= THRESHOLD,
           "C6": lambda f: f["w6"], "C4": lambda f: f["w4"], "C1": lambda f: f["w1"]}
RULE_OF = {"C3": 1, "C6": 2, "C4": 3, "C1": 4}


def main() -> None:
    import glob

    path = glob.glob(PARQUET_GLOB)[0]
    try:
        import pyarrow.parquet as pq
        rows = pq.read_table(path).to_pylist()
    except ImportError:
        import duckdb
        rows = [{"question": q, "answer": a} for q, a in
                duckdb.sql(f"SELECT question, answer FROM read_parquet('{path}')").fetchall()]
    run = {json.loads(l)["sample_index"]: json.loads(l) for l in RUN.open(encoding="utf-8")}
    assert len(run) == len(rows) == 864

    buckets = Counter()
    per_bucket_gold = defaultdict(Counter)
    confusion = Counter()
    rule_fires = Counter()
    rule_correct = Counter()
    specialist_fired = 0
    specialist_correct = 0
    gate_zone = {"n": 0, "ok": 0}
    residual_zone = {"n": 0, "ok": 0}
    symbolic_ok = 0
    samples = {}
    per_case = []

    for i, row in enumerate(rows):
        rec = run[i]
        gold = str(row["answer"])
        pred = rec["pipeline_answer"] or "unparsed"
        f = features(row["question"])
        g = gate_label(f)
        sym = g or residual_rule(f)[1]
        symbolic_ok += sym == gold
        fired = "[residual specialist]" in (rec["evidence"] or "")
        specialist_fired += fired
        specialist_correct += fired and rec["correct"]

        zone = gate_zone if gold in GATES else residual_zone
        zone["n"] += 1
        zone["ok"] += rec["correct"]

        # what rule the answer corresponds to, and what the policy would pick
        model_rule = RULE_OF.get(pred)
        policy_rule, policy_pick = (None, None) if g else residual_rule(f)
        if not g:
            rule_fires[policy_rule] += 1
            rule_correct[policy_rule] += policy_pick == gold

        if rec["correct"]:
            bucket = "correct"
        elif gold in GATES or pred in GATES:
            bucket = "gate_stage" if g and pred != g else "routing"
        elif sym == gold:
            supported = WITNESS[pred](f) if pred in WITNESS else False
            bucket = "rule_order" if supported else "unsupported_witness"
        else:
            bucket = "policy_limit_faithful" if pred == sym else "policy_limit_other"

        buckets[bucket] += 1
        if bucket != "correct":
            per_bucket_gold[bucket][gold] += 1
            confusion[(gold, pred)] += 1

        per_case.append({"i": i, "gold": gold, "pred": pred, "bucket": bucket,
                         "symbolic": sym, "specialist": fired})

        # one representative per bucket, preferring a short narrative
        key = bucket if bucket != "correct" else f"correct_{'gate' if gold in GATES else 'residual'}"
        cand = samples.get(key)
        if rec["narrative"] and (cand is None or len(rec["narrative"]) < len(cand["narrative"])):
            samples[key] = {
                "i": i, "gold": gold, "pred": pred, "bucket": bucket, "symbolic": sym,
                "specialist": fired, "narrative": rec["narrative"],
                "evidence": rec["evidence"], "ungrounded": rec["narrative_ungrounded"],
                "witnesses": {"advantage": f["advantage"], "w6": f["w6"], "w4": f["w4"],
                              "w1": f["w1"], "strong_c1": f["strong_c1"], "gate": g},
            }
    flagged = [r for r in run.values() if r["narrative_ungrounded"]]
    if flagged:
        r = flagged[0]
        samples["guard_flagged"] = {
            "i": r["sample_index"], "gold": r["target"], "pred": r["pipeline_answer"],
            "bucket": "guard", "narrative": r["narrative"], "evidence": r["evidence"],
            "ungrounded": r["narrative_ungrounded"],
        }

    out = {
        "total": 864,
        "correct": buckets["correct"],
        "accuracy": round(buckets["correct"] / 864, 4),
        "symbolic_on_same_run": symbolic_ok,
        "zones": {"gate": gate_zone, "residual": residual_zone},
        "specialist": {"fired": specialist_fired, "correct": specialist_correct},
        "buckets": dict(buckets),
        "bucket_gold_mix": {k: dict(v) for k, v in per_bucket_gold.items()},
        "rule_precision": {str(k): {"fires": rule_fires[k], "correct": rule_correct[k]}
                           for k in sorted(rule_fires)},
        "confusion": [{"gold": g, "pred": p, "n": n}
                      for (g, p), n in sorted(confusion.items(), key=lambda kv: -kv[1])],
        "samples": samples,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

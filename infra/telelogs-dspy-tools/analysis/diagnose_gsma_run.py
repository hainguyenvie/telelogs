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

Two rule families are supported, selected with --rule:

  presence  — the shipped-through-2026-08-02 ladder: advantage >= 142.5 -> C3,
              then any mod-30 pair -> C6, any gap >= -3 dB -> C4, any below-lobe
              row -> C1, else C3.
  magnitude — the ladder that replaced it (commit b40c48e): counts and depths
              instead of existence, C3 demoted to fallback. Thresholds fitted on
              the train residual pool only by analysis/fit_magnitude_rule.py.

Emits one JSON blob: layer attribution, per-rule precision, the 8x8 confusion
matrix, a dedicated breakdown of the fallback class (where the magnitude ladder
concentrates its remaining loss), and representative cases per bucket.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import os

ROOT = Path(os.environ.get("TL_ROOT", "/workspace/telelogs-bench4/dspy-tools"))
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT))
from neutral_tools import parse_case  # noqa: E402

PARQUET_GLOB = os.environ.get(
    "TL_PARQUET_GLOB",
    "/workspace/telelogs/cache/hf/hub/datasets--GSMA--ot-full/snapshots/*/telelogs/test-*.parquet")
GATES = ("C2", "C5", "C7", "C8")
RESIDUAL = ("C1", "C3", "C4", "C6")

# presence family (pre-b40c48e)
THRESHOLD = 142.5
# magnitude family (b40c48e), fitted on the 567-case train residual pool only
RESIDUE_PAIRS_MIN = 2      # equal_residue_pair_count > 2 -> C6
GAP_ROWS_MIN = 2           # noncolocated_gap_at_or_above_neg3db_count > 2 -> C4
LOBE_DEFICIT_MIN = 2.5     # deepest_below_lobe_deficit_deg > 2.5 -> C1


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
    below = [r for r in affected
             if r["elevation_deg"] is not None and r["elevation_deg"] < r["main_lobe_lower_deg"]]
    deficits = [r["main_lobe_lower_deg"] - r["elevation_deg"] for r in below]
    return {
        "gate_c2": bool(distances and max(distances) > 1.0),
        "gate_c5": transitions >= 3,
        "gate_c7": max(r["speed_kmh"] for r in obs) > 40.0,
        "gate_c8": bool(affected) and sum(r["scheduled_rbs"] for r in affected) / len(affected) < 160.0,
        "strong_c1": any(r["elevation_deg"] is not None and r["elevation_deg"] < r["main_lobe_lower_deg"]
                         and r["serving_rsrp_dbm"] <= -90.0 for r in affected),
        "advantage": (max(others.values()) - minima[lowest]) if others else None,
        # presence witnesses
        "w4": any(not n["same_gnodeb"] and n["brsrp_minus_serving_rsrp_db"] is not None
                  and n["brsrp_minus_serving_rsrp_db"] >= -3.0
                  for r in affected for n in r["neighbors"]),
        "w6": any(n["serving_mod30"] == n["neighbor_mod30"] for r in affected for n in r["neighbors"]),
        "w1": bool(below),
        # magnitudes behind them — the fields the shipped tools now expose
        "w6_count": sum(1 for r in affected for n in r["neighbors"]
                        if n["serving_mod30"] == n["neighbor_mod30"]),
        "w4_count": sum(1 for r in affected for n in r["neighbors"]
                        if not n["same_gnodeb"] and n["brsrp_minus_serving_rsrp_db"] is not None
                        and n["brsrp_minus_serving_rsrp_db"] >= -3.0),
        "w1_deficit": max(deficits) if deficits else None,
        "n_affected": len(affected),
        "n_rows": len(obs),
        "n_segments": len(by_pci),
    }


def gate_label(f: dict) -> str | None:
    for flag, label in (("gate_c2", "C2"), ("gate_c5", "C5"), ("gate_c7", "C7"), ("gate_c8", "C8")):
        if f[flag]:
            return label
    return "C1" if f["strong_c1"] else None


def residual_rule_presence(f: dict) -> tuple[int, str]:
    if f["advantage"] is not None and f["advantage"] >= THRESHOLD:
        return 1, "C3"
    if f["w6"]:
        return 2, "C6"
    if f["w4"]:
        return 3, "C4"
    if f["w1"]:
        return 4, "C1"
    return 5, "C3"


def residual_rule_magnitude(f: dict) -> tuple[int, str]:
    if f["w6_count"] > RESIDUE_PAIRS_MIN:
        return 1, "C6"
    if f["w4_count"] > GAP_ROWS_MIN:
        return 2, "C4"
    if f["w1_deficit"] is not None and f["w1_deficit"] > LOBE_DEFICIT_MIN:
        return 3, "C1"
    return 4, "C3"


RULE_LABELS = {
    "presence": {1: "C3 if advantage >= 142.5", 2: "C6 if a mod-30 pair exists",
                 3: "C4 if gap >= -3 dB", 4: "C1 if a row sits below the lobe",
                 5: "otherwise C3 (fallback)"},
    "magnitude": {1: f"C6 if equal_residue_pair_count > {RESIDUE_PAIRS_MIN}",
                  2: f"C4 if noncolocated_gap_at_or_above_neg3db_count > {GAP_ROWS_MIN}",
                  3: f"C1 if deepest_below_lobe_deficit_deg > {LOBE_DEFICIT_MIN}",
                  4: "otherwise C3 (fallback)"},
}
WITNESS = {
    "presence": {"C3": lambda f: f["advantage"] is not None and f["advantage"] >= THRESHOLD,
                 "C6": lambda f: f["w6"], "C4": lambda f: f["w4"], "C1": lambda f: f["w1"]},
    "magnitude": {"C3": lambda f: True,  # the fallback always "has" its witness
                  "C6": lambda f: f["w6_count"] > RESIDUE_PAIRS_MIN,
                  "C4": lambda f: f["w4_count"] > GAP_ROWS_MIN,
                  "C1": lambda f: f["w1_deficit"] is not None and f["w1_deficit"] > LOBE_DEFICIT_MIN},
}
RULE_OF = {"presence": {"C3": 1, "C6": 2, "C4": 3, "C1": 4},
           "magnitude": {"C6": 1, "C4": 2, "C1": 3, "C3": 4}}


def main() -> None:
    import glob

    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default=str(ROOT / "results/champion_gsma_full_magrule/results.jsonl"))
    parser.add_argument("--rule", choices=("presence", "magnitude"), default="magnitude")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    residual_rule = residual_rule_presence if args.rule == "presence" else residual_rule_magnitude
    witness = WITNESS[args.rule]
    rule_of = RULE_OF[args.rule]

    path = glob.glob(PARQUET_GLOB)[0]
    try:
        import pyarrow.parquet as pq
        rows = pq.read_table(path).to_pylist()
    except ImportError:
        import duckdb
        rows = [{"question": q, "answer": a} for q, a in
                duckdb.sql(f"SELECT question, answer FROM read_parquet('{path}')").fetchall()]
    run = {json.loads(l)["sample_index"]: json.loads(l) for l in Path(args.run).open(encoding="utf-8")}
    assert len(run) == len(rows) == 864

    buckets = Counter()
    per_bucket_gold = defaultdict(Counter)
    confusion = Counter()
    rule_fires = Counter()
    rule_correct = Counter()
    rule_gold_mix = defaultdict(Counter)
    specialist_fired = 0
    specialist_correct = 0
    gate_zone = {"n": 0, "ok": 0}
    residual_zone = {"n": 0, "ok": 0}
    symbolic_ok = 0
    samples = {}
    per_case = []
    # dedicated fallback analysis: where does the C3 mass actually come from?
    fallback = {"fires": 0, "gold": Counter(), "advantage_when_gold_c3": [],
                "advantage_when_gold_other": []}
    gold_c3 = {"n": 0, "policy_says": Counter(), "model_says": Counter(),
               "near_miss": Counter()}

    for i, row in enumerate(rows):
        rec = run[i]
        gold = str(row["answer"])
        pred = rec["pipeline_answer"] or "unparsed"
        f = features(row["question"])
        g = gate_label(f)
        policy_rule, policy_pick = (None, None) if g else residual_rule(f)
        sym = g or policy_pick
        symbolic_ok += sym == gold
        fired = "[residual specialist]" in (rec["evidence"] or "")
        specialist_fired += fired
        specialist_correct += fired and rec["correct"]

        zone = gate_zone if gold in GATES else residual_zone
        zone["n"] += 1
        zone["ok"] += rec["correct"]

        if not g:
            rule_fires[policy_rule] += 1
            rule_correct[policy_rule] += policy_pick == gold
            rule_gold_mix[policy_rule][gold] += 1
            if policy_pick == "C3" and policy_rule == max(RULE_LABELS[args.rule]):
                fallback["fires"] += 1
                fallback["gold"][gold] += 1
                key = "advantage_when_gold_c3" if gold == "C3" else "advantage_when_gold_other"
                if f["advantage"] is not None:
                    fallback[key].append(round(f["advantage"], 2))

        if gold == "C3":
            gold_c3["n"] += 1
            gold_c3["policy_says"][sym] += 1
            gold_c3["model_says"][pred] += 1
            # how close each competing rule came to NOT firing
            if not g:
                if f["w6_count"] > RESIDUE_PAIRS_MIN:
                    gold_c3["near_miss"][f"C6 fired with {f['w6_count']} pairs"] += 1
                elif f["w4_count"] > GAP_ROWS_MIN:
                    gold_c3["near_miss"][f"C4 fired with {f['w4_count']} rows"] += 1
                elif f["w1_deficit"] is not None and f["w1_deficit"] > LOBE_DEFICIT_MIN:
                    bucket_deg = f"C1 fired at {round(f['w1_deficit'], 1)} deg"
                    gold_c3["near_miss"][bucket_deg] += 1

        if rec["correct"]:
            bucket = "correct"
        elif gold in GATES or pred in GATES:
            bucket = "gate_stage" if g and pred != g else "routing"
        elif sym == gold:
            supported = witness[pred](f) if pred in witness else False
            bucket = "rule_order" if supported else "unsupported_witness"
        else:
            bucket = "policy_limit_faithful" if pred == sym else "policy_limit_other"

        buckets[bucket] += 1
        if bucket != "correct":
            per_bucket_gold[bucket][gold] += 1
            confusion[(gold, pred)] += 1

        per_case.append({"i": i, "gold": gold, "pred": pred, "bucket": bucket,
                         "symbolic": sym, "specialist": fired})

        key = bucket if bucket != "correct" else f"correct_{'gate' if gold in GATES else 'residual'}"
        cand = samples.get(key)
        if rec["narrative"] and (cand is None or len(rec["narrative"]) < len(cand["narrative"])):
            samples[key] = {
                "i": i, "gold": gold, "pred": pred, "bucket": bucket, "symbolic": sym,
                "specialist": fired, "narrative": rec["narrative"],
                "evidence": rec["evidence"], "ungrounded": rec["narrative_ungrounded"],
                "witnesses": {"advantage": f["advantage"], "w6_count": f["w6_count"],
                              "w4_count": f["w4_count"], "w1_deficit": f["w1_deficit"],
                              "strong_c1": f["strong_c1"], "gate": g},
            }
    flagged = [r for r in run.values() if r["narrative_ungrounded"]]
    if flagged:
        r = flagged[0]
        samples["guard_flagged"] = {
            "i": r["sample_index"], "gold": r["target"], "pred": r["pipeline_answer"],
            "bucket": "guard", "narrative": r["narrative"], "evidence": r["evidence"],
            "ungrounded": r["narrative_ungrounded"],
        }

    def summarize(values: list[float]) -> dict:
        if not values:
            return {}
        values = sorted(values)
        mid = len(values) // 2
        return {"n": len(values), "min": values[0], "median": values[mid], "max": values[-1],
                "mean": round(sum(values) / len(values), 2)}

    out = {
        "run": args.run,
        "rule_family": args.rule,
        "total": 864,
        "correct": buckets["correct"],
        "accuracy": round(buckets["correct"] / 864, 4),
        "symbolic_on_same_run": symbolic_ok,
        "zones": {"gate": gate_zone, "residual": residual_zone},
        "specialist": {"fired": specialist_fired, "correct": specialist_correct},
        "buckets": dict(buckets),
        "bucket_gold_mix": {k: dict(v) for k, v in per_bucket_gold.items()},
        "rule_precision": {str(k): {"rule": RULE_LABELS[args.rule][k],
                                    "fires": rule_fires[k], "correct": rule_correct[k],
                                    "precision": round(rule_correct[k] / rule_fires[k], 4) if rule_fires[k] else None,
                                    "gold_mix": dict(rule_gold_mix[k].most_common())}
                           for k in sorted(rule_fires)},
        "fallback_analysis": {
            "fires": fallback["fires"],
            "gold_mix": dict(fallback["gold"].most_common()),
            "advantage_when_gold_c3": summarize(fallback["advantage_when_gold_c3"]),
            "advantage_when_gold_other": summarize(fallback["advantage_when_gold_other"]),
        },
        "gold_c3_analysis": {
            "n": gold_c3["n"],
            "policy_says": dict(gold_c3["policy_says"].most_common()),
            "model_says": dict(gold_c3["model_says"].most_common()),
            "stolen_by": dict(gold_c3["near_miss"].most_common(12)),
        },
        "confusion": [{"gold": g, "pred": p, "n": n}
                      for (g, p), n in sorted(confusion.items(), key=lambda kv: -kv[1])],
        "samples": samples,
    }
    blob = json.dumps(out, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(blob, encoding="utf-8")
    print(blob)


if __name__ == "__main__":
    main()

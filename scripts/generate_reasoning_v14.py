"""Build TeleLogs v14 raw-to-facts and raw-to-solution supervision.

V14 closes the input/target gap found by the v13 ablation. Both lanes receive
only the raw benchmark question. One lane learns a compact, deterministic fact
sheet; the other emits the same fact sheet, a concise grounded decision, and
the boxed class. Records quarantined from causal reasoning remain useful for
fact extraction but are excluded from the solution lane.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

from generate_reasoning_v13 import (
    RAW,
    V12_AUDIT,
    load_jsonl,
    verified_facts,
    write_jsonl,
)


ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "data" / "reasoning_v14"
LEDGER = OUTDIR / "canonical_v14.audit.jsonl"
EXTRACT = OUTDIR / "extract_facts_raw_v14.jsonl"
SOLVE = OUTDIR / "solve_raw_v14.jsonl"
SUMMARY = OUTDIR / "summary_v14.json"

EXTRACT_SYSTEM = """You are a TeleLogs fact-extraction model.

Read the raw drive-test and engineering tables, perform the required row
alignment, joins, counting, geometry, modulo, and arithmetic, then return only
the compact JSON fact sheet. Preserve null when serving-cell engineering is
incomplete. Do not select a root-cause class in this extraction task."""


def compact_facts(package: dict) -> dict:
    observed = package["observed"]
    candidates = package["candidate_evidence"]
    return {
        "affected": {
            "rows": observed["affected_rows"],
            "minimum_throughput_mbps": observed[
                "minimum_throughput_mbps"
            ],
        },
        "C1": {
            "affected_below_lower_lobe": candidates["C1"][
                "affected_below_lower_lobe"
            ],
            "affected_weak_rsrp_witness": candidates["C1"][
                "affected_weak_rsrp_witness"
            ],
            "witness": candidates["C1"]["witness"],
        },
        "C2": {
            "distance_gate": candidates["C2"]["distance_gate"],
            "maximum_distance_km": candidates["C2"][
                "maximum_distance_km"
            ],
        },
        "C3": candidates["C3"]["serving_segment_comparison"],
        "C4": {
            "affected_overlap_gate": candidates["C4"][
                "affected_overlap_gate"
            ],
            "witness": candidates["C4"]["witness"],
        },
        "C5": {
            "change_count": candidates["C5"]["change_count"],
            "frequent_change_gate": candidates["C5"][
                "frequent_change_gate"
            ],
        },
        "C6": {
            "affected_modulo_30_collision": candidates["C6"][
                "affected_modulo_30_collision"
            ],
            "witness": candidates["C6"]["witness"],
        },
        "C7": {
            "maximum_speed_kmh": candidates["C7"]["maximum_speed_kmh"],
            "speed_gate": candidates["C7"]["speed_gate"],
        },
        "C8": {
            "affected_average_scheduled_rbs": candidates["C8"][
                "affected_average_scheduled_rbs"
            ],
            "affected_average_rb_gate": candidates["C8"][
                "affected_average_rb_gate"
            ],
        },
    }


def compact_json(facts: dict) -> str:
    return json.dumps(
        facts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def class_reason(label: str, facts: dict) -> str:
    if label == "C1":
        witness = facts["C1"]["witness"]
        return (
            f"At affected row {witness['row']}, UE elevation "
            f"{witness['ue_elevation_deg']:.2f} degrees is below the "
            f"{witness['main_lobe_lower_deg']:.2f}-degree lower main-lobe "
            f"edge and throughput is {witness['throughput_mbps']:.2f} Mbps."
        )
    if label == "C2":
        return (
            f"Maximum complete serving distance is "
            f"{facts['C2']['maximum_distance_km']:.3f} km, above 1 km."
        )
    if label == "C3":
        comparison = facts["C3"]
        return (
            f"Serving PCI {comparison['affected_pci']} falls to "
            f"{comparison['affected_min_mbps']:.2f} Mbps, while serving PCI "
            f"{comparison['alternative_pci']} has a "
            f"{comparison['alternative_min_mbps']:.2f}-Mbps low point; "
            "affected-row C4 and C6 gates are absent."
        )
    if label == "C4":
        witness = facts["C4"]["witness"]
        return (
            f"At affected row {witness['row']}, the strongest noncolocated "
            f"BRSRP-minus-serving-RSRP gap is "
            f"{witness['maximum_noncolocated_brsrp_minus_serving_rsrp_db']:.2f} "
            "dB, meeting the -3 dB overlap gate."
        )
    if label == "C5":
        return (
            f"The serving PCI changes {facts['C5']['change_count']} times, "
            "meeting the minimum of 3 changes."
        )
    if label == "C6":
        witness = facts["C6"]["witness"]
        return (
            f"At affected row {witness['row']}, serving PCI "
            f"{witness['serving_pci']} and neighbor PCI "
            f"{witness['neighbor_pci']} share modulo-30 residue "
            f"{witness['modulo_30_residue']}; the affected-row C4 gate is "
            "absent."
        )
    if label == "C7":
        return (
            f"Maximum speed is {facts['C7']['maximum_speed_kmh']:.2f} km/h, "
            "above 40 km/h."
        )
    if label == "C8":
        return (
            "Affected-row average scheduled RBs are "
            f"{facts['C8']['affected_average_scheduled_rbs']:.2f}, below 160."
        )
    raise ValueError(label)


def solve_answer(label: str, facts: dict, tier: str) -> str:
    note = (
        "\nEvidence note\nThe decision uses the strongest observable gate; "
        "some engineering evidence is incomplete."
        if tier == "controlled"
        else ""
    )
    return f"""Computed facts
{compact_json(facts)}

Decision
{label}: {class_reason(label, facts)}{note}

\\boxed{{{label}}}"""


def record(
    messages: list[dict],
    index: int,
    label: str,
    task: str,
    tier: str,
) -> dict:
    return {
        "messages": messages,
        "metadata": {
            "source_index": index,
            "label": label,
            "task": task,
            "evidence_tier": tier,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    outputs = (LEDGER, EXTRACT, SOLVE, SUMMARY)
    if any(path.exists() for path in outputs) and not args.overwrite:
        raise SystemExit(
            "v14 output already exists; pass --overwrite only after review"
        )

    raw_rows = json.loads(RAW.read_text())
    v12_rows = load_jsonl(V12_AUDIT)
    if len(raw_rows) != 2400 or len(v12_rows) != 2400:
        raise SystemExit("expected 2,400 aligned source and audit rows")

    ledger_rows = []
    extract_rows = []
    solve_rows = []
    for index, (source, audit) in enumerate(zip(raw_rows, v12_rows)):
        label = source["answer"]
        if (
            audit["source_index"] != index
            or audit["known_label_for_offline_evaluation"] != label
            or audit["validator_flags"]
        ):
            raise SystemExit(f"source/audit alignment failure at {index}")

        package = verified_facts(
            audit["calculator_facts"], audit["row_level_evidence"]
        )
        facts = compact_facts(package)
        tier = audit["evidence_tier"]
        extract_answer = compact_json(facts)
        extract_rows.append(
            record(
                [
                    {"role": "system", "content": EXTRACT_SYSTEM},
                    {"role": "user", "content": source["question"]},
                    {"role": "assistant", "content": extract_answer},
                ],
                index,
                label,
                "extract_facts_raw",
                tier,
            )
        )

        solution = None
        if audit["accepted_for_sft"]:
            solution = solve_answer(label, facts, tier)
            solve_rows.append(
                record(
                    [
                        {"role": "user", "content": source["question"]},
                        {"role": "assistant", "content": solution},
                    ],
                    index,
                    label,
                    "solve_raw",
                    tier,
                )
            )

        ledger_rows.append(
            {
                "source_index": index,
                "label": label,
                "evidence_tier": tier,
                "solution_ready": audit["accepted_for_sft"],
                "compact_facts": facts,
                "extract_answer": extract_answer,
                "solve_answer": solution,
            }
        )

    OUTDIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(LEDGER, ledger_rows)
    write_jsonl(EXTRACT, extract_rows)
    write_jsonl(SOLVE, solve_rows)
    summary = {
        "source_records": len(raw_rows),
        "extract_facts_raw_records": len(extract_rows),
        "solve_raw_records": len(solve_rows),
        "solution_quarantine_records": len(raw_rows) - len(solve_rows),
        "extract_labels": dict(
            sorted(collections.Counter(
                row["metadata"]["label"] for row in extract_rows
            ).items())
        ),
        "solve_labels": dict(
            sorted(collections.Counter(
                row["metadata"]["label"] for row in solve_rows
            ).items())
        ),
        "raw_sha256": hashlib.sha256(RAW.read_bytes()).hexdigest(),
        "v12_audit_sha256": hashlib.sha256(
            V12_AUDIT.read_bytes()
        ).hexdigest(),
        "extract_system_sha256": hashlib.sha256(
            EXTRACT_SYSTEM.encode()
        ).hexdigest(),
    }
    SUMMARY.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

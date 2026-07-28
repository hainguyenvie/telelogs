"""Build a two-lane TeleLogs v13 corpus for one multitask model.

The classifier lane keeps the raw-question decision skill explicit.  The
diagnostic lane gives the model calculator-verified facts and asks it to
select a class and write a short, grounded explanation.  The calculator,
not the language model, remains responsible for arithmetic and table joins.

V12 is read-only input.  V13 is always written to a separate directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw_train_2400" / "train.json"
V12_AUDIT = (
    ROOT / "data" / "reasoning_v12" / "canonical_reasoning_v12.audit.jsonl"
)
OUTDIR = ROOT / "data" / "reasoning_v13"
LEDGER = OUTDIR / "canonical_v13.audit.jsonl"
CLASSIFY = OUTDIR / "classify_raw_v13.jsonl"
DIAGNOSE = OUTDIR / "diagnose_verified_facts_v13.jsonl"
MULTITASK = OUTDIR / "multitask_v13.jsonl"
QUARANTINE = OUTDIR / "diagnostic_quarantine_v13.jsonl"
SUMMARY = OUTDIR / "summary_v13.json"

DIAGNOSTIC_SYSTEM = """You are a TeleLogs diagnostic model.

The JSON inside <verified_facts> was produced by a deterministic parser and
calculator. Treat it as authoritative. Do not recompute or replace its
numbers. Select the best-supported root cause from C1 through C8, explain the
decisive evidence concisely, disclose any stated evidence limitation, and end
with exactly one final answer in the form \\boxed{Ck}."""


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    )


def r2(value):
    return None if value is None else round(float(value), 2)


def r3(value):
    return None if value is None else round(float(value), 3)


def strongest_c1_witness(evidence: dict):
    rows = evidence["below_lower_lobe_low_throughput_rows"]
    if not rows:
        return None
    row = max(
        rows,
        key=lambda item: (
            item["distance_km"] if item["distance_km"] is not None else -1,
            -item["throughput_mbps"],
        ),
    )
    return {
        "row": row["row_index"] + 1,
        "serving_pci": row["serving_pci"],
        "distance_km": r3(row["distance_km"]),
        "ue_elevation_deg": r2(row["elevation_deg"]),
        "main_lobe_lower_deg": r2(row["main_lobe_lower_deg"]),
        "serving_rsrp_dbm": r2(row["serving_rsrp_dbm"]),
        "throughput_mbps": r2(row["throughput_mbps"]),
        "weak_rsrp_at_or_below_minus_90": (
            row["serving_rsrp_dbm"] <= -90
        ),
    }


def strongest_c4_witness(evidence: dict):
    rows = evidence["overlap_eligible_low_throughput_rows"]
    if not rows:
        return None
    row = max(rows, key=lambda item: item["maximum_gap_db"])
    return {
        "row": row["row_index"] + 1,
        "serving_pci": row["serving_pci"],
        "throughput_mbps": r2(row["throughput_mbps"]),
        "maximum_noncolocated_brsrp_minus_serving_rsrp_db": r2(
            row["maximum_gap_db"]
        ),
    }


def first_c6_witness(evidence: dict):
    rows = evidence["collision_low_throughput_rows"]
    if not rows:
        return None
    row = rows[0]
    neighbor = row["neighbor_pcis"][0]
    return {
        "row": row["row_index"] + 1,
        "serving_pci": row["serving_pci"],
        "neighbor_pci": neighbor,
        "modulo_30_residue": row["serving_pci"] % 30,
        "throughput_mbps": r2(row["throughput_mbps"]),
    }


def verified_facts(facts: dict, evidence: dict) -> dict:
    distance = facts["max_distance_km"]
    affected_rbs = evidence[
        "average_scheduled_rbs_low_throughput_rows"
    ]
    c3 = facts["c3_minimum_comparison"]
    c3_block = None
    if c3:
        c3_block = {
            "affected_pci": c3["affected_pci"],
            "affected_min_mbps": r2(c3["affected_min_mbps"]),
            "alternative_pci": c3["alternative_pci"],
            "alternative_min_mbps": r2(c3["alternative_min_mbps"]),
            "minimum_advantage_mbps": r2(
                c3["minimum_advantage_mbps"]
            ),
        }

    throughput = {
        str(pci): {
            "count": values["n"],
            "min_mbps": r2(values["min"]),
            "max_mbps": r2(values["max"]),
            "mean_mbps": r2(values["mean"]),
        }
        for pci, values in sorted(
            facts["throughput"].items(), key=lambda item: int(item[0])
        )
    }
    return {
        "thresholds": {
            "affected_throughput_below_mbps": 600,
            "C2_distance_above_km": 1,
            "C4_overlap_gap_at_least_db": -3,
            "C5_minimum_serving_pci_changes": 3,
            "C7_speed_above_kmh": 40,
            "C8_affected_average_rbs_below": 160,
        },
        "observed": {
            "drive_test_rows": evidence["observation_count"],
            "affected_rows": evidence["low_throughput_row_count"],
            "minimum_throughput_mbps": r2(
                min(values["min"] for values in facts["throughput"].values())
            ),
            "maximum_speed_kmh": r2(facts["max_speed"]),
            "maximum_complete_serving_distance_km": r3(distance),
            "serving_engineering_complete": facts[
                "serving_engineering_available"
            ],
            "serving_pci_changes": facts["handover_count"],
            "serving_pci_sequence": evidence["serving_pci_sequence"],
            "affected_average_scheduled_rbs": r2(affected_rbs),
            "serving_segment_throughput": throughput,
        },
        "candidate_evidence": {
            "C1": {
                "affected_below_lower_lobe": bool(
                    evidence[
                        "below_lower_lobe_low_throughput_row_count"
                    ]
                ),
                "affected_weak_rsrp_witness": bool(
                    evidence[
                        "below_lower_lobe_low_throughput_weak_rsrp_row_count"
                    ]
                ),
                "witness": strongest_c1_witness(evidence),
            },
            "C2": {
                "distance_gate": (
                    "inconclusive"
                    if distance is None
                    else "triggered"
                    if distance > 1
                    else "not_triggered"
                ),
                "maximum_distance_km": r3(distance),
            },
            "C3": {
                "serving_segment_comparison": c3_block,
            },
            "C4": {
                "affected_overlap_gate": bool(
                    evidence[
                        "overlap_eligible_low_throughput_row_count"
                    ]
                ),
                "witness": strongest_c4_witness(evidence),
            },
            "C5": {
                "frequent_change_gate": facts["handover_count"] >= 3,
                "change_count": facts["handover_count"],
                "transition_destinations": [
                    {
                        "row": row["row_index"] + 1,
                        "from_pci": row["previous_serving_pci"],
                        "to_pci": row["serving_pci"],
                        "throughput_mbps": r2(row["throughput_mbps"]),
                    }
                    for row in evidence["transition_destination_rows"]
                ],
            },
            "C6": {
                "affected_modulo_30_collision": bool(
                    evidence["collision_low_throughput_row_count"]
                ),
                "witness": first_c6_witness(evidence),
            },
            "C7": {
                "speed_gate": facts["max_speed"] > 40,
                "maximum_speed_kmh": r2(facts["max_speed"]),
            },
            "C8": {
                "affected_average_rb_gate": (
                    affected_rbs is not None and affected_rbs < 160
                ),
                "affected_average_scheduled_rbs": r2(affected_rbs),
            },
        },
    }


def format_value(value, digits=2):
    if value is None:
        return "unavailable"
    return f"{value:.{digits}f}"


def common_competing_checks(package: dict) -> str:
    observed = package["observed"]
    distance = observed["maximum_complete_serving_distance_km"]
    average_rbs = observed["affected_average_scheduled_rbs"]
    distance_check = (
        "distance=unavailable"
        if distance is None
        else f"distance={format_value(distance, 3)} km"
    )
    return (
        f"{distance_check}; "
        f"serving-PCI changes={observed['serving_pci_changes']}; "
        f"maximum speed={format_value(observed['maximum_speed_kmh'])} km/h; "
        f"affected-row average RBs={format_value(average_rbs)}"
    )


def diagnostic_answer(
    label: str,
    tier: str,
    package: dict,
    limitation: str | None,
) -> str:
    observed = package["observed"]
    candidates = package["candidate_evidence"]

    if label == "C1":
        witness = candidates["C1"]["witness"]
        evidence = (
            f"- At affected row {witness['row']}, serving PCI "
            f"{witness['serving_pci']} delivers "
            f"{witness['throughput_mbps']:.2f} Mbps. UE elevation "
            f"{witness['ue_elevation_deg']:.2f} degrees is below the "
            f"{witness['main_lobe_lower_deg']:.2f}-degree lower main-lobe "
            f"edge at {witness['distance_km']:.3f} km; serving RSRP is "
            f"{witness['serving_rsrp_dbm']:.2f} dBm."
        )
        conclusion = (
            "The row-aligned beam geometry supports excessive serving-cell "
            "downtilt as the best explanation."
        )
    elif label == "C2":
        distance = candidates["C2"]["maximum_distance_km"]
        evidence = (
            f"- Maximum complete serving distance is {distance:.3f} km, "
            "which is above the 1 km overshooting threshold."
        )
        conclusion = (
            "The decisive distance threshold identifies serving-cell "
            "overshooting."
        )
    elif label == "C3":
        comparison = candidates["C3"]["serving_segment_comparison"]
        evidence = (
            f"- While actually serving, PCI {comparison['affected_pci']} "
            f"falls to {comparison['affected_min_mbps']:.2f} Mbps, whereas "
            f"alternative PCI {comparison['alternative_pci']} has a "
            f"{comparison['alternative_min_mbps']:.2f}-Mbps low point, a "
            f"{comparison['minimum_advantage_mbps']:.2f}-Mbps advantage.\n"
            "- The affected-row C4 overlap and C6 collision gates are both "
            "absent, so the serving-segment comparison is usable."
        )
        conclusion = (
            "After excluding measured defect gates, the alternative serving "
            "cell provides the stronger relative-throughput explanation."
        )
    elif label == "C4":
        witness = candidates["C4"]["witness"]
        evidence = (
            f"- At affected row {witness['row']}, serving PCI "
            f"{witness['serving_pci']} delivers "
            f"{witness['throughput_mbps']:.2f} Mbps and the strongest "
            "non-colocated BRSRP-minus-serving-RSRP gap is "
            f"{witness['maximum_noncolocated_brsrp_minus_serving_rsrp_db']:.2f} "
            "dB, meeting the -3 dB overlap gate."
        )
        conclusion = (
            "The row-aligned, near-equal-power non-colocated neighbor is the "
            "best-supported overlap diagnosis."
        )
    elif label == "C5":
        changes = candidates["C5"]["change_count"]
        sequence = " -> ".join(
            str(pci) for pci in observed["serving_pci_sequence"]
        )
        evidence = (
            f"- The serving PCI changes {changes} times, meeting the "
            f"minimum of 3 changes. The observed sequence is {sequence}."
        )
        conclusion = (
            "The repeated serving-cell changes support handover-driven "
            "throughput degradation."
        )
    elif label == "C6":
        witness = candidates["C6"]["witness"]
        evidence = (
            f"- At affected row {witness['row']}, serving PCI "
            f"{witness['serving_pci']} and neighbor PCI "
            f"{witness['neighbor_pci']} share modulo-30 residue "
            f"{witness['modulo_30_residue']}, while throughput is "
            f"{witness['throughput_mbps']:.2f} Mbps.\n"
            "- The affected-row C4 overlap gate is absent."
        )
        conclusion = (
            "The row-aligned modulo-30 collision is the best observable "
            "interference mechanism; it is an eligibility signal rather than "
            "a direct DMRS interference measurement."
        )
    elif label == "C7":
        speed = candidates["C7"]["maximum_speed_kmh"]
        evidence = (
            f"- Maximum vehicle speed is {speed:.2f} km/h, above the "
            "40 km/h threshold."
        )
        conclusion = (
            "The decisive speed threshold identifies excessive vehicle speed."
        )
    elif label == "C8":
        average = candidates["C8"]["affected_average_scheduled_rbs"]
        evidence = (
            f"- Average scheduled allocation across affected rows is "
            f"{average:.2f} RBs, below the 160-RB threshold."
        )
        conclusion = (
            "The affected section is resource-constrained by insufficient "
            "scheduled RBs."
        )
    else:
        raise ValueError(label)

    limitation_text = (
        f"\n\nEvidence limitation\n- {limitation}."
        if tier == "controlled" and limitation
        else ""
    )
    return f"""Decision
{label}

Evidence
{evidence}

Competing checks
- {common_competing_checks(package)}.{limitation_text}

Conclusion
{conclusion}

\\boxed{{{label}}}"""


def classify_answer(label: str) -> str:
    return f"Decision: {label}\n\n\\boxed{{{label}}}"


def diagnostic_user(question: str, package: dict) -> str:
    facts_json = json.dumps(
        package, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return (
        f"{question}\n\n<verified_facts>\n{facts_json}\n"
        "</verified_facts>"
    )


def portable_record(
    messages: list[dict],
    source_index: int,
    label: str,
    task: str,
    tier: str,
) -> dict:
    return {
        "messages": messages,
        "metadata": {
            "source_index": source_index,
            "label": label,
            "task": task,
            "evidence_tier": tier,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    outputs = (
        LEDGER,
        CLASSIFY,
        DIAGNOSE,
        MULTITASK,
        QUARANTINE,
        SUMMARY,
    )
    if any(path.exists() for path in outputs) and not args.overwrite:
        raise SystemExit(
            "v13 output already exists; pass --overwrite only after review"
        )

    raw_rows = json.loads(RAW.read_text())
    v12_rows = load_jsonl(V12_AUDIT)
    if len(raw_rows) != 2400 or len(v12_rows) != 2400:
        raise SystemExit("expected exactly 2,400 aligned source records")

    ledger_rows = []
    classify_rows = []
    diagnose_rows = []
    quarantine_rows = []
    for index, (source, v12) in enumerate(zip(raw_rows, v12_rows)):
        if (
            v12["source_index"] != index
            or v12["known_label_for_offline_evaluation"]
            != source["answer"]
        ):
            raise SystemExit(f"source alignment failure at index {index}")
        if v12["validator_flags"]:
            raise SystemExit(f"v12 validator flags remain at index {index}")

        label = source["answer"]
        tier = v12["evidence_tier"]
        package = verified_facts(
            v12["calculator_facts"], v12["row_level_evidence"]
        )
        classify_messages = [
            {"role": "user", "content": source["question"]},
            {"role": "assistant", "content": classify_answer(label)},
        ]
        classify_record = portable_record(
            classify_messages, index, label, "classify_raw", tier
        )
        classify_rows.append(classify_record)

        diagnostic_ready = v12["accepted_for_sft"]
        diagnostic_output = None
        diagnostic_messages = None
        if diagnostic_ready:
            diagnostic_output = diagnostic_answer(
                label,
                tier,
                package,
                v12["quarantine_reason"],
            )
            diagnostic_messages = [
                {"role": "system", "content": DIAGNOSTIC_SYSTEM},
                {
                    "role": "user",
                    "content": diagnostic_user(source["question"], package),
                },
                {"role": "assistant", "content": diagnostic_output},
            ]
            diagnose_rows.append(
                portable_record(
                    diagnostic_messages,
                    index,
                    label,
                    "diagnose_from_verified_facts",
                    tier,
                )
            )
        else:
            quarantine_rows.append(
                {
                    "source_index": index,
                    "label": label,
                    "evidence_tier": tier,
                    "reason": v12["quarantine_reason"],
                    "verified_facts": package,
                }
            )

        ledger_rows.append(
            {
                "source_index": index,
                "label": label,
                "evidence_tier": tier,
                "diagnostic_ready": diagnostic_ready,
                "diagnostic_limitation": v12["quarantine_reason"],
                "verified_facts": package,
                "classify_raw_answer": classify_answer(label),
                "diagnostic_answer": diagnostic_output,
            }
        )

    multitask_rows = classify_rows + diagnose_rows
    OUTDIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(LEDGER, ledger_rows)
    write_jsonl(CLASSIFY, classify_rows)
    write_jsonl(DIAGNOSE, diagnose_rows)
    write_jsonl(MULTITASK, multitask_rows)
    write_jsonl(QUARANTINE, quarantine_rows)

    summary = {
        "source_records": len(raw_rows),
        "classify_raw_records": len(classify_rows),
        "diagnose_from_verified_facts_records": len(diagnose_rows),
        "multitask_records": len(multitask_rows),
        "diagnostic_quarantine_records": len(quarantine_rows),
        "diagnostic_labels": Counter(
            row["metadata"]["label"] for row in diagnose_rows
        ),
        "diagnostic_tiers": Counter(
            row["metadata"]["evidence_tier"] for row in diagnose_rows
        ),
        "classify_labels": Counter(
            row["metadata"]["label"] for row in classify_rows
        ),
        "raw_sha256": hashlib.sha256(RAW.read_bytes()).hexdigest(),
        "v12_audit_sha256": hashlib.sha256(
            V12_AUDIT.read_bytes()
        ).hexdigest(),
        "diagnostic_system_sha256": hashlib.sha256(
            DIAGNOSTIC_SYSTEM.encode()
        ).hexdigest(),
    }
    SUMMARY.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

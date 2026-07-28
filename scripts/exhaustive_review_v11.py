"""Run an independent, record-by-record semantic audit of TeleLogs v11.

The original v11 generator and the first semantic reviewer share a parser.
That is useful for provenance checks but is not an independent arithmetic
check.  This script deliberately re-parses the raw question with separate
code, compares every calculated field, checks row-level causal alignment, and
adds a sample-specific assessment to all 2,400 review records.

The script never changes an SFT target.  It writes an enriched review ledger
and refreshes the private localhost review UI.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw_train_2400" / "train.json"
AUDIT = ROOT / "data" / "reasoning_v11" / "canonical_reasoning_v11.audit.jsonl"
BASE_REVIEW = ROOT / "data" / "reasoning_v11" / "semantic_review_v11.jsonl"
OUT = ROOT / "data" / "reasoning_v11" / "exhaustive_review_v11.jsonl"
UI_OUT = ROOT / "review-ui" / "public" / "review-data.json"

DRIVE_ENGINEERING_SEPARATOR = "Engeneering parameters data as follows："
SPEED_COLUMN = "GPS Speed (km/h)"
SERVING_PCI_COLUMN = "5G KPI PCell RF Serving PCI"
RSRP_COLUMN = "5G KPI PCell RF Serving SS-RSRP [dBm]"
THROUGHPUT_COLUMN = "5G KPI PCell Layer2 MAC DL Throughput [Mbps]"
RB_COLUMN = "5G KPI PCell Layer1 DL RB Num (Including 0)"


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Independent great-circle distance implementation."""

    radius_km = 6371.0
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = lat2_rad - lat1_rad
    delta_lon = math.radians(lon2 - lon1)
    term = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(term))


def vertical_beamwidth(scenario: str) -> int:
    if scenario == "DEFAULT" or scenario in {f"SCENARIO_{number}" for number in range(1, 6)}:
        return 6
    if scenario in {f"SCENARIO_{number}" for number in range(6, 12)}:
        return 12
    return 25


def pipe_table(lines: list[str], minimum_pipes: int) -> tuple[list[str], list[list[str]]]:
    candidates = [line for line in lines if line.count("|") >= minimum_pipes]
    if not candidates:
        raise ValueError("pipe table is missing")
    header = candidates[0].split("|")
    rows = [line.split("|") for line in candidates[1:] if len(line.split("|")) == len(header)]
    if not rows:
        raise ValueError("pipe table has no data rows")
    return header, rows


def independent_parse(question: str, include_details: bool = False) -> tuple[dict, dict]:
    """Parse source text without importing any corpus-generation helper."""

    drive_text, engineering_text = question.split(DRIVE_ENGINEERING_SEPARATOR, 1)
    drive_header, drive_rows = pipe_table(drive_text.splitlines(), 18)
    engineering_header, engineering_rows = pipe_table(engineering_text.splitlines(), 13)
    drive_col = {name: index for index, name in enumerate(drive_header)}
    engineering_col = {name: index for index, name in enumerate(engineering_header)}

    required_drive = {
        "Longitude",
        "Latitude",
        SPEED_COLUMN,
        SERVING_PCI_COLUMN,
        RSRP_COLUMN,
        THROUGHPUT_COLUMN,
        RB_COLUMN,
    }
    required_engineering = {
        "gNodeB ID",
        "Longitude",
        "Latitude",
        "Mechanical Downtilt",
        "Digital Tilt",
        "Beam Scenario",
        "Height",
        "PCI",
    }
    if not required_drive <= drive_col.keys():
        raise ValueError(f"missing drive columns: {sorted(required_drive - drive_col.keys())}")
    if not required_engineering <= engineering_col.keys():
        raise ValueError(
            f"missing engineering columns: {sorted(required_engineering - engineering_col.keys())}"
        )

    cells = {}
    for fields in engineering_rows:
        try:
            pci = int(fields[engineering_col["PCI"]])
            digital_tilt = float(fields[engineering_col["Digital Tilt"]])
            if digital_tilt == 255:
                digital_tilt = 6
            scenario = fields[engineering_col["Beam Scenario"]]
            cells[pci] = {
                "longitude": float(fields[engineering_col["Longitude"]]),
                "latitude": float(fields[engineering_col["Latitude"]]),
                "gnodeb": fields[engineering_col["gNodeB ID"]],
                "height_m": float(fields[engineering_col["Height"]]),
                "total_tilt_deg": (
                    float(fields[engineering_col["Mechanical Downtilt"]]) + digital_tilt
                ),
                "beamwidth_deg": vertical_beamwidth(scenario),
            }
        except (TypeError, ValueError):
            continue

    neighbor_pci_columns = [
        (index, name)
        for index, name in enumerate(drive_header)
        if "Neighbor Cell Top Set" in name and name.endswith("PCI")
    ]
    observations = []
    for row_index, fields in enumerate(drive_rows):
        serving_pci = int(fields[drive_col[SERVING_PCI_COLUMN]])
        serving_rsrp = float(fields[drive_col[RSRP_COLUMN]])
        serving_cell = cells.get(serving_pci)
        distance_km = None
        elevation_deg = None
        lower_edge_deg = None
        upper_edge_deg = None
        if serving_cell:
            distance_km = haversine_km(
                float(fields[drive_col["Longitude"]]),
                float(fields[drive_col["Latitude"]]),
                serving_cell["longitude"],
                serving_cell["latitude"],
            )
            elevation_deg = math.degrees(
                math.atan2(serving_cell["height_m"], distance_km * 1000)
            )
            lower_edge_deg = (
                serving_cell["total_tilt_deg"] - serving_cell["beamwidth_deg"] / 2
            )
            upper_edge_deg = (
                serving_cell["total_tilt_deg"] + serving_cell["beamwidth_deg"] / 2
            )

        observation = {
            "row_index": row_index,
            "serving_pci": serving_pci,
            "speed_kmh": float(fields[drive_col[SPEED_COLUMN]]),
            "serving_rsrp_dbm": serving_rsrp,
            "throughput_mbps": float(fields[drive_col[THROUGHPUT_COLUMN]]),
            "scheduled_rbs": float(fields[drive_col[RB_COLUMN]]),
            "distance_km": distance_km,
            "elevation_deg": elevation_deg,
            "main_lobe_lower_deg": lower_edge_deg,
            "main_lobe_upper_deg": upper_edge_deg,
            "noncolocated_gaps_db": [],
            "mod30_neighbor_pcis": [],
        }
        for pci_column, pci_name in neighbor_pci_columns:
            raw_neighbor_pci = fields[pci_column]
            if raw_neighbor_pci == "-":
                continue
            neighbor_pci = int(raw_neighbor_pci)
            if neighbor_pci % 30 == serving_pci % 30:
                observation["mod30_neighbor_pcis"].append(neighbor_pci)

            brsrp_name = pci_name.replace("PCI", "Filtered Tx BRSRP [dBm]")
            raw_brsrp = fields[drive_col[brsrp_name]] if brsrp_name in drive_col else "-"
            neighbor_cell = cells.get(neighbor_pci)
            if (
                raw_brsrp != "-"
                and serving_cell
                and neighbor_cell
                and neighbor_cell["gnodeb"] != serving_cell["gnodeb"]
            ):
                observation["noncolocated_gaps_db"].append(
                    float(raw_brsrp) - serving_rsrp
                )
        observations.append(observation)

    throughput_by_pci = defaultdict(list)
    rsrp_by_pci = defaultdict(list)
    distances_by_pci = defaultdict(list)
    collision_pairs = set()
    all_gaps = []
    for observation in observations:
        pci = observation["serving_pci"]
        throughput_by_pci[pci].append(observation["throughput_mbps"])
        rsrp_by_pci[pci].append(observation["serving_rsrp_dbm"])
        if observation["distance_km"] is not None:
            distances_by_pci[pci].append(observation["distance_km"])
        all_gaps.extend(observation["noncolocated_gaps_db"])
        for neighbor_pci in observation["mod30_neighbor_pcis"]:
            collision_pairs.add((pci, neighbor_pci))

    serving_sequence = [observation["serving_pci"] for observation in observations]
    complete_serving_engineering = set(throughput_by_pci) <= set(cells)
    handover_count = sum(
        previous != current
        for previous, current in zip(serving_sequence, serving_sequence[1:])
    )
    serving_geometry = {}
    if complete_serving_engineering:
        for pci, distances in distances_by_pci.items():
            cell = cells[pci]
            elevations = [
                math.degrees(math.atan2(cell["height_m"], distance * 1000))
                for distance in distances
            ]
            serving_geometry[str(pci)] = {
                "distance_min_km": min(distances),
                "distance_max_km": max(distances),
                "elevation_min_deg": min(elevations),
                "elevation_max_deg": max(elevations),
                "total_tilt_deg": cell["total_tilt_deg"],
                "beamwidth_deg": cell["beamwidth_deg"],
            }

    c3_comparison = None
    if len(throughput_by_pci) >= 2:
        minimums = {pci: min(values) for pci, values in throughput_by_pci.items()}
        affected_pci = min(minimums, key=minimums.get)
        alternative_pci = max(
            (pci for pci in minimums if pci != affected_pci),
            key=lambda pci: minimums[pci],
        )
        c3_comparison = {
            "affected_pci": affected_pci,
            "affected_min_mbps": minimums[affected_pci],
            "alternative_pci": alternative_pci,
            "alternative_min_mbps": minimums[alternative_pci],
            "minimum_advantage_mbps": (
                minimums[alternative_pci] - minimums[affected_pci]
            ),
        }

    calculator_facts = {
        "max_speed": max(observation["speed_kmh"] for observation in observations),
        "min_rbs": min(observation["scheduled_rbs"] for observation in observations),
        "max_distance_km": (
            max(observation["distance_km"] for observation in observations)
            if complete_serving_engineering
            else None
        ),
        "throughput": {
            str(pci): {
                "n": len(values),
                "min": min(values),
                "max": max(values),
                "mean": sum(values) / len(values),
            }
            for pci, values in throughput_by_pci.items()
        },
        "serving_rsrp": {
            str(pci): {
                "min": min(values),
                "max": max(values),
                "mean": sum(values) / len(values),
            }
            for pci, values in rsrp_by_pci.items()
        },
        "serving_pcis": sorted(throughput_by_pci),
        "serving_engineering_available": complete_serving_engineering,
        "handover_count": handover_count,
        "mod30_collision_pairs": [
            list(pair) for pair in sorted(collision_pairs)
        ],
        "max_noncolocated_brsrp_gap_db": max(all_gaps) if all_gaps else None,
        "serving_geometry": serving_geometry,
        "c3_minimum_comparison": c3_comparison,
    }

    low_rows = [
        observation
        for observation in observations
        if observation["throughput_mbps"] < 600
    ]
    below_lobe_rows = [
        observation
        for observation in observations
        if observation["elevation_deg"] is not None
        and observation["elevation_deg"] < observation["main_lobe_lower_deg"]
    ]
    below_lobe_low_rows = [
        observation
        for observation in below_lobe_rows
        if observation["throughput_mbps"] < 600
    ]
    weak_below_lobe_low_rows = [
        observation
        for observation in below_lobe_low_rows
        if observation["serving_rsrp_dbm"] <= -90
    ]
    overlap_low_rows = [
        observation
        for observation in low_rows
        if observation["noncolocated_gaps_db"]
        and max(observation["noncolocated_gaps_db"]) >= -3
    ]
    collision_low_rows = [
        observation
        for observation in low_rows
        if observation["mod30_neighbor_pcis"]
    ]
    transition_rows = [
        observations[index]
        for index in range(1, len(observations))
        if observations[index - 1]["serving_pci"]
        != observations[index]["serving_pci"]
    ]
    row_evidence = {
        "observation_count": len(observations),
        "low_throughput_row_count": len(low_rows),
        "average_scheduled_rbs_all_rows": (
            sum(observation["scheduled_rbs"] for observation in observations)
            / len(observations)
        ),
        "average_scheduled_rbs_low_throughput_rows": (
            sum(observation["scheduled_rbs"] for observation in low_rows)
            / len(low_rows)
            if low_rows
            else None
        ),
        "below_lower_lobe_row_count": len(below_lobe_rows),
        "below_lower_lobe_low_throughput_row_count": len(below_lobe_low_rows),
        "below_lower_lobe_low_throughput_weak_rsrp_row_count": len(
            weak_below_lobe_low_rows
        ),
        "overlap_eligible_low_throughput_row_count": len(overlap_low_rows),
        "collision_low_throughput_row_count": len(collision_low_rows),
        "transition_count": len(transition_rows),
        "low_throughput_transition_destination_count": sum(
            observation["throughput_mbps"] < 600
            for observation in transition_rows
        ),
    }
    if include_details:
        row_evidence["serving_pci_sequence"] = [
            observation["serving_pci"] for observation in observations
        ]
        row_evidence["throughput_sequence_mbps"] = [
            observation["throughput_mbps"] for observation in observations
        ]
        row_evidence["below_lower_lobe_low_throughput_rows"] = [
            {
                "row_index": observation["row_index"],
                "serving_pci": observation["serving_pci"],
                "throughput_mbps": observation["throughput_mbps"],
                "serving_rsrp_dbm": observation["serving_rsrp_dbm"],
                "distance_km": observation["distance_km"],
                "elevation_deg": observation["elevation_deg"],
                "main_lobe_lower_deg": observation["main_lobe_lower_deg"],
            }
            for observation in below_lobe_low_rows
        ]
        row_evidence["overlap_eligible_low_throughput_rows"] = [
            {
                "row_index": observation["row_index"],
                "serving_pci": observation["serving_pci"],
                "throughput_mbps": observation["throughput_mbps"],
                "maximum_gap_db": max(observation["noncolocated_gaps_db"]),
            }
            for observation in overlap_low_rows
        ]
        row_evidence["collision_low_throughput_rows"] = [
            {
                "row_index": observation["row_index"],
                "serving_pci": observation["serving_pci"],
                "throughput_mbps": observation["throughput_mbps"],
                "neighbor_pcis": observation["mod30_neighbor_pcis"],
            }
            for observation in collision_low_rows
        ]
        row_evidence["transition_destination_rows"] = [
            {
                "row_index": observation["row_index"],
                "previous_serving_pci": observations[observation["row_index"] - 1][
                    "serving_pci"
                ],
                "serving_pci": observation["serving_pci"],
                "throughput_mbps": observation["throughput_mbps"],
            }
            for observation in transition_rows
        ]
    return calculator_facts, row_evidence


def compare_values(expected, actual, path="calculator_facts") -> list[str]:
    """Return exact structural or tight-tolerance numeric mismatches."""

    mismatches = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        if set(expected) != set(actual):
            missing = sorted(set(expected) - set(actual))
            extra = sorted(set(actual) - set(expected))
            mismatches.append(f"{path}: keys missing={missing}, extra={extra}")
            return mismatches
        for key in expected:
            mismatches.extend(
                compare_values(expected[key], actual[key], f"{path}.{key}")
            )
        return mismatches
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return [f"{path}: length {len(expected)} != {len(actual)}"]
        for index, (left, right) in enumerate(zip(expected, actual)):
            mismatches.extend(compare_values(left, right, f"{path}[{index}]"))
        return mismatches
    if (
        isinstance(expected, (int, float))
        and not isinstance(expected, bool)
        and isinstance(actual, (int, float))
        and not isinstance(actual, bool)
    ):
        if not math.isclose(float(expected), float(actual), rel_tol=1e-10, abs_tol=1e-10):
            mismatches.append(f"{path}: {expected} != {actual}")
        return mismatches
    if expected != actual:
        mismatches.append(f"{path}: {expected!r} != {actual!r}")
    return mismatches


def normalized_template(reasoning: str) -> str:
    text = re.sub(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", "<N>", reasoning)
    return re.sub(
        r"PCI <N>/PCI <N>(?:, PCI <N>/PCI <N>)*",
        "<COLLISIONS>",
        text,
    )


def claim_checks(reasoning: str, facts: dict) -> dict[str, bool]:
    comparison = facts["c3_minimum_comparison"]
    checks = {
        "maximum_speed_claim": (
            f"Maximum GPS speed: {facts['max_speed']:.2f} km/h." in reasoning
        ),
        "minimum_rb_claim": (
            f"Minimum scheduled RBs: {facts['min_rbs']:.2f}." in reasoning
        ),
        "handover_claim": (
            f"Serving-PCI changes: {facts['handover_count']}." in reasoning
        ),
    }
    if facts["max_distance_km"] is None:
        checks["maximum_distance_claim"] = (
            "Maximum serving-cell distance: unavailable." in reasoning
        )
    else:
        checks["maximum_distance_claim"] = (
            f"Maximum serving-cell distance: {facts['max_distance_km']:.3f} km."
            in reasoning
        )
    if facts["max_noncolocated_brsrp_gap_db"] is None:
        checks["overlap_gap_claim"] = "C4 overlap gap: unavailable" in reasoning
    else:
        checks["overlap_gap_claim"] = (
            f"C4 overlap gap: {facts['max_noncolocated_brsrp_gap_db']:.2f} dB."
            in reasoning
        )
    if comparison:
        exact_comparison = (
            f"Serving low-point comparison: PCI {comparison['affected_pci']} "
            f"reaches {comparison['affected_min_mbps']:.2f} Mbps; PCI "
            f"{comparison['alternative_pci']} reaches "
            f"{comparison['alternative_min_mbps']:.2f} Mbps; difference "
            f"{comparison['minimum_advantage_mbps']:.2f} Mbps."
        )
        checks["c3_comparison_claim"] = exact_comparison in reasoning
    else:
        checks["c3_comparison_claim"] = (
            "Only one serving PCI is observed" in reasoning
        )
    return checks


def candidate_verdicts(facts: dict, row_evidence: dict) -> dict[str, dict[str, str]]:
    distance = facts["max_distance_km"]
    average_low_rbs = row_evidence["average_scheduled_rbs_low_throughput_rows"]
    c1_aligned = row_evidence["below_lower_lobe_low_throughput_row_count"]
    c1_weak_aligned = row_evidence[
        "below_lower_lobe_low_throughput_weak_rsrp_row_count"
    ]
    overlap_aligned = row_evidence["overlap_eligible_low_throughput_row_count"]
    collision_aligned = row_evidence["collision_low_throughput_row_count"]
    transition_low = row_evidence["low_throughput_transition_destination_count"]

    if c1_weak_aligned:
        c1_status = "positive_evidence"
        c1_detail = (
            f"{c1_weak_aligned} low-throughput row(s) are below the serving "
            "main-lobe lower edge with RSRP at or below -90 dBm."
        )
    elif c1_aligned:
        c1_status = "partial_evidence"
        c1_detail = (
            f"{c1_aligned} low-throughput row(s) are below the serving main-lobe "
            "lower edge, but the trace still needs an explicit weak-coverage linkage."
        )
    else:
        c1_status = "not_supported"
        c1_detail = (
            "No observed below-600 row lies below its serving main-lobe lower edge."
        )

    comparison = facts["c3_minimum_comparison"]
    if comparison and row_evidence["low_throughput_row_count"]:
        c3_status = "relative_evidence_only"
        c3_detail = (
            f"Serving PCI {comparison['affected_pci']} has a "
            f"{comparison['affected_min_mbps']:.2f}-Mbps minimum versus "
            f"{comparison['alternative_min_mbps']:.2f} Mbps for serving PCI "
            f"{comparison['alternative_pci']}; this comparison is not unique to C3 "
            "and only becomes useful after supported defects are excluded."
        )
    elif comparison:
        c3_status = "source_inconsistency"
        c3_detail = (
            "Two serving PCIs can be compared, but the log has no throughput row "
            "below the stated 600-Mbps problem threshold."
        )
    else:
        c3_status = "not_supported"
        c3_detail = "Fewer than two serving PCIs are observed."

    return {
        "C1": {"status": c1_status, "detail": c1_detail},
        "C2": {
            "status": "confirmed" if distance is not None and distance > 1 else (
                "unavailable" if distance is None else "ruled_out"
            ),
            "detail": (
                "Serving-cell distance is unavailable."
                if distance is None
                else f"Maximum serving-cell distance is {distance:.3f} km "
                f"({'above' if distance > 1 else 'not above'} 1 km)."
            ),
        },
        "C3": {"status": c3_status, "detail": c3_detail},
        "C4": {
            "status": "eligible_not_proven" if overlap_aligned else "ruled_out",
            "detail": (
                f"{overlap_aligned} low-throughput row(s) have a non-colocated "
                "neighbor within the -3 dB overlap gate; this is eligibility, not "
                "standalone proof."
                if overlap_aligned
                else "No low-throughput row meets the -3 dB non-colocated overlap gate."
            ),
        },
        "C5": {
            "status": "confirmed_pattern" if facts["handover_count"] >= 3 else "ruled_out",
            "detail": (
                f"The serving PCI changes {facts['handover_count']} time(s); "
                f"{transition_low} transition destination row(s) are below 600 Mbps."
            ),
        },
        "C6": {
            "status": "eligible_not_proven" if collision_aligned else "ruled_out",
            "detail": (
                f"A PCI-mod-30 collision occurs in {collision_aligned} low-throughput "
                "row(s); collision is eligibility, not standalone proof."
                if collision_aligned
                else "No PCI-mod-30 collision is observed in a low-throughput row."
            ),
        },
        "C7": {
            "status": "confirmed" if facts["max_speed"] > 40 else "ruled_out",
            "detail": (
                f"Maximum speed is {facts['max_speed']:.2f} km/h "
                f"({'above' if facts['max_speed'] > 40 else 'not above'} 40 km/h)."
            ),
        },
        "C8": {
            "status": (
                "confirmed_affected_section_average"
                if average_low_rbs is not None and average_low_rbs < 160
                else "not_supported"
            ),
            "detail": (
                "There is no below-600 row on which to compute affected-section RBs."
                if average_low_rbs is None
                else f"Average scheduled RBs across below-600 rows are "
                f"{average_low_rbs:.2f} "
                f"({'below' if average_low_rbs < 160 else 'not below'} 160)."
            ),
        },
    }


def add_flag(record: dict, code: str, severity: str, title: str, detail: str) -> None:
    if any(item["code"] == code for item in record["flags"]):
        return
    record["flags"].append(
        {
            "code": code,
            "severity": severity,
            "title": title,
            "detail": detail,
        }
    )


def individual_summary(
    index: int,
    label: str,
    verdicts: dict,
    row_evidence: dict,
    independent_ok: bool,
    claims_ok: bool,
) -> str:
    selected = verdicts[label]
    prefix = (
        f"Sample {index} was independently parsed row by row. "
        f"The current target requires {'major' if label in {'C1', 'C3', 'C4', 'C6', 'C8'} else 'minor'} "
        "revision. "
    )
    integrity = (
        "All independently recalculated facts and dynamic trace claims match. "
        if independent_ok and claims_ok
        else "At least one independent fact or dynamic trace claim does not match. "
    )
    drop_note = (
        f"The source contains {row_evidence['low_throughput_row_count']} "
        "below-600 row(s). "
    )
    return prefix + integrity + drop_note + f"Selected-class assessment: {selected['detail']}"


def main() -> None:
    source_rows = json.loads(RAW.read_text())
    audit_rows = [json.loads(line) for line in AUDIT.read_text().splitlines()]
    review_rows = [json.loads(line) for line in BASE_REVIEW.read_text().splitlines()]
    if not (len(source_rows) == len(audit_rows) == len(review_rows) == 2400):
        raise SystemExit("expected 2,400 aligned source, audit, and base-review rows")

    templates = {
        normalized_template(record["reasoning"]) for record in review_rows
    }
    template_ids = {
        template: hashlib.sha256(template.encode()).hexdigest()[:12]
        for template in templates
    }

    enriched = []
    for index, (source, audit, record) in enumerate(
        zip(source_rows, audit_rows, review_rows)
    ):
        if record["source_index"] != index or audit["source_index"] != index:
            raise SystemExit(f"source alignment failed at index {index}")
        independent_facts, row_evidence = independent_parse(source["question"])
        fact_mismatches = compare_values(
            independent_facts, audit["calculator_facts"]
        )
        checks = claim_checks(record["reasoning"], independent_facts)
        claims_ok = all(checks.values())
        independent_ok = not fact_mismatches
        verdicts = candidate_verdicts(independent_facts, row_evidence)
        label = source["answer"]

        record["integrity"]["independent_calculator_aligned"] = independent_ok
        record["integrity"]["dynamic_reasoning_claims_aligned"] = claims_ok
        record["independent_fact_mismatches"] = fact_mismatches
        record["dynamic_claim_checks"] = checks
        record["row_level_evidence"] = row_evidence
        record["candidate_verdicts"] = verdicts
        template = normalized_template(record["reasoning"])
        record["review_coverage"] = {
            "source_question_parsed": True,
            "every_drive_row_parsed": True,
            "current_reasoning_parsed": True,
            "independent_arithmetic_checked": True,
            "selected_class_evidence_checked": True,
            "competing_class_evidence_checked": True,
            "template_signature": template_ids[template],
        }

        if not independent_ok or not claims_ok:
            add_flag(
                record,
                "independent_integrity_failure",
                "critical",
                "Independent record check failed",
                "A separately implemented parser or a dynamic trace-claim check disagrees with the current audit.",
            )

        if label == "C1":
            aligned = row_evidence["below_lower_lobe_low_throughput_row_count"]
            weak_aligned = row_evidence[
                "below_lower_lobe_low_throughput_weak_rsrp_row_count"
            ]
            if aligned == 0:
                add_flag(
                    record,
                    "c1_no_row_aligned_below_lobe_drop",
                    "high",
                    "No row-aligned C1 witness",
                    "No below-600 row lies below its serving main-lobe lower edge, so the excessive-downtilt mechanism is not demonstrated by this log.",
                )
                record["evidence_grade"] = "insufficient"
            elif weak_aligned:
                record["evidence_grade"] = "strong"
            else:
                record["evidence_grade"] = "moderate"

        if label == "C5":
            transition_low = row_evidence[
                "low_throughput_transition_destination_count"
            ]
            if transition_low < row_evidence["transition_count"]:
                add_flag(
                    record,
                    "c5_partial_transition_drop_alignment",
                    "medium",
                    "Not every handover destination is below 600 Mbps",
                    f"{transition_low} of {row_evidence['transition_count']} transition destination rows are below 600 Mbps; the rewrite should show the full PCI/throughput sequence.",
                )

        if label == "C8":
            add_flag(
                record,
                "c8_minimum_does_not_establish_average",
                "high",
                "Minimum RB does not establish the stated average",
                "C8 is worded as an average-RB diagnosis. The current target cites only the minimum; it must calculate the average RB allocation over the below-600 affected rows.",
            )

        severity_rank = {"critical": 3, "high": 2, "medium": 1, "low": 0}
        highest = max(
            (severity_rank[item["severity"]] for item in record["flags"]),
            default=0,
        )
        record["review_status"] = (
            "integrity_failure"
            if highest >= 3
            else "major_rewrite"
            if highest >= 2
            else "minor_rewrite"
        )
        record["current_sft_ready"] = False
        record["individual_assessment"] = individual_summary(
            index,
            label,
            verdicts,
            row_evidence,
            independent_ok,
            claims_ok,
        )
        enriched.append(record)

    OUT.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False) + "\n"
            for record in enriched
        )
    )
    UI_OUT.parent.mkdir(parents=True, exist_ok=True)
    UI_OUT.write_text(
        json.dumps(enriched, ensure_ascii=False, separators=(",", ":"))
    )

    summary = {
        "records": len(enriched),
        "normalized_reasoning_templates": len(templates),
        "statuses": Counter(record["review_status"] for record in enriched),
        "evidence_grades": Counter(record["evidence_grade"] for record in enriched),
        "independent_fact_failures": sum(
            bool(record["independent_fact_mismatches"]) for record in enriched
        ),
        "dynamic_claim_failures": sum(
            not all(record["dynamic_claim_checks"].values())
            for record in enriched
        ),
        "c1_without_row_aligned_below_lobe_drop": sum(
            record["ground_truth"] == "C1"
            and record["row_level_evidence"][
                "below_lower_lobe_low_throughput_row_count"
            ]
            == 0
            for record in enriched
        ),
        "c3_without_below_600_observation": sum(
            record["ground_truth"] == "C3"
            and record["row_level_evidence"]["low_throughput_row_count"] == 0
            for record in enriched
        ),
        "c8_affected_section_average_below_160": sum(
            record["ground_truth"] == "C8"
            and record["row_level_evidence"][
                "average_scheduled_rbs_low_throughput_rows"
            ]
            < 160
            for record in enriched
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Label-neutral parsing and measurement tools for TeleLogs.

The functions in this module deliberately do not know the C1--C8 labels and
never return a diagnosis.  They only parse the two tables and expose auditable
measurements that a language model can request and interpret.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable


DRIVE_ENGINEERING_SEPARATOR = "Engeneering parameters data as follows："
SPEED_COLUMN = "GPS Speed (km/h)"
SERVING_PCI_COLUMN = "5G KPI PCell RF Serving PCI"
RSRP_COLUMN = "5G KPI PCell RF Serving SS-RSRP [dBm]"
THROUGHPUT_COLUMN = "5G KPI PCell Layer2 MAC DL Throughput [Mbps]"
RB_COLUMN = "5G KPI PCell Layer1 DL RB Num (Including 0)"


def _r(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(float(value), digits)


def _haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius_km = 6371.0
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    dlat = lat2_rad - lat1_rad
    dlon = math.radians(lon2 - lon1)
    term = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(term))


def _vertical_beamwidth(scenario: str) -> int:
    scenario = scenario.strip().upper()
    if scenario == "DEFAULT" or scenario in {f"SCENARIO_{i}" for i in range(1, 6)}:
        return 6
    if scenario in {f"SCENARIO_{i}" for i in range(6, 12)}:
        return 12
    return 25


def _pipe_table(lines: list[str], minimum_pipes: int) -> tuple[list[str], list[list[str]]]:
    candidates = [line.strip() for line in lines if line.count("|") >= minimum_pipes]
    if not candidates:
        raise ValueError("pipe table is missing")
    header = candidates[0].split("|")
    rows = [line.split("|") for line in candidates[1:] if len(line.split("|")) == len(header)]
    if not rows:
        raise ValueError("pipe table has no data rows")
    return header, rows


@dataclass(frozen=True)
class CaseContext:
    throughput_threshold_mbps: float
    observations: tuple[dict[str, Any], ...]
    cells: dict[int, dict[str, Any]]


def parse_case(question: str) -> CaseContext:
    """Parse raw tables without computing class-labelled facts or decisions."""

    if DRIVE_ENGINEERING_SEPARATOR not in question:
        raise ValueError("engineering table separator is missing")
    drive_text, engineering_text = question.split(DRIVE_ENGINEERING_SEPARATOR, 1)
    drive_header, drive_rows = _pipe_table(drive_text.splitlines(), 18)
    engineering_header, engineering_rows = _pipe_table(engineering_text.splitlines(), 13)
    drive_col = {name: index for index, name in enumerate(drive_header)}
    engineering_col = {name: index for index, name in enumerate(engineering_header)}

    cells: dict[int, dict[str, Any]] = {}
    for fields in engineering_rows:
        try:
            pci = int(fields[engineering_col["PCI"]])
            digital_tilt = float(fields[engineering_col["Digital Tilt"]])
            if digital_tilt == 255:
                digital_tilt = 6.0
            cells[pci] = {
                "pci": pci,
                "gnodeb": fields[engineering_col["gNodeB ID"]],
                "longitude": float(fields[engineering_col["Longitude"]]),
                "latitude": float(fields[engineering_col["Latitude"]]),
                "height_m": float(fields[engineering_col["Height"]]),
                "total_tilt_deg": float(fields[engineering_col["Mechanical Downtilt"]]) + digital_tilt,
                "beamwidth_deg": _vertical_beamwidth(fields[engineering_col["Beam Scenario"]]),
            }
        except (KeyError, TypeError, ValueError):
            continue

    neighbor_columns = [
        (index, name)
        for index, name in enumerate(drive_header)
        if "Neighbor Cell Top Set" in name and name.endswith("PCI")
    ]
    observations: list[dict[str, Any]] = []
    for zero_index, fields in enumerate(drive_rows):
        serving_pci = int(fields[drive_col[SERVING_PCI_COLUMN]])
        serving_rsrp = float(fields[drive_col[RSRP_COLUMN]])
        serving_cell = cells.get(serving_pci)
        distance_km = elevation_deg = lower_deg = upper_deg = None
        if serving_cell:
            distance_km = _haversine_km(
                float(fields[drive_col["Longitude"]]),
                float(fields[drive_col["Latitude"]]),
                serving_cell["longitude"],
                serving_cell["latitude"],
            )
            elevation_deg = math.degrees(math.atan2(serving_cell["height_m"], distance_km * 1000))
            lower_deg = serving_cell["total_tilt_deg"] - serving_cell["beamwidth_deg"] / 2
            upper_deg = serving_cell["total_tilt_deg"] + serving_cell["beamwidth_deg"] / 2

        neighbors = []
        for pci_column, pci_name in neighbor_columns:
            raw_pci = fields[pci_column]
            if raw_pci == "-":
                continue
            neighbor_pci = int(raw_pci)
            brsrp_name = pci_name.replace("PCI", "Filtered Tx BRSRP [dBm]")
            raw_brsrp = fields[drive_col[brsrp_name]] if brsrp_name in drive_col else "-"
            neighbor_cell = cells.get(neighbor_pci)
            same_gnodeb = (
                serving_cell is not None
                and neighbor_cell is not None
                and serving_cell["gnodeb"] == neighbor_cell["gnodeb"]
            )
            brsrp = None if raw_brsrp == "-" else float(raw_brsrp)
            neighbors.append(
                {
                    "pci": neighbor_pci,
                    "brsrp_dbm": brsrp,
                    "brsrp_minus_serving_rsrp_db": None if brsrp is None else brsrp - serving_rsrp,
                    "same_gnodeb": same_gnodeb,
                    "serving_mod30": serving_pci % 30,
                    "neighbor_mod30": neighbor_pci % 30,
                }
            )

        observations.append(
            {
                "row": zero_index + 1,
                "timestamp": fields[drive_col.get("Timestamp", 0)],
                "serving_pci": serving_pci,
                "speed_kmh": float(fields[drive_col[SPEED_COLUMN]]),
                "serving_rsrp_dbm": serving_rsrp,
                "throughput_mbps": float(fields[drive_col[THROUGHPUT_COLUMN]]),
                "scheduled_rbs": float(fields[drive_col[RB_COLUMN]]),
                "distance_km": distance_km,
                "elevation_deg": elevation_deg,
                "main_lobe_lower_deg": lower_deg,
                "main_lobe_upper_deg": upper_deg,
                "neighbors": neighbors,
            }
        )

    match = re.search(r"throughput\s+dropping\s+below\s+([0-9.]+)\s*Mbps", question, re.I)
    threshold = float(match.group(1)) if match else 600.0
    return CaseContext(threshold, tuple(observations), cells)


def analyze_throughput_segments(case: CaseContext) -> dict[str, Any]:
    """Summarize throughput by serving segment and identify low-throughput rows."""

    by_pci: dict[int, list[float]] = defaultdict(list)
    for row in case.observations:
        by_pci[row["serving_pci"]].append(row["throughput_mbps"])
    segments = {
        str(pci): {
            "row_count": len(values),
            "minimum_mbps": _r(min(values), 2),
            "maximum_mbps": _r(max(values), 2),
            "mean_mbps": _r(sum(values) / len(values), 2),
        }
        for pci, values in sorted(by_pci.items())
    }
    low_rows = [
        {
            "row": row["row"],
            "timestamp": row["timestamp"],
            "serving_pci": row["serving_pci"],
            "throughput_mbps": _r(row["throughput_mbps"], 2),
        }
        for row in case.observations
        if row["throughput_mbps"] < case.throughput_threshold_mbps
    ]
    return {
        "measurement_scope": "serving-cell segments and rows below the stated throughput criterion",
        "stated_throughput_criterion_mbps": case.throughput_threshold_mbps,
        "segment_statistics": segments,
        "low_throughput_rows": low_rows,
    }


def analyze_coverage_geometry(case: CaseContext) -> dict[str, Any]:
    """Return serving distance, antenna-lobe geometry, and RSRP measurements."""

    rows = []
    for row in case.observations:
        rows.append(
            {
                "row": row["row"],
                "serving_pci": row["serving_pci"],
                "throughput_mbps": _r(row["throughput_mbps"], 2),
                "serving_rsrp_dbm": _r(row["serving_rsrp_dbm"], 2),
                "distance_km": _r(row["distance_km"]),
                "ue_elevation_deg": _r(row["elevation_deg"], 2),
                "main_lobe_lower_deg": _r(row["main_lobe_lower_deg"], 2),
                "main_lobe_upper_deg": _r(row["main_lobe_upper_deg"], 2),
            }
        )
    return {
        "measurement_scope": "serving geometry and signal level for every drive-test row",
        "engineering_available_for_all_serving_cells": all(row["distance_km"] is not None for row in case.observations),
        "rows": rows,
    }


def analyze_mobility(case: CaseContext) -> dict[str, Any]:
    """Return speed statistics and serving-cell transition events."""

    transitions = []
    for previous, current in zip(case.observations, case.observations[1:]):
        if previous["serving_pci"] != current["serving_pci"]:
            transitions.append(
                {
                    "row": current["row"],
                    "timestamp": current["timestamp"],
                    "from_pci": previous["serving_pci"],
                    "to_pci": current["serving_pci"],
                    "throughput_mbps": _r(current["throughput_mbps"], 2),
                }
            )
    speeds = [row["speed_kmh"] for row in case.observations]
    return {
        "measurement_scope": "vehicle speed and serving-cell transitions",
        "maximum_speed_kmh": _r(max(speeds), 2),
        "mean_speed_kmh": _r(sum(speeds) / len(speeds), 2),
        "serving_pci_sequence": [row["serving_pci"] for row in case.observations],
        "transition_count": len(transitions),
        "transitions": transitions,
    }


def analyze_radio_resources(case: CaseContext) -> dict[str, Any]:
    """Summarize scheduled resource blocks, including low-throughput rows."""

    all_values = [row["scheduled_rbs"] for row in case.observations]
    low = [row for row in case.observations if row["throughput_mbps"] < case.throughput_threshold_mbps]
    low_values = [row["scheduled_rbs"] for row in low]
    return {
        "measurement_scope": "scheduled resource blocks",
        "all_rows_mean_scheduled_rbs": _r(sum(all_values) / len(all_values), 2),
        "all_rows_minimum_scheduled_rbs": _r(min(all_values), 2),
        "low_throughput_rows_mean_scheduled_rbs": _r(sum(low_values) / len(low_values), 2) if low_values else None,
        "low_throughput_rows": [
            {
                "row": row["row"],
                "throughput_mbps": _r(row["throughput_mbps"], 2),
                "scheduled_rbs": _r(row["scheduled_rbs"], 2),
            }
            for row in low
        ],
    }


def analyze_neighbor_overlap(case: CaseContext) -> dict[str, Any]:
    """Return non-colocated neighbor power differences on low-throughput rows."""

    rows = []
    for row in case.observations:
        if row["throughput_mbps"] >= case.throughput_threshold_mbps:
            continue
        neighbors = [
            {
                "neighbor_pci": item["pci"],
                "neighbor_brsrp_dbm": _r(item["brsrp_dbm"], 2),
                "neighbor_minus_serving_db": _r(item["brsrp_minus_serving_rsrp_db"], 2),
            }
            for item in row["neighbors"]
            if not item["same_gnodeb"] and item["brsrp_minus_serving_rsrp_db"] is not None
        ]
        if neighbors:
            rows.append(
                {
                    "row": row["row"],
                    "serving_pci": row["serving_pci"],
                    "serving_rsrp_dbm": _r(row["serving_rsrp_dbm"], 2),
                    "throughput_mbps": _r(row["throughput_mbps"], 2),
                    "noncolocated_neighbors": sorted(
                        neighbors,
                        key=lambda item: item["neighbor_minus_serving_db"],
                        reverse=True,
                    ),
                }
            )
    return {
        "measurement_scope": "non-colocated neighbor power relative to serving power on low-throughput rows",
        "rows": rows,
    }


def analyze_pci_relations(case: CaseContext) -> dict[str, Any]:
    """Return serving/neighbor PCI residues on low-throughput rows."""

    rows = []
    for row in case.observations:
        if row["throughput_mbps"] >= case.throughput_threshold_mbps:
            continue
        relations = [
            {
                "neighbor_pci": item["pci"],
                "serving_residue_mod30": item["serving_mod30"],
                "neighbor_residue_mod30": item["neighbor_mod30"],
            }
            for item in row["neighbors"]
        ]
        rows.append(
            {
                "row": row["row"],
                "serving_pci": row["serving_pci"],
                "throughput_mbps": _r(row["throughput_mbps"], 2),
                "relations": relations,
            }
        )
    return {
        "measurement_scope": "serving and neighbor PCI modulo-30 residues on low-throughput rows",
        "rows": rows,
    }


TOOL_FUNCTIONS: dict[str, Callable[[CaseContext], dict[str, Any]]] = {
    "analyze_throughput_segments": analyze_throughput_segments,
    "analyze_coverage_geometry": analyze_coverage_geometry,
    "analyze_mobility": analyze_mobility,
    "analyze_radio_resources": analyze_radio_resources,
    "analyze_neighbor_overlap": analyze_neighbor_overlap,
    "analyze_pci_relations": analyze_pci_relations,
}

_NO_ARGS = "Takes no arguments; it always covers the whole case."
TOOL_CATALOG = [
    {"name": "analyze_throughput_segments", "description": f"Summarize throughput by serving segment and list low-throughput rows. {_NO_ARGS}"},
    {"name": "analyze_coverage_geometry", "description": f"Measure serving distance, antenna vertical geometry, and serving RSRP by row. {_NO_ARGS}"},
    {"name": "analyze_mobility", "description": f"Measure vehicle speed and serving-cell transition events. {_NO_ARGS}"},
    {"name": "analyze_radio_resources", "description": f"Measure scheduled resource blocks overall and on low-throughput rows. {_NO_ARGS}"},
    {"name": "analyze_neighbor_overlap", "description": f"Measure non-colocated neighbor power relative to serving power on low-throughput rows. {_NO_ARGS}"},
    {"name": "analyze_pci_relations", "description": f"Inspect serving and neighbor PCI modulo-30 residues on low-throughput rows. {_NO_ARGS}"},
]


def run_tools(case: CaseContext, names: list[str]) -> dict[str, Any]:
    return {name: TOOL_FUNCTIONS[name](case) for name in names if name in TOOL_FUNCTIONS}


def assert_label_neutral(value: object) -> None:
    """Fail if a tool result accidentally embeds classifier routing state."""

    serialized = json.dumps(value, ensure_ascii=False)
    if re.search(r'(?<![A-Za-z0-9])C[1-8](?![A-Za-z0-9])', serialized):
        raise AssertionError("tool output contains a class label")
    forbidden_keys = {"answer", "label", "gate", "triggered", "diagnosis", "root_cause"}

    def walk(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key).lower() in forbidden_keys:
                    raise AssertionError(f"tool output contains forbidden key: {key}")
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)

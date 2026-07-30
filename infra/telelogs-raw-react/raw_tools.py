"""Deterministic, mechanism-specific tools over one raw TeleLogs case."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from exhaustive_review_v11 import independent_parse  # noqa: E402


def _r2(value: Any) -> float | None:
    return None if value is None else round(float(value), 2)


def _r3(value: Any) -> float | None:
    return None if value is None else round(float(value), 3)


class RawCaseCalculator:
    """Parse raw tables lazily and expose only requested verified evidence."""

    def __init__(
        self,
        raw_table: str,
        c3_advantage_threshold_mbps: float = 142.5,
    ) -> None:
        self.raw_table = raw_table
        self.c3_advantage_threshold_mbps = c3_advantage_threshold_mbps
        self._facts: dict[str, Any] | None = None
        self._evidence: dict[str, Any] | None = None

    def _parsed(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if self._facts is None or self._evidence is None:
            self._facts, self._evidence = independent_parse(
                self.raw_table,
                include_details=True,
            )
        return self._facts, self._evidence

    @staticmethod
    def _verified(payload: dict[str, Any]) -> str:
        return json.dumps(
            {"verified": True, **payload},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def inspect_raw_case(self) -> str:
        """Return table scope and an ordered shortlist of useful tools."""

        facts, evidence = self._parsed()
        distance = facts["max_distance_km"]
        affected_rbs = evidence["average_scheduled_rbs_low_throughput_rows"]
        candidate_tools: list[str] = []

        # Proof-level mechanisms retain the established precedence.
        if distance is not None and distance > 1:
            candidate_tools.append("analyze_radio_geometry")
        elif facts["handover_count"] >= 3:
            candidate_tools.append("analyze_serving_transitions")
        elif facts["max_speed"] > 40:
            candidate_tools.append("analyze_mobility")
        elif affected_rbs is not None and affected_rbs < 160:
            candidate_tools.append("analyze_resource_usage")
        elif evidence[
            "below_lower_lobe_low_throughput_weak_rsrp_row_count"
        ]:
            candidate_tools.append("analyze_signal_coverage")
        else:
            # Residual cases may require comparison of competing mechanisms.
            candidate_tools.append("compare_segment_throughput")
            if evidence["overlap_eligible_low_throughput_row_count"]:
                candidate_tools.append("analyze_neighbor_overlap")
            if evidence["collision_low_throughput_row_count"]:
                candidate_tools.append("analyze_pci_pattern")
            if evidence["below_lower_lobe_low_throughput_row_count"]:
                candidate_tools.append("analyze_signal_coverage")

        return self._verified(
            {
                "observation_type": "raw_case_inspection",
                "drive_test_rows": evidence["observation_count"],
                "affected_rows": evidence["low_throughput_row_count"],
                "serving_cells_observed": len(facts["serving_pcis"]),
                "engineering_join_complete": facts[
                    "serving_engineering_available"
                ],
                "candidate_tools": candidate_tools[:3],
                "instruction": (
                    "Call only tools listed in candidate_tools. When one "
                    "candidate is listed, call it once. When two or more are "
                    "listed, call the first two before deciding so that the "
                    "competing mechanisms are compared."
                ),
            }
        )

    def analyze_radio_geometry(self) -> str:
        facts, evidence = self._parsed()
        distance = facts["max_distance_km"]
        supported = distance is not None and distance > 1
        return self._verified(
            {
                "observation_type": "radio_geometry",
                "mechanism": "unusually_long_serving_distance",
                "measurements": {
                    "maximum_serving_distance_km": _r3(distance),
                    "engineering_join_complete": facts[
                        "serving_engineering_available"
                    ],
                },
                "source_rows": list(range(1, evidence["observation_count"] + 1)),
                "conclusive": supported,
                "interpretation": (
                    "The serving connection extends beyond the normal distance "
                    "range." if supported else
                    "The observed serving distances do not support overshooting."
                ),
            }
        )

    def analyze_serving_transitions(self) -> str:
        facts, evidence = self._parsed()
        changes = facts["handover_count"]
        transitions = evidence["transition_destination_rows"]
        supported = changes >= 3
        return self._verified(
            {
                "observation_type": "serving_transitions",
                "mechanism": "repeated_serving_cell_changes",
                "measurements": {
                    "serving_change_count": changes,
                    "affected_transition_destinations": evidence[
                        "low_throughput_transition_destination_count"
                    ],
                },
                "source_rows": [row["row_index"] + 1 for row in transitions],
                "conclusive": supported,
                "interpretation": (
                    "Repeated serving-cell changes are frequent enough to "
                    "destabilize throughput." if supported else
                    "Serving-cell changes are too limited to dominate the case."
                ),
            }
        )

    def analyze_mobility(self) -> str:
        facts, _ = self._parsed()
        speed = facts["max_speed"]
        supported = speed > 40
        return self._verified(
            {
                "observation_type": "mobility",
                "mechanism": "high_mobility",
                "measurements": {"maximum_speed_kmh": _r2(speed)},
                "conclusive": supported,
                "interpretation": (
                    "Vehicle speed is high enough for mobility to dominate the "
                    "throughput degradation." if supported else
                    "Vehicle speed remains below the level associated with the "
                    "mobility mechanism."
                ),
            }
        )

    def analyze_resource_usage(self) -> str:
        _, evidence = self._parsed()
        average = evidence["average_scheduled_rbs_low_throughput_rows"]
        supported = average is not None and average < 160
        return self._verified(
            {
                "observation_type": "resource_usage",
                "mechanism": "radio_resource_pressure",
                "measurements": {
                    "affected_average_scheduled_rbs": _r2(average)
                },
                "source_rows": [
                    index + 1
                    for index, throughput in enumerate(
                        evidence["throughput_sequence_mbps"]
                    )
                    if throughput < 600
                ],
                "conclusive": supported,
                "interpretation": (
                    "The affected rows receive unusually few scheduled radio "
                    "resources." if supported else
                    "Scheduled resources in the affected rows do not support "
                    "resource pressure as the dominant mechanism."
                ),
            }
        )

    def analyze_signal_coverage(self) -> str:
        _, evidence = self._parsed()
        rows = evidence["below_lower_lobe_low_throughput_rows"]
        weak_rows = [
            row for row in rows if row["serving_rsrp_dbm"] <= -90
        ]
        witness = None
        if rows:
            row = max(
                rows,
                key=lambda item: (
                    item["distance_km"]
                    if item["distance_km"] is not None
                    else -1,
                    -item["throughput_mbps"],
                ),
            )
            witness = {
                "row": row["row_index"] + 1,
                "serving_pci": row["serving_pci"],
                "serving_rsrp_dbm": _r2(row["serving_rsrp_dbm"]),
                "throughput_mbps": _r2(row["throughput_mbps"]),
                "distance_km": _r3(row["distance_km"]),
                "ue_elevation_deg": _r2(row["elevation_deg"]),
                "main_lobe_lower_deg": _r2(row["main_lobe_lower_deg"]),
            }
        return self._verified(
            {
                "observation_type": "signal_coverage",
                "mechanism": "weak_or_below_lobe_coverage",
                "measurements": {
                    "below_lobe_affected_rows": len(rows),
                    "direct_weak_signal_rows": len(weak_rows),
                    "witness": witness,
                },
                "source_rows": [row["row_index"] + 1 for row in rows],
                "conclusive": bool(weak_rows),
                "supported": bool(rows),
                "interpretation": (
                    "A low-throughput row lies below expected antenna coverage "
                    "and also has directly weak serving signal." if weak_rows else
                    "Below-lobe coverage is present without a directly weak "
                    "signal witness." if rows else
                    "The affected rows do not support below-lobe weak coverage."
                ),
            }
        )

    def compare_segment_throughput(self) -> str:
        facts, _ = self._parsed()
        comparison = facts["c3_minimum_comparison"]
        if comparison is None:
            return self._verified(
                {
                    "observation_type": "segment_throughput",
                    "mechanism": "serving_segment_throughput_disparity",
                    "measurements": None,
                    "conclusive": False,
                    "interpretation": (
                        "Fewer than two serving segments are available for a "
                        "throughput comparison."
                    ),
                }
            )
        advantage = comparison["minimum_advantage_mbps"]
        strong = advantage >= self.c3_advantage_threshold_mbps
        return self._verified(
            {
                "observation_type": "segment_throughput",
                "mechanism": "serving_segment_throughput_disparity",
                "measurements": {
                    "affected_pci": comparison["affected_pci"],
                    "affected_minimum_mbps": _r2(
                        comparison["affected_min_mbps"]
                    ),
                    "alternative_pci": comparison["alternative_pci"],
                    "alternative_minimum_mbps": _r2(
                        comparison["alternative_min_mbps"]
                    ),
                    "minimum_advantage_mbps": _r2(advantage),
                    "calibration_threshold_mbps": (
                        self.c3_advantage_threshold_mbps
                    ),
                },
                "conclusive": strong,
                "interpretation": (
                    "The alternative serving segment retains substantially more "
                    "minimum throughput than the affected segment." if strong else
                    "The segment throughput contrast is not strong enough to "
                    "override better-supported radio mechanisms."
                ),
            }
        )

    def analyze_neighbor_overlap(self) -> str:
        _, evidence = self._parsed()
        rows = evidence["overlap_eligible_low_throughput_rows"]
        witness = None
        if rows:
            row = max(rows, key=lambda item: item["maximum_gap_db"])
            witness = {
                "row": row["row_index"] + 1,
                "serving_pci": row["serving_pci"],
                "throughput_mbps": _r2(row["throughput_mbps"]),
                "strongest_noncolocated_margin_db": _r2(
                    row["maximum_gap_db"]
                ),
            }
        return self._verified(
            {
                "observation_type": "neighbor_overlap",
                "mechanism": "non_colocated_neighbor_overlap",
                "measurements": {
                    "affected_overlap_rows": len(rows),
                    "witness": witness,
                },
                "source_rows": [row["row_index"] + 1 for row in rows],
                "conclusive": bool(rows),
                "interpretation": (
                    "A non-colocated neighbor competes closely with the serving "
                    "cell in affected rows." if rows else
                    "No affected row contains sufficiently strong non-colocated "
                    "neighbor overlap."
                ),
            }
        )

    def analyze_pci_pattern(self) -> str:
        _, evidence = self._parsed()
        rows = evidence["collision_low_throughput_rows"]
        witness = None
        if rows:
            row = rows[0]
            neighbor = row["neighbor_pcis"][0]
            witness = {
                "row": row["row_index"] + 1,
                "serving_pci": row["serving_pci"],
                "neighbor_pci": neighbor,
                "modulo_30_residue": row["serving_pci"] % 30,
                "throughput_mbps": _r2(row["throughput_mbps"]),
            }
        return self._verified(
            {
                "observation_type": "pci_pattern",
                "mechanism": "modulo_30_pci_collision",
                "measurements": {
                    "affected_collision_rows": len(rows),
                    "witness": witness,
                },
                "source_rows": [row["row_index"] + 1 for row in rows],
                "conclusive": bool(rows),
                "interpretation": (
                    "An affected row contains serving and neighbor PCIs with the "
                    "same modulo-30 residue." if rows else
                    "No affected row contains a modulo-30 PCI collision."
                ),
            }
        )

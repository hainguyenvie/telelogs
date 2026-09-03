#!/usr/bin/env python3
"""The answer the tools alone imply, for the rare case the LM produces none.

Measured failure mode, once in ~864 cases on every run: greedy decoding falls
into a repetition loop ("C3 is not triggered." over and over) until it hits the
token cap, so the `answer` field is never emitted and the completion carries no
\\boxed{}. The official parser scores that wrong with certainty.

This computes the class from the tool observations only -- no LM call, fully
deterministic -- using the same two ladders the pipeline is built around: the
four exact gate criteria in their stated order, then the magnitude ladder for the
residual half. Obeying both end to end scores 95.84% on the GRPO train split, so
as a last resort it is worth far more than the guaranteed zero of an empty answer.

It is a FALLBACK, never a shortcut: the pipeline's answer wins whenever there is
one, and every use of this path is flagged in the response so it can be counted
rather than hidden.
"""
from __future__ import annotations

from typing import Any

from neutral_tools import TOOL_FUNCTIONS

STAGE1 = ("analyze_throughput_segments", "analyze_coverage_geometry",
          "analyze_mobility", "analyze_radio_resources")
STAGE2 = ("analyze_pci_relations", "analyze_neighbor_overlap")


def observe(case) -> dict[str, Any]:
    return {name: TOOL_FUNCTIONS[name](case) for name in STAGE1 + STAGE2}


def gate_answer(obs: dict[str, Any]) -> str | None:
    """The four exact criteria, in the order the shipped prompt states them."""
    geo = obs["analyze_coverage_geometry"]
    mob = obs["analyze_mobility"]
    rad = obs["analyze_radio_resources"]
    distance = geo.get("maximum_distance_km")
    scheduled_rbs = rad.get("low_throughput_rows_mean_scheduled_rbs")
    if distance is not None and distance > 1.0:
        return "C2"
    if mob["transition_count"] >= 3:
        return "C5"
    if mob["maximum_speed_kmh"] > 40.0:
        return "C7"
    if scheduled_rbs is not None and scheduled_rbs < 160.0:
        return "C8"
    return None


def _below_lobe_low_rows(obs: dict[str, Any], threshold: float) -> list[dict]:
    """Below-lobe rows are not pre-filtered by throughput; the ladder filters them."""
    rows = obs["analyze_coverage_geometry"].get("rows_below_main_lobe_lower_edge") or []
    return [r for r in rows
            if r.get("throughput_mbps") is not None and r["throughput_mbps"] < threshold]


def _strong_c1(obs: dict[str, Any], threshold: float) -> bool:
    """The sufficient witness the ladder states before any of its rules."""
    for row in _below_lobe_low_rows(obs, threshold):
        rsrp = row.get("serving_rsrp_dbm")
        if rsrp is not None and rsrp <= -90:
            return True
    return False


def residual_answer(obs: dict[str, Any], threshold: float) -> str:
    """The magnitude ladder: how much of the phenomenon is present, not whether."""
    if _strong_c1(obs, threshold):
        return "C1"
    if (obs["analyze_pci_relations"].get("equal_residue_pair_count") or 0) > 2:
        return "C6"
    if (obs["analyze_neighbor_overlap"].get("noncolocated_gap_at_or_above_neg3db_count") or 0) > 2:
        return "C4"
    deficit = obs["analyze_coverage_geometry"].get("deepest_below_lobe_deficit_deg")
    if deficit is not None and deficit > 2.5:
        return "C1"
    return "C3"


def symbolic_answer(case) -> str:
    """Gate first, then the residual ladder. Always returns a class."""
    obs = observe(case)
    gated = gate_answer(obs)
    if gated is not None:
        return gated
    return residual_answer(obs, case.throughput_threshold_mbps)

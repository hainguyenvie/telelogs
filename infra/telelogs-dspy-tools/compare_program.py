"""Pre-computed signed comparisons: an arithmetic aid, not a decision.

Round-4 measurement: on the 376 official residual cases, with all six observations
guaranteed on the table and the rule order printed verbatim in the prompt, the LM
scores 47.9% while a plain-Python executor of the same rules scores 76.9%. Reading
those 109 losses, the dominant mechanics are arithmetic, not knowledge — writing a
correct inequality and then labelling it the wrong way, or comparing against the
wrong side of a negative threshold.

So this variant hands the model the comparisons already written out. What it does
NOT hand over is the decision: no rule ordering, no rule-to-class mapping, no
"satisfied/triggered" verdict, no class name anywhere. The constants it compares
against (142.5 Mbps, -3 dB, -90 dBm) are the ones already printed in the prompt.
The model still has to know the order, stop at the first satisfied rule, and map it
to a class.

This is a deliberate step toward the line between "measurement tool" and "answer",
and it is reported as such rather than folded into the default program: it exists to
measure how much of the 109-case gap is arithmetic and how much is the ordered
decision itself.
"""

from __future__ import annotations

import json
from typing import Any

from neutral_tools import assert_label_neutral, run_tools
from forced_program import ForcedMeasurementProgram

COMPARISON_HEADER = (
    "\n\n[Signed comparisons of the fields you already measured, written out for you. "
    "These are arithmetic only: they do not say which rule is satisfied, in what order "
    "the rules apply, or which cause they point to. Apply your own procedure to them.]\n"
)


def _first(observations: dict[str, Any], tool: str, field: str) -> Any:
    for key, blob in (observations or {}).items():
        if key.endswith(tool) and isinstance(blob, dict):
            if field in blob:
                return blob[field]
            for child in blob.values():
                if isinstance(child, dict) and field in child:
                    return child[field]
    return None


def _written(measured: float, constant: float) -> str:
    if measured > constant:
        return f"{measured} > {constant}"
    if measured < constant:
        return f"{measured} < {constant}"
    return f"{measured} = {constant}"


def comparison_block(case, observations: dict[str, Any]) -> str:
    """Signed comparisons for the residual fields, computed from the observations."""
    missing = [
        name for name in ("analyze_throughput_segments", "analyze_coverage_geometry",
                          "analyze_pci_relations", "analyze_neighbor_overlap")
        if not any(key.endswith(name) for key in (observations or {}))
    ]
    if missing:
        extra = run_tools(case, missing)
        for value in extra.values():
            assert_label_neutral(value)
        observations = {**(observations or {}), **extra}

    rows: list[dict[str, Any]] = []

    advantage = _first(observations, "analyze_throughput_segments", "minimum_difference_mbps")
    if isinstance(advantage, (int, float)):
        rows.append({"field": "minimum_difference_mbps", "measured": advantage,
                     "constant": 142.5, "written": _written(float(advantage), 142.5)})

    pairs = _first(observations, "analyze_pci_relations", "equal_residue_pairs")
    if isinstance(pairs, list):
        rows.append({"field": "equal_residue_pairs_count", "measured": len(pairs),
                     "constant": 0, "written": _written(float(len(pairs)), 0.0)})

    gap = _first(observations, "analyze_neighbor_overlap", "best_noncolocated_gap")
    if isinstance(gap, dict):
        gap = gap.get("neighbor_minus_serving_db")
    if isinstance(gap, (int, float)):
        rows.append({"field": "best_noncolocated_gap", "measured": gap,
                     "constant": -3.0, "written": _written(float(gap), -3.0)})

    lobe = _first(observations, "analyze_coverage_geometry", "rows_below_main_lobe_lower_edge")
    if isinstance(lobe, list):
        rows.append({"field": "rows_below_main_lobe_lower_edge_count", "measured": len(lobe),
                     "constant": 0, "written": _written(float(len(lobe)), 0.0)})
        rsrps = [row.get("serving_rsrp_dbm") for row in lobe if isinstance(row, dict)]
        rsrps = [value for value in rsrps if isinstance(value, (int, float))]
        if rsrps:
            weakest = min(rsrps)
            rows.append({"field": "weakest_serving_rsrp_dbm_among_rows_below_lower_edge",
                         "measured": weakest, "constant": -90.0,
                         "written": _written(float(weakest), -90.0)})

    payload = {
        "measurement_scope": "signed comparisons of measured residual fields against the constants stated in the case",
        "comparisons": rows,
    }
    assert_label_neutral(payload)
    return COMPARISON_HEADER + json.dumps(payload, ensure_ascii=False)


class ComparisonProgram(ForcedMeasurementProgram):
    """Forced measurement, plus the signed comparisons written out.

    Single variable against b3_react_forced (official 76.27%): the same program, the
    same forced observations, the same audit loop — only the arithmetic is pre-written.
    """

    def extra_block(self, case, observations: dict[str, Any]) -> str:
        return comparison_block(case, observations)

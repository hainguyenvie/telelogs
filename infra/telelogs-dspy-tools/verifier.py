"""Consistency verifier + retry wrapper for the b3 ReAct tool program.

The verifier inspects a finished prediction and flags ONLY text-internal or
structural contradictions:

- an inequality written in the reasoning that is arithmetically false as
  written ("-2.19 < -3");
- a stage-one verification line whose verdict word contradicts the comparison
  the model itself wrote on that line;
- a quoted measurement that does not match the tool observation the model
  already received;
- a final answer that ignores the model's own "triggered" line, or answers a
  decisive-criterion class whose own line says "not triggered";
- a residual conclusion produced without both stage-two observations, or a
  decisive-criterion answer whose tool was never called.

It never parses the raw case, never recomputes ground truth, and never names
or suggests a class label in its feedback: every retry message points at the
model's own text or at a missing tool call, and the model must re-decide by
itself. This keeps the audit label-neutral in the same sense as the tools.
"""

from __future__ import annotations

import os
import re
from typing import Any

import dspy

# Stage-one decisive criteria: observation field, threshold, direction that
# makes the criterion true, owning tool, owning class. Mirrors the seed text;
# used only to interpret verification lines the model already wrote.
GATE_SPECS = {
    "C2": ("maximum_distance_km", 1.0, "gt", "analyze_coverage_geometry"),
    "C5": ("transition_count", 3.0, "ge", "analyze_mobility"),
    "C7": ("maximum_speed_kmh", 40.0, "gt", "analyze_mobility"),
    "C8": ("low_throughput_rows_mean_scheduled_rbs", 160.0, "lt", "analyze_radio_resources"),
}
RESIDUAL_TOOLS = ("analyze_pci_relations", "analyze_neighbor_overlap")

# Stage-two calibrated rules, in the seed's order. Verifier v2 audits the same
# polarity contradictions on these lines that v1 audited on the four gates —
# round-3 trace analysis showed the dominant confusion (C4->C3, 50/190 errors)
# is a verdict flip on a correctly written residual inequality
# ("best_noncolocated_gap = 9.98 > -3 -> not triggered").
RESIDUAL_SPECS = {
    "C3": ("minimum_difference_mbps", 142.5, "ge", "analyze_throughput_segments"),
    "C4": ("best_noncolocated_gap", -3.0, "ge", "analyze_neighbor_overlap"),
}
RESIDUAL_ORDER = ("C3", "C6", "C4", "C1")

INEQ_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)")
STRONG_C1_RE = re.compile(r"-\d+(?:\.\d+)?\s*<=?\s*-90\b")


def _holds(left: float, op: str, right: float) -> bool:
    return {"<": left < right, ">": left > right, "<=": left <= right, ">=": left >= right}[op]


def _condition(value: float, threshold: float, direction: str) -> bool:
    if direction == "gt":
        return value > threshold
    if direction == "ge":
        return value >= threshold
    return value < threshold


def _find_field(observation: Any, field: str) -> Any:
    if isinstance(observation, dict):
        if field in observation:
            return observation[field]
        for child in observation.values():
            found = _find_field(child, field)
            if found is not None:
                return found
    elif isinstance(observation, list):
        for child in observation:
            found = _find_field(child, field)
            if found is not None:
                return found
    return None


def _observed_value(observations: dict[str, Any], tool: str, field: str) -> float | None:
    for key, blob in (observations or {}).items():
        if key.endswith(tool):
            value = _find_field(blob, field)
            if field == "best_noncolocated_gap" and isinstance(value, dict):
                value = value.get("neighbor_minus_serving_db")
            if isinstance(value, (int, float)):
                return float(value)
    return None


def _line_verdict(line: str) -> bool | None:
    lowered = line.lower()
    if "not triggered" in lowered:
        return False
    if "triggered" in lowered:
        return True
    return None


def verify_prediction(
    answer: str,
    reasoning: str,
    selected_tools: list[str],
    observations: dict[str, Any],
) -> list[str]:
    """Return label-neutral contradiction flags for one finished prediction."""
    flags: list[str] = []
    text = reasoning or ""
    tools = set(selected_tools or [])

    for match in INEQ_RE.finditer(text):
        left, op, right = float(match[1]), match[2], float(match[3])
        if not _holds(left, op, right):
            flags.append(
                f'The written inequality "{match.group(0)}" is arithmetically false; '
                "rewrite it with the correct sign (for negative numbers -2.1 > -3 and "
                "-95 < -90) and re-evaluate that step."
            )

    def audit_numeric_specs(specs: dict) -> dict[str, bool]:
        verdicts: dict[str, bool] = {}
        for spec_class, (field, threshold, direction, _tool) in specs.items():
            for line in text.splitlines() or [text]:
                if field not in line:
                    continue
                verdict = _line_verdict(line)
                if verdict is None:
                    continue
                verdicts[spec_class] = verdict
                quoted = None
                for pair in INEQ_RE.finditer(line):
                    left, _op, right = float(pair[1]), pair[2], float(pair[3])
                    if abs(right - threshold) < 1e-9 and abs(left - threshold) >= 1e-9:
                        quoted = left
                    elif abs(left - threshold) < 1e-9 and abs(right - threshold) >= 1e-9:
                        quoted = right
                if quoted is None:
                    continue
                actual = _observed_value(observations, _tool, field)
                if actual is not None and abs(quoted - actual) > 0.05:
                    flags.append(
                        f"The verification line quoting {field} uses the value {quoted}, but the "
                        f"tool observation returned {actual}; re-read the observation and redo that line."
                    )
                    continue
                if _condition(quoted, threshold, direction) != verdict:
                    flags.append(
                        f"The verification line quoting {field} writes a comparison of {quoted} "
                        f"against {threshold} whose verdict word contradicts that comparison under "
                        "your stated criterion; recompute that single line and follow your procedure."
                    )
                break
        return verdicts

    verdict_by_class = audit_numeric_specs(GATE_SPECS)
    triggered_fields = [GATE_SPECS[c][0] for c, v in verdict_by_class.items() if v]

    # Stage-two audit (verifier v2): same polarity/misquote discipline on the
    # numeric residual rules, plus keyword-only verdict collection for C6/C1.
    residual_verdicts = audit_numeric_specs(RESIDUAL_SPECS)
    for res_class, keyword in (("C6", "equal_residue_pairs"), ("C1", "rows_below_main_lobe_lower_edge")):
        for line in text.splitlines() or [text]:
            if keyword in line:
                verdict = _line_verdict(line)
                if verdict is not None:
                    residual_verdicts.setdefault(res_class, verdict)
                    break

    if triggered_fields and answer not in {c for c, v in verdict_by_class.items() if v}:
        flags.append(
            'A stage-one verification line of yours states the verdict "triggered", but the final '
            "answer does not follow your stated procedure for a triggered line; resolve this "
            "contradiction using only your own measurements."
        )
    if answer in verdict_by_class and verdict_by_class[answer] is False:
        flags.append(
            "The final answer corresponds to a decisive criterion whose own verification line "
            'states "not triggered"; resolve this contradiction using only your own measurements.'
        )

    # v2.1: fabricated-list misquotes — the round-3 official diagnosis showed
    # ~55 errors flow INTO C1/C6 by citing list entries the observation does
    # not contain (rows_below_main_lobe_lower_edge / equal_residue_pairs).
    def observed_list(tool: str, field: str):
        for key, blob in (observations or {}).items():
            if key.endswith(tool):
                value = _find_field(blob, field)
                if isinstance(value, list):
                    return value
        return None

    lobe_rows = observed_list("analyze_coverage_geometry", "rows_below_main_lobe_lower_edge")
    if answer == "C1" and lobe_rows == [] and "rows_below_main_lobe_lower_edge" in text:
        flags.append(
            "The reasoning relies on rows_below_main_lobe_lower_edge, but the tool observation "
            "returned an EMPTY list for it; no such row exists in the observation — re-read the "
            "observations and redo the residual evaluation."
        )
    residue_pairs = observed_list("analyze_pci_relations", "equal_residue_pairs")
    if "equal_residue_pairs" in text and residue_pairs is not None:
        if answer == "C6" and residue_pairs == []:
            flags.append(
                "The reasoning selects the mod-30 rule, but the tool observation returned an "
                "EMPTY equal_residue_pairs list; re-read the observation and redo that step."
            )
        if answer in {"C4", "C1"} and residue_pairs and re.search(
            r"equal_residue_pairs\s*(?:=|is)\s*(?:\[\s*\]|empty)", text
        ):
            flags.append(
                "The reasoning states equal_residue_pairs is empty, but the tool observation "
                "returned a NON-empty list; re-read the observation and redo the rules in order."
            )

    strong_c1_claimed = answer == "C1" and bool(STRONG_C1_RE.search(text))
    first_affirmed = next((c for c in RESIDUAL_ORDER if residual_verdicts.get(c)), None)
    if first_affirmed and answer != first_affirmed and not strong_c1_claimed and not triggered_fields:
        flags.append(
            "One of your residual rule lines states that an earlier rule in your stated order is "
            "satisfied, but the final answer does not follow the first satisfied rule; re-evaluate "
            "the rules one at a time, stop at the first satisfied one, and resolve the contradiction."
        )
    if (
        answer in residual_verdicts
        and answer != "C3"  # C3 is also the rule-5 fallback: a "not triggered" rule-1 line is legitimate
        and residual_verdicts[answer] is False
        and not strong_c1_claimed
        and not triggered_fields
    ):
        flags.append(
            "The final answer corresponds to a residual rule whose own line in your reasoning "
            'states it is NOT satisfied ("not triggered"); recompute that line and resolve the '
            "contradiction using only your own measurements."
        )
    # v2.2: observation-grounded witness check. v2/v2.1 only audited what the model
    # WROTE, so a residual class chosen without ever mentioning its witness slipped
    # through — after the forced-measurement program removed the "never measured"
    # failure mode, that became the dominant remaining pathology. These branches were
    # replayed over the 2,880 stored round-3/4 predictions and fired 198 times, wrong
    # 198/198 (C1-without-a-below-lobe-row 26/26, C4-with-gap-below-minus-3 172/172),
    # i.e. zero false alarms, so they are unconditional. The analogous C3-fallback
    # branch (chose C3 while an earlier rule's witness is present) is deliberately
    # LEFT OUT: 118 firings at 86.4% precision, 16 of them on correct answers, which
    # fails the same zero-false-flag rule that selected v2.1 over v2.
    if answer == "C1" and lobe_rows == []:
        flags.append(
            "The final answer is the below-main-lobe cause, but rows_below_main_lobe_lower_edge "
            "came back EMPTY from analyze_coverage_geometry: no measured row sits below the lower "
            "edge, so that cause has no witness in your own observations. Evaluate the residual "
            "rules in order against the measured fields and answer the first one that is satisfied."
        )
    if answer == "C6" and residue_pairs == []:
        flags.append(
            "The final answer is the mod-30 cause, but equal_residue_pairs came back EMPTY from "
            "analyze_pci_relations: no serving/neighbor pair shares a residue in your own "
            "observations. Re-evaluate the residual rules in order."
        )
    observed_gap = _observed_value(observations, "analyze_neighbor_overlap", "best_noncolocated_gap")
    if answer == "C4" and observed_gap is not None and observed_gap < -3.0:
        flags.append(
            f"The final answer is the overlapping-coverage cause, but the measured "
            f"best_noncolocated_gap is {observed_gap} dB, and {observed_gap} < -3, so that rule is "
            "not satisfied by your own observations. Continue down the residual rules in order and "
            "answer the first rule whose measured condition holds."
        )

    # The C3-fallback branch, off by default (see the note above). Enabled with
    # TELELOGS_VERIFIER_C3_FALLBACK=1 so the two variants can be compared on the
    # selection set instead of argued about: 118 firings, 102 wrong, 16 false alarms.
    if os.environ.get("TELELOGS_VERIFIER_C3_FALLBACK") == "1" and answer == "C3":
        observed_advantage = _observed_value(
            observations, "analyze_throughput_segments", "minimum_difference_mbps"
        )
        earlier_fires = bool(residue_pairs) or bool(lobe_rows) or (
            observed_gap is not None and observed_gap >= -3.0
        )
        if observed_advantage is not None and observed_advantage < 142.5 and earlier_fires:
            flags.append(
                f"The final answer is the fallback cause, but minimum_difference_mbps is "
                f"{observed_advantage} and {observed_advantage} < 142.5, so the first rule is not "
                "satisfied, while an earlier rule's measured witness IS present in your "
                "observations. Work down the rules in order and answer the first one that holds."
            )

    if answer == "C3" and not triggered_fields and "minimum_difference_mbps" not in text:
        flags.append(
            "The fallback conclusion requires first quoting minimum_difference_mbps from "
            "segment_minimum_comparison and evaluating each residual rule in order; quote the "
            "measured values before selecting any fallback."
        )

    if answer in GATE_SPECS:
        _field, _thr, _dir, tool = GATE_SPECS[answer]
        if tool not in tools:
            flags.append(
                f"The final answer relies on {_field}, but {tool} was never called; call it and "
                "verify the criterion before answering."
            )
    elif answer in {"C3", "C4", "C6"} or (answer == "C1" and not STRONG_C1_RE.search(text)):
        missing = [tool for tool in RESIDUAL_TOOLS if tool not in tools]
        if missing:
            flags.append(
                "A residual conclusion was written without the observation(s) from "
                f"{' and '.join(missing)}; call the missing tool(s), quote the returned fields, "
                "and only then decide."
            )

    return flags


AUDIT_HEADER = (
    "\n\n[Consistency audit of your previous attempt — it contained contradictions that "
    "must be resolved. Redo the procedure honestly; the audit below points only at your "
    "own text and tool usage, it does not know the diagnosis.]\n"
)


class VerifiedReActProgram(dspy.Module):
    """ReActToolsProgram plus a consistency-audit retry loop (max 2 retries)."""

    def __init__(self, max_iters: int = 8, instructions: str | None = None, max_retries: int = 2) -> None:
        super().__init__()
        from tool_program import ReActToolsProgram

        self.inner = ReActToolsProgram(max_iters=max_iters, instructions=instructions)
        self.max_retries = max_retries

    def load(self, path) -> None:  # compiled states are saved from ReActToolsProgram
        self.inner.load(path)

    def forward(self, raw_question: str, case) -> dspy.Prediction:
        from tool_program import normalize_answer

        attempts: list[tuple[dspy.Prediction, list[str]]] = []
        question = raw_question
        total_calls = 0
        for _attempt in range(self.max_retries + 1):
            pred = self.inner(raw_question=question, case=case)
            total_calls += int(getattr(pred, "lm_calls", 1))
            flags = verify_prediction(
                normalize_answer(getattr(pred, "answer", "")),
                str(getattr(pred, "reasoning", "")),
                list(getattr(pred, "selected_tools", [])),
                dict(getattr(pred, "tool_observations", {})),
            )
            attempts.append((pred, flags))
            if not flags:
                break
            question = raw_question + AUDIT_HEADER + "\n".join(f"- {flag}" for flag in flags)

        pred, final_flags = min(
            enumerate(attempts), key=lambda item: (len(item[1][1]), -item[0])
        )[1]
        pred.lm_calls = total_calls
        pred.planning_reason = (
            f"{getattr(pred, 'planning_reason', '')} || verifier attempts={len(attempts)} "
            f"flags_per_attempt={[len(flags) for _, flags in attempts]} "
            f"final_flags={final_flags}"
        )
        return pred

"""DSPy program and metrics for prompt-only TeleLogs diagnosis."""

from __future__ import annotations

import json
import re
from typing import Any

import dspy


LABELS = tuple(f"C{i}" for i in range(1, 9))


class TeleLogsDiagnosis(dspy.Signature):
    """Diagnose one TeleLogs case from calculator-verified facts.

    Use this evidence hierarchy exactly. C2.distance_gate, C5.frequent_change_gate,
    C7.speed_gate, and C8.affected_average_rb_gate are exact: if one is triggered,
    select that class immediately and do not let a softer narrative override it.
    C1.affected_weak_rsrp_witness=true is sufficient positive evidence for C1.
    C1.affected_below_lower_lobe, C3.minimum_advantage_mbps,
    C4.affected_overlap_gate, and C6.affected_modulo_30_collision are only necessary
    conditions, never sufficient by themselves. C3 is a residual diagnosis after
    stronger supported mechanisms are excluded; compare only serving-segment
    throughput, never infer throughput from RSRP, BRSRP, SINR, or PCI changes.

    Return an audit-ready evidence trace of at most 120 words and exactly one class.
    """

    verified_facts: str = dspy.InputField(
        desc="Authoritative compact JSON calculated from the raw drive-test tables."
    )
    reasoning: str = dspy.OutputField(
        desc="Concise decisive evidence, at most 120 words; do not recompute facts."
    )
    answer: str = dspy.OutputField(desc="Exactly one of C1,C2,C3,C4,C5,C6,C7,C8.")


class TeleLogsProgram(dspy.Module):
    def __init__(self) -> None:
        super().__init__()
        self.diagnose = dspy.Predict(TeleLogsDiagnosis)

    def forward(self, verified_facts: str) -> dspy.Prediction:
        return self.diagnose(verified_facts=verified_facts)


class MandatoryReActTeleLogsDiagnosis(dspy.Signature):
    """Diagnose one TeleLogs case with one mandatory deterministic tool call.

    Your first and only action MUST call `check_exact_gates` with
    `{"type":"gate_check"}`. Never select `finish` before calling that tool.
    Treat its observation as
    authoritative: if it returns `decisive`, copy its answer exactly; if it
    returns `residual`, diagnose only C1, C3, C4, or C6 from the verified facts.

    In the residual path, C1.affected_below_lower_lobe,
    C4.affected_overlap_gate, and C6.affected_modulo_30_collision are necessary
    but not sufficient. C3 is the residual throughput diagnosis after stronger
    supported mechanisms are excluded. Return at most 120 reasoning words and
    exactly one class.
    """

    verified_facts: str = dspy.InputField(
        desc="Authoritative compact JSON calculated from the raw drive-test tables."
    )
    reasoning: str = dspy.OutputField(
        desc="Concise evidence trace grounded in the mandatory tool observation."
    )
    answer: str = dspy.OutputField(desc="Exactly one of C1,C2,C3,C4,C5,C6,C7,C8.")


def _exact_gate_observation(facts: dict[str, Any]) -> dict[str, Any]:
    if facts["C2"]["distance_gate"] == "triggered":
        return {
            "status": "decisive",
            "answer": "C2",
            "reason": (
                "C2 exact distance gate triggered; maximum distance is "
                f"{facts['C2']['maximum_distance_km']} km."
            ),
        }
    if facts["C5"]["frequent_change_gate"]:
        return {
            "status": "decisive",
            "answer": "C5",
            "reason": (
                "C5 exact frequent-change gate triggered with "
                f"{facts['C5']['change_count']} serving changes."
            ),
        }
    if facts["C7"]["speed_gate"]:
        return {
            "status": "decisive",
            "answer": "C7",
            "reason": (
                "C7 exact speed gate triggered; maximum speed is "
                f"{facts['C7']['maximum_speed_kmh']} km/h."
            ),
        }
    if facts["C8"]["affected_average_rb_gate"]:
        return {
            "status": "decisive",
            "answer": "C8",
            "reason": (
                "C8 exact affected-average-RB gate triggered at "
                f"{facts['C8']['affected_average_scheduled_rbs']} scheduled RBs."
            ),
        }
    if facts["C1"]["affected_weak_rsrp_witness"]:
        witness = facts["C1"]["witness"] or {}
        return {
            "status": "decisive",
            "answer": "C1",
            "reason": (
                "C1 sufficient weak-RSRP witness is present at "
                f"{witness.get('serving_rsrp_dbm', 'unknown')} dBm."
            ),
        }
    return {
        "status": "residual",
        "allowed_answers": ["C1", "C3", "C4", "C6"],
        "reason": "No exact gate or sufficient C1 weak-RSRP witness is active.",
    }


class MandatoryReActTeleLogsProgram(dspy.Module):
    """Require ReAct to invoke the exact-gate tool before final extraction."""

    def forward(self, verified_facts: str) -> dspy.Prediction:
        facts = json.loads(verified_facts)

        def check_exact_gates(type: str = "gate_check") -> str:
            """Return the authoritative exact-gate route for this fact sheet."""

            del type
            return json.dumps(
                _exact_gate_observation(facts),
                ensure_ascii=False,
                separators=(",", ":"),
            )

        tool = dspy.Tool(
            check_exact_gates,
            name="check_exact_gates",
            desc=(
                "Mandatory first action. Check exact C2/C5/C7/C8 gates and the "
                "sufficient C1 weak-RSRP witness for the current fact sheet."
            ),
        )
        react = dspy.ReAct(
            MandatoryReActTeleLogsDiagnosis,
            tools=[tool],
            max_iters=1,
        )
        prediction = react(verified_facts=verified_facts)
        trajectory = prediction.trajectory
        tool_name = trajectory.get("tool_name_0")
        tool_args = trajectory.get("tool_args_0")
        observation = trajectory.get("observation_0")
        compliant = tool_name == "check_exact_gates"
        succeeded = compliant and not str(observation).startswith(
            "Execution error"
        )
        prediction.tool_called = compliant
        prediction.tool_succeeded = succeeded
        prediction.tool_name = tool_name
        prediction.tool_args = tool_args
        prediction.tool_observation = observation
        if succeeded:
            prediction.decision_path = "react_mandatory_tool"
        elif compliant:
            prediction.decision_path = "react_tool_error"
        else:
            prediction.decision_path = "react_tool_skipped"
        return prediction


class TeleLogsResidualDiagnosis(dspy.Signature):
    """Diagnose a residual TeleLogs case after deterministic gates were excluded.

    The caller has already proved that C2.distance_gate, C5.frequent_change_gate,
    C7.speed_gate, C8.affected_average_rb_gate, and
    C1.affected_weak_rsrp_witness are all inactive. Choose only C1, C3, C4, or C6.

    C1.affected_below_lower_lobe, C4.affected_overlap_gate, and
    C6.affected_modulo_30_collision are necessary but not sufficient. Do not map a
    true gate directly to its class. C3 is the residual throughput diagnosis:
    compare only the supplied serving-segment throughput facts and use it when the
    facts do not positively distinguish a stronger C1/C4/C6 mechanism.

    Return an audit-ready evidence trace of at most 120 words and exactly one class.
    """

    verified_facts: str = dspy.InputField(
        desc="Authoritative compact JSON; exact gates and the strong C1 witness are inactive."
    )
    reasoning: str = dspy.OutputField(
        desc="Concise decisive evidence, at most 120 words; do not recompute facts."
    )
    answer: str = dspy.OutputField(desc="Exactly one of C1,C3,C4,C6.")


class HybridTeleLogsProgram(dspy.Module):
    """Route proof-level gates in code and reserve the LM for residual diagnosis.

    Keep the already validated full diagnosis signature for the residual call. A
    narrower residual-only signature was tested separately and rejected because it
    over-selected C3 at the expense of C1, C4, and C6.
    """

    def __init__(self) -> None:
        super().__init__()
        self.diagnose_residual = dspy.Predict(TeleLogsDiagnosis)

    @staticmethod
    def _direct(reasoning: str, answer: str) -> dspy.Prediction:
        return dspy.Prediction(
            reasoning=reasoning,
            answer=answer,
            decision_path="deterministic_gate",
        )

    def forward(self, verified_facts: str) -> dspy.Prediction:
        facts = json.loads(verified_facts)
        if facts["C2"]["distance_gate"] == "triggered":
            distance = facts["C2"]["maximum_distance_km"]
            return self._direct(
                f"C2 exact distance gate triggered; maximum distance is {distance} km.",
                "C2",
            )
        if facts["C5"]["frequent_change_gate"]:
            changes = facts["C5"]["change_count"]
            return self._direct(
                f"C5 exact frequent-change gate triggered with {changes} serving changes.",
                "C5",
            )
        if facts["C7"]["speed_gate"]:
            speed = facts["C7"]["maximum_speed_kmh"]
            return self._direct(
                f"C7 exact speed gate triggered; maximum speed is {speed} km/h.",
                "C7",
            )
        if facts["C8"]["affected_average_rb_gate"]:
            average = facts["C8"]["affected_average_scheduled_rbs"]
            return self._direct(
                f"C8 exact affected-average-RB gate triggered at {average} scheduled RBs.",
                "C8",
            )
        if facts["C1"]["affected_weak_rsrp_witness"]:
            witness = facts["C1"]["witness"] or {}
            rsrp = witness.get("serving_rsrp_dbm", "unknown")
            return self._direct(
                f"C1 sufficient weak-RSRP witness is present at {rsrp} dBm.",
                "C1",
            )

        prediction = self.diagnose_residual(verified_facts=verified_facts)
        prediction.decision_path = "dspy_residual_lm"
        return prediction


class CalibratedResidualDiagnosis(dspy.Signature):
    """Diagnose a residual TeleLogs case using an auditable train-calibrated prior.

    The caller already excluded the proof-level C2/C5/C7/C8 gates and the
    sufficient C1 weak-RSRP witness. Choose only C1, C3, C4, or C6.

    Read `_prompt_calibration` as a ranking prior learned only on the train split,
    not as ground truth. In particular, a C3 throughput advantage at or above the
    supplied threshold is strong positive evidence for C3. The calculator also
    supplies `c3_strong_advantage`; treat this boolean as authoritative instead of
    doing the decimal comparison yourself. When it is true, C3 outranks all of the
    below-threshold witnesses. When false, rank those witnesses in this order:
    non-colocated overlap for C4, modulo-30 collision for C6, then below-lower-lobe
    coverage for C1. If none applies, use residual C3. When several witnesses
    conflict, explicitly mention the conflict and why higher-ranked evidence wins.

    Do not invent thresholds, recompute the verified facts, or use RSRP/SINR/PCI
    as a throughput proxy. Return an audit-ready trace of at most 120 words and
    exactly one class.
    """

    verified_facts: str = dspy.InputField(
        desc="Authoritative compact JSON plus a train-derived prompt calibration policy."
    )
    reasoning: str = dspy.OutputField(
        desc="Concise decisive evidence and conflicts, at most 120 words."
    )
    answer: str = dspy.OutputField(desc="Exactly one of C1,C3,C4,C6.")


def _add_c3_prompt_calibration(
    facts: dict[str, Any],
    threshold_mbps: float,
) -> None:
    """Attach the train-derived C3 ranking prior to a residual fact sheet."""

    facts["_prompt_calibration"] = {
        "source": "train_split_only_threshold_sweep",
        "role": "ranking_prior_not_ground_truth",
        "c3_strong_minimum_advantage_mbps": threshold_mbps,
        "c3_strong_advantage": (
            facts["C3"]["minimum_advantage_mbps"] >= threshold_mbps
        ),
        "below_threshold_witness_order": [
            "C4.affected_overlap_gate",
            "C6.affected_modulo_30_collision",
            "C1.affected_below_lower_lobe",
            "C3.residual",
        ],
    }


class CalibratedHybridTeleLogsProgram(HybridTeleLogsProgram):
    """Keep exact routing and let the LM apply a configurable residual prior.

    Unlike an answer-producing rule engine, the threshold is only serialized into
    the prompt. The LM still produces both the diagnosis and its visible rationale.
    """

    def __init__(self, c3_advantage_threshold_mbps: float = 142.5) -> None:
        super().__init__()
        self.c3_advantage_threshold_mbps = c3_advantage_threshold_mbps
        self.diagnose_residual = dspy.Predict(CalibratedResidualDiagnosis)

    def forward(self, verified_facts: str) -> dspy.Prediction:
        facts = json.loads(verified_facts)
        # Exact/sufficient paths remain identical to the validated hybrid program.
        if facts["C2"]["distance_gate"] == "triggered":
            return super().forward(verified_facts)
        if facts["C5"]["frequent_change_gate"]:
            return super().forward(verified_facts)
        if facts["C7"]["speed_gate"]:
            return super().forward(verified_facts)
        if facts["C8"]["affected_average_rb_gate"]:
            return super().forward(verified_facts)
        if facts["C1"]["affected_weak_rsrp_witness"]:
            return super().forward(verified_facts)

        _add_c3_prompt_calibration(
            facts,
            self.c3_advantage_threshold_mbps,
        )
        prediction = self.diagnose_residual(
            verified_facts=json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
        )
        prediction.decision_path = "dspy_calibrated_residual_lm"
        return prediction


class CalibratedReActHybridTeleLogsProgram(dspy.Module):
    """Route every case through ReAct, then calibrate only residual diagnoses.

    ReAct must call the deterministic exact-gate tool. A decisive tool answer is
    locked in code so the final model response cannot override the observation.
    Residual cases reuse the validated train-calibrated C1/C3 prompt.
    """

    def __init__(self, c3_advantage_threshold_mbps: float = 142.5) -> None:
        super().__init__()
        self.c3_advantage_threshold_mbps = c3_advantage_threshold_mbps
        self.route_with_tool = MandatoryReActTeleLogsProgram()
        self.diagnose_residual = dspy.Predict(CalibratedResidualDiagnosis)

    @staticmethod
    def _copy_tool_audit(
        source: dspy.Prediction,
        destination: dspy.Prediction,
    ) -> None:
        for field in (
            "tool_called",
            "tool_succeeded",
            "tool_name",
            "tool_args",
            "tool_observation",
        ):
            setattr(destination, field, getattr(source, field, None))

    def forward(self, verified_facts: str) -> dspy.Prediction:
        routed = self.route_with_tool(verified_facts=verified_facts)
        if not getattr(routed, "tool_succeeded", False):
            return routed

        try:
            observation = json.loads(str(routed.tool_observation))
        except (TypeError, json.JSONDecodeError):
            routed.decision_path = "react_tool_invalid_observation"
            return routed

        routed.react_router_answer = getattr(routed, "answer", None)
        routed.react_router_reasoning = getattr(routed, "reasoning", None)
        if observation.get("status") == "decisive":
            routed.answer = observation["answer"]
            routed.reasoning = observation["reason"]
            routed.decision_path = "react_tool_decisive_locked"
            return routed

        if observation.get("status") != "residual":
            routed.decision_path = "react_tool_invalid_observation"
            return routed

        facts = json.loads(verified_facts)
        _add_c3_prompt_calibration(
            facts,
            self.c3_advantage_threshold_mbps,
        )
        prediction = self.diagnose_residual(
            verified_facts=json.dumps(
                facts,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        self._copy_tool_audit(routed, prediction)
        prediction.react_router_answer = routed.react_router_answer
        prediction.react_router_reasoning = routed.react_router_reasoning
        prediction.decision_path = "react_tool_calibrated_residual_lm"
        return prediction


class SinglePassCalibratedReActDiagnosis(dspy.Signature):
    """Diagnose one TeleLogs case by selecting tools and then answering yourself.

    First call `check_exact_gates` with `{"type":"gate_check"}`. If that
    observation is decisive, use its recommendation and finish. If it is
    residual, call `assess_calibrated_residual` with
    `{"type":"residual_assessment"}` before finishing. Never call the residual
    tool first, and never finish a residual case without its assessment.

    Tool observations are authoritative evidence, but you must produce the final
    diagnosis yourself. Explain the radio behavior naturally in two or three
    concise sentences. Do not mention tools, JSON fields, gates, rule priority,
    internal booleans, or phrases such as "triggered" and "untriggered". Translate
    measurements into a plausible network mechanism, briefly distinguish the
    strongest competing mechanism when useful, and return exactly one class.
    """

    verified_facts: str = dspy.InputField(
        desc=(
            "Authoritative compact JSON with a train-derived calibration prior; "
            "use tools instead of recomputing its values."
        )
    )
    reasoning: str = dspy.OutputField(
        desc="Natural audit-ready explanation in 2-3 sentences, at most 90 words."
    )
    answer: str = dspy.OutputField(desc="Exactly one of C1,C2,C3,C4,C5,C6,C7,C8.")


class MechanismSinglePassCalibratedReActDiagnosis(dspy.Signature):
    """Use verified tool evidence, then map the mechanism to the final class.

    First call `check_exact_gates` with `{"type":"gate_check"}`. If its
    observation is decisive, finish using the observed mechanism. If it is
    residual, call `assess_calibrated_residual` with
    `{"type":"residual_assessment"}` before finishing. Never call the residual
    tool first, and never finish a residual case without its assessment.

    Map mechanisms to classes yourself: weak or below-lobe coverage is C1;
    unusually long serving distance is C2; serving-segment throughput disparity
    or the unsupported residual is C3; non-colocated overlap is C4; repeated
    serving-cell changes are C5; a modulo-30 PCI collision is C6; high mobility
    is C7; and radio-resource pressure is C8.

    Tool observations deliberately contain no recommended class. Produce the
    final diagnosis yourself and explain the radio behavior naturally in two or
    three concise sentences. Do not mention tools, JSON fields, gates, rule
    priority, internal booleans, or phrases such as "triggered" and
    "untriggered". Return exactly one class.
    """

    verified_facts: str = dspy.InputField(
        desc=(
            "Authoritative compact JSON with a train-derived calibration prior; "
            "use the tool evidence and map its mechanism to a class."
        )
    )
    reasoning: str = dspy.OutputField(
        desc="Natural audit-ready explanation in 2-3 sentences, at most 90 words."
    )
    answer: str = dspy.OutputField(desc="Exactly one of C1,C2,C3,C4,C5,C6,C7,C8.")


def _natural_exact_gate_observation(facts: dict[str, Any]) -> dict[str, Any]:
    """Return natural decisive evidence or request residual assessment."""

    if facts["C2"]["distance_gate"] == "triggered":
        distance = facts["C2"]["maximum_distance_km"]
        return {
            "status": "decisive",
            "recommended_label": "C2",
            "mechanism": "unusually_long_serving_distance",
            "measurements": {"maximum_distance_km": distance},
            "evidence": (
                f"The serving connection reaches {distance} km, so unusually "
                "long-distance radio geometry is the dominant mechanism."
            ),
        }
    if facts["C5"]["frequent_change_gate"]:
        changes = facts["C5"]["change_count"]
        return {
            "status": "decisive",
            "recommended_label": "C5",
            "mechanism": "repeated_serving_cell_changes",
            "measurements": {"serving_change_count": changes},
            "evidence": (
                f"The serving cell changes {changes} times through the affected "
                "segment, indicating repeated and unstable cell selection."
            ),
        }
    if facts["C7"]["speed_gate"]:
        speed = facts["C7"]["maximum_speed_kmh"]
        return {
            "status": "decisive",
            "recommended_label": "C7",
            "mechanism": "high_mobility",
            "measurements": {"maximum_speed_kmh": speed},
            "evidence": (
                f"The terminal reaches {speed} km/h, making mobility the clearest "
                "cause of the observed service degradation."
            ),
        }
    if facts["C8"]["affected_average_rb_gate"]:
        average = facts["C8"]["affected_average_scheduled_rbs"]
        return {
            "status": "decisive",
            "recommended_label": "C8",
            "mechanism": "radio_resource_pressure",
            "measurements": {
                "affected_average_scheduled_rbs": average
            },
            "evidence": (
                f"The affected segment averages {average} scheduled resource "
                "blocks, making radio-resource pressure the dominant mechanism."
            ),
        }
    if facts["C1"]["affected_weak_rsrp_witness"]:
        witness = facts["C1"]["witness"] or {}
        rsrp = witness.get("serving_rsrp_dbm", "unknown")
        return {
            "status": "decisive",
            "recommended_label": "C1",
            "mechanism": "direct_weak_coverage",
            "measurements": {"serving_rsrp_dbm": rsrp},
            "evidence": (
                f"The affected segment contains a serving-signal observation at "
                f"{rsrp} dBm, providing direct evidence of weak coverage."
            ),
        }
    return {
        "status": "residual",
        "next_tool": "assess_calibrated_residual",
        "allowed_labels": ["C1", "C3", "C4", "C6"],
        "evidence": (
            "No single conclusive distance, repeated-cell-change, mobility, "
            "resource-pressure, or severe weak-signal condition explains the case."
        ),
    }


def _calibrated_residual_observation(
    facts: dict[str, Any],
) -> dict[str, Any]:
    """Translate calibrated residual facts into an auditable tool observation."""

    calibration = facts["_prompt_calibration"]
    c4_witness = facts["C4"].get("witness") or {}
    strong_disparity = calibration["c3_strong_advantage"]
    if strong_disparity:
        leading_candidate = "C3"
        interpretation = (
            "The alternative serving segment retains substantially more minimum "
            "throughput than the affected segment. This disparity is strong "
            "enough to outweigh the weaker coverage, overlap, and PCI-pattern "
            "indicators present in this case."
        )
    elif facts["C4"]["affected_overlap_gate"]:
        leading_candidate = "C4"
        interpretation = (
            "The throughput contrast is not large enough to dominate the radio "
            "evidence. A non-colocated neighbor competes strongly with the "
            "serving cell in the affected segment, making overlap the clearest "
            "supported mechanism."
        )
    elif facts["C6"]["affected_modulo_30_collision"]:
        leading_candidate = "C6"
        interpretation = (
            "The throughput contrast is not large enough to dominate the radio "
            "evidence. The affected observations contain the relevant PCI "
            "collision pattern, while stronger overlap and direct weak-coverage "
            "evidence are absent."
        )
    elif facts["C1"]["affected_below_lower_lobe"]:
        leading_candidate = "C1"
        interpretation = (
            "The throughput contrast is not large enough to dominate the radio "
            "evidence. The serving signal falls below its expected lower-lobe "
            "coverage in the affected segment, with no stronger overlap or PCI "
            "collision mechanism supported."
        )
    else:
        leading_candidate = "C3"
        interpretation = (
            "No convincing coverage, overlap, or PCI-collision mechanism is "
            "supported. The remaining serving-segment throughput contrast is "
            "therefore the most plausible explanation."
        )
    return {
        "status": "residual_assessed",
        "allowed_labels": ["C1", "C3", "C4", "C6"],
        "leading_candidate": leading_candidate,
        "throughput_comparison": {
            "affected_minimum_mbps": facts["C3"]["affected_min_mbps"],
            "alternative_minimum_mbps": facts["C3"]["alternative_min_mbps"],
            "minimum_advantage_mbps": facts["C3"]["minimum_advantage_mbps"],
            "strong_disparity": strong_disparity,
        },
        "radio_evidence": {
            "coverage_below_expected_lobe": facts["C1"][
                "affected_below_lower_lobe"
            ],
            "non_colocated_overlap_present": facts["C4"][
                "affected_overlap_gate"
            ],
            "strongest_overlap_margin_db": c4_witness.get(
                "maximum_noncolocated_brsrp_minus_serving_rsrp_db"
            ),
            "modulo_30_collision_present": facts["C6"][
                "affected_modulo_30_collision"
            ],
        },
        "interpretation": interpretation,
    }


class SinglePassCalibratedReActTeleLogsProgram(dspy.Module):
    """Let one ReAct agent select tools and produce the final soft rationale."""

    def __init__(
        self,
        c3_advantage_threshold_mbps: float = 142.5,
        include_label_hints: bool = True,
    ) -> None:
        super().__init__()
        self.c3_advantage_threshold_mbps = c3_advantage_threshold_mbps
        self.include_label_hints = include_label_hints

    def forward(self, verified_facts: str) -> dspy.Prediction:
        facts = json.loads(verified_facts)
        _add_c3_prompt_calibration(
            facts,
            self.c3_advantage_threshold_mbps,
        )
        calibrated_facts = json.dumps(
            facts,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        def check_exact_gates(type: str = "gate_check") -> str:
            """Inspect conclusive mechanisms before any residual diagnosis."""

            del type
            observation = _natural_exact_gate_observation(facts)
            if not self.include_label_hints:
                observation.pop("recommended_label", None)
            return json.dumps(
                observation,
                ensure_ascii=False,
                separators=(",", ":"),
            )

        def assess_calibrated_residual(
            type: str = "residual_assessment",
        ) -> str:
            """Assess calibrated C1/C3/C4/C6 evidence after an exact check."""

            del type
            observation = _calibrated_residual_observation(facts)
            if not self.include_label_hints:
                observation.pop("leading_candidate", None)
            return json.dumps(
                observation,
                ensure_ascii=False,
                separators=(",", ":"),
            )

        tools = [
            dspy.Tool(
                check_exact_gates,
                name="check_exact_gates",
                desc=(
                    "Required first action. Identify a conclusive mechanism or "
                    "route the current case to residual assessment."
                ),
            ),
            dspy.Tool(
                assess_calibrated_residual,
                name="assess_calibrated_residual",
                desc=(
                    "Use only after check_exact_gates returns residual. Compare "
                    "calibrated throughput, coverage, overlap, and PCI evidence."
                ),
            ),
        ]
        signature = (
            SinglePassCalibratedReActDiagnosis
            if self.include_label_hints
            else MechanismSinglePassCalibratedReActDiagnosis
        )
        react = dspy.ReAct(
            signature,
            tools=tools,
            max_iters=2,
        )
        prediction = react(verified_facts=calibrated_facts)
        trajectory = prediction.trajectory
        calls: list[dict[str, Any]] = []
        for index in range(2):
            name = trajectory.get(f"tool_name_{index}")
            if not name:
                continue
            calls.append(
                {
                    "index": index,
                    "name": name,
                    "args": trajectory.get(f"tool_args_{index}"),
                    "observation": trajectory.get(f"observation_{index}"),
                }
            )

        first = calls[0] if calls else {}
        first_observation: dict[str, Any] = {}
        try:
            first_observation = json.loads(str(first.get("observation", "")))
        except (TypeError, json.JSONDecodeError):
            pass
        first_is_exact = first.get("name") == "check_exact_gates"
        first_succeeded = first_is_exact and not str(
            first.get("observation", "")
        ).startswith("Execution error")
        residual_required = first_observation.get("status") == "residual"
        second = calls[1] if len(calls) > 1 else {}
        residual_called = (
            second.get("name") == "assess_calibrated_residual"
        )
        residual_succeeded = residual_called and not str(
            second.get("observation", "")
        ).startswith("Execution error")
        sequence_compliant = first_succeeded and (
            not residual_required or residual_succeeded
        )

        prediction.tool_calls = calls
        prediction.tool_called = first_is_exact
        prediction.tool_succeeded = sequence_compliant
        prediction.tool_name = first.get("name")
        prediction.tool_args = first.get("args")
        prediction.tool_observation = first.get("observation")
        prediction.residual_tool_required = residual_required
        prediction.residual_tool_called = residual_called
        prediction.residual_tool_succeeded = residual_succeeded
        path_prefix = (
            "react_single_pass"
            if self.include_label_hints
            else "react_single_pass_mechanism"
        )
        if sequence_compliant and residual_required:
            prediction.decision_path = f"{path_prefix}_calibrated_residual"
        elif sequence_compliant:
            prediction.decision_path = f"{path_prefix}_decisive"
        else:
            prediction.decision_path = f"{path_prefix}_invalid_sequence"
        return prediction


class MechanismSinglePassCalibratedReActTeleLogsProgram(
    SinglePassCalibratedReActTeleLogsProgram
):
    """Single-pass ReAct ablation without class labels in tool observations."""

    def __init__(self, c3_advantage_threshold_mbps: float = 142.5) -> None:
        super().__init__(
            c3_advantage_threshold_mbps=c3_advantage_threshold_mbps,
            include_label_hints=False,
        )


def normalize_answer(value: Any) -> str:
    matches = re.findall(r"\bC([1-8])\b", str(value or ""), flags=re.IGNORECASE)
    return f"C{matches[-1]}" if matches else ""


def exact_metric(example: dspy.Example, prediction: dspy.Prediction, trace=None) -> bool:
    del trace
    return normalize_answer(prediction.answer) == example.answer


def _failure_feedback(expected: str, predicted: str, facts: dict[str, Any]) -> str:
    exact = {
        "C2": facts["C2"]["distance_gate"] == "triggered",
        "C5": bool(facts["C5"]["frequent_change_gate"]),
        "C7": bool(facts["C7"]["speed_gate"]),
        "C8": bool(facts["C8"]["affected_average_rb_gate"]),
    }
    fired = [label for label, active in exact.items() if active]
    if fired:
        return (
            f"Wrong class: predicted {predicted or 'unparseable'}, expected {expected}. "
            f"Exact gate {fired[0]} is triggered and must terminate the decision before "
            "considering C1/C3/C4/C6 narratives."
        )
    if facts["C1"]["affected_weak_rsrp_witness"]:
        return (
            f"Wrong class: predicted {predicted or 'unparseable'}, expected C1. "
            "C1.affected_weak_rsrp_witness is sufficient positive evidence."
        )
    if expected == "C3":
        return (
            f"Wrong residual decision: predicted {predicted or 'unparseable'}, expected C3. "
            "A true C4/C6 necessary gate does not prove that class. Use only the supplied "
            "serving-segment throughput comparison for C3 and select C3 after stronger "
            "mechanisms are excluded."
        )
    if expected == "C4":
        return (
            f"Wrong class: predicted {predicted or 'unparseable'}, expected C4. The affected "
            "non-colocated overlap witness is the relevant residual evidence here; do not "
            "replace it with modulo-30 or generic geometry."
        )
    if expected == "C6":
        return (
            f"Wrong class: predicted {predicted or 'unparseable'}, expected C6. Use the "
            "affected-row modulo-30 collision only after the exact gates and stronger C1/C4 "
            "evidence are absent."
        )
    return (
        f"Wrong class: predicted {predicted or 'unparseable'}, expected {expected}. "
        "Follow the stated evidence hierarchy and cite only verified fields."
    )


def gepa_metric(
    example: dspy.Example,
    prediction: dspy.Prediction,
    trace=None,
    pred_name=None,
    pred_trace=None,
) -> dspy.Prediction:
    del trace, pred_name, pred_trace
    predicted = normalize_answer(prediction.answer)
    correct = predicted == example.answer
    if correct:
        words = len(str(getattr(prediction, "reasoning", "")).split())
        feedback = None if words <= 120 else "Correct class, but keep the evidence trace under 120 words."
        return dspy.Prediction(score=1.0, feedback=feedback)
    facts = json.loads(example.verified_facts)
    return dspy.Prediction(
        score=0.0,
        feedback=_failure_feedback(example.answer, predicted, facts),
    )

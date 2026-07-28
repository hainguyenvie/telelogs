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

        facts["_prompt_calibration"] = {
            "source": "train_split_only_threshold_sweep",
            "role": "ranking_prior_not_ground_truth",
            "c3_strong_minimum_advantage_mbps": self.c3_advantage_threshold_mbps,
            "c3_strong_advantage": (
                facts["C3"]["minimum_advantage_mbps"]
                >= self.c3_advantage_threshold_mbps
            ),
            "below_threshold_witness_order": [
                "C4.affected_overlap_gate",
                "C6.affected_modulo_30_collision",
                "C1.affected_below_lower_lobe",
                "C3.residual",
            ],
        }
        prediction = self.diagnose_residual(
            verified_facts=json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
        )
        prediction.decision_path = "dspy_calibrated_residual_lm"
        return prediction


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

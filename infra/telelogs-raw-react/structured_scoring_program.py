"""Structured eight-tool scoring followed by shortlisted DSPy ReAct."""

from __future__ import annotations

import json
import re
from typing import Any

import dspy

from raw_react_program import normalize_answer
from raw_tools import RawCaseCalculator


TOOL_ORDER = (
    "analyze_signal_coverage",
    "analyze_radio_geometry",
    "compare_segment_throughput",
    "analyze_neighbor_overlap",
    "analyze_serving_transitions",
    "analyze_pci_pattern",
    "analyze_mobility",
    "analyze_resource_usage",
)


class StructuredToolScorer(dspy.Signature):
    """Score every available calculator before any calculator is executed.

    Read the original drive-test and engineering tables. Evaluate all eight
    calculator hypotheses independently from raw columns and affected rows.
    Do not default to generic coverage, mobility, or interference. Scores must
    reflect how necessary each calculator is for verifying the observed
    degradation. Do not output C1-C8 labels or claim a verified measurement.

    Return one compact JSON object with `scores`, `evidence_rows`, and
    `selection_reasoning`. Both `scores` and `evidence_rows` must contain exactly
    the eight calculator names. Scores range from 0 to 1. Keep the reasoning
    under 60 words. Use at least two distinct score levels and exactly one
    highest-scoring tool; never return all-zero or all-equal scores. Use no
    prose outside the JSON object.
    """

    raw_table: str = dspy.InputField(
        desc="Original TeleLogs raw drive-test and engineering tables."
    )
    ensemble_perspective: str = dspy.InputField(
        desc=(
            "Independent review perspective. Follow it while still scoring all "
            "eight tools."
        )
    )
    validation_feedback: str = dspy.InputField(
        desc="Empty on the first attempt; schema errors to repair on retry."
    )
    scorecard_json: str = dspy.OutputField(
        desc=(
            "Compact strict JSON: {\"scores\":{\"tool\":0.0,...},"
            "\"evidence_rows\":{\"tool\":[],...},"
            "\"selection_reasoning\":\"under 60 words\"}. Required tools: "
            "analyze_signal_coverage, analyze_radio_geometry, "
            "compare_segment_throughput, analyze_neighbor_overlap, "
            "analyze_serving_transitions, analyze_pci_pattern, "
            "analyze_mobility, analyze_resource_usage."
        )
    )


class ShortlistedTeleLogsDecision(dspy.Signature):
    """Diagnose the case using only the calculators selected by the scorer.

    Call every available calculator exactly once before answering. Compare their
    verified observations with the raw-table context and scorecard. Tool
    observations contain measurements, not a class recommendation.

    Map weak/below-lobe coverage to C1, long serving distance to C2, serving
    segment throughput disparity to C3, non-colocated overlap to C4, repeated
    serving transitions to C5, modulo-30 PCI collision to C6, high mobility to
    C7, and radio-resource pressure to C8. Explain naturally in two or three
    concise sentences without mentioning tool names, scores, JSON fields,
    internal gates, or words such as "triggered" and "untriggered".
    """

    raw_table: str = dspy.InputField(desc="Original raw TeleLogs case.")
    scorecard: str = dspy.InputField(
        desc="Validated eight-tool scorecard and selected shortlist."
    )
    reasoning: str = dspy.OutputField(
        desc="Grounded natural explanation, 2-3 sentences and at most 100 words."
    )
    answer: str = dspy.OutputField(desc="Exactly one of C1,C2,C3,C4,C5,C6,C7,C8.")


def _extract_json(value: object) -> dict:
    text = str(value or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("scorecard_json does not contain a JSON object")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("scorecard_json root must be an object")
    return parsed


def validate_scorecard(value: object) -> dict:
    card = _extract_json(value)
    scores = card.get("scores")
    evidence_rows = card.get("evidence_rows")
    if not isinstance(scores, dict) or set(scores) != set(TOOL_ORDER):
        raise ValueError("scores must contain the exact eight-tool catalog")
    if (
        not isinstance(evidence_rows, dict)
        or set(evidence_rows) != set(TOOL_ORDER)
    ):
        raise ValueError(
            "evidence_rows must contain the exact eight-tool catalog"
        )
    normalized = []
    numeric_scores = []
    for tool in TOOL_ORDER:
        try:
            score = float(scores[tool])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid score for {tool}") from exc
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"score outside [0,1] for {tool}")
        numeric_scores.append(score)
        raw_rows = evidence_rows[tool]
        rows = []
        if isinstance(raw_rows, list):
            for row in raw_rows:
                try:
                    normalized_row = int(row)
                except (TypeError, ValueError):
                    continue
                if 1 <= normalized_row <= 50:
                    rows.append(normalized_row)
        normalized.append(
            {
                "tool": tool,
                "score": score,
                "evidence_rows": rows,
            }
        )
    if sum(numeric_scores) <= 0:
        raise ValueError("all-zero scores are not a usable ranking")
    if len(set(numeric_scores)) < 2:
        raise ValueError("scores require at least two distinct levels")
    if numeric_scores.count(max(numeric_scores)) != 1:
        raise ValueError("exactly one tool must have the highest score")
    serialized = json.dumps(card, ensure_ascii=False)
    if re.search(r"gold_label|recommended_label", serialized, re.I):
        raise ValueError("scorecard contains a forbidden label field")
    reasoning = str(card.get("selection_reasoning", "")).strip()
    if not reasoning:
        raise ValueError("selection_reasoning is required")
    reasoning = re.sub(
        r"\bC[1-8]\b",
        "the corresponding mechanism",
        reasoning,
        flags=re.IGNORECASE,
    )
    ranked = sorted(
        normalized,
        key=lambda item: (-item["score"], TOOL_ORDER.index(item["tool"])),
    )
    return {
        "tool_assessments": normalized,
        "selection_reasoning": reasoning,
        "ranked_tools": [item["tool"] for item in ranked],
        "ranked_scores": [item["score"] for item in ranked],
    }


class StructuredScoringReActProgram(dspy.Module):
    def __init__(
        self,
        c3_advantage_threshold_mbps: float = 142.5,
        scorer_attempts: int = 2,
        ensemble_size: int = 1,
        top2_min_score: float = 0.5,
        top2_max_gap: float = 0.2,
    ) -> None:
        super().__init__()
        self.scorer = dspy.Predict(StructuredToolScorer)
        self.c3_advantage_threshold_mbps = c3_advantage_threshold_mbps
        self.scorer_attempts = scorer_attempts
        if ensemble_size not in {1, 3}:
            raise ValueError("ensemble_size must be 1 or 3")
        self.ensemble_size = ensemble_size
        self.top2_min_score = top2_min_score
        self.top2_max_gap = top2_max_gap

    def forward(self, raw_table: str) -> dspy.Prediction:
        perspectives = [
            (
                "Balanced independent review: compare every mechanism and do "
                "not reuse a default ranking."
            )
        ]
        if self.ensemble_size == 3:
            perspectives = [
                (
                    "Exact-gate review: carefully inspect serving distance, "
                    "serving transitions, maximum speed, and affected-row RBs; "
                    "still score all eight tools."
                ),
                (
                    "Radio-interaction review: carefully inspect antenna "
                    "coverage, non-colocated neighbor overlap, and modulo-30 "
                    "PCI patterns; still score all eight tools."
                ),
                (
                    "Residual-throughput review: compare throughput by serving "
                    "segment against all competing explanations; still score "
                    "all eight tools."
                ),
            ]

        member_cards = []
        scorer_raws = []
        member_attempts = []
        for perspective in perspectives:
            feedback = ""
            member_card = None
            scorer_raw = None
            attempts = 0
            for attempts in range(1, self.scorer_attempts + 1):
                scored = self.scorer(
                    raw_table=raw_table,
                    ensemble_perspective=perspective,
                    validation_feedback=feedback,
                )
                scorer_raw = str(scored.scorecard_json)
                try:
                    member_card = validate_scorecard(scorer_raw)
                    break
                except (
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                ) as exc:
                    feedback = (
                        f"Previous output was invalid: {exc}. Return only the "
                        "required strict JSON with all eight exact tools."
                    )
            if member_card is None:
                raise ValueError(
                    "Structured scorer ensemble member failed after "
                    f"{attempts} attempts: {feedback}"
                )
            member_cards.append(member_card)
            scorer_raws.append(scorer_raw)
            member_attempts.append(attempts)

        score_maps = [
            {
                item["tool"]: item["score"]
                for item in card["tool_assessments"]
            }
            for card in member_cards
        ]
        row_maps = [
            {
                item["tool"]: item["evidence_rows"]
                for item in card["tool_assessments"]
            }
            for card in member_cards
        ]
        aggregated = []
        for tool in TOOL_ORDER:
            aggregated.append(
                {
                    "tool": tool,
                    "score": round(
                        sum(scores[tool] for scores in score_maps)
                        / len(score_maps),
                        4,
                    ),
                    "evidence_rows": sorted(
                        {
                            row
                            for rows in row_maps
                            for row in rows[tool]
                        }
                    ),
                }
            )
        aggregated_ranked = sorted(
            aggregated,
            key=lambda item: (
                -item["score"],
                TOOL_ORDER.index(item["tool"]),
            ),
        )
        scorecard = {
            "tool_assessments": aggregated,
            "selection_reasoning": " | ".join(
                card["selection_reasoning"] for card in member_cards
            ),
            "ranked_tools": [
                item["tool"] for item in aggregated_ranked
            ],
            "ranked_scores": [
                item["score"] for item in aggregated_ranked
            ],
        }

        ranked = scorecard["ranked_tools"]
        scores = scorecard["ranked_scores"]
        selected = [ranked[0]]
        if scores[1] >= self.top2_min_score or scores[0] - scores[1] <= self.top2_max_gap:
            selected.append(ranked[1])

        calculator = RawCaseCalculator(
            raw_table,
            c3_advantage_threshold_mbps=self.c3_advantage_threshold_mbps,
        )

        def make_tool(name: str) -> dspy.Tool:
            def calculate(type: str = "verify") -> str:
                del type
                return getattr(calculator, name)()

            calculate.__name__ = name
            calculate.__doc__ = (
                f"Calculate verified {name} evidence from the raw tables."
            )
            return dspy.Tool(
                calculate,
                name=name,
                desc=calculate.__doc__,
            )

        tools = [make_tool(name) for name in selected]
        card_for_decision = json.dumps(
            {
                **scorecard,
                "selected_tools": selected,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        react = dspy.ReAct(
            ShortlistedTeleLogsDecision,
            tools=tools,
            max_iters=len(selected),
        )
        prediction = react(
            raw_table=raw_table,
            scorecard=card_for_decision,
        )
        trajectory = prediction.trajectory
        calls = []
        for index in range(len(selected)):
            name = trajectory.get(f"tool_name_{index}")
            if name:
                calls.append(
                    {
                        "index": index,
                        "name": name,
                        "args": trajectory.get(f"tool_args_{index}"),
                        "observation": trajectory.get(
                            f"observation_{index}"
                        ),
                    }
                )
        called = [
            call["name"]
            for call in calls
            if call["name"] not in {"finish"}
        ]
        execution_errors = [
            call
            for call in calls
            if str(call.get("observation", "")).startswith("Execution error")
        ]
        prediction.answer = normalize_answer(prediction.answer)
        prediction.scorecard = scorecard
        prediction.scorer_raw = scorer_raws[-1]
        prediction.scorer_raws = scorer_raws
        prediction.scorer_attempts = sum(member_attempts)
        prediction.ensemble_member_attempts = member_attempts
        prediction.ensemble_scorecards = member_cards
        prediction.ensemble_size = len(member_cards)
        prediction.structured_valid = True
        prediction.ranked_tools = ranked
        prediction.ranked_scores = scores
        prediction.selected_tools = selected
        prediction.tool_calls = calls
        prediction.specialized_tools = called
        prediction.tool_succeeded = (
            all(name in called for name in selected)
            and not execution_errors
        )
        prediction.decision_path = (
            "structured_scoring_react"
            if prediction.tool_succeeded
            else "structured_scoring_invalid_tool_sequence"
        )
        return prediction

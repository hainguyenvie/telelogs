#!/usr/bin/env python3
"""Rewrite deterministic TeleLogs rationales while keeping labels locked."""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

import dspy

from generate_teacher_reasoning import evenly_spaced, facts_of, read_jsonl
from telelogs_program import normalize_answer


LABELS = ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8")
MACHINE_IDENTIFIER = re.compile(
    r"\bC[1-8]\.[A-Za-z0-9_]+|\b[A-Za-z]+(?:_[A-Za-z0-9]+)+\b"
)
FORBIDDEN_STYLE = re.compile(
    r"\bother exact gates\b|\bremain(?:s|ed)? untriggered\b|"
    r"\bprimary (?:mechanism|driver)\b|"
    r"\bnecessary but not sufficient by itself\b|"
    r"\bC2\s*,\s*C5\s*,\s*C7\s*,?\s*(?:and|&)\s*C8\b|"
    r"\babsent weak-signal witness\b|\bsufficient C1 shortcut\b|"
    r"\bno (?:overlap|modulo-30 collision) candidate\b|"
    r"\b(?:gate|trigger)(?:s|ed|ing)?\b|\brule(?:s|d)? out\b|"
    r"\bunsupported\b|\black(?:s|ed|ing)? decisive support\b",
    flags=re.IGNORECASE,
)
OBSERVATION_LANGUAGE = re.compile(
    r"\b(?:observed|measured|sample|segment|throughput|distance|speed|"
    r"scheduled|resource|RSRP|signal|lower lobe|overlap|collision|"
    r"serving-cell change|trajectory|reaches|km)\b",
    flags=re.IGNORECASE,
)
RATIONALE_PROMPT_VERSION = "dspy_locked_observation_v6"


class LockedTeleLogsRationale(dspy.Signature):
    """Write a grounded TeleLogs explanation for an already locked answer.

    Never classify the case and never change the locked answer. Begin with the
    strongest positive observation in the supplied evidence. Explain why it
    supports the locked diagnosis, comparing at most two material alternatives.
    Absence of another gate is not positive causal evidence. Do not list inactive
    classes or gates. For a residual diagnosis, say "best-supported diagnosis",
    never "primary mechanism", "proves", or "confirms causation". Use natural
    diagnostic prose without JSON paths, snake_case names, or invented thresholds.
    Keep the reasoning between 25 and 90 English words.
    """

    locked_answer: str = dspy.InputField(
        desc="Authoritative answer C1-C8. It must be returned unchanged."
    )
    evidence: str = dspy.InputField(
        desc="Compact label-specific JSON containing only relevant verified observations."
    )
    revision_feedback: str = dspy.InputField(
        desc="Binding corrections from validation, or 'none' on the first attempt."
    )
    reasoning: str = dspy.OutputField(
        desc="Natural grounded explanation, 25-90 English words."
    )
    answer: str = dspy.OutputField(
        desc="Exactly the supplied locked_answer, unchanged."
    )


class LockedRationaleProgram(dspy.Module):
    def __init__(self) -> None:
        super().__init__()
        self.write_rationale = dspy.Predict(LockedTeleLogsRationale)

    def forward(
        self, locked_answer: str, evidence: str, revision_feedback: str = "none"
    ) -> dspy.Prediction:
        return self.write_rationale(
            locked_answer=locked_answer,
            evidence=evidence,
            revision_feedback=revision_feedback,
        )


def deterministic_match(label: str, facts: dict[str, Any]) -> bool:
    if label == "C1":
        return bool(facts["C1"]["affected_weak_rsrp_witness"])
    if label == "C2":
        return facts["C2"]["distance_gate"] == "triggered"
    if label == "C5":
        return bool(facts["C5"]["frequent_change_gate"])
    if label == "C7":
        return bool(facts["C7"]["speed_gate"])
    if label == "C8":
        return bool(facts["C8"]["affected_average_rb_gate"])
    return False


def fact_interpretation(label: str, facts: dict[str, Any]) -> list[str]:
    notes = []
    witness = facts["C1"].get("witness") or {}
    if (
        label == "C1"
        and facts["C1"]["affected_weak_rsrp_witness"]
        and witness
        and not witness.get("weak_rsrp_at_or_below_minus_90")
    ):
        notes.append(
            "At least one affected row supplies the authoritative weak-signal "
            "evidence, but the displayed C1 witness object is a separate geometric "
            "row. Do not quote its RSRP as weak evidence. Include this exact natural "
            'sentence: "The weak-signal evidence comes from an affected sample '
            'separate from the displayed lower-lobe geometry."'
        )
    return notes


def facts_for_rationale(
    label: str, facts: dict[str, Any]
) -> dict[str, Any]:
    prompt_facts = copy.deepcopy(facts)
    witness = facts["C1"].get("witness") or {}
    if (
        label == "C1"
        and facts["C1"]["affected_weak_rsrp_witness"]
        and witness
        and not witness.get("weak_rsrp_at_or_below_minus_90")
    ):
        prompt_facts["C1"]["witness"] = {
            "note": (
                "Representative geometric row omitted from the rationale view "
                "because it is not the affected weak-signal sample."
            )
        }
    return prompt_facts


def evidence_for_rationale(
    label: str, facts: dict[str, Any]
) -> dict[str, Any]:
    """Expose positive observations plus at most two material competitors."""
    c1 = facts["C1"]
    c3 = facts["C3"]
    if label == "C1":
        return {
            "diagnosis": "C1",
            "coverage_observations": {
                "affected_below_lower_lobe": c1["affected_below_lower_lobe"],
                "affected_weak_signal_sample_present": c1[
                    "affected_weak_rsrp_witness"
                ],
                "representative_geometry": facts_for_rationale(label, facts)[
                    "C1"
                ].get("witness"),
            },
            "material_comparison": {
                "alternative_segment_minimum_throughput_advantage_mbps": c3[
                    "minimum_advantage_mbps"
                ]
            },
            "interpretation_notes": fact_interpretation(label, facts),
        }
    if label == "C2":
        return {
            "diagnosis": "C2",
            "distance_observation": {
                "maximum_complete_serving_distance_km": facts["C2"][
                    "maximum_distance_km"
                ]
            },
        }
    if label == "C3":
        return {
            "diagnosis": "C3",
            "serving_segment_throughput_comparison": {
                "affected_pci": c3["affected_pci"],
                "affected_minimum_mbps": c3["affected_min_mbps"],
                "alternative_pci": c3["alternative_pci"],
                "alternative_minimum_mbps": c3["alternative_min_mbps"],
                "minimum_advantage_mbps": c3["minimum_advantage_mbps"],
            },
            "material_competitors": {
                "weak_signal_sample_present": c1[
                    "affected_weak_rsrp_witness"
                ],
                "noncolocated_overlap_observed": facts["C4"][
                    "affected_overlap_gate"
                ],
                "modulo_30_collision_observed": facts["C6"][
                    "affected_modulo_30_collision"
                ],
            },
        }
    if label == "C4":
        return {
            "diagnosis": "C4",
            "overlap_observation": facts["C4"].get("witness"),
            "material_comparison": {
                "alternative_segment_minimum_throughput_advantage_mbps": c3[
                    "minimum_advantage_mbps"
                ],
                "weak_signal_sample_present": c1[
                    "affected_weak_rsrp_witness"
                ],
            },
        }
    if label == "C5":
        return {
            "diagnosis": "C5",
            "mobility_observation": {
                "serving_cell_change_count": facts["C5"]["change_count"]
            },
        }
    if label == "C6":
        return {
            "diagnosis": "C6",
            "collision_observation": facts["C6"].get("witness"),
            "material_comparison": {
                "alternative_segment_minimum_throughput_advantage_mbps": c3[
                    "minimum_advantage_mbps"
                ],
                "noncolocated_overlap_observed": facts["C4"][
                    "affected_overlap_gate"
                ],
            },
        }
    if label == "C7":
        return {
            "diagnosis": "C7",
            "mobility_observation": {
                "maximum_speed_kmh": facts["C7"]["maximum_speed_kmh"]
            },
        }
    if label == "C8":
        return {
            "diagnosis": "C8",
            "resource_observation": {
                "affected_average_scheduled_rbs": facts["C8"][
                    "affected_average_scheduled_rbs"
                ]
            },
        }
    raise ValueError(f"Unsupported locked answer: {label}")


def select_predictions(
    predictions: list[dict[str, Any]], per_label: int
) -> list[dict[str, Any]]:
    if per_label <= 0:
        return sorted(
            (
                row
                for row in predictions
                if row.get("answer") in LABELS and not row.get("error")
            ),
            key=lambda row: row["source_index"],
        )
    selected = []
    for label in LABELS:
        pool = [
            row
            for row in predictions
            if row.get("answer") == label and not row.get("error")
        ]
        selected.extend(evenly_spaced(pool, per_label))
    return sorted(selected, key=lambda row: row["source_index"])


def select_demos(
    demos: list[dict[str, Any]], label: str, count: int
) -> list[dict[str, Any]]:
    pool = []
    for demo in demos:
        if demo["label"] != label or not demo.get("demo_ready"):
            continue
        pool.append(demo)
    return evenly_spaced(pool, count)


def sanitize_demo_reasoning(reasoning: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", reasoning.strip())
    kept = [sentence for sentence in sentences if not FORBIDDEN_STYLE.search(sentence)]
    cleaned = " ".join(kept).strip()
    cleaned = re.sub(
        r"\bleaving\s+([^.]*)\s+as the strongest residual candidate\b",
        r"so \1 is the best-supported residual diagnosis",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


def clean_demo_reasoning(label: str, facts: dict[str, Any]) -> str:
    """Build evidence-aligned demonstrations without stale teacher context."""
    c1 = facts["C1"]
    c3 = facts["C3"]
    if label == "C1":
        witness = c1.get("witness") or {}
        if c1["affected_weak_rsrp_witness"]:
            if witness.get("weak_rsrp_at_or_below_minus_90"):
                return (
                    f"An affected sample records serving RSRP at "
                    f"{witness['serving_rsrp_dbm']} dBm together with low throughput "
                    f"of {witness['throughput_mbps']} Mbps. This weak serving signal "
                    "directly supports a coverage-related diagnosis. The accompanying "
                    "antenna geometry is consistent with that observation, making C1 "
                    "the best-supported diagnosis."
                )
            return (
                "At least one affected sample supplies weak-signal evidence supporting "
                "a coverage-related diagnosis. The weak-signal evidence comes from an "
                "affected sample separate from the displayed lower-lobe geometry. The "
                "geometric representative is therefore not misread as the weak sample, "
                "and C1 remains the best-supported diagnosis."
            )
        return (
            f"At {witness['distance_km']} km, the UE elevation of "
            f"{witness['ue_elevation_deg']} degrees lies below the antenna main-lobe "
            f"lower bound of {witness['main_lobe_lower_deg']} degrees while throughput "
            f"is {witness['throughput_mbps']} Mbps. This measured coverage geometry "
            f"supports C1 more directly than the {c3['minimum_advantage_mbps']} Mbps "
            "serving-segment throughput comparison."
        )
    if label == "C2":
        distance = facts["C2"]["maximum_distance_km"]
        return (
            f"The complete serving trajectory reaches {distance} km from the cell, "
            "showing that service persists unusually far beyond the intended local "
            "coverage area. This measured distance is direct evidence of overshooting, "
            "so C2 is the best-supported diagnosis."
        )
    if label == "C3":
        return (
            f"The affected serving segment falls to {c3['affected_min_mbps']} Mbps, "
            f"whereas alternative PCI {c3['alternative_pci']} retains "
            f"{c3['alternative_min_mbps']} Mbps at its lowest point. The resulting "
            f"{c3['minimum_advantage_mbps']} Mbps separation is a persistent "
            "serving-segment throughput disparity, making C3 the best-supported "
            "residual diagnosis."
        )
    if label == "C4":
        witness = facts["C4"]["witness"]
        return (
            f"During an affected sample with throughput of "
            f"{witness['throughput_mbps']} Mbps, a non-colocated neighbor is "
            f"{witness['maximum_noncolocated_brsrp_minus_serving_rsrp_db']} dB "
            "stronger than the serving signal. This direct overlap observation "
            "supports interference from competing coverage, making C4 the "
            "best-supported diagnosis."
        )
    if label == "C5":
        changes = facts["C5"]["change_count"]
        return (
            f"The serving cell changes {changes} times across the short measured "
            "trajectory. This repeated switching is the clearest observed mobility "
            "pattern and directly supports excessive handover activity, making C5 "
            "the best-supported diagnosis."
        )
    if label == "C6":
        witness = facts["C6"]["witness"]
        return (
            f"At an affected sample with throughput of "
            f"{witness['throughput_mbps']} Mbps, serving PCI "
            f"{witness['serving_pci']} and neighbor PCI {witness['neighbor_pci']} "
            f"share modulo-30 residue {witness['modulo_30_residue']}. This measured "
            "identity collision provides the most specific interference evidence, "
            "making C6 the best-supported diagnosis."
        )
    if label == "C7":
        speed = facts["C7"]["maximum_speed_kmh"]
        return (
            f"The measured speed reaches {speed} km/h during the affected trajectory. "
            "At this mobility level, rapid radio-condition changes can impair serving "
            "continuity and throughput, making the high-mobility diagnosis C7 the "
            "best-supported explanation."
        )
    if label == "C8":
        rbs = facts["C8"]["affected_average_scheduled_rbs"]
        return (
            f"Affected low-throughput samples receive only {rbs} scheduled resource "
            "blocks on average. This directly observed allocation shortfall ties the "
            "degradation to insufficient radio resources, making C8 the best-supported "
            "diagnosis."
        )
    raise ValueError(f"Unsupported demo label: {label}")


def compile_fewshot_program(
    demos_by_label: dict[str, list[dict[str, Any]]],
) -> dspy.Module:
    selected = [
        demo
        for rank in range(max(len(rows) for rows in demos_by_label.values()))
        for label in LABELS
        for demo in (
            [demos_by_label[label][rank]]
            if rank < len(demos_by_label[label])
            else []
        )
    ]
    trainset = [rationale_example(demo) for demo in selected]
    return dspy.LabeledFewShot(k=len(trainset)).compile(
        LockedRationaleProgram(), trainset=trainset, sample=False
    )


def rationale_example(demo: dict[str, Any]) -> dspy.Example:
    return dspy.Example(
        locked_answer=demo["label"],
        evidence=json.dumps(
            evidence_for_rationale(demo["label"], facts_of(demo)),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        revision_feedback="none",
        reasoning=clean_demo_reasoning(demo["label"], facts_of(demo)),
        answer=demo["label"],
    ).with_inputs("locked_answer", "evidence", "revision_feedback")


def rationale_gepa_metric(
    example: dspy.Example,
    prediction: dspy.Prediction,
    trace=None,
    pred_name=None,
    pred_trace=None,
) -> dspy.Prediction:
    del trace, pred_name, pred_trace
    locked_answer = str(example.locked_answer)
    returned_answer = normalize_answer(getattr(prediction, "answer", ""))
    reasoning = str(getattr(prediction, "reasoning", "") or "").strip()
    issues = []
    earned = 0.0
    if returned_answer == locked_answer:
        earned += 0.35
    else:
        issues.append("Return the locked answer unchanged.")
    words = len(reasoning.split())
    if 25 <= words <= 100:
        earned += 0.10
    else:
        issues.append(f"Keep the explanation between 25 and 100 words; got {words}.")
    forbidden = FORBIDDEN_STYLE.search(reasoning)
    if not forbidden:
        earned += 0.20
    else:
        issues.append(
            f"Remove the mechanical or causal-overclaiming phrase: {forbidden.group(0)}."
        )
    first_sentence = re.split(r"(?<=[.!?])\s+", reasoning)[0]
    if OBSERVATION_LANGUAGE.search(first_sentence):
        earned += 0.15
    else:
        issues.append("Begin with a concrete observed measurement or radio condition.")
    if locked_answer in set(
        re.findall(r"\bC[1-8]\b", reasoning, flags=re.IGNORECASE)
    ):
        earned += 0.10
    else:
        issues.append("Explain why the locked diagnosis is best supported.")
    if not MACHINE_IDENTIFIER.search(reasoning):
        earned += 0.10
    else:
        issues.append("Use natural prose rather than JSON field identifiers.")
    feedback = (
        None
        if not issues
        else " ".join(issues)
        + " Use only numbers present in the compact evidence and compare at most "
        "two material alternatives."
    )
    return dspy.Prediction(score=round(earned, 4), feedback=feedback)


def parse_response(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("response is not a JSON object")
    return value


def request_rationale(
    prediction: dict[str, Any],
    fact_row: dict[str, Any],
    demos: list[dict[str, Any]],
    *,
    endpoint: str,
    api_key: str,
    model: str,
    max_tokens: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    locked_answer = prediction["answer"]
    facts = facts_of(fact_row)
    if not deterministic_match(locked_answer, facts):
        raise ValueError(
            f"source_index={prediction['source_index']} does not satisfy "
            f"locked deterministic label {locked_answer}"
        )
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for demo in demos:
        messages.extend(
            [
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "locked_answer": demo["label"],
                            "verified_facts": facts_for_rationale(
                                demo["label"], facts_of(demo)
                            ),
                            "fact_interpretation": fact_interpretation(
                                demo["label"], facts_of(demo)
                            ),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "answer": demo["label"],
                            "reasoning": demo["gold_reasoning"],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ]
        )
    messages.append(
        {
            "role": "user",
            "content": json.dumps(
                {
                    "locked_answer": locked_answer,
                    "verified_facts": facts_for_rationale(
                        locked_answer, facts
                    ),
                    "fact_interpretation": fact_interpretation(
                        locked_answer, facts
                    ),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
    )
    payload = {
            "model": model,
            "rationale_prompt_version": RATIONALE_PROMPT_VERSION,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.monotonic()
    base = {
        "source_index": prediction["source_index"],
        "target": prediction.get("target"),
        "locked_answer": locked_answer,
        "decision_path": prediction.get("decision_path"),
        "original_reasoning": prediction.get("reasoning"),
        "model": model,
    }
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        choice = body["choices"][0]
        message = choice.get("message") or {}
        content = message.get("content")
        result = {
            **base,
            "finish_reason": choice.get("finish_reason"),
            "reasoning_content": message.get("reasoning_content"),
            "usage": body.get("usage"),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "raw_content": content,
            "error": None,
        }
        if not content:
            result["error"] = "empty_visible_content"
            return result
        try:
            parsed = parse_response(content)
            rewritten = parsed.get("reasoning")
            result["returned_answer"] = parsed.get("answer")
            result["answer_unchanged"] = parsed.get("answer") == locked_answer
            result["rewritten_reasoning"] = rewritten
            validation_errors = []
            if not result["answer_unchanged"]:
                validation_errors.append("locked_answer_changed")
            words = len(str(rewritten or "").split())
            if not isinstance(rewritten, str) or not 20 <= words <= 120:
                validation_errors.append(f"reasoning_word_count={words}")
            identifier = MACHINE_IDENTIFIER.search(str(rewritten or ""))
            if identifier:
                validation_errors.append(
                    f"machine_identifier={identifier.group(0)}"
                )
            witness = facts["C1"].get("witness") or {}
            if (
                locked_answer == "C1"
                and facts["C1"]["affected_weak_rsrp_witness"]
                and witness
                and not witness.get("weak_rsrp_at_or_below_minus_90")
            ):
                if str(witness.get("serving_rsrp_dbm")) in str(rewritten):
                    validation_errors.append(
                        "geometry_rsrp_misused_as_weak_evidence"
                    )
                rewritten_lower = str(rewritten).lower()
                if not any(
                    phrase in rewritten_lower
                    for phrase in (
                        "separate geometric",
                        "not the displayed geometric witness",
                        "geometric row is not the weak-signal sample",
                        "weak-signal evidence comes from an affected sample separate",
                    )
                ):
                    validation_errors.append(
                        "weak_and_geometry_witness_not_distinguished"
                    )
            result["rationale_verified"] = not validation_errors
            result["validation_errors"] = validation_errors
        except (json.JSONDecodeError, ValueError) as exc:
            result["error"] = f"invalid_json: {exc}"
        return result
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            **base,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": f"HTTP {exc.code}: {detail[:1000]}",
        }
    except Exception as exc:  # noqa: BLE001 - preserve resumable row failure
        return {
            **base,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def validate_rationale(
    *,
    locked_answer: str,
    returned_answer: str,
    reasoning: str,
    facts: dict[str, Any],
    evidence: dict[str, Any],
) -> list[str]:
    errors = []
    if normalize_answer(returned_answer) != locked_answer:
        errors.append("locked_answer_changed")
    words = len(str(reasoning or "").split())
    if not isinstance(reasoning, str) or not 25 <= words <= 100:
        errors.append(f"reasoning_word_count={words}")
    identifier = MACHINE_IDENTIFIER.search(str(reasoning or ""))
    if identifier:
        errors.append(f"machine_identifier={identifier.group(0)}")
    forbidden = FORBIDDEN_STYLE.search(str(reasoning or ""))
    if forbidden:
        errors.append(f"forbidden_style={forbidden.group(0)}")
    if not OBSERVATION_LANGUAGE.search(str(reasoning or "")):
        errors.append("missing_positive_observation")
    first_sentence = re.split(r"(?<=[.!?])\s+", str(reasoning or "").strip())[0]
    if not OBSERVATION_LANGUAGE.search(first_sentence):
        errors.append("first_sentence_not_observational")
    if locked_answer not in set(
        re.findall(r"\bC[1-8]\b", str(reasoning or ""), flags=re.IGNORECASE)
    ):
        errors.append("locked_label_not_explained")
    labels_mentioned = set(
        re.findall(r"\bC[1-8]\b", str(reasoning or ""), flags=re.IGNORECASE)
    )
    labels_mentioned.discard(locked_answer)
    if len(labels_mentioned) > 2:
        errors.append(f"too_many_competing_labels={len(labels_mentioned)}")
    supplied_numbers: list[float] = []

    def collect_numbers(value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            supplied_numbers.append(float(value))
            return
        if isinstance(value, dict):
            for child in value.values():
                collect_numbers(child)
        elif isinstance(value, list):
            for child in value:
                collect_numbers(child)

    collect_numbers(evidence)
    numeric_text = re.sub(r"\bC[1-8]\b|\bmodulo-30\b", "", str(reasoning or ""))
    used_numbers = [
        float(number)
        for number in re.findall(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?", numeric_text)
    ]
    unsupported_numbers = [
        number
        for number in used_numbers
        if not any(
            abs(number - supplied) <= 0.011
            or (
                locked_answer == "C4"
                and abs(abs(number) - abs(supplied)) <= 0.011
            )
            for supplied in supplied_numbers
        )
    ]
    if unsupported_numbers:
        errors.append(
            "unsupported_numbers="
            + ",".join(f"{number:g}" for number in unsupported_numbers)
        )
    if locked_answer == "C4":
        margin = ((facts["C4"].get("witness") or {}).get(
            "maximum_noncolocated_brsrp_minus_serving_rsrp_db"
        ))
        reasoning_lower = str(reasoning or "").lower()
        if margin is not None and margin < 0 and re.search(
            rf"\b{re.escape(f'{abs(margin):g}')}\s*dB\s+stronger\b",
            reasoning,
            flags=re.IGNORECASE,
        ):
            errors.append("c4_margin_direction_should_be_weaker")
        if margin is not None and margin > 0 and re.search(
            rf"\b{re.escape(f'{abs(margin):g}')}\s*dB\s+weaker\b",
            reasoning_lower,
            flags=re.IGNORECASE,
        ):
            errors.append("c4_margin_direction_should_be_stronger")
    witness = facts["C1"].get("witness") or {}
    if (
        locked_answer == "C1"
        and facts["C1"]["affected_weak_rsrp_witness"]
        and witness
        and not witness.get("weak_rsrp_at_or_below_minus_90")
    ):
        if str(witness.get("serving_rsrp_dbm")) in str(reasoning):
            errors.append("geometry_rsrp_misused_as_weak_evidence")
        reasoning_lower = str(reasoning).lower()
        if not any(
            phrase in reasoning_lower
            for phrase in (
                "separate geometric",
                "not the displayed geometric witness",
                "geometric row is not the weak-signal sample",
                "weak-signal evidence comes from an affected sample separate",
            )
        ):
            errors.append("weak_and_geometry_witness_not_distinguished")
    return errors


def request_rationale_dspy(
    prediction: dict[str, Any],
    fact_row: dict[str, Any],
    program: dspy.Module,
    *,
    max_retries: int,
) -> dict[str, Any]:
    locked_answer = prediction["answer"]
    facts = facts_of(fact_row)
    evidence_object = evidence_for_rationale(locked_answer, facts)
    evidence = json.dumps(
        evidence_object,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    started = time.monotonic()
    attempts = []
    feedback = "none"
    for attempt_number in range(1, max_retries + 2):
        try:
            output = program(
                locked_answer=locked_answer,
                evidence=evidence,
                revision_feedback=feedback,
            )
            returned_answer = normalize_answer(getattr(output, "answer", ""))
            reasoning = str(getattr(output, "reasoning", "") or "").strip()
            validation_errors = validate_rationale(
                locked_answer=locked_answer,
                returned_answer=returned_answer,
                reasoning=reasoning,
                facts=facts,
                evidence=evidence_object,
            )
            attempts.append(
                {
                    "attempt": attempt_number,
                    "returned_answer": returned_answer,
                    "reasoning": reasoning,
                    "validation_errors": validation_errors,
                }
            )
            if not validation_errors:
                return {
                    "source_index": prediction["source_index"],
                    "target": prediction.get("target"),
                    "locked_answer": locked_answer,
                    "returned_answer": returned_answer,
                    "answer_unchanged": returned_answer == locked_answer,
                    "decision_path": prediction.get("decision_path"),
                    "original_reasoning": prediction.get("reasoning"),
                    "rewritten_reasoning": reasoning,
                    "rationale_verified": True,
                    "validation_errors": [],
                    "attempt_count": attempt_number,
                    "attempts": attempts,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "rationale_prompt_version": RATIONALE_PROMPT_VERSION,
                    "error": None,
                }
            feedback = (
                "Rewrite the explanation and fix every issue: "
                + "; ".join(validation_errors)
                + ". Keep the locked answer unchanged."
            )
        except Exception as exc:  # noqa: BLE001 - preserve resumable row failure
            attempts.append(
                {
                    "attempt": attempt_number,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            feedback = (
                "The previous generation failed. Return the locked answer and a "
                "25-90 word grounded explanation using the supplied evidence."
            )
    last = attempts[-1]
    return {
        "source_index": prediction["source_index"],
        "target": prediction.get("target"),
        "locked_answer": locked_answer,
        "returned_answer": last.get("returned_answer", ""),
        "answer_unchanged": last.get("returned_answer") == locked_answer,
        "decision_path": prediction.get("decision_path"),
        "original_reasoning": prediction.get("reasoning"),
        "rewritten_reasoning": last.get("reasoning"),
        "rationale_verified": False,
        "validation_errors": last.get("validation_errors", []),
        "attempt_count": len(attempts),
        "attempts": attempts,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "rationale_prompt_version": RATIONALE_PROMPT_VERSION,
        "error": last.get("error"),
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--demos", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--merged-output",
        type=Path,
        help="Optional full predictions JSONL with verified rationales merged.",
    )
    parser.add_argument("--per-label", type=int, default=8)
    parser.add_argument("--demos-per-label", type=int, default=2)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=400)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument(
        "--gepa-max-metric-calls",
        type=int,
        default=0,
        help="Run GEPA on the DSPy few-shot rationale module when greater than zero.",
    )
    parser.add_argument("--gepa-val-per-label", type=int, default=2)
    parser.add_argument(
        "--program",
        type=Path,
        help="Load a previously compiled DSPy rationale program.",
    )
    parser.add_argument("--model", default="openai/netLLMv1.0")
    parser.add_argument(
        "--api-base",
        default="https://stream-netmind.viettel.vn/gateway/v1",
    )
    parser.add_argument("--api-key-env", default="NETMIND_API_KEY")
    args = parser.parse_args()
    if args.program and args.gepa_max_metric_calls > 0:
        parser.error("--program and --gepa-max-metric-calls are mutually exclusive")

    api_key = os.environ.get(args.api_key_env) or os.environ.get("DSPY_API_KEY")
    if not api_key:
        raise SystemExit(f"{args.api_key_env} is required")
    lm = dspy.LM(
        args.model,
        api_base=args.api_base.rstrip("/"),
        api_key=api_key,
        temperature=0.0,
        max_tokens=args.max_tokens,
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": False,
            }
        },
        cache=False,
    )
    dspy.configure(lm=lm)
    facts_by_index = {
        row["source_index"]: row for row in read_jsonl(args.facts)
    }
    original_predictions = read_jsonl(args.predictions)
    selected = select_predictions(original_predictions, args.per_label)
    teacher_demos = read_jsonl(args.demos)
    demos_by_label = {
        label: select_demos(teacher_demos, label, args.demos_per_label)
        for label in LABELS
    }
    missing_demos = {
        label: len(rows)
        for label, rows in demos_by_label.items()
        if len(rows) < args.demos_per_label
    }
    if missing_demos:
        raise ValueError(f"Insufficient teacher demos: {missing_demos}")
    program = compile_fewshot_program(demos_by_label)
    gepa_train_examples = 0
    gepa_val_examples = 0
    if args.gepa_max_metric_calls > 0:
        selected_demo_indices = {
            demo["source_index"]
            for rows in demos_by_label.values()
            for demo in rows
        }
        remaining_by_label = {
            label: [
                demo
                for demo in teacher_demos
                if demo.get("label") == label
                and demo.get("demo_ready")
                and demo["source_index"] not in selected_demo_indices
            ]
            for label in LABELS
        }
        val_demos = [
            demo
            for label in LABELS
            for demo in remaining_by_label[label][: args.gepa_val_per_label]
        ]
        train_demos = [
            demo
            for label in LABELS
            for demo in remaining_by_label[label][args.gepa_val_per_label :]
        ]
        if not train_demos or not val_demos:
            raise ValueError("GEPA requires non-overlapping train and validation demos")
        gepa_trainset = [rationale_example(demo) for demo in train_demos]
        gepa_valset = [rationale_example(demo) for demo in val_demos]
        optimizer = dspy.GEPA(
            metric=rationale_gepa_metric,
            reflection_lm=lm,
            max_metric_calls=args.gepa_max_metric_calls,
            num_threads=args.workers,
            track_stats=True,
        )
        program = optimizer.compile(
            program,
            trainset=gepa_trainset,
            valset=gepa_valset,
        )
        gepa_train_examples = len(gepa_trainset)
        gepa_val_examples = len(gepa_valset)
    if args.program:
        if not args.program.is_file():
            raise ValueError(f"Saved DSPy program does not exist: {args.program}")
        program.load(args.program)
    for row in selected:
        if row["source_index"] not in facts_by_index:
            raise ValueError(f"Missing facts for source_index={row['source_index']}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    program.save(args.output_dir / "program.json")
    output_path = args.output_dir / "locked_rationales.jsonl"
    existing: dict[int, dict[str, Any]] = {}
    if output_path.exists():
        existing = {
            row["source_index"]: row for row in read_jsonl(output_path)
        }
    pending = [
        row
        for row in selected
        if row["source_index"] not in existing
        or existing[row["source_index"]].get("error")
        or not existing[row["source_index"]].get("rationale_verified")
        or existing[row["source_index"]].get("rationale_prompt_version")
        != RATIONALE_PROMPT_VERSION
    ]
    lock = threading.Lock()
    completed = 0

    def run_one(prediction: dict[str, Any]) -> dict[str, Any]:
        nonlocal completed
        label = prediction["answer"]
        result = request_rationale_dspy(
            prediction,
            facts_by_index[prediction["source_index"]],
            program,
            max_retries=args.max_retries,
        )
        with lock:
            existing[prediction["source_index"]] = result
            write_jsonl(
                output_path,
                [existing[index] for index in sorted(existing)],
            )
            completed += 1
            print(
                f"[{completed}/{len(pending)}] source_index="
                f"{prediction['source_index']} label={label} "
                f"verified={result.get('rationale_verified', False)} "
                f"error={result.get('error')}",
                flush=True,
            )
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        list(executor.map(run_one, pending))

    results = [existing[row["source_index"]] for row in selected]
    by_label = {}
    for label in LABELS:
        group = [row for row in results if row["locked_answer"] == label]
        by_label[label] = {
            "total": len(group),
            "verified": sum(bool(row.get("rationale_verified")) for row in group),
            "answer_unchanged": sum(
                bool(row.get("answer_unchanged")) for row in group
            ),
        }
    finish_reasons = Counter(
        row.get("finish_reason") or "(none)" for row in results
    )
    summary = {
        "experiment": "dspy_locked_all_labels_rationale_fewshot",
        "model": args.model,
        "selected": len(selected),
        "verified": sum(bool(row.get("rationale_verified")) for row in results),
        "answer_unchanged": sum(
            bool(row.get("answer_unchanged")) for row in results
        ),
        "errors": sum(bool(row.get("error")) for row in results),
        "workers": args.workers,
        "max_tokens": args.max_tokens,
        "temperature": 0.0,
        "enable_thinking": False,
        "framework": "dspy.LabeledFewShot",
        "loaded_program": str(args.program.resolve()) if args.program else None,
        "gepa_max_metric_calls": args.gepa_max_metric_calls,
        "gepa_train_examples": gepa_train_examples,
        "gepa_val_examples": gepa_val_examples,
        "rationale_prompt_version": RATIONALE_PROMPT_VERSION,
        "max_retries": args.max_retries,
        "demos_per_label": args.demos_per_label,
        "by_label": by_label,
        "finish_reasons": dict(finish_reasons),
        "output": str(output_path.resolve()),
    }
    (args.output_dir / "rationale_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if args.merged_output:
        verified_by_index = {
            row["source_index"]: row
            for row in results
            if row.get("rationale_verified")
            and row.get("answer_unchanged")
            and not row.get("error")
        }
        merged = []
        for source in original_predictions:
            row = dict(source)
            rationale = verified_by_index.get(row["source_index"])
            if rationale:
                row["reasoning_before_locked_rationale"] = row.get("reasoning")
                row["reasoning"] = rationale["rewritten_reasoning"]
                row["rationale_model"] = args.model
                row["rationale_fewshot"] = True
                row["rationale_verified"] = True
            merged.append(row)
        args.merged_output.parent.mkdir(parents=True, exist_ok=True)
        write_jsonl(args.merged_output, merged)
        merged_by_label = {}
        for label in sorted({row.get("target") for row in merged}):
            group = [row for row in merged if row.get("target") == label]
            merged_by_label[label] = {
                "correct": sum(bool(row.get("correct")) for row in group),
                "total": len(group),
                "accuracy": (
                    sum(bool(row.get("correct")) for row in group) / len(group)
                    if group
                    else 0.0
                ),
            }
        merged_summary = {
            "total": len(merged),
            "correct": sum(bool(row.get("correct")) for row in merged),
            "accuracy": (
                sum(bool(row.get("correct")) for row in merged) / len(merged)
                if merged
                else 0.0
            ),
            "locked_rationales_requested": len(selected),
            "locked_rationales_merged": len(verified_by_index),
            "by_label": merged_by_label,
            "output": str(args.merged_output.resolve()),
        }
        args.merged_output.with_name("merged_summary.json").write_text(
            json.dumps(merged_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

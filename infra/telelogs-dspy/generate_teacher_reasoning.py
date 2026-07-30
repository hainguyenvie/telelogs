#!/usr/bin/env python3
"""Generate balanced, grounded teacher rationales from TeleLogs verified facts."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


LABELS = ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8")
REQUIRED_EVIDENCE = {
    "C2": ("C2.distance_gate", "triggered"),
    "C5": ("C5.frequent_change_gate", True),
    "C7": ("C7.speed_gate", True),
    "C8": ("C8.affected_average_rb_gate", True),
    "C4": ("C4.affected_overlap_gate", True),
    "C6": ("C6.affected_modulo_30_collision", True),
}
SYSTEM_PROMPT = """You are a senior LTE drive-test diagnosis teacher.
Write one concise, audit-ready training rationale for the authoritative gold label.
Use only the supplied verified facts. The gold label is fixed: do not relabel it.
Explain the strongest positive evidence and why the strongest competing mechanisms
do not override it. Never invent a threshold, measurement, event, or causal fact.
Do not call a necessary condition sufficient. C2/C5/C7/C8 exact gates override
residual narratives; a true C1 weak-RSRP witness is sufficient for C1.
If C1 weak-RSRP is false, never say C1 is absent, invalid, or excluded.
Never infer or mention an unstated C3 threshold. A numeric C3 advantage is not an
exact gate and cannot by itself prove or disprove C3. For residual C1/C3/C4/C6,
use cautious comparative language: do not say definitive, proves, directly
explains, becomes sufficient, or confirms causation.
The reasoning is for a human reader: never expose JSON paths, dotted field names,
snake_case identifiers, or assignments such as C3.minimum_advantage_mbps=101.7.
Keep machine field names only in the evidence array. Paraphrase them naturally,
for example: "the alternative segment retains a 101.7 Mbps minimum-throughput
advantage over the affected serving segment."
Gate terminology is allowed, but a gate state must never be the sole explanation.
Lead with observed mechanism-level evidence such as distance, serving-cell change
count, maximum speed, or scheduled RB usage; then, if useful, connect that evidence
to the gate outcome. Do not dump a list such as "C2/C5/C7 gates are not triggered."
Discuss only the strongest alternatives that materially compete with the gold label.
The mandatory_revision_feedback list is binding. Satisfy every item literally before
returning JSON; if an observation is not worth quantifying, remove every mention of
that observation instead of describing it vaguely.
Return JSON only, without markdown, with this exact shape:
{"gold_label":"C1","reasoning_style":"mechanism_observation_v2","reasoning":"40-100 English words","evidence":[{"path":"C1.field","value":true}],"alternatives_rejected":["C3"],"self_check":"grounded"}
Every evidence path must exist in verified_facts and its value must match exactly."""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc
    return rows


def facts_of(row: dict[str, Any]) -> dict[str, Any]:
    value = row["verified_facts"]
    return json.loads(value) if isinstance(value, str) else value


def exact_inactive(facts: dict[str, Any]) -> bool:
    return not (
        facts["C2"]["distance_gate"] == "triggered"
        or facts["C5"]["frequent_change_gate"]
        or facts["C7"]["speed_gate"]
        or facts["C8"]["affected_average_rb_gate"]
        or facts["C1"]["affected_weak_rsrp_witness"]
    )


def eligible(row: dict[str, Any]) -> bool:
    facts = facts_of(row)
    label = row["label"]
    if label == "C1":
        return bool(
            facts["C1"]["affected_weak_rsrp_witness"]
            or (
                exact_inactive(facts)
                and facts["C1"]["affected_below_lower_lobe"]
            )
        )
    if label == "C2":
        return facts["C2"]["distance_gate"] == "triggered"
    if label == "C3":
        return exact_inactive(facts)
    if label == "C4":
        return exact_inactive(facts) and facts["C4"]["affected_overlap_gate"]
    if label == "C5":
        return bool(facts["C5"]["frequent_change_gate"])
    if label == "C6":
        return exact_inactive(facts) and facts["C6"]["affected_modulo_30_collision"]
    if label == "C7":
        return bool(facts["C7"]["speed_gate"])
    if label == "C8":
        return bool(facts["C8"]["affected_average_rb_gate"])
    return False


def evenly_spaced(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if len(rows) < count:
        raise ValueError(f"Only {len(rows)} eligible rows; need {count}")
    ordered = sorted(rows, key=lambda row: row["source_index"])
    if count == 1:
        return [ordered[len(ordered) // 2]]
    indices = [round(index * (len(ordered) - 1) / (count - 1)) for index in range(count)]
    return [ordered[index] for index in indices]


def select_balanced(rows: list[dict[str, Any]], per_label: int) -> list[dict[str, Any]]:
    train = [row for row in rows if row["split"] == "train" and eligible(row)]
    selected: list[dict[str, Any]] = []
    for label in LABELS:
        pool = [row for row in train if row["label"] == label]
        if label == "C1" and per_label >= 2:
            decisive = [
                row
                for row in pool
                if facts_of(row)["C1"]["affected_weak_rsrp_witness"]
            ]
            residual = [
                row
                for row in pool
                if not facts_of(row)["C1"]["affected_weak_rsrp_witness"]
            ]
            decisive_count = per_label // 2
            selected.extend(evenly_spaced(decisive, decisive_count))
            selected.extend(evenly_spaced(residual, per_label - decisive_count))
        else:
            selected.extend(evenly_spaced(pool, per_label))
    return sorted(selected, key=lambda row: (LABELS.index(row["label"]), row["source_index"]))


def resolve_path(facts: dict[str, Any], path: str) -> Any:
    current: Any = facts
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


def same_value(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= 1e-6
    return left == right


def parse_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        # Some OpenAI-compatible deployments occasionally escape the remaining
        # top-level JSON fields after emitting a valid reasoning string.
        value = json.loads(text.replace('\\"', '"'))
    if not isinstance(value, dict):
        raise ValueError("Teacher response is not a JSON object")
    return value


def enrich_teacher_evidence(
    teacher: dict[str, Any], facts: dict[str, Any]
) -> None:
    """Attach calculator facts for audit without forcing machine paths into prose."""

    standard_paths = (
        "C1.affected_below_lower_lobe",
        "C1.affected_weak_rsrp_witness",
        "C2.distance_gate",
        "C2.maximum_distance_km",
        "C3.minimum_advantage_mbps",
        "C4.affected_overlap_gate",
        "C5.change_count",
        "C5.frequent_change_gate",
        "C6.affected_modulo_30_collision",
        "C7.maximum_speed_kmh",
        "C7.speed_gate",
        "C8.affected_average_scheduled_rbs",
        "C8.affected_average_rb_gate",
    )
    evidence = teacher.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
        teacher["evidence"] = evidence
    present = {
        item.get("path")
        for item in evidence
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    for path in standard_paths:
        if path not in present:
            evidence.append({"path": path, "value": resolve_path(facts, path)})


def validate_teacher(
    teacher: dict[str, Any], row: dict[str, Any]
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    facts = facts_of(row)
    label = row["label"]
    if teacher.get("gold_label") != label:
        errors.append("gold_label_mismatch")
    if teacher.get("reasoning_style") != "mechanism_observation_v2":
        errors.append("reasoning_style_mismatch")
    reasoning = teacher.get("reasoning")
    word_count = len(str(reasoning or "").split())
    if not isinstance(reasoning, str) or not 20 <= word_count <= 120:
        errors.append(f"reasoning_word_count={word_count}")
    reasoning_lower = str(reasoning or "").lower()
    machine_identifier = re.search(
        r"\bC[1-8]\.[A-Za-z0-9_]+|\b[A-Za-z]+(?:_[A-Za-z0-9]+)+\b",
        str(reasoning or ""),
    )
    if machine_identifier:
        errors.append(f"machine_identifier_in_reasoning={machine_identifier.group(0)}")
    c4_active = bool(facts["C4"]["affected_overlap_gate"])
    c6_active = bool(facts["C6"]["affected_modulo_30_collision"])
    if (
        not c4_active
        and "no overlap candidate is present" not in reasoning_lower
        and re.search(
            r"(?:c4\s+)?overlap.{0,35}\b(?:present|active|triggered|exists|true)\b",
            reasoning_lower,
        )
    ):
        errors.append("reasoning_claims_false_c4_overlap")
    if c4_active and re.search(
        r"(?:c4\s+)?overlap.{0,35}\b(?:absent|inactive|false|not triggered)\b",
        reasoning_lower,
    ):
        errors.append("reasoning_denies_true_c4_overlap")
    if (
        not c6_active
        and "no modulo-30 collision candidate is present" not in reasoning_lower
        and re.search(
            r"(?:c6\s+)?(?:modulo(?:-30)?\s+)?collision.{0,35}"
            r"\b(?:present|active|triggered|exists|true)\b",
            reasoning_lower,
        )
    ):
        errors.append("reasoning_claims_false_c6_collision")
    if c6_active and re.search(
        r"(?:c6\s+)?(?:modulo(?:-30)?\s+)?collision.{0,35}"
        r"\b(?:absent|inactive|false|not triggered)\b",
        reasoning_lower,
    ):
        errors.append("reasoning_denies_true_c6_collision")
    if "threshold" in reasoning_lower and not any(
        phrase in reasoning_lower
        for phrase in (
            "without asserting a specific threshold",
            "without a stated threshold",
            "without implying a specific threshold",
            "does not use a threshold",
            "no threshold",
        )
    ):
        errors.append("unstated_threshold_claim")
    if not facts["C1"]["affected_weak_rsrp_witness"]:
        invalid_c1_claims = (
            "c1 is absent",
            "c1 is invalid",
            "c1 is excluded",
            "excluding c1",
            "eliminating c1",
            "c1 lacks the necessary",
            "c1 lacks necessary",
            "failing its necessary condition",
            "exact gates for c1",
            "c1 weak-rsrp is absent, excluding",
            "c1 weak-rsrp is false, removing",
            "c1 weak-rsrp is false, excluding",
        )
        safe_c1_limit = any(
            phrase in reasoning_lower
            for phrase in (
                "not excluding c1",
                "without excluding c1",
                "c1 is not excluded",
                "does not exclude c1",
            )
        )
        if (
            any(phrase in reasoning_lower for phrase in invalid_c1_claims)
            and not safe_c1_limit
        ):
            errors.append("false_c1_exclusion_from_weak_witness")
    unsupported_c3_ranking = (
        "advantage is insufficient",
        "insufficient advantage",
        "advantage was not met",
        "advantage is negligible",
        "negligible throughput advantage",
    )
    if any(phrase in reasoning_lower for phrase in unsupported_c3_ranking):
        errors.append("unsupported_c3_advantage_ranking")
    if teacher.get("self_check") != "grounded":
        errors.append("self_check_not_grounded")
    evidence = teacher.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("missing_evidence")
        evidence = []
    valid_paths: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            errors.append("invalid_evidence_item")
            continue
        path = item["path"]
        try:
            actual = resolve_path(facts, path)
        except KeyError:
            errors.append(f"unknown_evidence_path={path}")
            continue
        if not same_value(actual, item.get("value")):
            errors.append(f"evidence_value_mismatch={path}")
            continue
        valid_paths.add(path)
    observation_checks = (
        (
            ("distance",),
            "C2.maximum_distance_km",
            facts["C2"]["maximum_distance_km"],
        ),
        (
            ("handover", "cell change", "serving-cell change"),
            "C5.change_count",
            None,
        ),
        (
            ("speed", "km/h"),
            "C7.maximum_speed_kmh",
            facts["C7"]["maximum_speed_kmh"],
        ),
        (
            ("scheduled rb", "average rb"),
            "C8.affected_average_scheduled_rbs",
            facts["C8"]["affected_average_scheduled_rbs"],
        ),
    )
    for terms, path, numeric_value in observation_checks:
        if not any(term in reasoning_lower for term in terms):
            continue
        if path not in valid_paths:
            errors.append(f"missing_observation_evidence={path}")
        if numeric_value is not None and str(numeric_value) not in str(reasoning):
            errors.append(f"missing_observation_value={path}")
    if label == "C1":
        required = (
            "C1.affected_weak_rsrp_witness"
            if facts["C1"]["affected_weak_rsrp_witness"]
            else "C1.affected_below_lower_lobe"
        )
        if required not in valid_paths:
            errors.append(f"missing_required_evidence={required}")
        witness = facts["C1"].get("witness") or {}
        if (
            facts["C1"]["affected_weak_rsrp_witness"]
            and witness
            and not witness.get("weak_rsrp_at_or_below_minus_90")
        ):
            witness_rsrp = str(witness.get("serving_rsrp_dbm"))
            required_distinction = (
                "the displayed below-lobe observation is a separate geometric witness"
            )
            if witness_rsrp in str(reasoning):
                errors.append("c1_geometry_rsrp_misused_as_weak_witness")
            if required_distinction not in reasoning_lower:
                errors.append("c1_weak_and_geometry_witness_not_distinguished")
        if not facts["C1"]["affected_weak_rsrp_witness"]:
            forbidden = (
                "lower lobe status alone validates",
                "lower-lobe status alone validates",
                "lower lobe alone validates",
                "lower-lobe alone validates",
                "below the lower lobe confirms",
                "below-lower-lobe confirms",
                "definitive lower lobe",
                "definitive lower-lobe",
                "sufficient for c1",
                "proves c1",
                "lacks sufficient throughput advantage",
                "insufficient throughput advantage",
                "throughput advantage is insufficient",
            )
            if any(phrase in reasoning_lower for phrase in forbidden):
                errors.append("c1_necessary_condition_claimed_sufficient")
            if (
                "not sufficient" not in reasoning_lower
                and "residual" not in reasoning_lower
            ):
                errors.append("c1_residual_limitation_not_acknowledged")
            for comparison_path in (
                "C3.minimum_advantage_mbps",
                "C4.affected_overlap_gate",
                "C6.affected_modulo_30_collision",
            ):
                if comparison_path not in valid_paths:
                    errors.append(
                        f"missing_comparison_evidence={comparison_path}"
                    )
    elif label == "C3":
        for comparison_path in (
            "C1.affected_weak_rsrp_witness",
            "C3.minimum_advantage_mbps",
            "C4.affected_overlap_gate",
            "C6.affected_modulo_30_collision",
        ):
            if comparison_path not in valid_paths:
                errors.append(
                    f"missing_comparison_evidence={comparison_path}"
                )
        if "residual" not in reasoning_lower:
            errors.append("c3_not_described_as_residual")
        advantage = facts["C3"]["minimum_advantage_mbps"]
        required_comparison = (
            f"the alternative segment retains a {advantage} mbps "
            "minimum-throughput advantage over the affected serving segment"
        ).lower()
        if required_comparison not in reasoning_lower:
            errors.append("c3_advantage_direction_not_explicit")
        required_c4_sentence = (
            "the overlap candidate is present, but it is necessary rather than sufficient"
            if c4_active
            else "no overlap candidate is present"
        )
        required_c6_sentence = (
            "the modulo-30 collision candidate is present, but it is necessary rather than sufficient"
            if c6_active
            else "no modulo-30 collision candidate is present"
        )
        if required_c4_sentence not in reasoning_lower:
            errors.append("c3_c4_state_not_explicit")
        if required_c6_sentence not in reasoning_lower:
            errors.append("c3_c6_state_not_explicit")
    else:
        required_path, required_value = REQUIRED_EVIDENCE[label]
        if resolve_path(facts, required_path) != required_value:
            errors.append(f"source_not_eligible={required_path}")
        if required_path not in valid_paths:
            errors.append(f"missing_required_evidence={required_path}")
    if label in {"C1", "C3", "C4", "C6"} and not (
        label == "C1" and facts["C1"]["affected_weak_rsrp_witness"]
    ):
        residual_overclaims = (
            "is definitive",
            "definitive diagnosis",
            "proves",
            "directly explains",
            "becomes sufficient",
            "sufficient explanation",
            "confirms causation",
            "confirms c1",
            "confirms c3",
            "confirms c4",
            "confirms c6",
        )
        if any(phrase in reasoning_lower for phrase in residual_overclaims):
            errors.append("residual_causal_overclaim")
    if label in {"C4", "C6"} and not any(
        phrase in reasoning_lower
        for phrase in (
            "not sufficient",
            "cannot prove causation alone",
            "does not confirm causation or prove sufficiency alone",
            "without claiming definitive causation or sufficiency",
        )
    ):
        errors.append(f"{label.lower()}_necessary_limit_not_acknowledged")
    return not errors, errors


def request_teacher(
    row: dict[str, Any],
    *,
    endpoint: str,
    api_key: str,
    model: str,
    max_tokens: int,
    timeout_seconds: int,
    revision_feedback: list[str] | None = None,
) -> dict[str, Any]:
    facts = facts_of(row)
    distance = facts["C2"]["maximum_distance_km"]
    distance_guidance = (
        f"The maximum observed distance is {distance} km, providing decisive "
        "excessive-distance evidence."
        if facts["C2"]["distance_gate"] == "triggered"
        else f"The maximum observed distance is {distance} km; this does not show "
        "the decisive excessive-distance pattern used for C2."
    )
    changes = facts["C5"]["change_count"]
    change_word = "change" if changes == 1 else "changes"
    change_guidance = (
        f"{changes} serving-cell {change_word} show a frequent handover pattern."
        if facts["C5"]["frequent_change_gate"]
        else f"Only {changes} serving-cell {change_word} were observed, so a frequent "
        "handover pattern is not supported."
    )
    speed = facts["C7"]["maximum_speed_kmh"]
    speed_guidance = (
        f"The maximum speed is {speed} km/h, providing decisive high-mobility evidence."
        if facts["C7"]["speed_gate"]
        else f"The maximum speed is {speed} km/h; this does not show the decisive "
        "high-mobility pattern used for C7."
    )
    average_rbs = facts["C8"]["affected_average_scheduled_rbs"]
    rb_guidance = (
        f"The affected segment averages {average_rbs} scheduled RBs, providing "
        "decisive resource-starvation evidence."
        if facts["C8"]["affected_average_rb_gate"]
        else f"The affected segment averages {average_rbs} scheduled RBs; this does "
        "not show the decisive resource-starvation pattern used for C8."
    )
    case_constraints = [
        "The rationale must explicitly distinguish necessary from sufficient evidence.",
        "Use natural diagnostic prose in reasoning. Field paths named in these "
        "constraints may appear only in the evidence array, never in reasoning.",
        "Whenever reasoning uses distance, handover count, maximum speed, or "
        "scheduled RB usage, state the observed value naturally and include the "
        "matching machine path/value in the evidence array for audit.",
        "Never summarize a used observation only as low, moderate, normal, or high; "
        "state its exact supplied number in the reasoning.",
    ]
    if not facts["C1"]["affected_weak_rsrp_witness"]:
        case_constraints.append(
            "C1.affected_weak_rsrp_witness=false only removes the sufficient C1 "
            "shortcut. It does not exclude, eliminate, invalidate, or remove C1, "
            "and it must not be described as a false exact C1 gate. State this in "
            'natural prose as: "The absent weak-signal witness removes only the '
            'sufficient C1 shortcut; it does not exclude C1."'
        )
    if row["label"] == "C1" and not facts["C1"]["affected_weak_rsrp_witness"]:
        case_constraints.extend(
            [
                "C1.affected_below_lower_lobe=true is necessary evidence only; it "
                "does not confirm, prove, validate, or definitively establish C1.",
                "State that the weak-RSRP witness is absent and that C1 is selected "
                "comparatively within the residual candidates, not deterministically.",
                "Do not say the C3 throughput advantage is insufficient or below a "
                "threshold. State its exact supplied value and only say that it is "
                "not an exact gate by itself.",
                "The evidence array must include C1.affected_below_lower_lobe, "
                "C1.affected_weak_rsrp_witness, C3.minimum_advantage_mbps, "
                "C4.affected_overlap_gate, and C6.affected_modulo_30_collision.",
            ]
        )
    if (
        row["label"] == "C1"
        and facts["C1"]["affected_weak_rsrp_witness"]
        and facts["C1"].get("witness")
        and not facts["C1"]["witness"].get(
            "weak_rsrp_at_or_below_minus_90"
        )
    ):
        case_constraints.extend(
            [
                "The case-level boolean confirms at least one affected weak-signal "
                "sample, but the displayed C1 witness object is a different geometric "
                "row and its RSRP must not be quoted as weak evidence.",
                'The reasoning must contain this exact natural sentence: "At least '
                "one affected sample provides weak-signal evidence; the displayed "
                'below-lobe observation is a separate geometric witness."',
            ]
        )
    if row["label"] in {"C4", "C6"}:
        case_constraints.extend(
            [
                f"The active {row['label']} candidate condition is necessary but not "
                "sufficient by itself; justify selection comparatively after exact "
                "gates are excluded.",
                "Do not say the witness confirms causation, directly explains the "
                "throughput, becomes sufficient, or makes the diagnosis definitive.",
                f'The reasoning must contain this exact sentence: "The {row["label"]} '
                f'condition is necessary but not sufficient by itself."',
            ]
        )
    if row["label"] == "C3":
        advantage = facts["C3"]["minimum_advantage_mbps"]
        c4_sentence = (
            "The overlap candidate is present, but it is necessary rather than sufficient."
            if facts["C4"]["affected_overlap_gate"]
            else "No overlap candidate is present."
        )
        c6_sentence = (
            "The modulo-30 collision candidate is present, but it is necessary rather than sufficient."
            if facts["C6"]["affected_modulo_30_collision"]
            else "No modulo-30 collision candidate is present."
        )
        case_constraints.extend(
            [
                "Treat C3 as the residual throughput diagnosis after stronger "
                "supported mechanisms are excluded; do not invent a numeric decision "
                "threshold.",
                "The evidence array must include C1.affected_weak_rsrp_witness, "
                "C3.minimum_advantage_mbps, C4.affected_overlap_gate, and "
                "C6.affected_modulo_30_collision with their exact supplied values.",
                "If a C4 or C6 candidate condition is true, acknowledge that it is "
                "present but necessary rather than sufficient; do not claim it is absent.",
                "Report C3.minimum_advantage_mbps neutrally. Do not say it meets or "
                "misses a threshold, is sufficient/insufficient, confirms C3, or "
                "overrides another candidate.",
                f'The reasoning must contain this exact natural sentence: "The '
                f"alternative segment retains a {advantage} Mbps minimum-throughput "
                'advantage over the affected serving segment."',
                f'The reasoning must contain this exact natural sentence: "{c4_sentence}"',
                f'The reasoning must contain this exact natural sentence: "{c6_sentence}"',
            ]
        )
    user_prompt = json.dumps(
        {
            "gold_label": row["label"],
            "verified_facts": facts,
            "case_constraints": case_constraints,
            "mechanism_observation_guidance": {
                "distance": distance_guidance,
                "handover_frequency": change_guidance,
                "mobility": speed_guidance,
                "scheduled_resources": rb_guidance,
                "instruction": (
                    "Use only the one or two observations that best distinguish the "
                    "gold label from its strongest alternatives. Paraphrase rather "
                    "than copying this guidance mechanically."
                ),
            },
            "mandatory_revision_feedback": revision_feedback or [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
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
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        choice = body["choices"][0]
        message = choice.get("message") or {}
        content = message.get("content")
        result: dict[str, Any] = {
            "source_index": row["source_index"],
            "split": row["split"],
            "gold_label": row["label"],
            "model": model,
            "finish_reason": choice.get("finish_reason"),
            "content": content,
            "reasoning_content": message.get("reasoning_content"),
            "usage": body.get("usage"),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": None,
        }
        if not content:
            result["error"] = "empty_visible_content"
            return result
        try:
            teacher = parse_json_content(content)
            enrich_teacher_evidence(teacher, facts)
            verified, validation_errors = validate_teacher(teacher, row)
            result["teacher"] = teacher
            result["reasoning_verified"] = verified
            result["validation_errors"] = validation_errors
        except (json.JSONDecodeError, ValueError) as exc:
            result["error"] = f"invalid_teacher_json: {exc}"
        return result
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {
            "source_index": row["source_index"],
            "split": row["split"],
            "gold_label": row["label"],
            "model": model,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": f"HTTP {exc.code}: {detail[:1000]}",
        }
    except Exception as exc:  # noqa: BLE001 - preserve per-row failures for resume
        return {
            "source_index": row["source_index"],
            "split": row["split"],
            "gold_label": row["label"],
            "model": model,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="Qwen/Qwen3.5-122B-A10B-FP8")
    parser.add_argument(
        "--api-base",
        default="https://stream-netmind.viettel.vn/gateway/v1",
    )
    parser.add_argument("--api-key-env", default="NETMIND_API_KEY")
    parser.add_argument("--per-label", type=int, default=8)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--max-tokens", type=int, default=400)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        raise SystemExit(f"{args.api_key_env} is required")
    selected = select_balanced(read_jsonl(args.dataset), args.per_label)
    if args.limit is not None:
        selected = selected[: args.limit]
    selected_by_index = {row["source_index"]: row for row in selected}

    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = args.output_dir / "teacher_reasoning_raw.jsonl"
    existing: dict[int, dict[str, Any]] = {}
    if raw_path.exists():
        for result in read_jsonl(raw_path):
            source_index = result["source_index"]
            if source_index not in selected_by_index:
                continue
            if result.get("teacher"):
                enrich_teacher_evidence(
                    result["teacher"], facts_of(selected_by_index[source_index])
                )
                verified, validation_errors = validate_teacher(
                    result["teacher"], selected_by_index[source_index]
                )
                result["reasoning_verified"] = verified
                result["validation_errors"] = validation_errors
            existing[source_index] = result
    pending = [
        row
        for row in selected
        if row["source_index"] not in existing
        or existing[row["source_index"]].get("error")
        or not existing[row["source_index"]].get("reasoning_verified")
    ]

    lock = threading.Lock()
    completed = 0

    def run_one(row: dict[str, Any]) -> dict[str, Any]:
        nonlocal completed
        previous = existing.get(row["source_index"]) or {}
        feedback: list[str] = []
        previous_errors = previous.get("validation_errors") or []
        omit_distance_and_speed = {
            "missing_observation_value=C2.maximum_distance_km",
            "missing_observation_value=C7.maximum_speed_kmh",
        }.issubset(set(previous_errors))
        if omit_distance_and_speed:
            feedback.append(
                "Distance and speed are not material competitors in this sample. "
                "Omit both from the revised reasoning and focus on the gold mechanism "
                "plus the strongest C1/C3 comparison."
            )
        for error in previous_errors:
            if error.startswith("missing_observation_value="):
                path = error.split("=", 1)[1]
                if omit_distance_and_speed and path in {
                    "C2.maximum_distance_km",
                    "C7.maximum_speed_kmh",
                }:
                    continue
                feedback.append(
                    f"You previously mentioned {path} without its measured value. "
                    f'If you retain it, use the exact value '
                    f"{resolve_path(facts_of(row), path)} in the reasoning. Otherwise "
                    "remove every word referring to that observation."
                )
            elif error == "c1_residual_limitation_not_acknowledged":
                feedback.append(
                    "Explicitly state that the lower-lobe geometry is necessary but "
                    "not sufficient by itself and that C1 is a residual comparison."
                )
            elif error == "unstated_threshold_claim":
                feedback.append(
                    "Do not use the word threshold or imply any unstated cutoff."
                )
            else:
                feedback.append(
                    f"Correct the previous validation issue: {error}."
                )
        result = request_teacher(
            row,
            endpoint=args.api_base.rstrip("/") + "/chat/completions",
            api_key=api_key,
            model=args.model,
            max_tokens=args.max_tokens,
            timeout_seconds=args.timeout_seconds,
            revision_feedback=feedback,
        )
        with lock:
            existing[row["source_index"]] = result
            ordered = [existing[index] for index in sorted(existing)]
            write_jsonl(raw_path, ordered)
            completed += 1
            print(
                f"[{completed}/{len(pending)}] source_index={row['source_index']} "
                f"label={row['label']} verified={result.get('reasoning_verified', False)} "
                f"error={result.get('error')}",
                flush=True,
            )
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        list(executor.map(run_one, pending))

    raw_rows = [existing[index] for index in sorted(existing)]
    verified_rows = []
    for result in raw_rows:
        if not result.get("reasoning_verified"):
            continue
        source = selected_by_index[result["source_index"]]
        teacher = result["teacher"]
        verified_rows.append(
            {
                "source_index": source["source_index"],
                "split": source["split"],
                "label": source["label"],
                "verified_facts": source["verified_facts"],
                "gold_reasoning": teacher["reasoning"],
                "reasoning_source": args.model,
                "reasoning_style": teacher["reasoning_style"],
                "reasoning_verified": True,
                "demo_ready": True,
                "teacher_evidence": teacher["evidence"],
                "alternatives_rejected": teacher.get("alternatives_rejected", []),
            }
        )
    verified_path = args.output_dir / "teacher_reasoning_verified.jsonl"
    write_jsonl(verified_path, verified_rows)

    by_label = {
        label: {
            "selected": sum(row["label"] == label for row in selected),
            "verified": sum(row["label"] == label for row in verified_rows),
        }
        for label in LABELS
    }
    summary = {
        "model": args.model,
        "dataset": str(args.dataset.resolve()),
        "selected": len(selected),
        "completed": len(raw_rows),
        "verified": len(verified_rows),
        "errors": sum(bool(row.get("error")) for row in raw_rows),
        "validation_failures": sum(
            not row.get("error") and not row.get("reasoning_verified")
            for row in raw_rows
        ),
        "workers": args.workers,
        "max_tokens": args.max_tokens,
        "temperature": 0.0,
        "enable_thinking": False,
        "by_label": by_label,
        "raw_output": str(raw_path.resolve()),
        "verified_output": str(verified_path.resolve()),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

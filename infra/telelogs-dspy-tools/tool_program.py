"""DSPy programs for raw, all-tools, model-planned, and agentic TeleLogs diagnosis."""

from __future__ import annotations

import contextvars
import json
import re
from typing import Any

import dspy

from neutral_tools import TOOL_CATALOG, TOOL_FUNCTIONS, CaseContext, assert_label_neutral, run_tools


class RawTeleLogsDiagnosis(dspy.Signature):
    """Diagnose one TeleLogs case directly from the complete raw question.

    Analyze the drive-test table, engineering parameters, and all eight root-cause
    definitions in the question. Select the single best-supported cause. Ground the
    explanation in concrete rows and measurements and do not invent missing values.
    """

    raw_question: str = dspy.InputField(desc="Complete unmodified TeleLogs benchmark question.")
    reasoning: str = dspy.OutputField(desc="Concise evidence-based diagnostic explanation, at most 180 words.")
    answer: str = dspy.OutputField(desc="Exactly one class identifier: C1,C2,C3,C4,C5,C6,C7,or C8.")


class ToolSelection(dspy.Signature):
    """Plan which label-neutral measurements are needed for a TeleLogs diagnosis.

    Read the complete case and choose between two and six tools from the supplied
    catalog. Select tools because their measurements can confirm or eliminate
    plausible causes in this specific case. Tool names must be copied exactly. Do
    not decide the final class at this planning stage.
    """

    raw_question: str = dspy.InputField(desc="Complete unmodified TeleLogs benchmark question.")
    tool_catalog: str = dspy.InputField(desc="JSON catalog of available measurement tools.")
    planning_reason: str = dspy.OutputField(desc="Short explanation of the measurements needed, without a final class.")
    requested_tools: list[str] = dspy.OutputField(desc="Two to six exact tool names from tool_catalog.")


class EvidenceBasedDiagnosis(dspy.Signature):
    """Diagnose a TeleLogs case using raw input and label-neutral tool observations.

    The observations contain measurements only; they do not contain a diagnosis.
    Compare the supplied measurements with the eight cause definitions in the raw
    question. Resolve competing evidence explicitly, cite decisive rows and values,
    and select exactly one best-supported class. Never treat an unrequested or
    missing measurement as evidence that a cause is absent.
    """

    raw_question: str = dspy.InputField(desc="Complete unmodified TeleLogs benchmark question.")
    selected_tools: str = dspy.InputField(desc="JSON list of measurement tools used for this case.")
    tool_observations: str = dspy.InputField(desc="Authoritative JSON measurements returned by deterministic tools.")
    planning_reason: str = dspy.InputField(desc="Why these measurements were selected; may state that all tools ran.")
    reasoning: str = dspy.OutputField(desc="Auditable diagnosis citing tool measurements and row evidence, at most 180 words.")
    answer: str = dspy.OutputField(desc="Exactly one class identifier: C1,C2,C3,C4,C5,C6,C7,or C8.")


def normalize_answer(value: Any) -> str:
    text = str(value or "")
    matches = re.findall(r"\bC([1-8])\b", text, flags=re.IGNORECASE)
    if matches:
        return f"C{matches[-1]}"
    boxed = re.findall(r"\\boxed\{\s*([1-8])\s*\}", text)
    return f"C{boxed[-1]}" if boxed else ""


def normalize_requested_tools(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = re.findall(r"analyze_[a-z_]+", value)
    if not isinstance(value, (list, tuple)):
        value = []
    selected = []
    for item in value:
        name = str(item).strip()
        if name in TOOL_FUNCTIONS and name not in selected:
            selected.append(name)
    return selected[:6]


class RawProgram(dspy.Module):
    def __init__(self) -> None:
        super().__init__()
        self.diagnose = dspy.Predict(RawTeleLogsDiagnosis)

    def forward(self, raw_question: str, case: CaseContext | None = None) -> dspy.Prediction:
        del case
        pred = self.diagnose(raw_question=raw_question)
        pred.selected_tools = []
        pred.tool_observations = {}
        pred.planning_reason = "No tools: direct raw-question DSPy baseline."
        pred.lm_calls = 1
        return pred


class AllToolsProgram(dspy.Module):
    def __init__(self, instructions: str | None = None) -> None:
        super().__init__()
        signature = (
            EvidenceBasedDiagnosis.with_instructions(instructions)
            if instructions
            else EvidenceBasedDiagnosis
        )
        self.diagnose = dspy.Predict(signature)

    def forward(self, raw_question: str, case: CaseContext) -> dspy.Prediction:
        selected = list(TOOL_FUNCTIONS)
        observations = run_tools(case, selected)
        assert_label_neutral(observations)
        planning_reason = "All label-neutral measurement tools were executed for the controlled all-tools baseline."
        pred = self.diagnose(
            raw_question=raw_question,
            selected_tools=json.dumps(selected, ensure_ascii=False),
            tool_observations=json.dumps(observations, ensure_ascii=False, separators=(",", ":")),
            planning_reason=planning_reason,
        )
        pred.selected_tools = selected
        pred.tool_observations = observations
        pred.planning_reason = planning_reason
        pred.lm_calls = 1
        return pred


class PlannedToolsProgram(dspy.Module):
    def __init__(self) -> None:
        super().__init__()
        self.plan = dspy.Predict(ToolSelection)
        self.diagnose = dspy.Predict(EvidenceBasedDiagnosis)

    def forward(self, raw_question: str, case: CaseContext) -> dspy.Prediction:
        plan = self.plan(
            raw_question=raw_question,
            tool_catalog=json.dumps(TOOL_CATALOG, ensure_ascii=False, separators=(",", ":")),
        )
        selected = normalize_requested_tools(plan.requested_tools)
        observations = run_tools(case, selected)
        assert_label_neutral(observations)
        pred = self.diagnose(
            raw_question=raw_question,
            selected_tools=json.dumps(selected, ensure_ascii=False),
            tool_observations=json.dumps(observations, ensure_ascii=False, separators=(",", ":")),
            planning_reason=str(plan.planning_reason),
        )
        pred.selected_tools = selected
        pred.tool_observations = observations
        pred.planning_reason = str(plan.planning_reason)
        pred.lm_calls = 2
        return pred


class AgenticToolDiagnosis(dspy.Signature):
    """Diagnose one TeleLogs case by iteratively requesting label-neutral measurements.

    The available tools return deterministic measurements parsed from the raw
    tables; they never return a diagnosis. Call the tools whose measurements can
    confirm or eliminate plausible causes for this specific case, read each
    observation before deciding what to measure next, and stop calling tools once
    the decisive evidence is in hand. Compare the collected measurements with the
    eight cause definitions in the raw question, resolve competing evidence
    explicitly, and cite decisive rows and values. Never treat a measurement you
    did not request as evidence that a cause is absent, and never recompute or
    contradict a returned measurement.
    """

    raw_question: str = dspy.InputField(desc="Complete unmodified TeleLogs benchmark question.")
    reasoning: str = dspy.OutputField(desc="Auditable diagnosis citing tool measurements and row evidence, at most 180 words.")
    answer: str = dspy.OutputField(desc="Exactly one class identifier: C1,C2,C3,C4,C5,C6,C7,or C8.")


_ACTIVE_CASE: contextvars.ContextVar[CaseContext | None] = contextvars.ContextVar(
    "telelogs_active_case", default=None
)


def _case_tool(name: str, description: str):
    def call() -> str:
        case = _ACTIVE_CASE.get()
        if case is None:
            raise RuntimeError("no active CaseContext bound for tool execution")
        observation = TOOL_FUNCTIONS[name](case)
        assert_label_neutral(observation)
        return json.dumps(observation, ensure_ascii=False, separators=(",", ":"))

    call.__name__ = name
    call.__doc__ = description
    return dspy.Tool(call, name=name, desc=description)


class ReActToolsProgram(dspy.Module):
    """LM-driven function calling: the model chooses, sequences, and reads tools."""

    def __init__(self, max_iters: int = 8, instructions: str | None = None) -> None:
        super().__init__()
        signature = (
            AgenticToolDiagnosis.with_instructions(instructions)
            if instructions
            else AgenticToolDiagnosis
        )
        self.react = dspy.ReAct(
            signature,
            tools=[_case_tool(item["name"], item["description"]) for item in TOOL_CATALOG],
            max_iters=max_iters,
        )

    def forward(self, raw_question: str, case: CaseContext) -> dspy.Prediction:
        token = _ACTIVE_CASE.set(case)
        try:
            pred = self.react(raw_question=raw_question)
        finally:
            _ACTIVE_CASE.reset(token)
        trajectory = dict(getattr(pred, "trajectory", {}) or {})
        selected, observations, steps = [], {}, 0
        for step in range(len(trajectory)):
            tool_name = trajectory.get(f"tool_name_{step}")
            if tool_name is None:
                break
            steps += 1
            if tool_name not in TOOL_FUNCTIONS:
                continue
            selected.append(tool_name)
            raw_observation = trajectory.get(f"observation_{step}")
            try:
                observations[f"step{step}_{tool_name}"] = json.loads(raw_observation)
            except (TypeError, json.JSONDecodeError):
                observations[f"step{step}_{tool_name}"] = raw_observation
        pred.selected_tools = selected
        pred.tool_observations = observations
        thoughts = [
            str(trajectory.get(f"thought_{step}", "")).strip()
            for step in range(steps)
            if str(trajectory.get(f"thought_{step}", "")).strip()
        ]
        pred.planning_reason = " | ".join(thoughts) if thoughts else "ReAct trajectory without recorded thoughts."
        pred.lm_calls = steps + 1
        return pred


from verifier import VerifiedReActProgram  # noqa: E402  (needs ReActToolsProgram defined above)
from forced_program import ForcedMeasurementProgram  # noqa: E402

PROGRAMS = {
    "b0_raw": RawProgram,
    "b1_all_tools": AllToolsProgram,
    "b2_planned_tools": PlannedToolsProgram,
    "b3_react_tools": ReActToolsProgram,
    "b3_react_verified": VerifiedReActProgram,
    "b3_react_forced": ForcedMeasurementProgram,
}

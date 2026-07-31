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
# Live record of the current trajectory's tool usage, so the check tool can
# audit "what has actually been measured so far" without waiting for the
# trajectory to finish. Reset per forward() alongside the case.
_CALL_RECORD: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "telelogs_call_record", default=None
)


def _case_tool(name: str, description: str):
    def call() -> str:
        case = _ACTIVE_CASE.get()
        if case is None:
            raise RuntimeError("no active CaseContext bound for tool execution")
        observation = TOOL_FUNCTIONS[name](case)
        assert_label_neutral(observation)
        record = _CALL_RECORD.get()
        if record is not None:
            record["selected"].append(name)
            record["observations"][f"live_{name}"] = observation
        return json.dumps(observation, ensure_ascii=False, separators=(",", ":"))

    call.__name__ = name
    call.__doc__ = description
    return dspy.Tool(call, name=name, desc=description)


CHECK_TOOL_NAME = "check_verification_lines"
CHECK_TOOL_DESC = (
    "Audit your drafted verification lines against the measurements already returned "
    "in this trajectory. Pass the complete drafted lines as verification_lines and the "
    "class you currently intend to answer as tentative_answer. Returns the list of "
    "contradictions found in your own text or tool usage — a misquoted value, an "
    "inequality whose verdict word contradicts the comparison, a conclusion missing its "
    "required measurement. It does not know the diagnosis and never suggests a class. "
    "Call it after drafting your verification lines, before the final answer."
)


def run_verification_check(verification_lines: str, tentative_answer: str) -> str:
    """The consistency audit, exposed mid-trajectory. Pure code, label-neutral."""
    from verifier import verify_prediction  # deferred: verifier imports this module

    record = _CALL_RECORD.get() or {"selected": [], "observations": {}}
    flags = verify_prediction(
        normalize_answer(tentative_answer),
        str(verification_lines or ""),
        list(record["selected"]),
        dict(record["observations"]),
    )
    result = {"contradictions": flags}
    if not flags:
        result["note"] = "No contradiction detected in the checked lines."
    assert_label_neutral(result)
    return json.dumps(result, ensure_ascii=False)


def _check_tool():
    return dspy.Tool(run_verification_check, name=CHECK_TOOL_NAME, desc=CHECK_TOOL_DESC)


class ReActToolsProgram(dspy.Module):
    """LM-driven function calling: the model chooses, sequences, and reads tools."""

    def __init__(self, max_iters: int = 8, instructions: str | None = None,
                 include_check_tool: bool = False) -> None:
        super().__init__()
        signature = (
            AgenticToolDiagnosis.with_instructions(instructions)
            if instructions
            else AgenticToolDiagnosis
        )
        tools = [_case_tool(item["name"], item["description"]) for item in TOOL_CATALOG]
        if include_check_tool:
            tools.append(_check_tool())
        self.react = dspy.ReAct(
            signature,
            tools=tools,
            max_iters=max_iters,
        )

    def forward(self, raw_question: str, case: CaseContext) -> dspy.Prediction:
        token = _ACTIVE_CASE.set(case)
        record_token = _CALL_RECORD.set({"selected": [], "observations": {}})
        try:
            pred = self.react(raw_question=raw_question)
        finally:
            _ACTIVE_CASE.reset(token)
            _CALL_RECORD.reset(record_token)
        trajectory = dict(getattr(pred, "trajectory", {}) or {})
        selected, observations, steps = [], {}, 0
        check_calls = 0
        for step in range(len(trajectory)):
            tool_name = trajectory.get(f"tool_name_{step}")
            if tool_name is None:
                break
            steps += 1
            if tool_name == CHECK_TOOL_NAME:
                check_calls += 1
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
        pred.check_tool_calls = check_calls
        return pred


class CheckReActToolsProgram(ReActToolsProgram):
    """ReAct with the consistency audit available as a callable 7th tool.

    The audit itself is unchanged code (verifier.verify_prediction); packaging it
    as a tool moves the correction inside the trajectory instead of after it. The
    model may still ignore the tool, so the post-hoc acceptance gate downstream is
    what guarantees the floor. max_iters is raised to 10 because a disciplined
    trajectory now spends one or two extra steps on the check calls.
    """

    def __init__(self, max_iters: int = 10, instructions: str | None = None) -> None:
        super().__init__(max_iters=max_iters, instructions=instructions, include_check_tool=True)


from verifier import VerifiedReActProgram  # noqa: E402  (needs ReActToolsProgram defined above)
from forced_program import ForcedMeasurementProgram  # noqa: E402
from specialist_program import CheckSpecialistProgram, FastSpecialistProgram, SpecialistProgram  # noqa: E402
from compare_program import ComparisonProgram  # noqa: E402

PROGRAMS = {
    "b0_raw": RawProgram,
    "b1_all_tools": AllToolsProgram,
    "b2_planned_tools": PlannedToolsProgram,
    "b3_react_tools": ReActToolsProgram,
    "b3_react_check_tools": CheckReActToolsProgram,
    "b3_react_check_specialist": CheckSpecialistProgram,
    "b3_react_verified": VerifiedReActProgram,
    "b3_react_forced": ForcedMeasurementProgram,
    "b3_react_specialist": SpecialistProgram,
    "b3_react_specialist_fast": FastSpecialistProgram,
    "b3_react_compare": ComparisonProgram,
}

"""Single-program ReAct with NON-OPTIONAL stage-two measurement.

Round-3 diagnosis of the best single voter (s11 + verifier v2.1) on official-864
split its residual zone in two:

    called both stage-2 tools : 194 cases, 63.9% correct
    missing a stage-2 tool   : 182 cases, 34.1% correct (it answered C1 in 120 of them)

All 182 under-measured cases had already burned the full three audit attempts —
the verifier asked for the missing measurement every time and was ignored. Of
those, 88 would be answered correctly by the calibrated policy once the numbers
are on the table, i.e. +10.2 points on official for one program with no vote.

So this module stops asking. When the model reaches a residual conclusion while
a stage-2 observation is missing, the missing tools are executed and their output
is handed to the model as an authoritative measurement block, then the model
decides again. Running a tool supplies no label — `assert_label_neutral` is
applied to every forced observation, so this cannot leak the answer; it only
removes the model's option to skip the measurement it was told to take.
"""

from __future__ import annotations

import json
from typing import Any

import dspy

from neutral_tools import assert_label_neutral, run_tools
from verifier import AUDIT_HEADER, RESIDUAL_TOOLS, STRONG_C1_RE, verify_prediction

# A residual answer needs both stage-2 measurements. C1 is exempt only when the
# model quoted the strong witness (elevation below the lower edge with RSRP <= -90),
# which is decided inside analyze_coverage_geometry.
RESIDUAL_ANSWERS = {"C1", "C3", "C4", "C6"}

FORCED_HEADER = (
    "\n\n[Required measurements you did not take. These are the authoritative outputs of "
    "the deterministic measurement tools named below, executed for this case. They contain "
    "measurements only, no diagnosis. Evaluate the residual rules in order against them and "
    "quote the fields you use.]\n"
)


def missing_stage_two(answer: str, reasoning: str, selected_tools: list[str]) -> list[str]:
    """Which stage-2 tools this answer needed but never ran."""
    if answer not in RESIDUAL_ANSWERS:
        return []
    if answer == "C1" and STRONG_C1_RE.search(reasoning or ""):
        return []
    called = set(selected_tools or [])
    return [tool for tool in RESIDUAL_TOOLS if tool not in called]


def forced_block(case, tools: list[str]) -> tuple[str, dict[str, Any]]:
    observations = run_tools(case, tools)
    for value in observations.values():
        assert_label_neutral(value)
    lines = [f"{name}: {json.dumps(value, ensure_ascii=False)}" for name, value in observations.items()]
    return FORCED_HEADER + "\n".join(lines), observations


class ForcedMeasurementProgram(dspy.Module):
    """ReAct + consistency audit + non-optional stage-two measurement.

    One program, one answer — no voting. Compiled states load from
    ReActToolsProgram exactly like the verified program does.
    """

    def __init__(self, max_iters: int = 8, instructions: str | None = None, max_retries: int = 2) -> None:
        super().__init__()
        from tool_program import ReActToolsProgram

        self.inner = ReActToolsProgram(max_iters=max_iters, instructions=instructions)
        self.max_retries = max_retries

    def load(self, path) -> None:
        self.inner.load(path)

    def forward(self, raw_question: str, case) -> dspy.Prediction:
        from tool_program import normalize_answer

        attempts: list[tuple[dspy.Prediction, list[str]]] = []
        question = raw_question
        total_calls = 0
        forced: dict[str, Any] = {}
        forced_rounds = 0

        for _attempt in range(self.max_retries + 1):
            pred = self.inner(raw_question=question, case=case)
            total_calls += int(getattr(pred, "lm_calls", 1))
            answer = normalize_answer(getattr(pred, "answer", ""))
            reasoning = str(getattr(pred, "reasoning", ""))
            selected = list(getattr(pred, "selected_tools", []))
            observations = dict(getattr(pred, "tool_observations", {}))
            if forced:
                # keep the forced numbers visible to the audit and to the saved trace
                observations = {**observations, **{f"forced_{name}": value for name, value in forced.items()}}
                pred.tool_observations = observations
                selected = selected + [name for name in forced if name not in selected]
                pred.selected_tools = selected

            flags = verify_prediction(answer, reasoning, selected, observations)
            attempts.append((pred, flags))
            if not flags:
                break

            missing = missing_stage_two(answer, reasoning, selected)
            if missing:
                block, observed = forced_block(case, missing)
                forced.update(observed)
                forced_rounds += 1
                question = raw_question + block
                if len(flags) > 1:
                    question += AUDIT_HEADER + "\n".join(f"- {flag}" for flag in flags)
            else:
                question = raw_question + AUDIT_HEADER + "\n".join(f"- {flag}" for flag in flags)
                if forced:
                    block, _ = forced_block(case, list(forced))
                    question += block

        pred, final_flags = min(enumerate(attempts), key=lambda item: (len(item[1][1]), -item[0]))[1]
        pred.lm_calls = total_calls
        pred.planning_reason = (
            f"{getattr(pred, 'planning_reason', '')} || verifier attempts={len(attempts)} "
            f"forced_rounds={forced_rounds} forced_tools={sorted(forced)} "
            f"flags_per_attempt={[len(flags) for _, flags in attempts]} "
            f"final_flags={final_flags}"
        )
        return pred

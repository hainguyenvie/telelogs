"""Single-program variant: a dedicated residual step instead of one monolithic prompt.

Round-4 measurement on the forced program: with all six observations guaranteed on
the table, the residual zone still only reaches ~60% while a pure-Python executor of
the same published rules reaches ~77%. The remaining loss is not missing data — it is
the model working the ordered rule list inside a prompt that is also carrying the four
gate criteria, the eight cause definitions and the whole drive-test table.

So the residual step gets its own call. Routing stays with the LM: the specialist is
invoked only because the first pass concluded a residual class, i.e. the model itself
decided no decisive criterion fired. The specialist sees the same observations and the
same rule order the seed instruction already states, and nothing else. It cannot see a
label, and it cannot overrule a gate answer.
"""

from __future__ import annotations

import json
import re

import dspy

from verifier import RESIDUAL_TOOLS
from forced_program import RESIDUAL_ANSWERS, forced_block  # noqa: F401  (forced_block reused)


GATE_CLASSES = {"C2", "C5", "C7", "C8"}
_CLASS_LINE = re.compile(r"^\s*[-*]?\s*\**\s*(C[1-8])\b")


def gate_lines_only(reasoning: str) -> str:
    """The first pass's gate verification, without the residual lines the specialist supersedes.

    The first pass writes four gate lines and then, when no criterion fires, its own
    residual attempt. The specialist redecides that second part from scratch, so keeping
    both leaves the saved trace saying the same thing twice — and occasionally saying it
    two different ways, which reads as the system contradicting itself when it is not.

    Only the leading gate block is kept. Anything unrecognised falls through untouched:
    a presentation tweak must never be able to drop evidence.
    """
    lines = (reasoning or "").splitlines()
    kept, seen_gate = [], False
    for line in lines:
        match = _CLASS_LINE.match(line)
        if match and match.group(1) not in GATE_CLASSES:
            break
        if match:
            seen_gate = True
        kept.append(line)
    if not seen_gate:
        return reasoning or ""
    return "\n".join(kept).rstrip()


class ResidualDecision(dspy.Signature):
    """Apply the calibrated residual tie-break to measurements that are already on the table.

    The four decisive criteria have all been verified as "not triggered" for this case,
    so the cause is one of: C1 (serving downtilt too large, weak coverage at the far
    end), C3 (a neighbouring cell provides higher throughput), C4 (non-colocated
    co-frequency neighbours cause severe overlapping coverage), C6 (neighbour and
    serving cell share PCI mod 30).

    Work the rules one at a time, in exactly this order, quoting the named observation
    field and writing comparisons as signed inequalities between the two numbers
    (for negative numbers -2.1 > -3 and -95 < -90):

    0. A row in rows_below_main_lobe_lower_edge whose throughput is below the stated
       criterion AND whose serving RSRP satisfies RSRP <= -90 proves C1 outright.
    1. otherwise minimum_difference_mbps from segment_minimum_comparison of at least
       142.5 selects C3 (undefined when the drive has a single segment);
    2. otherwise a non-empty equal_residue_pairs selects C6;
    3. otherwise a best_noncolocated_gap of -3 dB or stronger (gap > -3 or gap = -3)
       selects C4 (null means there is no non-colocated neighbour);
    4. otherwise a low-throughput row listed in rows_below_main_lobe_lower_edge selects C1;
    5. otherwise select C3.

    Stop at the first satisfied rule: it is the diagnosis, and later rules must not be
    evaluated or mentioned. Every number you write must appear in the observations.
    """

    tool_observations: str = dspy.InputField(desc="Authoritative JSON measurements from all six tools.")
    gate_lines: str = dspy.InputField(desc="The four decisive-criterion verification lines already written for this case.")
    reasoning: str = dspy.OutputField(desc="One line per rule considered, in order, stopping at the first satisfied rule.")
    answer: str = dspy.OutputField(desc="Exactly one of C1, C3, C4, C6.")


class SpecialistProgram(dspy.Module):
    """Forced-measurement ReAct for stage one, then a dedicated residual decider."""

    def __init__(self, max_iters: int = 8, instructions: str | None = None, max_retries: int = 2,
                 defer_residual_rerun: bool = False, include_check_tool: bool = False) -> None:
        super().__init__()
        from forced_program import ForcedMeasurementProgram

        self.inner = ForcedMeasurementProgram(
            max_iters=max_iters, instructions=instructions, max_retries=max_retries,
            defer_residual_rerun=defer_residual_rerun, include_check_tool=include_check_tool,
        )
        self.residual = dspy.Predict(ResidualDecision)

    def load(self, path) -> None:
        self.inner.load(path)

    def forward(self, raw_question: str, case) -> dspy.Prediction:
        from tool_program import normalize_answer

        pred = self.inner(raw_question=raw_question, case=case)
        answer = normalize_answer(getattr(pred, "answer", ""))
        if answer not in RESIDUAL_ANSWERS:
            pred.planning_reason = f"{getattr(pred, 'planning_reason', '')} || specialist=skipped (gate answer)"
            return pred

        observations = dict(getattr(pred, "tool_observations", {}))
        missing = [
            tool for tool in RESIDUAL_TOOLS
            if not any(key.endswith(tool) for key in observations)
        ]
        if missing:
            block, observed = forced_block(case, missing)
            observations.update({f"forced_{name}": value for name, value in observed.items()})
            pred.tool_observations = observations

        decision = self.residual(
            tool_observations=json.dumps(observations, ensure_ascii=False),
            gate_lines=str(getattr(pred, "reasoning", "")),
        )
        specialist_answer = normalize_answer(getattr(decision, "answer", ""))
        pred.lm_calls = int(getattr(pred, "lm_calls", 1)) + 1
        if specialist_answer in RESIDUAL_ANSWERS:
            pred.planning_reason = (
                f"{getattr(pred, 'planning_reason', '')} || specialist={specialist_answer} "
                f"(first pass said {answer})"
            )
            # keep the audited text verbatim; `reasoning` becomes the final explanation
            pred.audit_trace = str(getattr(pred, "reasoning", ""))
            pred.reasoning = (
                f"{gate_lines_only(pred.audit_trace)}\n\n[residual specialist]\n"
                f"{getattr(decision, 'reasoning', '')}"
            )
            pred.answer = specialist_answer
        else:
            pred.planning_reason = f"{getattr(pred, 'planning_reason', '')} || specialist=unparsed, kept {answer}"
        return pred


class FastSpecialistProgram(SpecialistProgram):
    """Same decisions, without the re-run that the specialist makes redundant.

    When the audit's only complaint is that a residual conclusion was reached
    without its stage-two measurements, the current pipeline re-runs the entire
    ReAct trajectory so the model can look at the numbers it skipped. But the
    specialist then redecides from those same numbers regardless, so the re-run
    buys a discarded intermediate answer for about 7.7 LM calls, on 41 of 96 dev
    cases. This variant attaches the measurements and goes straight to the
    specialist.

    Registered separately so the saving can be A/B'd against the shipped program
    rather than assumed: identical answers would mean the re-run was never doing
    work, and any difference is a real cost of the shortcut.
    """

    def __init__(self, max_iters: int = 8, instructions: str | None = None,
                 max_retries: int = 2) -> None:
        super().__init__(max_iters=max_iters, instructions=instructions,
                         max_retries=max_retries, defer_residual_rerun=True)


class CheckSpecialistProgram(SpecialistProgram):
    """The full specialist stack with the audit also available as a 7th tool.

    Layer ② (the post-hoc acceptance gate) is unchanged — the tool only gives the
    model a chance to fix its lines before that gate sees them. Compiled states
    must come from CheckReActToolsProgram: the old demos never call the new tool,
    so loading a 6-tool compile here would measure demo mismatch, not the tool.
    """

    def __init__(self, max_iters: int = 10, instructions: str | None = None,
                 max_retries: int = 2) -> None:
        super().__init__(max_iters=max_iters, instructions=instructions,
                         max_retries=max_retries, include_check_tool=True)

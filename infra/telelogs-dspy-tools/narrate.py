"""Turn a locked diagnosis into readable prose without touching the decision.

The rigid trace ("- C2: maximum_distance_km = 0.11 < 1 -> not triggered") is
load-bearing: the consistency verifier parses exactly those lines — field name,
inequality, verdict word — so relaxing the format would blind the audit. The fix
is not to soften the audited text but to stop asking one string to do two jobs.

This layer runs AFTER the answer is locked and produces a separate `narrative`
field. The class is an input here, never an output, so narration cannot change
the verdict; and because it reads the same observations, it cannot introduce a
number the tools did not measure without that being visible next to the audited
lines.
"""

from __future__ import annotations

import json
import re

import dspy

# Numbers that are thresholds from the task definition rather than measurements:
# a narrative may legitimately say "below the 160 RB threshold" without 160
# appearing in any observation. Everything else must be traceable.
RULE_CONSTANTS = {"1", "3", "30", "40", "160", "142.5", "-3", "90", "-90", "600"}
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def ungrounded_numbers(narrative: str, observations: dict, raw_question: str = "") -> list[str]:
    """Numbers asserted in the prose that appear in no measurement and no rule constant.

    Same principle as the consistency verifier, applied to the presentation layer:
    the narrative is the part a human quotes, so it is the part that must not
    invent a value. Pure text comparison, no LM, no knowledge of the class.
    """
    haystack = json.dumps(observations, ensure_ascii=False) + "\n" + (raw_question or "")
    out = []
    for token in dict.fromkeys(_NUMBER.findall(narrative or "")):
        if token in RULE_CONSTANTS or token in haystack:
            continue
        # 2.774 quoted as 2.77, or 33.0 quoted as 33
        trimmed = token.rstrip("0").rstrip(".") if "." in token else token
        if trimmed and trimmed in haystack:
            continue
        out.append(token)
    return out


class NarrateDiagnosis(dspy.Signature):
    """Explain an already-decided TeleLogs diagnosis the way a radio engineer would.

    The verdict in `answer` is final: it was produced by an audited procedure and
    machine-checked against the measurements. Do not re-open it, do not hedge it,
    and do not propose an alternative class. Your only job is to make the finding
    readable for a human report.

    Write flowing prose, not a checklist. Open with what actually happens on this
    road section, then explain the mechanism in physical terms (coverage geometry,
    interference, scheduling, mobility), and use the decisive numbers from
    `audited_evidence` inline as supporting detail rather than as a list of gate
    verdicts. Mention in one clause what the strongest competing explanation was
    and which measurement ruled it out. Never introduce a value that is not in
    `tool_observations` or `audited_evidence`.
    """

    raw_question: str = dspy.InputField(desc="The original case, for context on the road section and cells.")
    tool_observations: str = dspy.InputField(desc="Authoritative JSON measurements; the only source of numbers.")
    audited_evidence: str = dspy.InputField(desc="The machine-checked evidence lines behind the verdict.")
    answer: str = dspy.InputField(desc="The final class. It is fixed input, not something to decide.")
    language: str = dspy.InputField(desc="Language to write the narrative in.")
    narrative: str = dspy.OutputField(desc="Four to seven sentences of natural technical prose, no bullet points, no restating the rule list.")


class NarrationLayer(dspy.Module):
    """Optional presentation pass; adds `narrative`, changes nothing else."""

    def __init__(self, language: str = "English") -> None:
        super().__init__()
        self.language = language
        self.narrate = dspy.Predict(NarrateDiagnosis)

    def forward(self, raw_question: str, pred) -> str:
        answer = str(getattr(pred, "answer", "") or "").strip()
        if not answer:
            return ""
        observations = json.dumps(dict(getattr(pred, "tool_observations", {})), ensure_ascii=False)
        result = self.narrate(
            raw_question=raw_question,
            tool_observations=observations,
            audited_evidence=str(getattr(pred, "reasoning", "")),
            answer=answer,
            language=self.language,
        )
        narrative = str(getattr(result, "narrative", "") or "").strip()
        pred.narrative_ungrounded = ungrounded_numbers(
            narrative, dict(getattr(pred, "tool_observations", {})), raw_question
        )
        return narrative

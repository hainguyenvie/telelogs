"""DSPy ReAct program that begins with raw TeleLogs tables."""

from __future__ import annotations

import json
import re
from typing import Any

import dspy

from raw_tools import RawCaseCalculator


class RawTableTeleLogsDiagnosis(dspy.Signature):
    """Diagnose a TeleLogs case by choosing tools over the raw tables.

    First call `inspect_raw_case`. After inspection, call only tools named in
    its ordered `candidate_tools`. If it lists one candidate, call that tool.
    If it lists two or more candidates, call the first two before deciding;
    those observations represent mechanisms that must be distinguished. Do not
    replace a listed candidate with an unrelated tool, and do not finish after
    inspection alone.

    Map verified mechanisms to classes yourself: weak or below-lobe coverage is
    C1; unusually long serving distance is C2; serving-segment throughput
    disparity is C3; non-colocated overlap is C4; repeated serving-cell changes
    are C5; modulo-30 PCI collision is C6; high mobility is C7; and radio-resource
    pressure is C8.

    Tool observations contain measurements and provenance, never a class label.
    Produce the final answer yourself. Explain the network behavior naturally in
    two or three concise sentences. Do not mention tool names, JSON fields,
    internal rules, booleans, or phrases such as "triggered" and "untriggered".
    """

    raw_table: str = dspy.InputField(
        desc=(
            "The original TeleLogs question with raw drive-test and engineering "
            "tables. No verified fact sheet or gold label is included."
        )
    )
    reasoning: str = dspy.OutputField(
        desc="Natural grounded explanation in 2-3 sentences, at most 100 words."
    )
    answer: str = dspy.OutputField(desc="Exactly one of C1,C2,C3,C4,C5,C6,C7,C8.")


class FreeRawTableTeleLogsDiagnosis(dspy.Signature):
    """Diagnose a TeleLogs case by freely choosing calculators over raw tables.

    Read the original drive-test and engineering tables, decide which physical
    mechanisms are plausible, and call at least one relevant calculator before
    answering. The available calculators cover radio geometry, signal coverage,
    serving transitions, mobility, resource usage, serving-segment throughput,
    neighbor overlap, and PCI patterns. You may call additional calculators
    when observations conflict or when a competing explanation must be ruled
    out. There is no prescribed tool order and no deterministic shortlist.

    Map verified mechanisms to classes yourself: weak or below-lobe coverage is
    C1; unusually long serving distance is C2; serving-segment throughput
    disparity is C3; non-colocated overlap is C4; repeated serving-cell changes
    are C5; modulo-30 PCI collision is C6; high mobility is C7; and radio-resource
    pressure is C8.

    Tool observations contain measurements and provenance, never a class label.
    Produce the final answer yourself. Explain the network behavior naturally in
    two or three concise sentences. Do not mention tool names, JSON fields,
    internal rules, booleans, or phrases such as "triggered" and "untriggered".
    """

    raw_table: str = dspy.InputField(
        desc=(
            "The original TeleLogs question with raw drive-test and engineering "
            "tables. No verified fact sheet, shortlist, or gold label is included."
        )
    )
    reasoning: str = dspy.OutputField(
        desc="Natural grounded explanation in 2-3 sentences, at most 100 words."
    )
    answer: str = dspy.OutputField(desc="Exactly one of C1,C2,C3,C4,C5,C6,C7,C8.")


class CommitPlanRawTableTeleLogsDiagnosis(dspy.Signature):
    """Diagnose one raw TeleLogs case with a committed analysis plan.

    Your first action must be `commit_analysis_plan`. Read the raw table and
    choose one or two exact calculator names whose measurements are necessary.
    Give a concrete rationale grounded in raw columns or row patterns. The plan
    tool does not calculate facts and does not recommend a class.

    After the plan is accepted, call `execute_committed_plan` exactly once. It
    runs only the calculators you committed and returns their separate verified
    observations. Compare those observations before deciding.
    Map weak or below-lobe coverage to C1; unusually long serving distance to C2;
    serving-segment throughput disparity to C3; non-colocated overlap to C4;
    repeated serving-cell changes to C5; modulo-30 PCI collision to C6; high
    mobility to C7; and radio-resource pressure to C8.

    Explain the network behavior naturally in two or three concise sentences.
    Do not mention tool names, JSON fields, internal rules, or words such as
    "triggered" and "untriggered".
    """

    raw_table: str = dspy.InputField(
        desc=(
            "Original drive-test and engineering tables. No verified facts, "
            "deterministic shortlist, or gold label is included."
        )
    )
    reasoning: str = dspy.OutputField(
        desc="Natural grounded explanation in 2-3 sentences, at most 100 words."
    )
    answer: str = dspy.OutputField(desc="Exactly one of C1,C2,C3,C4,C5,C6,C7,C8.")


class FreeToolSelection(dspy.Signature):
    """Freely plan which calculators are useful for one raw TeleLogs case.

    Inspect the raw values and identify the one to three most plausible physical
    mechanisms. Before selecting, screen all eight mechanisms rather than
    defaulting to interference, overlap, or mobility. From the raw affected rows
    check maximum GPS speed, average scheduled RBs, serving-PCI change count,
    serving/neighbor PCI modulo-30 patterns, serving distance and antenna
    geometry when engineering data permits, minimum throughput by serving PCI,
    non-colocated neighbor strength, and weak/below-lobe coverage.

    Select calculators because specific raw measurements need verification, not
    because of their position in the catalog. Cite concrete values or row-level
    patterns in the selection reasoning. Include a competing calculator when
    two mechanisms could explain the same affected rows. Do not diagnose a C1-C8
    class yet and do not invent values that are absent from the tables.
    """

    raw_table: str = dspy.InputField(
        desc="Original drive-test and engineering tables without verified facts."
    )
    tool_catalog: str = dspy.InputField(
        desc="Available calculator names and the measurements each one verifies."
    )
    selection_reasoning: str = dspy.OutputField(
        desc=(
            "Balanced screening of all mechanisms using concrete raw values, "
            "then a brief justification for the selected measurements; at most "
            "160 words."
        )
    )
    selected_tools: str = dspy.OutputField(
        desc=(
            "One to three exact calculator names from the catalog, separated "
            "by commas, in the order they should be called."
        )
    )


class RawTableReActTeleLogsProgram(dspy.Module):
    """Let one ReAct agent select mechanism calculators over the raw case."""

    TOOL_NAMES = (
        "inspect_raw_case",
        "analyze_radio_geometry",
        "analyze_signal_coverage",
        "analyze_serving_transitions",
        "analyze_mobility",
        "analyze_resource_usage",
        "compare_segment_throughput",
        "analyze_neighbor_overlap",
        "analyze_pci_pattern",
    )

    def __init__(
        self,
        c3_advantage_threshold_mbps: float = 142.5,
        max_iters: int = 3,
        tool_selection: str = "guided",
    ) -> None:
        super().__init__()
        if tool_selection not in {"guided", "free", "commit_plan"}:
            raise ValueError(
                "tool_selection must be 'guided', 'free', or 'commit_plan'"
            )
        self.c3_advantage_threshold_mbps = c3_advantage_threshold_mbps
        self.max_iters = max_iters
        self.tool_selection = tool_selection
        self.tool_planner = dspy.Predict(FreeToolSelection)

    def forward(self, raw_table: str) -> dspy.Prediction:
        calculator = RawCaseCalculator(
            raw_table,
            c3_advantage_threshold_mbps=self.c3_advantage_threshold_mbps,
        )
        selection_reasoning = None
        planned_tools: list[str] = []
        executed_tools: list[str] = []

        def _require_committed(tool_name: str) -> None:
            if self.tool_selection != "commit_plan":
                return
            if not planned_tools:
                raise ValueError(
                    "Commit an analysis plan before calling calculators."
                )
            if tool_name not in planned_tools:
                raise ValueError(
                    f"{tool_name} is outside the committed analysis plan."
                )

        def inspect_raw_case(type: str = "inspect") -> str:
            """Inspect table scope and shortlist relevant analysis tools."""

            del type
            return calculator.inspect_raw_case()

        def analyze_radio_geometry(type: str = "geometry") -> str:
            """Calculate serving distances from raw drive and engineering rows."""

            del type
            _require_committed("analyze_radio_geometry")
            return calculator.analyze_radio_geometry()

        def analyze_signal_coverage(type: str = "coverage") -> str:
            """Calculate below-lobe geometry and weak-signal row evidence."""

            del type
            _require_committed("analyze_signal_coverage")
            return calculator.analyze_signal_coverage()

        def analyze_serving_transitions(type: str = "transitions") -> str:
            """Calculate serving-cell transitions and affected destinations."""

            del type
            _require_committed("analyze_serving_transitions")
            return calculator.analyze_serving_transitions()

        def analyze_mobility(type: str = "mobility") -> str:
            """Calculate maximum vehicle speed from raw drive rows."""

            del type
            _require_committed("analyze_mobility")
            return calculator.analyze_mobility()

        def analyze_resource_usage(type: str = "resources") -> str:
            """Calculate scheduled resources over affected raw rows."""

            del type
            _require_committed("analyze_resource_usage")
            return calculator.analyze_resource_usage()

        def compare_segment_throughput(type: str = "throughput") -> str:
            """Compare minimum throughput across raw serving segments."""

            del type
            _require_committed("compare_segment_throughput")
            return calculator.compare_segment_throughput()

        def analyze_neighbor_overlap(type: str = "overlap") -> str:
            """Join raw neighbor BRSRP with engineering sites and assess overlap."""

            del type
            _require_committed("analyze_neighbor_overlap")
            return calculator.analyze_neighbor_overlap()

        def analyze_pci_pattern(type: str = "pci_pattern") -> str:
            """Inspect affected raw rows for serving-neighbor PCI collisions."""

            del type
            _require_committed("analyze_pci_pattern")
            return calculator.analyze_pci_pattern()

        all_specialized_functions = (
            analyze_radio_geometry,
            analyze_signal_coverage,
            analyze_serving_transitions,
            analyze_mobility,
            analyze_resource_usage,
            compare_segment_throughput,
            analyze_neighbor_overlap,
            analyze_pci_pattern,
        )
        specialized_functions = all_specialized_functions
        function_by_name = {
            function.__name__: function
            for function in all_specialized_functions
        }
        if self.tool_selection == "free":
            catalog = "\n".join(
                f"- {function.__name__}: {(function.__doc__ or '').strip()}"
                for function in specialized_functions
            )
            plan = self.tool_planner(
                raw_table=raw_table,
                tool_catalog=catalog,
            )
            selection_reasoning = str(plan.selection_reasoning)
            allowed = function_by_name
            for name in re.findall(
                r"(?:analyze|compare)_[a-z_]+",
                str(plan.selected_tools),
            ):
                if name in allowed and name not in planned_tools:
                    planned_tools.append(name)
            planned_tools = planned_tools[: self.max_iters]
            if not planned_tools:
                raise ValueError(
                    "Free tool planner returned no valid calculator names: "
                    f"{plan.selected_tools!r}"
                )
            specialized_functions = tuple(
                allowed[name] for name in planned_tools
            )
        allowed_names = set(function_by_name)

        def commit_analysis_plan(
            selected_tools: str,
            rationale: str,
        ) -> str:
            """Commit one or two calculators before evidence is calculated.

            Use exact calculator names. C1-C8 mechanism aliases are also
            accepted and normalized to their corresponding calculators.
            """

            nonlocal selection_reasoning
            if planned_tools:
                return json.dumps(
                    {
                        "plan_already_committed": True,
                        "selected_tools": planned_tools,
                        "instruction": "Call execute_committed_plan now.",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            aliases = {
                "C1": "analyze_signal_coverage",
                "C2": "analyze_radio_geometry",
                "C3": "compare_segment_throughput",
                "C4": "analyze_neighbor_overlap",
                "C5": "analyze_serving_transitions",
                "C6": "analyze_pci_pattern",
                "C7": "analyze_mobility",
                "C8": "analyze_resource_usage",
            }
            selected: list[str] = []
            for token in re.findall(
                r"(?:analyze|compare)_[a-z_]+|\bC[1-8]\b",
                str(selected_tools),
                flags=re.IGNORECASE,
            ):
                name = aliases.get(token.upper(), token.lower())
                if name in allowed_names and name not in selected:
                    selected.append(name)
            if not 1 <= len(selected) <= 2:
                raise ValueError(
                    "Select one or two exact calculator names."
                )
            planned_tools.extend(selected)
            selection_reasoning = str(rationale)
            return json.dumps(
                {
                    "plan_committed": True,
                    "selected_tools": planned_tools,
                    "instruction": (
                        "Call every committed calculator before deciding. "
                        "Calculators outside this plan are unavailable."
                    ),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )

        def execute_committed_plan(type: str = "execute") -> str:
            """Run every committed calculator and return separate observations."""

            del type
            if not planned_tools:
                raise ValueError(
                    "Commit an analysis plan before executing calculators."
                )
            if executed_tools:
                return json.dumps(
                    {
                        "plan_already_executed": True,
                        "calculators": executed_tools,
                        "instruction": "Produce the final diagnosis now.",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            observations = []
            for name in planned_tools:
                observation = json.loads(function_by_name[name]())
                executed_tools.append(name)
                observations.append(
                    {
                        "calculator": name,
                        "observation": observation,
                    }
                )
            return json.dumps(
                {
                    "plan_executed": True,
                    "observations": observations,
                    "instruction": (
                        "Compare all verified observations and produce the "
                        "final diagnosis."
                    ),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )

        if self.tool_selection == "guided":
            functions = (inspect_raw_case, *specialized_functions)
        elif self.tool_selection == "commit_plan":
            functions = (commit_analysis_plan, execute_committed_plan)
        else:
            functions = specialized_functions
        tools = [
            dspy.Tool(
                function,
                name=function.__name__,
                desc=(function.__doc__ or "").strip(),
            )
            for function in functions
        ]
        signature = {
            "guided": RawTableTeleLogsDiagnosis,
            "free": FreeRawTableTeleLogsDiagnosis,
            "commit_plan": CommitPlanRawTableTeleLogsDiagnosis,
        }[self.tool_selection]
        react = dspy.ReAct(
            signature,
            tools=tools,
            max_iters=self.max_iters,
        )
        prediction = react(raw_table=raw_table)
        trajectory = prediction.trajectory
        calls: list[dict[str, Any]] = []
        for index in range(self.max_iters):
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

        first_is_inspection = bool(
            calls and calls[0]["name"] == "inspect_raw_case"
        )
        first_is_commit = bool(
            calls and calls[0]["name"] == "commit_analysis_plan"
        )
        specialized = [
            call
            for call in calls
            if call["name"]
            not in {
                "inspect_raw_case",
                "commit_analysis_plan",
                "execute_committed_plan",
                "finish",
            }
        ]
        execution_errors = [
            call
            for call in calls
            if call["name"] != "finish"
            if str(call.get("observation", "")).startswith("Execution error")
        ]
        candidate_tools: list[str] = []
        if calls:
            try:
                inspection = json.loads(
                    str(calls[0].get("observation", ""))
                )
                candidate_tools = list(
                    inspection.get("candidate_tools", [])
                )
            except (TypeError, ValueError):
                candidate_tools = []
        if self.tool_selection in {"free", "commit_plan"}:
            candidate_tools = planned_tools
        required_candidates = candidate_tools[:2]
        specialized_names = [call["name"] for call in specialized]
        if self.tool_selection == "commit_plan":
            specialized_names = list(executed_tools)
        followed_candidates = bool(required_candidates) and all(
            name in specialized_names for name in required_candidates
        )
        if self.tool_selection == "guided":
            sequence_compliant = (
                first_is_inspection
                and bool(specialized)
                and followed_candidates
                and not execution_errors
            )
            decision_path = (
                "raw_react_tool_sequence"
                if sequence_compliant
                else "raw_react_invalid_sequence"
            )
        elif self.tool_selection == "free":
            followed_candidates = bool(planned_tools) and all(
                name in specialized_names for name in planned_tools
            )
            sequence_compliant = (
                bool(specialized)
                and followed_candidates
                and not execution_errors
            )
            decision_path = (
                "raw_react_free_tool_sequence"
                if sequence_compliant
                else "raw_react_free_invalid_sequence"
            )
        else:
            followed_candidates = bool(planned_tools) and all(
                name in specialized_names for name in planned_tools
            )
            sequence_compliant = (
                first_is_commit
                and bool(executed_tools)
                and followed_candidates
                and not execution_errors
            )
            decision_path = (
                "raw_react_commit_plan_sequence"
                if sequence_compliant
                else "raw_react_commit_plan_invalid_sequence"
            )
        prediction.tool_calls = calls
        prediction.tool_called = bool(calls)
        prediction.inspected = first_is_inspection
        prediction.plan_committed = bool(planned_tools)
        prediction.tool_succeeded = sequence_compliant
        prediction.specialized_tools = [
            call["name"] for call in specialized
        ]
        if self.tool_selection == "commit_plan":
            prediction.specialized_tools = list(executed_tools)
        prediction.executed_tools = list(executed_tools)
        prediction.candidate_tools = candidate_tools
        prediction.followed_candidates = followed_candidates
        prediction.tool_selection = self.tool_selection
        prediction.selection_reasoning = selection_reasoning
        prediction.planned_tools = planned_tools
        prediction.decision_path = decision_path
        return prediction


def normalize_answer(value: Any) -> str:
    matches = re.findall(r"\bC([1-8])\b", str(value or ""), flags=re.IGNORECASE)
    return f"C{matches[-1]}" if matches else ""

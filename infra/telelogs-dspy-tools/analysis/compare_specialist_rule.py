#!/usr/bin/env python3
"""A/B the old presence-based residual rule against the new magnitude-based one.

Runs BOTH `ResidualDecision` signatures — the old text (frozen here verbatim
from git history, pre-edit) and the current one in `specialist_program.py`
(magnitude/count thresholds, fit by `fit_magnitude_rule.py`) — over the same
frozen `{tool_observations, gate_lines, target}` records produced by
`collect_residual_dataset.py`. Paired McNemar on the pooled dev-96 + sel200
residual subset is the selection gate, exactly the protocol
`optimize_specialist.py` already commits to: "the pooled dev-96 + sel200
residual subset decides ... holdout and official are only touched
afterwards."

IMPORTANT: `tool_observations` in the residual-set jsonl files must have been
collected AFTER the new scalar fields were added to `neutral_tools.py`
(`equal_residue_pair_count`, `noncolocated_gap_at_or_above_neg3db_count`,
`deepest_below_lobe_deficit_deg`) — re-run `collect_residual_dataset.py` first
if those files predate that change, or the new rule has nothing to read.

    python3 compare_specialist_rule.py \
        --eval results/residual_sets/dev96.jsonl results/residual_sets/sel200.jsonl \
        --out results/compare_magnitude_rule
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dspy  # noqa: E402  (needed at module scope for the class body below)


# The old, presence-based rule text, frozen verbatim from git history
# (infra/telelogs-dspy-tools/specialist_program.py, pre-magnitude-rule commit,
# `git show <pre-edit-sha>:infra/telelogs-dspy-tools/specialist_program.py`) —
# defined as a plain class, the same mechanism `ResidualDecision` itself uses,
# rather than a dynamic Signature constructor whose exact call shape isn't
# worth risking on an unfamiliar API path.
class OldResidualDecision(dspy.Signature):
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


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open(encoding="utf-8")]


def mcnemar(base: list[bool], new: list[bool]) -> tuple[int, int, float]:
    n01 = sum(1 for b, n in zip(base, new) if not b and n)
    n10 = sum(1 for b, n in zip(base, new) if b and not n)
    total = n01 + n10
    if total == 0:
        return n01, n10, 1.0
    tail = sum(math.comb(total, k) for k in range(0, min(n01, n10) + 1))
    return n01, n10, min(1.0, 2 * tail / 2 ** total)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval", nargs="+", required=True)
    parser.add_argument("--max-tokens", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    from run_tool_experiment import API_BASE
    from specialist_program import ResidualDecision as NewResidualDecision
    from tool_program import normalize_answer

    dspy.configure(lm=dspy.LM(model="openai/Qwen/Qwen3-8B", api_base=API_BASE,
                              api_key="local", max_tokens=args.max_tokens, temperature=0.0,
                              extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                              cache=False))

    old_program = dspy.Predict(OldResidualDecision)
    new_program = dspy.Predict(NewResidualDecision)

    evals = {Path(p).stem: load(Path(p)) for p in args.eval}
    print(json.dumps({"stage": "start", "eval": {k: len(v) for k, v in evals.items()}}), flush=True)

    def run(program, records):
        def one(r):
            try:
                out = program(tool_observations=json.dumps(r["tool_observations"], ensure_ascii=False),
                              gate_lines=r["gate_lines"])
                return normalize_answer(getattr(out, "answer", "")), None
            except Exception as exc:  # noqa: BLE001 - a failed case scores wrong, never fatal
                return "", f"{type(exc).__name__}: {exc}"
        with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            return list(pool.map(one, records))

    report = {"splits": {}}
    pooled_old, pooled_new = [], []
    for name, records in evals.items():
        old_out = run(old_program, records)
        new_out = run(new_program, records)
        old_ok = [a == r["target"] for (a, _), r in zip(old_out, records)]
        new_ok = [a == r["target"] for (a, _), r in zip(new_out, records)]
        pooled_old += old_ok
        pooled_new += new_ok
        wins, losses, p = mcnemar(old_ok, new_ok)
        report["splits"][name] = {
            "n": len(records),
            "old_rule": round(sum(old_ok) / len(records), 4),
            "new_rule": round(sum(new_ok) / len(records), 4),
            "new_per_label_correct": dict(sorted(Counter(
                r["target"] for (a, _), r in zip(new_out, records) if a == r["target"]).items())),
            "mcnemar_new_wins": wins, "mcnemar_old_wins": losses, "p_value": round(p, 6),
            "errors_old": sum(1 for a, e in old_out if e),
            "errors_new": sum(1 for a, e in new_out if e),
        }
        print(json.dumps({"stage": "eval", "split": name, **report["splits"][name]}), flush=True)

    wins, losses, p = mcnemar(pooled_old, pooled_new)
    report["pooled"] = {
        "n": len(pooled_old),
        "old_rule": round(sum(pooled_old) / len(pooled_old), 4),
        "new_rule": round(sum(pooled_new) / len(pooled_new), 4),
        "mcnemar_new_wins": wins, "mcnemar_old_wins": losses, "p_value": round(p, 6),
    }
    print(json.dumps({"stage": "pooled", **report["pooled"]}), flush=True)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()

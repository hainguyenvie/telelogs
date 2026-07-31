#!/usr/bin/env python3
"""Compile the residual specialist's prompt — the one layer that never saw an optimizer.

Layer 4 is worth +9.96 points on official-864 and its instruction is hand-written.
Layer 1, the only surface any optimizer ever touched, plateaued at 72.92% on
dev-96 and the seed lottery there spans 34.4-72.9% — wider than any wording
effect, which is why searching it further fits noise. The specialist is a much
better-conditioned target: one job, four output classes instead of eight, demos
that carry no tool trajectory, and one LM call per evaluation instead of eleven.

Runs entirely on the frozen inputs from collect_residual_dataset.py, so a search
round costs ~2.5 s per example rather than ~22 s.

    python3 optimize_specialist.py --train residual_sets/train.jsonl \
        --eval residual_sets/dev96.jsonl residual_sets/sel200.jsonl \
        --optimizer bootstrap --demos 4 --out results/optimized/specialist_boot4

The selection rule is fixed in advance: the pooled dev-96 + sel200 residual
subset decides, by paired McNemar against the hand-written baseline, and holdout
and official are only touched afterwards.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "code"))

RESIDUAL = ("C1", "C3", "C4", "C6")


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open(encoding="utf-8")]


def mcnemar(base: list[bool], new: list[bool]) -> tuple[int, int, float]:
    """Exact two-sided binomial test over the discordant pairs."""
    n01 = sum(1 for b, n in zip(base, new) if not b and n)
    n10 = sum(1 for b, n in zip(base, new) if b and not n)
    total = n01 + n10
    if total == 0:
        return n01, n10, 1.0
    tail = sum(math.comb(total, k) for k in range(0, min(n01, n10) + 1))
    return n01, n10, min(1.0, 2 * tail / 2 ** total)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", required=True)
    parser.add_argument("--eval", nargs="+", required=True)
    parser.add_argument("--optimizer", choices=["none", "bootstrap", "mipro"], default="bootstrap")
    parser.add_argument("--demos", type=int, default=4)
    parser.add_argument("--trials", type=int, default=12, help="MIPRO candidate trials")
    parser.add_argument("--max-tokens", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    import dspy
    from dspy.teleprompt import BootstrapFewShot
    from run_tool_experiment import API_BASE
    from specialist_program import ResidualDecision
    from tool_program import normalize_answer

    dspy.configure(lm=dspy.LM(model="openai/Qwen/Qwen3-8B", api_base=API_BASE,
                              api_key="local", max_tokens=args.max_tokens, temperature=0.0,
                              # same fix as collect_residual_dataset: Qwen3's <think>
                              # block otherwise eats the budget and breaks parsing
                              extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                              cache=False))

    def to_example(record: dict) -> "dspy.Example":
        return dspy.Example(
            tool_observations=json.dumps(record["tool_observations"], ensure_ascii=False),
            gate_lines=record["gate_lines"],
            answer=record["target"],
        ).with_inputs("tool_observations", "gate_lines")

    train = [to_example(r) for r in load(Path(args.train))]
    evals = {Path(p).stem: load(Path(p)) for p in args.eval}
    print(json.dumps({"stage": "start", "train": len(train),
                      "eval": {k: len(v) for k, v in evals.items()},
                      "optimizer": args.optimizer, "demos": args.demos}), flush=True)

    def metric(gold, pred, trace=None):
        return normalize_answer(getattr(pred, "answer", "")) == gold.answer

    student = dspy.Predict(ResidualDecision)

    if args.optimizer == "bootstrap":
        compiled = BootstrapFewShot(
            metric=metric, max_bootstrapped_demos=args.demos,
            max_labeled_demos=args.demos, max_rounds=1,
        ).compile(student, trainset=train)
    elif args.optimizer == "mipro":
        from dspy.teleprompt import MIPROv2
        compiled = MIPROv2(metric=metric, auto=None, num_candidates=args.trials,
                           init_temperature=1.0).compile(
            student, trainset=train, num_trials=args.trials,
            max_bootstrapped_demos=args.demos, max_labeled_demos=args.demos,
            requires_permission_to_run=False)
    else:
        compiled = student

    baseline = dspy.Predict(ResidualDecision)   # the hand-written instruction, no demos

    def run(program, records):
        import concurrent.futures as futures
        def one(r):
            try:
                out = program(tool_observations=json.dumps(r["tool_observations"], ensure_ascii=False),
                              gate_lines=r["gate_lines"])
                answer = normalize_answer(getattr(out, "answer", ""))
            except Exception as exc:  # noqa: BLE001 - a failed case scores wrong, never fatal
                return "", f"{type(exc).__name__}: {exc}"
            return answer, None
        with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            return list(pool.map(one, records))

    report = {"optimizer": args.optimizer, "demos": args.demos, "splits": {}}
    pooled_base, pooled_new = [], []
    for name, records in evals.items():
        base_out = run(baseline, records)
        new_out = run(compiled, records)
        base_ok = [a == r["target"] for (a, _), r in zip(base_out, records)]
        new_ok = [a == r["target"] for (a, _), r in zip(new_out, records)]
        pooled_base += base_ok
        pooled_new += new_ok
        report["splits"][name] = {
            "n": len(records),
            "baseline": round(sum(base_ok) / len(records), 4),
            "compiled": round(sum(new_ok) / len(records), 4),
            "compiled_per_label": dict(sorted(Counter(
                r["target"] for (a, _), r in zip(new_out, records) if a == r["target"]).items())),
            "errors": sum(1 for a, e in new_out if e),
        }
        print(json.dumps({"stage": "eval", "split": name, **report["splits"][name]}), flush=True)

    wins, losses, p = mcnemar(pooled_base, pooled_new)
    report["pooled"] = {
        "n": len(pooled_base),
        "baseline": round(sum(pooled_base) / len(pooled_base), 4),
        "compiled": round(sum(pooled_new) / len(pooled_new), 4),
        "mcnemar_compiled_wins": wins, "mcnemar_baseline_wins": losses, "p_value": round(p, 6),
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        compiled.save(out_dir / "program.json")
        report["program"] = str(out_dir / "program.json")
    except Exception as exc:  # noqa: BLE001 - MIPRO demos can hold non-string dict keys
        print(json.dumps({"stage": "save_fallback", "error": f"{type(exc).__name__}: {exc}"}), flush=True)
        compiled.save(out_dir / "program.pkl")
        report["program"] = str(out_dir / "program.pkl")
    (out_dir / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()

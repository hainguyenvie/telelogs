#!/usr/bin/env python3
"""Freeze the residual specialist's inputs so its prompt can be optimized offline.

The specialist is a one-call module: it sees `tool_observations` and `gate_lines`
and nothing else. Producing those two inputs is the expensive part — it means
running the whole ReAct loop, the audit and forced measurement — but they do not
depend on the specialist's own prompt at all. So collect them once, and every
subsequent optimizer round costs one LM call per example instead of eleven.

    python3 collect_residual_dataset.py --split train --per-label 40 \
        --out results/residual_sets/train.jsonl

Only cases the gate check leaves undecided are kept: those are the ones the
specialist is ever asked about. The gate decision is taken symbolically from the
observations, never from the label. The label is written to the file because the
optimizer's metric needs it, and it is never part of an input field.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "code"))

LABELS = tuple(f"C{i}" for i in range(1, 9))
GATE_LABELS = {"C2", "C5", "C7", "C8"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default="/workspace/telelogs-bench4/dspy-tools/data/train.json")
    parser.add_argument("--split", default="train")
    parser.add_argument("--per-label", type=int, default=40)
    parser.add_argument("--offset-per-label", type=int, default=0)
    parser.add_argument("--program", required=True, help="compiled ReAct program.json")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=1000)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    import dspy
    from run_tool_experiment import API_BASE, LABELS as RUNNER_LABELS, balanced_slice, load_rows
    from tool_program import normalize_answer
    from neutral_tools import parse_case
    from forced_program import ForcedMeasurementProgram
    from specialist_program import gate_lines_only

    assert tuple(RUNNER_LABELS) == LABELS, "label order drifted from the runner"

    dspy.configure(lm=dspy.LM(model="openai/Qwen/Qwen3-8B", api_base=API_BASE,
                              api_key="local", max_tokens=args.max_tokens, temperature=0.0,
                              # without this Qwen3 emits a <think> block that eats the
                              # token budget and breaks the ReAct adapter's parsing —
                              # 299/320 collection failures in run t1
                              extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                              cache=False))

    rows = load_rows(Path(args.raw))
    chosen = balanced_slice(rows, args.split, args.per_label, args.offset_per_label)
    print(json.dumps({"stage": "start", "candidates": len(chosen), "split": args.split}), flush=True)

    program = ForcedMeasurementProgram()
    program.load(args.program)

    def one(row):
        case = parse_case(row["question"])
        started = time.monotonic()
        try:
            # the program binds the active case on a contextvar inside forward()
            pred = program(raw_question=row["question"], case=case)
        except Exception as exc:  # noqa: BLE001 - a dropped case is logged, never fatal
            return {"source_index": row["source_index"], "error": f"{type(exc).__name__}: {exc}"}
        observations = dict(getattr(pred, "tool_observations", {}))
        reasoning = str(getattr(pred, "reasoning", ""))
        return {
            "source_index": row["source_index"],
            "target": row["label"],
            "first_pass_answer": normalize_answer(getattr(pred, "answer", "")),
            "tool_observations": observations,
            "gate_lines": gate_lines_only(reasoning),
            "lm_calls": int(getattr(pred, "lm_calls", 1)),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "error": None,
        }

    results = []
    with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for done, future in enumerate(
                futures.as_completed([pool.submit(one, r) for r in chosen]), start=1):
            results.append(future.result())
            if done % 20 == 0:
                print(json.dumps({"stage": "progress", "done": done, "of": len(chosen)}), flush=True)

    ok = [r for r in results if not r.get("error")]
    # the specialist is only ever consulted on cases the first pass called residual
    kept = [r for r in ok if r["first_pass_answer"] not in GATE_LABELS]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as stream:
        for record in sorted(kept, key=lambda r: r["source_index"]):
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(json.dumps({
        "stage": "done", "out": str(out), "candidates": len(chosen),
        "completed": len(ok), "errors": len(results) - len(ok), "kept_residual": len(kept),
        "gold_mix": dict(sorted(Counter(r["target"] for r in kept).items())),
        "first_pass_accuracy": round(sum(r["first_pass_answer"] == r["target"] for r in kept)
                                     / max(1, len(kept)), 4),
        "mean_lm_calls": round(sum(r["lm_calls"] for r in ok) / max(1, len(ok)), 2),
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()

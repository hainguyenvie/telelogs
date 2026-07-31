#!/usr/bin/env python3
"""The shipped champion pipeline, run and scored under the GSMA ot-full harness.

Same contract as full4_eval.py's TeleLogs track: questions come from the
GSMA--ot-full parquet (verified byte-identical, same order, to
data/test_official864.json), and the score is computed by the OFFICIAL parser —
the last \\boxed{...} in the completion, first integer compared to the target's
first integer. Our pipeline's answer is emitted inside the completion as
"Final answer: \\boxed{N}"; if the pipeline fails to produce a class, the
completion carries no boxed value and the official parser scores it wrong.

The completion a grader reads is: the engineer narrative (layer 5), then the
audited evidence lines, then the boxed answer. Resumable: finished sample_ids
are skipped on restart.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "code"))

BOXED_PATTERN = re.compile(r"\\boxed\{((?:[^{}]|\{[^{}]*\})*)\}")
DIGIT_PATTERN = re.compile(r"\d+")


def parse_boxed(response: str) -> str:
    matches = BOXED_PATTERN.findall(response or "")
    if not matches:
        return ""
    answer = re.sub(r"\n\s*", "", matches[-1].strip())
    return answer.lstrip(":").rstrip("./")


def first_int(text: str) -> int | None:
    match = DIGIT_PATTERN.search(text or "")
    return int(match.group()) if match else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parquet", required=True)
    parser.add_argument("--program", required=True, help="compiled ReAct program.json")
    parser.add_argument("--method", default="b3_react_specialist")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=1000)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    import dspy
    from run_tool_experiment import API_BASE, MODEL
    from tool_program import PROGRAMS, normalize_answer
    from neutral_tools import parse_case
    from narrate import NarrationLayer

    dspy.configure(lm=dspy.LM(MODEL, api_base=API_BASE, api_key="local",
                              temperature=0.0, max_tokens=args.max_tokens,
                              extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                              cache=False))

    import pyarrow.parquet as pq
    rows = pq.read_table(args.parquet).to_pylist()
    samples = [{"sample_id": f"telelogs-{i:05d}", "sample_index": i,
                "question": r["question"], "target": str(r["answer"])}
               for i, r in enumerate(rows)]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    done: dict[str, dict] = {}
    if results_path.exists():
        for line in results_path.open(encoding="utf-8"):
            try:
                r = json.loads(line)
                if not r.get("error"):
                    done[r["sample_id"]] = r
            except json.JSONDecodeError:
                continue
    pending = [s for s in samples if s["sample_id"] not in done]
    print(json.dumps({"stage": "start", "total": len(samples), "pending": len(pending),
                      "resumed": len(done)}), flush=True)

    program = PROGRAMS[args.method]()
    program.load(args.program)
    narrator = NarrationLayer()

    def run_one(sample: dict) -> dict:
        started = time.monotonic()
        try:
            case = parse_case(sample["question"])
            pred = program(raw_question=sample["question"], case=case)
            answer = normalize_answer(getattr(pred, "answer", ""))
            narrative = narrator(raw_question=sample["question"], pred=pred)
            evidence = str(getattr(pred, "reasoning", ""))
            parts = []
            if narrative:
                parts.append(narrative)
            if evidence:
                parts.append("[Audited evidence]\n" + evidence)
            if answer:
                parts.append(f"Final answer: \\boxed{{{answer[1]}}}")
            completion = "\n\n".join(parts)
            error = None
        except Exception as exc:  # noqa: BLE001 - an errored case scores wrong, run continues
            answer, narrative, evidence, completion = "", "", "", ""
            error = f"{type(exc).__name__}: {exc}"
        # the OFFICIAL scorer, applied to the completion text only
        parsed = parse_boxed(completion)
        predicted = first_int(parsed)
        correct = predicted is not None and predicted == first_int(sample["target"])
        return {
            "sample_id": sample["sample_id"],
            "sample_index": sample["sample_index"],
            "benchmark": "telelogs",
            "target": sample["target"],
            "parsed_answer": parsed,
            "correct": correct,
            "pipeline_answer": answer,
            "narrative": narrative,
            "narrative_ungrounded": list(getattr(pred, "narrative_ungrounded", [])) if not error else [],
            "evidence": evidence,
            "completion": completion,
            "lm_calls": (int(getattr(pred, "lm_calls", 0)) + 1) if not error else 0,
            "error": error,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }

    results = dict(done)
    with results_path.open("a", encoding="utf-8", buffering=1) as sink:
        with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            for n, future in enumerate(
                    futures.as_completed([pool.submit(run_one, s) for s in pending]), 1):
                r = future.result()
                results[r["sample_id"]] = r
                sink.write(json.dumps(r, ensure_ascii=False) + "\n")
                if n % 25 == 0:
                    ok = sum(v["correct"] for v in results.values())
                    print(json.dumps({"stage": "progress", "done": len(results),
                                      "of": len(samples), "correct": ok}), flush=True)

    rows_out = sorted(results.values(), key=lambda r: r["sample_index"])
    ok = sum(r["correct"] for r in rows_out)
    per_label: dict[str, list[int]] = {}
    for r in rows_out:
        lab = r["target"]
        per_label.setdefault(lab, [0, 0])
        per_label[lab][1] += 1
        per_label[lab][0] += r["correct"]
    confusions = Counter(
        (r["target"], r["pipeline_answer"] or "unparsed")
        for r in rows_out if not r["correct"]
    )
    summary = {
        "suite": "GSMA ot-full · telelogs · official boxed-int scorer",
        "method": args.method,
        "program": args.program,
        "total": len(rows_out),
        "correct": ok,
        "accuracy": round(ok / len(rows_out), 4),
        "errors": sum(bool(r["error"]) for r in rows_out),
        "unboxed": sum(1 for r in rows_out if not r["parsed_answer"]),
        "narratives": sum(1 for r in rows_out if r["narrative"]),
        "ungrounded_flagged": sum(1 for r in rows_out if r["narrative_ungrounded"]),
        "mean_lm_calls": round(sum(r["lm_calls"] for r in rows_out) / len(rows_out), 2),
        "per_label": {k: v for k, v in sorted(per_label.items())},
        "top_confusions": [
            {"target": t, "prediction": p, "count": c}
            for (t, p), c in confusions.most_common(12)
        ],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "# Champion pipeline under the GSMA ot-full harness — TeleLogs",
        "",
        f"Accuracy (official boxed-int scorer): **{ok}/{len(rows_out)} = {ok/len(rows_out)*100:.2f}%**",
        f"Request errors: **{summary['errors']}** · completions without a boxed answer: **{summary['unboxed']}**",
        f"Narratives present: **{summary['narratives']}/{len(rows_out)}** · flagged by grounding guard: **{summary['ungrounded_flagged']}**",
        "",
        "| Class | Correct | Total | Accuracy |",
        "|---|---:|---:|---:|",
    ]
    for lab, (c, t) in sorted(per_label.items()):
        lines.append(f"| {lab} | {c} | {t} | {c/t*100:.2f}% |")
    lines += ["", "Scorer: last \\boxed{...} in the completion, first integer vs target — "
                  "identical to full4_eval.py. Dataset: GSMA--ot-full telelogs test parquet "
                  "(verified identical to data/test_official864.json)."]
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()

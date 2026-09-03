#!/usr/bin/env python3
"""Profile the GSMA ot-full oranbench split before any modelling decision.

Reads the parquet exactly as the GSMA harness does (pyarrow, row order
preserved) and reports the things that decide how a specialist is built:
answer-index balance overall and per difficulty stratum, the knowledge-free
priors any score has to beat, question and choice lengths, duplicates, and the
distractor tells.

oranbench differs from the other two MCQ columns in three ways that matter:
  * exactly 4 choices, always, and a hard 500/500/500 difficulty stratification
  * every choice string carries its own "1. ".."4. " numbering, which the
    Inspect template then re-letters -- the model sees "A) 1. ..." and the two
    numbering systems disagree on 3 of every 4 rows
  * the gold index is badly skewed (A+B = 65.3%), so the fixed-letter floor is
    34.4% against a 25% random baseline

Also writes a readable JSONL mirror next to the parquet so the rest of the
tooling never has to open a parquet file again.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PARQUET = ROOT / "data/oranbench/test-00000-of-00001.parquet"
DEFAULT_JSONL = ROOT / "data/oranbench/test.jsonl"

# The upstream generator prefixes every option with its own 1-based number.
LEADING_NUMBER = re.compile(r"^\s*(\d+)\s*[.)]\s+")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")

TELL_PATTERNS = {
    "all of the above": re.compile(r"(?i)all of the above"),
    "none of the above": re.compile(r"(?i)none of the above"),
    "both a and b": re.compile(r"(?i)\bboth\b"),
    "not mentioned": re.compile(r"(?i)not (mentioned|specified|provided|discussed|defined)"),
}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def strip_number(choice: str) -> str:
    return LEADING_NUMBER.sub("", choice)


def percentiles(values: list[int], points=(0, 25, 50, 75, 90, 99, 100)) -> dict[str, int]:
    if not values:
        return {}
    ordered = sorted(values)
    out = {}
    for p in points:
        index = min(len(ordered) - 1, max(0, round((p / 100) * (len(ordered) - 1))))
        out[f"p{p}"] = ordered[index]
    return out


def index_distribution(rows: list[dict]) -> dict:
    """Answer-index counts plus the constants any score has to beat."""
    answers = Counter(int(r["answer"]) for r in rows)
    counts = Counter(len(r["choices"]) for r in rows)
    n = len(rows)
    fixed = {
        chr(65 + i): round(answers.get(i, 0) / n, 4)
        for i in range(max(counts) if counts else 0)
    }
    return {
        "n": n,
        "answer_index_counts": {str(k): answers[k] for k in sorted(answers)},
        "answer_index_share": {str(k): round(answers[k] / n, 4) for k in sorted(answers)},
        "best_fixed_letter": max(fixed.items(), key=lambda kv: kv[1]) if fixed else None,
        "fixed_letter_scores": fixed,
        "random_baseline": round(sum(1 / len(r["choices"]) for r in rows) / n, 4),
        "choice_count_distribution": {str(k): counts[k] for k in sorted(counts)},
    }


def knowledge_free_priors(rows: list[dict]) -> dict:
    """Every shortcut that scores without reading O-RAN. These are the floor."""
    n = len(rows)

    def share(pred) -> float:
        return round(sum(1 for r in rows if pred(r)) / n, 4)

    longest = share(lambda r: max(range(len(r["choices"])), key=lambda i: len(r["choices"][i])) == r["answer"])
    shortest = share(lambda r: min(range(len(r["choices"])), key=lambda i: len(r["choices"][i])) == r["answer"])

    def most_overlap(r):
        qwords = set(w.lower() for w in WORD.findall(r["question"]))
        scores = [len(qwords & set(w.lower() for w in WORD.findall(c))) for c in r["choices"]]
        return max(range(len(scores)), key=lambda i: scores[i])

    return {
        "longest_choice": longest,
        "shortest_choice": shortest,
        "max_question_overlap": share(lambda r: most_overlap(r) == r["answer"]),
        "note": "the highest of these, not 0.25, is the score a model must beat to have learned anything",
    }


def numbering_audit(rows: list[dict]) -> dict:
    """The '1. '..'4. ' prefix the generator baked into every option.

    Inspect's multiple_choice() re-letters the options as A)..D), so the model
    is shown two numbering systems at once. They agree only when the gold index
    is 0 -- i.e. the prompt says 'A) 1. ...' for the right answer 30.9% of the
    time and 'B) 2. ...', 'C) 3. ...' otherwise. Any prompt-side handling of
    this has to be measured, not assumed.
    """
    numbered = in_order = 0
    total_choices = 0
    for r in rows:
        ok = True
        for i, c in enumerate(r["choices"]):
            total_choices += 1
            m = LEADING_NUMBER.match(c)
            if m:
                numbered += 1
                if int(m.group(1)) != i + 1:
                    ok = False
            else:
                ok = False
        in_order += int(ok)
    return {
        "choices_with_leading_number": f"{numbered}/{total_choices}",
        "rows_numbered_1_to_4_in_order": f"{in_order}/{len(rows)}",
        "letter_matches_own_number": round(sum(1 for r in rows if r["answer"] == 0) / len(rows), 4),
    }


def duplicate_audit(rows: list[dict]) -> dict:
    by_question = defaultdict(list)
    by_full = defaultdict(list)
    for i, r in enumerate(rows):
        by_question[normalize(r["question"])].append(i)
        by_full[(normalize(r["question"]), tuple(normalize(c) for c in r["choices"]))].append(i)
    dup_q = {k: v for k, v in by_question.items() if len(v) > 1}
    dup_f = {k: v for k, v in by_full.items() if len(v) > 1}
    contradictory = sum(
        1 for v in dup_f.values() if len({rows[i]["answer"] for i in v}) > 1
    )
    return {
        "duplicate_question_strings": len(dup_q),
        "duplicate_question_and_choices": len(dup_f),
        "duplicates_with_disagreeing_gold": contradictory,
        "examples": [rows[v[0]]["question"][:90] for v in list(dup_f.values())[:3]],
    }


def tell_audit(rows: list[dict]) -> dict:
    out = {}
    for name, pattern in TELL_PATTERNS.items():
        hits = [r for r in rows if any(pattern.search(c) for c in r["choices"])]
        if not hits:
            out[name] = {"rows": 0}
            continue
        gold_is_tell = sum(1 for r in hits if pattern.search(r["choices"][r["answer"]]))
        out[name] = {
            "rows": len(hits),
            "gold_is_the_tell": gold_is_tell,
            "rate": round(gold_is_tell / len(hits), 4),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", type=Path, default=DEFAULT_PARQUET)
    ap.add_argument("--jsonl-out", type=Path, default=DEFAULT_JSONL)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    digest = hashlib.sha256(args.parquet.read_bytes()).hexdigest()
    rows = pq.read_table(args.parquet).to_pylist()

    report = {
        "parquet": str(args.parquet.relative_to(ROOT)),
        "sha256": digest,
        "rows": len(rows),
        "fields": sorted(rows[0].keys()),
        "overall": index_distribution(rows),
        "knowledge_free_priors": knowledge_free_priors(rows),
        "numbering": numbering_audit(rows),
        "duplicates": duplicate_audit(rows),
        "distractor_tells": tell_audit(rows),
        "question_chars": percentiles([len(r["question"]) for r in rows]),
        "choice_chars": percentiles([len(c) for r in rows for c in r["choices"]]),
        "by_difficulty": {},
    }
    for level in sorted({r["difficulty"] for r in rows}):
        subset = [r for r in rows if r["difficulty"] == level]
        report["by_difficulty"][level] = {
            **index_distribution(subset),
            "priors": knowledge_free_priors(subset),
            "question_chars_p50": percentiles([len(r["question"]) for r in subset])["p50"],
        }

    print(json.dumps(report, indent=2))

    if not args.no_write:
        args.jsonl_out.parent.mkdir(parents=True, exist_ok=True)
        with args.jsonl_out.open("w", encoding="utf-8") as fh:
            for i, r in enumerate(rows):
                fh.write(json.dumps({
                    "row_id": i,
                    "question": r["question"],
                    "choices": list(r["choices"]),
                    "answer": int(r["answer"]),
                    "target": chr(65 + int(r["answer"])),
                    "difficulty": r["difficulty"],
                    "choices_unnumbered": [strip_number(c) for c in r["choices"]],
                }, ensure_ascii=False) + "\n")
        print(f"\nwrote {args.jsonl_out.relative_to(ROOT)} ({len(rows)} rows)")


if __name__ == "__main__":
    main()

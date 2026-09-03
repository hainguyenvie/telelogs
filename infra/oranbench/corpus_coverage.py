#!/usr/bin/env python3
"""Is the answer to each oranbench question actually in the O-RAN corpus?

Retrieval is the only lever that has paid off on the other GSMA MCQ columns, and
it only pays where the passage is present. Before building a retriever this
measures presence directly, three ways, against the 169 marked specification
documents mirrored from `GSMA/oran`:

  verbatim gold   the gold option's text appears word-for-word in some document
  verbatim any    the same, for at least one distractor (the control -- if the
                  distractors are equally present, presence is not a signal)
  term co-hit     the question's rare terms all appear inside one document,
                  i.e. a retriever could plausibly land on the right page

Reported overall, per difficulty, and split by whether the question is drawn
from a document the mirror actually holds.

    python infra/oranbench/corpus_coverage.py --sample 400
"""
from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "data/oranbench/corpus_oran_specs"
TEST_JSONL = ROOT / "data/oranbench/test.jsonl"

LEADING_NUMBER = re.compile(r"^\s*\d+\s*[.)]\s+")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
STOP = set("the and for that with this from are was were which shall should may can not any all its "
           "has have been also such other more than when where what does not into per use used using "
           "based within between during about over under only these those there their they them".split())


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def load_corpus() -> tuple[list[str], list[str]]:
    names, blobs = [], []
    for path in sorted(CORPUS.rglob("*.md")):
        rel = path.relative_to(CORPUS)
        if len(rel.parts) == 1:
            continue          # STATUS.md and friends live at the top level, not specs
        names.append(str(rel))
        blobs.append(norm(path.read_text(encoding="utf-8", errors="ignore")))
    return names, blobs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0, help="0 = all 1,500 rows")
    ap.add_argument("--min-words", type=int, default=4, help="skip options too short to be evidence")
    ap.add_argument("--seed", type=int, default=20260825)
    args = ap.parse_args()

    names, blobs = load_corpus()
    joined = "\n".join(blobs)
    print(f"corpus: {len(blobs)} documents, {len(joined)/1e6:.1f} MB normalised\n")

    rows = [json.loads(line) for line in TEST_JSONL.read_text(encoding="utf-8").splitlines()]
    if args.sample:
        rows = random.Random(args.seed).sample(rows, min(args.sample, len(rows)))

    stats: Counter = Counter()
    per_level: dict[str, Counter] = {}
    for r in rows:
        level = per_level.setdefault(r["difficulty"], Counter())
        options = [norm(LEADING_NUMBER.sub("", c)) for c in r["choices"]]
        gold = options[r["answer"]]
        distractors = [o for i, o in enumerate(options) if i != r["answer"]]

        for bucket in (stats, level):
            bucket["rows"] += 1

        if len(gold.split()) >= args.min_words:
            hit = gold in joined
            for bucket in (stats, level):
                bucket["gold_scored"] += 1
                bucket["gold_verbatim"] += int(hit)
        usable = [d for d in distractors if len(d.split()) >= args.min_words]
        if usable:
            hit = any(d in joined for d in usable)
            for bucket in (stats, level):
                bucket["distractor_scored"] += 1
                bucket["distractor_verbatim"] += int(hit)

        terms = {w.lower() for w in WORD.findall(r["question"])} - STOP
        # length ties must break deterministically: a bare sort over a set is
        # ordered by hash and moved this number by 1pp between runs
        rare = sorted(terms, key=lambda w: (-len(w), w))[:4]
        if rare:
            landed = any(all(t in blob for t in rare) for blob in blobs)
            for bucket in (stats, level):
                bucket["term_scored"] += 1
                bucket["term_cohit"] += int(landed)

    def pct(c: Counter, num: str, den: str) -> str:
        return f"{c[num]}/{c[den]} = {c[num]/c[den]:.2%}" if c[den] else "n/a"

    print(f"{'slice':10} {'rows':>6}  {'gold verbatim':>22}  {'distractor verbatim':>22}  {'term co-hit':>22}")
    for label, c in [("ALL", stats)] + sorted(per_level.items()):
        print(f"{label:10} {c['rows']:6}  {pct(c,'gold_verbatim','gold_scored'):>22}  "
              f"{pct(c,'distractor_verbatim','distractor_scored'):>22}  {pct(c,'term_cohit','term_scored'):>22}")

    lift = (stats["gold_verbatim"] / max(stats["gold_scored"], 1)) - \
           (stats["distractor_verbatim"] / max(stats["distractor_scored"], 1))
    print(f"\ngold-minus-distractor presence: {lift:+.2%}")
    print("A positive gap means literal presence in the corpus is by itself a label-free signal.")
    print("A gap near zero means retrieval has to be read, not matched.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Carve a clean dev/train pool out of ORAN-Bench-13K, disjoint from the test set.

The GSMA ot-full `oranbench` split is 1,500 rows sampled from the 13,952-question
ORAN-Bench-13K (Gajjar & Shah, arXiv:2407.06245, MIT licence). This script

  1. proves the containment: every one of the 1,500 test rows is matched in the
     13K file on (question, choices), gold label AND difficulty tag;
  2. removes them, leaving a pool that no leaderboard row is scored on;
  3. drops the malformed rows (gold index outside 0..3) and internal duplicates;
  4. writes a stratified dev / holdout / train split of what is left.

The containment proof is the point. Anything trained on the raw 13K file is
trained on the leaderboard test set, and the two files give no warning: the
upstream copy is labelled `train`, ships from a different repo, and is
byte-identical to what GSMA calls `test`.

    python infra/oranbench/build_pool.py
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
TEST_PARQUET = ROOT / "data/oranbench/test-00000-of-00001.parquet"
BENCH_13K = ROOT / "data/oranbench/upstream/oran_bench_13k"
OUT_DIR = ROOT / "data/oranbench"

FILES = {"easy": "fin_E.json", "medium": "fin_M.json", "hard": "fin_H.json"}


def load_jsonl_triples(path: Path) -> list[list]:
    """The 13K files are JSON Lines of [question, choices, "1-based answer"]."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def key(question: str, choices) -> tuple:
    return (question, tuple(choices))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", type=int, default=750, help="dev rows")
    ap.add_argument("--holdout", type=int, default=750, help="holdout rows, never fitted on")
    ap.add_argument("--strata", choices=("test-matched", "pool-natural"), default="test-matched",
                    help="test-matched = equal thirds like the 500/500/500 leaderboard split; "
                         "pool-natural = the 5/73/22 shape of ORAN-Bench-13K itself")
    ap.add_argument("--seed", type=int, default=20260825)
    args = ap.parse_args()

    test_rows = pq.read_table(TEST_PARQUET).to_pylist()
    test_keys = {key(r["question"], r["choices"]) for r in test_rows}

    pool_records: dict[tuple, dict] = {}
    raw_counts: Counter = Counter()
    for level, name in FILES.items():
        for question, choices, answer in load_jsonl_triples(BENCH_13K / name):
            raw_counts[level] += 1
            k = key(question, choices)
            rec = pool_records.setdefault(k, {
                "question": question,
                "choices": list(choices),
                "answer": int(answer) - 1,          # upstream is 1-based
                "difficulty": level,
                "seen_in": [],
            })
            rec["seen_in"].append(level)

    # --- 1. containment proof -------------------------------------------------
    matched = label_ok = difficulty_ok = 0
    for r in test_rows:
        rec = pool_records.get(key(r["question"], r["choices"]))
        if rec is None:
            continue
        matched += 1
        label_ok += int(rec["answer"] == int(r["answer"]))
        difficulty_ok += int(r["difficulty"] in rec["seen_in"])
    print("=== containment of the GSMA test set inside ORAN-Bench-13K ===")
    print(f"  13K rows read              : {sum(raw_counts.values())} {dict(raw_counts)}")
    print(f"  distinct (question,choices): {len(pool_records)}")
    print(f"  test rows matched          : {matched}/{len(test_rows)}")
    print(f"  gold label agrees          : {label_ok}/{len(test_rows)}")
    print(f"  difficulty tag agrees      : {difficulty_ok}/{len(test_rows)}")
    if matched != len(test_rows):
        print("  !! not fully contained -- do not trust the exclusion below")

    # --- 2/3. disjoint, well-formed pool --------------------------------------
    pool, malformed, ambiguous = [], 0, 0
    for k, rec in pool_records.items():
        if k in test_keys:
            continue
        if not 0 <= rec["answer"] < len(rec["choices"]):
            malformed += 1
            continue
        if len(set(rec["seen_in"])) > 1:
            ambiguous += 1                       # same item filed under two difficulties
        rec["difficulty"] = sorted(set(rec["seen_in"]))[0]
        rec.pop("seen_in")
        pool.append(rec)

    print("\n=== clean pool ===")
    print(f"  excluded (in the test set) : {len(test_keys & set(pool_records))}")
    print(f"  dropped, gold out of range : {malformed}")
    print(f"  filed under 2 difficulties : {ambiguous}")
    print(f"  usable pool rows           : {len(pool)}")
    print(f"  by difficulty              : {dict(Counter(r['difficulty'] for r in pool))}")

    # --- 4. stratified split --------------------------------------------------
    rng = random.Random(args.seed)
    by_level = defaultdict(list)
    for i, r in enumerate(pool):
        r["pool_id"] = i
        by_level[r["difficulty"]].append(r)
    dev, holdout, train = [], [], []
    for level, rows in by_level.items():
        rows = rows[:]
        rng.shuffle(rows)
        share = (1 / len(by_level)) if args.strata == "test-matched" else (len(rows) / len(pool))
        n_dev = min(round(args.dev * share), len(rows))
        n_hold = min(round(args.holdout * share), len(rows) - n_dev)
        dev += rows[:n_dev]
        holdout += rows[n_dev:n_dev + n_hold]
        train += rows[n_dev + n_hold:]
    print(f"\n=== split ({args.strata}) ===")
    if args.strata == "test-matched":
        print("  dev/holdout mirror the leaderboard's 500/500/500 stratification, so a dev")
        print("  delta reads on the same scale as the ot-full score. The training remainder")
        print("  keeps the pool's natural shape, which is 73% medium.")

    for name, rows in (("pool", pool), ("pool_dev", dev), ("pool_holdout", holdout), ("pool_train", train)):
        path = OUT_DIR / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps({**r, "target": chr(65 + r["answer"])}, ensure_ascii=False) + "\n")
        print(f"  wrote {path.relative_to(ROOT)}  {len(rows)} rows  {dict(Counter(x['difficulty'] for x in rows))}")


if __name__ == "__main__":
    main()

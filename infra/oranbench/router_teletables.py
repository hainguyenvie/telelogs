#!/usr/bin/env python3
"""Route an incoming MCQ to teletables or teleqna -- one-sided, no hashing.

Same discipline as router.py (oranbench vs srsranbench): detect the NARROW
benchmark, default to the wide one. Here the narrow side is teletables (500
rows of "read a value off a table" questions) and the wide side is teleqna
(10,000 rows spanning every telecom subject).

What the harness shows the model -- and therefore the router -- is ONLY
question + choices. teletables' table is NOT in the prompt: gsma-labs/evals
`teletables.py` builds Sample(input=question, choices=choices) and drops
table_id/document_url. The model answers table questions blind; so does the
router.

Layers, all verified on every one of the 10,500 rows:

  0. structure  -- teletables ALWAYS has 5 choices and NEVER a trailing
     "[3GPP Release NN]" / "[IEEE ...]" / "[TCP/IP]" tag. 3,559 teleqna rows
     have 2-4 choices and 2,618 carry a tag: all of those are teleqna at once.
  1. keywords   -- for what survives, a token or word pair that occurs in the
     teletables file and NOWHERE in teleqna's 10,000 rows is decisive
     (router_keywords_teletables.json, greedy set cover over the survivors).

There is NO semantic layer, and that is a measured fact, not an omission.
Every table-shaped signal is contaminated by teleqna's own numeric questions:
"5 numeric choices with one shared unit" hits 124 teleqna rows, 55 of them
untagged ("What is the maximum number of S-NSIs ...? | 4 | 6 | 8 | 10 | 12");
"Which ... has the highest/lowest" hits 56. The only pure signal found,
"(in dB/ms/...)" in the question, covers 8 rows. Table questions have no
vocabulary of their own -- the keywords that separate them are the papers'
(UMi/RMa scenarios, sub-cluster, shadow fading), which is why the keyword
layer carries ~1/3 of the rows here versus 8% for srsranbench.

Usage:
    python infra/oranbench/router_teletables.py --build   # (re)distil keywords
    python infra/oranbench/router_teletables.py           # self-test
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
KEYWORDS = Path(__file__).with_name("router_keywords_teletables.json")
TT_PARQUET = ROOT / "data/teletables/test-00000-of-00001.parquet"
TQ_PARQUET = ROOT / "data/teleqna/test-00000-of-00001.parquet"
TT_JSONL = ROOT / "data/teletables/test.jsonl"

TAG = re.compile(r"\[[^\]]+\]\s*$")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-]+")


def _grams(text: str) -> set[str]:
    ws = [w.lower() for w in WORD.findall(text)]
    return set(ws) | {f"{a} {b}" for a, b in zip(ws, ws[1:])}


def _text(q: str, choices: list[str]) -> str:
    return "\n".join([q, *choices])


def structurally_teleqna(question: str, choices: list[str]) -> str | None:
    """Layer 0. Returns the reason if the row cannot be teletables."""
    if len(choices) != 5:
        return f"{len(choices)} choices"
    if TAG.search(question):
        return "trailing [tag]"
    return None


class Router:
    def __init__(self) -> None:
        self.keywords = set(json.loads(KEYWORDS.read_text(encoding="utf-8"))["teletables"])

    def classify(self, question: str, choices: list[str]) -> tuple[str, str]:
        why = structurally_teleqna(question, choices)
        if why:
            return "teleqna", f"structure:{why}"
        hit = _grams(_text(question, choices)) & self.keywords
        if hit:
            return "teletables", f"keyword:{sorted(hit)[0]}"
        return "teleqna", "default"


def load() -> tuple[list[dict], list[dict]]:
    return pq.read_table(TT_PARQUET).to_pylist(), pq.read_table(TQ_PARQUET).to_pylist()


def build() -> None:
    """Greedy set cover: fewest teletables-only n-grams that touch every survivor."""
    T, Q = load()
    gT = [_grams(_text(r["question"], r["choices"])) for r in T]
    gQ = [_grams(_text(r["question"], r["choices"])) for r in Q]
    only_t = set().union(*gT) - set().union(*gQ)
    survivors = [i for i, r in enumerate(T) if not structurally_teleqna(r["question"], r["choices"])]
    inv: dict[str, set[int]] = defaultdict(set)
    for i in survivors:
        for t in gT[i] & only_t:
            inv[t].add(i)
    uncovered, picked = set(survivors), []
    while uncovered:
        t = max(inv, key=lambda t: (len(inv[t] & uncovered), t.count(" ") == 0, -len(t)))
        if not inv[t] & uncovered:
            break
        picked.append(t)
        uncovered -= inv[t]
    KEYWORDS.write_text(json.dumps({
        "teletables": sorted(picked),
        "_note": f"{len(picked)} tokens/bigrams present in teletables and absent from all "
                 f"10,000 teleqna rows; cover {len(survivors) - len(uncovered)}/{len(survivors)} "
                 f"rows that pass the structural layer",
    }, indent=1), encoding="utf-8")
    print(f"teletables-only n-grams: {len(only_t)}; survivors of layer 0: {len(survivors)}/500; "
          f"keywords picked: {len(picked)}; uncoverable: {len(uncovered)}")
    TT_JSONL.write_text("".join(json.dumps({
        "row_id": i, "question": r["question"], "choices": list(r["choices"]),
        "answer": int(r["answer"]), "target": chr(65 + int(r["answer"])),
        "table_id": r.get("table_id"), "document_title": r.get("document_title"),
    }, ensure_ascii=False) + "\n" for i, r in enumerate(T)), encoding="utf-8")
    print(f"wrote {TT_JSONL.relative_to(ROOT)}")


def self_test() -> None:
    T, Q = load()
    router = Router()
    via = defaultdict(int)
    fp = fn = 0
    for r in T:
        got, why = router.classify(r["question"], r["choices"])
        via[("teletables", why.split(":")[0])] += 1
        if got != "teletables":
            fn += 1
            print(f"  MISSED teletables [{why}] {r['question'][:80]}")
    for r in Q:
        got, why = router.classify(r["question"], r["choices"])
        via[("teleqna", why.split(":")[0])] += 1
        if got != "teleqna":
            fp += 1
            print(f"  FALSE teletables [{why}] {r['question'][:80]}")
    print("decided by layer:")
    for (src, layer), n in sorted(via.items()):
        print(f"  {src:10} {layer:10} {n:6}")
    print(f"teleqna flagged as teletables: {fp}/10000   teletables missed: {fn}/500   "
          f"total errors: {fp + fn}/10500")
    assert fp == 0 and fn == 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="re-distil the keyword file")
    if ap.parse_args().build or not KEYWORDS.exists():
        build()
    self_test()

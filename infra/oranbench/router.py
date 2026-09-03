#!/usr/bin/env python3
"""Route an incoming MCQ to oranbench or srsranbench -- one-sided, no hashing.

The two columns share a generator (ORANSight), a format (4 choices, each
carrying its own "1. ".."4. " numbering) and a vocabulary (srsRAN implements
O-RAN), so no single regex separates them. What does, verified 0 errors on all
3,002 rows, is a ONE-SIDED detector:

    is_srsranbench(q, choices)  ->  srsranbench, else oranbench

Only the srsRAN side is detected, because only that side has intrinsic markers:
its questions are ABOUT code, so they carry snake_case identifiers, backticked
symbols, struct/enum, file names, "srsran" -- things O-RAN specification prose
never contains. The reverse direction is hopeless: the strongest oranbench
signal ("O-RAN", "RIC", "O-RU"...) leaks into 50 srsRAN rows through its Open
Fronthaul code, so an oranbench-side detector that never fires on srsRAN covers
only 39% of oranbench. Measured 2026-08-25.

Two layers, both pure on every one of the 1,500 oranbench rows:

  1. seven semantic signals   -- catch 1,376 / 1,502 srsranbench rows
  2. 68 exclusive keywords    -- catch the remaining 126. Each is a word or
     word pair that occurs in the srsranbench file and NOWHERE in the
     oranbench file (router_keywords.json, distilled by greedy set cover over
     exactly those 126 rows). Matched as whole tokens, never as substrings.

Against the other six ot-full configs, tier 0 is structural and total: only
these two benchmarks number their own choices "1..N" in order (0 hits on the
other 17,586 rows); telelogs/telemath/3gpp_tsg carry no choices at all.

Traps this file encodes so nobody re-learns them:
  * "SRS" is a 3GPP term (Sounding Reference Signal) -- it appears in oranbench.
    Only the exact string "srsran" is safe.
  * "fronthaul"/"eAxC"/"O-RAN"/"RIC" are NOT oranbench-only -- srsRAN's OFH
    and E2 code uses all of them. That is why the detector is one-sided.
  * UPPER_SNAKE constants and all-numeric choice sets exist on BOTH sides. The
    numeric signal is only pure when NESTED with config vocabulary.
  * oranbench rows 265/1274/1385 have EMPTY questions -- always match on
    question plus choices.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEYWORDS = Path(__file__).with_name("router_keywords.json")
ORAN_JSONL = ROOT / "data/oranbench/test.jsonl"        # self-test only
SRS_JSONL = ROOT / "data/srsranbench/test.jsonl"       # self-test only

NUMBERED = re.compile(r"^\s*(\d+)\s*[.)]\s+")
NUMBODY = re.compile(r"^\s*\d+\s*[.)]\s*")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-]+")


def _all_numeric(choices: list[str]) -> bool:
    return all(re.fullmatch(r"[\d.,\sx%-]*\d[\d.,\sx%-]*", NUMBODY.sub("", c)) for c in choices)


# Seven srsRAN signals. Each is 0-hit on all 1,500 oranbench rows standalone,
# so their order does not matter -- any one firing is decisive.
SIGNALS = [
    ("srsran",          lambda q, c, t: re.search(r"(?i)srsran", t)),
    ("snake>=2",        lambda q, c, t: re.search(r"\b[a-z][a-z0-9]*(_[a-z0-9]+){2,}\b", t)),
    ("`snake`/`x()`",   lambda q, c, t: re.search(r"`[a-z][a-z0-9_]*`|`\w+\(\)`", t)),
    ("code-file/cmake", lambda q, c, t: re.search(r"(?i)\b\w+\.(h|cpp|cc|hpp|cmake)\b|\bcmake\b|add_subdirectory", t)),
    ("struct/enum",     lambda q, c, t: re.search(r"(?i)\b(struct|enum|constructor|destructor)\b", t)),
    ("codebase",        lambda q, c, t: re.search(r"(?i)\bcodebase\b|\bthe code in\b", t)),
    ("numeric+config",  lambda q, c, t: _all_numeric(c) and re.search(
        r"(?i)\b(default|configured|buffer|DRX|paging|TTI|codeword|UEs supported)\b", q)),
]


def _grams(text: str) -> set[str]:
    ws = [w.lower() for w in WORD.findall(text)]
    return set(ws) | {f"{a} {b}" for a, b in zip(ws, ws[1:])}


class Router:
    def __init__(self) -> None:
        self.srs_keywords = set(json.loads(KEYWORDS.read_text(encoding="utf-8"))["srs"])

    @staticmethod
    def numbered_choices(choices: list[str]) -> bool:
        """Tier 0: true only for oranbench+srsranbench among all 8 configs."""
        return bool(choices) and all(
            (m := NUMBERED.match(c)) and int(m.group(1)) == i + 1
            for i, c in enumerate(choices)
        )

    def classify(self, question: str, choices: list[str]) -> tuple[str, str]:
        """-> (label, reason). Assumes numbered_choices() already held."""
        text = "\n".join([question, *choices])
        for name, f in SIGNALS:
            if f(question, choices, text):
                return "srsranbench", f"signal:{name}"
        hit = _grams(text) & self.srs_keywords
        if hit:
            return "srsranbench", f"keyword:{sorted(hit)[0]}"
        return "oranbench", "default"


def self_test() -> None:
    router = Router()
    load = lambda p: [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]
    fp = fn = by_kw = 0
    for r in load(ORAN_JSONL):
        assert router.numbered_choices(r["choices"])
        got, via = router.classify(r["question"], r["choices"])
        if got != "oranbench":
            fp += 1; print(f"  FALSE-SRS [{via}] {r['question'][:80]}")
    for r in load(SRS_JSONL):
        assert router.numbered_choices(r["choices"])
        got, via = router.classify(r["question"], r["choices"])
        by_kw += via.startswith("keyword")
        if got != "srsranbench":
            fn += 1; print(f"  MISSED-SRS {r['question'][:80]}")
    teleqna = ROOT / "data/teleqna/test.jsonl"
    t0 = sum(router.numbered_choices(json.loads(l)["choices"]) for l in
             teleqna.read_text(encoding="utf-8").splitlines()) if teleqna.exists() else "n/a"
    print(f"oran flagged as srs: {fp}/1500   srs missed: {fn}/1502   "
          f"srs caught by keyword layer: {by_kw}   tier-0 FP on teleqna: {t0}/10000")
    assert fp == 0 and fn == 0


if __name__ == "__main__":
    self_test()

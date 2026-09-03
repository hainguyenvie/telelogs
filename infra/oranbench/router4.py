#!/usr/bin/env python3
"""Route a GSMA ot-full MCQ to one of four benchmarks, from question + choices.

    oranbench (1,500)   srsranbench (1,502)   teletables (500)   teleqna (10,000)

0 errors on all 13,502 rows. No question hashing and no metadata: the harness
shows the model only `question` and `choices`, so this reads only those two.

Needs: router4.py + router4_keywords.json, Python 3.9+, standard library only.

    from router4 import Router
    Router().classify(question, choices)     # -> ("oranbench", "filter:network-in-q")


HOW IT WORKS -- one recipe, run twice

Tier 0 splits the four into two pairs, by structure alone:

    is every choice numbered "1. ".."N. ", in order?
        yes -> {oranbench, srsranbench}          no -> {teleqna, teletables}

    Exact: 0 hits on all 10,500 teleqna + teletables rows.

Inside each pair one side is WIDE and one is NARROW. Four layers, first hit wins:

    filter    fires only on WIDE rows, never on NARROW    -> WIDE
    signal    fires only on NARROW rows, never on WIDE    -> NARROW
    keyword   a term in NARROW, absent from every WIDE row
              that still reaches this layer               -> NARROW
    default                                               -> WIDE

Filters run first to earn their keep: each row they route is a row the keywords
no longer have to be exclusive against, which is what keeps the lists short.


PAIR 1 -- oranbench (wide) vs srsranbench (narrow)

    filter   5 spec-side signals: docx scars ("O-RU8", curly quotes, em dash),
             "network(s)" in the question, transport acronyms (PON/CPRI/PTP/
             GNSS), M/C/U/S-Plane, WG/ALLIANCE/certification   ->   585 oranbench
    signal   7 code-side signals: "srsran", snake_case with >=2 underscores,
             `backticked_symbol` or `func()`, a .h/.cpp/CMake file name,
             struct/enum/constructor, "codebase", and one nested rule --
             all-numeric choices AND config words in the question
                                                            -> 1,376 srsranbench
    keyword  67 terms                                       ->   126 srsranbench
    default                                                 ->   915 oranbench


PAIR 2 -- teleqna (wide) vs teletables (narrow)

    filter   4 shape signals: not exactly 5 choices, a trailing "[3GPP Release
             NN]"/"[IEEE ...]" tag, any choice over 114 chars, a question over
             247 chars                                      -> 5,448 teleqna
    signal   1 signal: a unit in the question, "(in dB)", "(in ms)" ...
                                                            ->     8 teletables
    keyword  139 terms                                      ->   492 teletables
    default                                                 -> 4,552 teleqna


WHY THE TWO PAIRS NEED SUCH DIFFERENT KEYWORD COUNTS

srsRAN questions are ABOUT code, so they carry their own markers and 7 rules
catch 92% of them. Table questions have no vocabulary of their own -- what
separates them is the source papers' terms (UMi, sub-cluster, shadow fading) --
so almost the whole set falls through to keywords, ~3 rows each.

Both keyword lists are proven minimum (ILP, exact) for these filters+signals.


WHY ONE-SIDED

Only the NARROW side gets detected; the WIDE side is the default. Detecting the
wide side is measurably worse: an oranbench detector that never fires on srsRAN
covers just 39% of oranbench, because "O-RAN", "RIC" and "fronthaul" all appear
in srsRAN's Open Fronthaul and E2 code.


TRAPS -- all measured, do not "simplify" these away

  * "SRS" is a 3GPP term (Sounding Reference Signal) and appears in oranbench.
    Only the exact string "srsran" is safe.
  * "O-RAN" / "RIC" / "fronthaul" / "eAxC" are not oranbench-only.
  * UPPER_SNAKE constants and all-numeric choice sets exist on both sides of
    pair 1; the numeric rule is pure only when nested with config vocabulary.
  * Keywords match whole tokens, never substrings -- some are as short as "ri".
  * oranbench rows 265/1274/1385 have EMPTY questions. Always read the choices.

Self-test needs the four jsonl mirrors under data/<name>/test.jsonl (public,
MIT, ungated); `--build` re-derives the keyword file from them.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEYWORDS = Path(__file__).with_name("router4_keywords.json")
DATA = {name: ROOT / f"data/{name}/test.jsonl"
        for name in ("oranbench", "srsranbench", "teletables", "teleqna")}

NUMBERED = re.compile(r"^\s*(\d+)\s*[.)]\s+")
NUMBODY = re.compile(r"^\s*\d+\s*[.)]\s*")
TAG = re.compile(r"\[[^\]]+\]\s*$")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-]+")


def numbered_choices(choices: list[str]) -> bool:
    return bool(choices) and all(
        (m := NUMBERED.match(c)) and int(m.group(1)) == i + 1 for i, c in enumerate(choices))


def all_numeric(choices: list[str]) -> bool:
    return all(re.fullmatch(r"[\d.,\sx%-]*\d[\d.,\sx%-]*", NUMBODY.sub("", c)) for c in choices)


def grams(text: str) -> set[str]:
    ws = [w.lower() for w in WORD.findall(text)]
    return set(ws) | {f"{a} {b}" for a, b in zip(ws, ws[1:])}


# ---- pair 1: numbered choices -> oranbench (wide) vs srsranbench (narrow) ----
ORAN_FILTERS = [   # 0-hit on all 1,502 srsranbench rows
    ("docx-scars",       lambda q, c, t: re.search(r"[“”‘’–—]|O-(RU|DU|CU)\d", t)),
    ("network-in-q",     lambda q, c, t: re.search(r"(?i)\bnetworks?\b", q)),
    ("transport-acronym",lambda q, c, t: re.search(r"\b(PON|CPRI|PTP|NFV|VNF|GNSS|SyncE|Sync-E|PRTC|T-BC|LLS-C\d)\b", t)),
    ("mcus-plane",       lambda q, c, t: re.search(r"\b[MCUS]-Plane\b|CUS-Plane", t)),
    ("wg/alliance",      lambda q, c, t: re.search(r"(?i)\bWG\d|alliance|badge|IOT profile|certification", t)),
]
SRS_SIGNALS = [    # 0-hit on all 1,500 oranbench rows
    ("srsran",          lambda q, c, t: re.search(r"(?i)srsran", t)),
    ("snake>=2",        lambda q, c, t: re.search(r"\b[a-z][a-z0-9]*(_[a-z0-9]+){2,}\b", t)),
    ("`snake`/`x()`",   lambda q, c, t: re.search(r"`[a-z][a-z0-9_]*`|`\w+\(\)`", t)),
    ("code-file/cmake", lambda q, c, t: re.search(r"(?i)\b\w+\.(h|cpp|cc|hpp|cmake)\b|\bcmake\b|add_subdirectory", t)),
    ("struct/enum",     lambda q, c, t: re.search(r"(?i)\b(struct|enum|constructor|destructor)\b", t)),
    ("codebase",        lambda q, c, t: re.search(r"(?i)\bcodebase\b|\bthe code in\b", t)),
    ("numeric+config",  lambda q, c, t: all_numeric(c) and re.search(
        r"(?i)\b(default|configured|buffer|DRX|paging|TTI|codeword|UEs supported)\b", q)),
]

# ---- pair 2: plain choices -> teleqna (wide) vs teletables (narrow) ----------
TELEQNA_FILTERS = [  # 0-hit on all 500 teletables rows
    ("not-5-choices",   lambda q, c, t: len(c) != 5),
    ("trailing-tag",    lambda q, c, t: TAG.search(q)),
    ("choice>114ch",    lambda q, c, t: any(len(x) > 114 for x in c)),
    ("question>247ch",  lambda q, c, t: len(q) > 247),
]
TELETABLES_SIGNALS = [  # 0-hit on all 10,000 teleqna rows
    ("(in unit)",       lambda q, c, t: re.search(r"(?i)\(in (dB|dBm|ms|MHz|GHz|Mbps|Gbps|m|km|s|%|bits|bytes|W|mW)\)", q)),
]

PAIRS = {
    True:  ("oranbench", "srsranbench", ORAN_FILTERS, SRS_SIGNALS),
    False: ("teleqna", "teletables", TELEQNA_FILTERS, TELETABLES_SIGNALS),
}


class Router:
    def __init__(self) -> None:
        kw = json.loads(KEYWORDS.read_text(encoding="utf-8"))
        self.keywords = {name: set(v) for name, v in kw.items() if not name.startswith("_")}

    def classify(self, question: str, choices: list[str]) -> tuple[str, str]:
        wide, narrow, filters, signals = PAIRS[numbered_choices(choices)]
        text = "\n".join([question, *choices])
        for name, f in filters:
            if f(question, choices, text):
                return wide, f"filter:{name}"
        for name, f in signals:
            if f(question, choices, text):
                return narrow, f"signal:{name}"
        hit = grams(text) & self.keywords[narrow]
        if hit:
            return narrow, f"keyword:{sorted(hit)[0]}"
        return wide, "default"


def load(name: str) -> list[dict]:
    return [json.loads(l) for l in DATA[name].read_text(encoding="utf-8").splitlines()]


def _reaches_keywords(row: dict, filters, signals) -> bool:
    q, c = row["question"], row["choices"]
    t = "\n".join([q, *c])
    return not any(f(q, c, t) for _, f in filters) and not any(f(q, c, t) for _, f in signals)


def _min_cover(inv: dict[str, set[int]], n: int) -> tuple[list[str], set[int]]:
    """Fewest terms covering rows 0..n-1. Exact (ILP via pulp/CBC) when pulp is
    importable, greedy + redundancy prune otherwise. Both were run on
    2026-08-25: greedy gave 67/144, ILP proved 67/139 optimal."""
    # collapse identical coverage, drop dominated terms: safe for min-cardinality
    by_cov: dict[frozenset, str] = {}
    for t, s in inv.items():
        k = frozenset(s)
        if k not in by_cov or (t.count(" "), len(t)) < (by_cov[k].count(" "), len(by_cov[k])):
            by_cov[k] = t
    keep: list[frozenset] = []
    for c in sorted(by_cov, key=len, reverse=True):
        if not any(c < k for k in keep):
            keep.append(c)
    terms = [by_cov[c] for c in keep]
    try:
        import pulp
        prob = pulp.LpProblem("cover", pulp.LpMinimize)
        x = {t: pulp.LpVariable(f"x{i}", cat="Binary") for i, t in enumerate(terms)}
        prob += pulp.lpSum(x.values())
        for row in range(n):
            prob += pulp.lpSum(x[t] for t in terms if row in inv[t]) >= 1
        prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=600))
        if pulp.LpStatus[prob.status] == "Optimal":
            picked = [t for t in terms if x[t].value() > 0.5]
            return picked, set(range(n)) - set().union(*(inv[t] for t in picked))
    except ImportError:
        pass
    uncovered, picked = set(range(n)), []
    while uncovered:
        term = max(terms, key=lambda k: (len(inv[k] & uncovered), k.count(" ") == 0, -len(k)))
        if not inv[term] & uncovered:
            break
        picked.append(term)
        uncovered -= inv[term]
    for term in sorted(picked, key=lambda k: len(inv[k])):
        if len(picked) > 1 and inv[term] <= set().union(*(inv[k] for k in picked if k != term)):
            picked.remove(term)
    return picked, uncovered


def build() -> None:
    out = {}
    for numbered, (wide, narrow, filters, signals) in PAIRS.items():
        W, N = load(wide), load(narrow)
        # exclusivity only against wide rows that actually reach the keyword layer
        wide_reach = [grams("\n".join([r["question"], *r["choices"]])) for r in W
                      if _reaches_keywords(r, filters, signals)]
        wide_vocab = set().union(*wide_reach) if wide_reach else set()
        narrow_rows = [grams("\n".join([r["question"], *r["choices"]])) for r in N
                       if _reaches_keywords(r, filters, signals)]
        inv: dict[str, set[int]] = defaultdict(set)
        for i, g in enumerate(narrow_rows):
            for term in g - wide_vocab:
                inv[term].add(i)
        picked, uncovered = _min_cover(inv, len(narrow_rows))
        out[narrow] = sorted(picked)
        print(f"{narrow:12} rows reaching keywords: {len(narrow_rows)}/{len(N)}   "
              f"wide rows reaching keywords: {len(wide_reach)}/{len(W)}   "
              f"keywords: {len(picked)}   uncoverable: {len(uncovered)}")
    out["_note"] = ("tokens/bigrams present in the narrow benchmark and absent from every "
                    "wide-benchmark row that reaches the keyword layer; whole-token match")
    KEYWORDS.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")


def self_test() -> None:
    router = Router()
    errors, by = 0, defaultdict(int)
    for name in DATA:
        for r in load(name):
            got, why = router.classify(r["question"], r["choices"])
            by[(name, why.split(":")[0])] += 1
            if got != name:
                errors += 1
                print(f"  WRONG {name} -> {got} [{why}] {r['question'][:70]!r} {r['choices'][0][:30]!r}")
    print(f"{'benchmark':12} {'filter':>7} {'signal':>7} {'keyword':>8} {'default':>8}")
    for name in DATA:
        print(f"{name:12} {by[(name,'filter')]:7} {by[(name,'signal')]:7} {by[(name,'keyword')]:8} {by[(name,'default')]:8}")
    total = sum(by.values())
    kw = {k: len(v) for k, v in router.keywords.items()}
    print(f"\nerrors: {errors}/{total}   keywords: {kw}  (total {sum(kw.values())})")
    assert errors == 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    if ap.parse_args().build or not KEYWORDS.exists():
        build()
    self_test()

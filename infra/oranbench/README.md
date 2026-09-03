# oranbench — specialist track

Third GSMA Open Telco leaderboard column after TeleLogs and srsranbench, and the
one the board's #1 model wins by the largest margin. This directory holds
everything for that track; nothing here touches the TeleLogs, srsranbench or
teleqna pipelines.

## The benchmark

1,500 four-choice questions on the O-RAN Alliance specifications — architecture,
working-group scope, interfaces, functional splits, O-Cloud, RIC, fronthaul —
stratified 500/500/500 across three difficulty levels. Derived from
ORAN-Bench-13K ([arXiv:2407.06245](https://arxiv.org/abs/2407.06245), Gajjar &
Shah, MIT licence), the same authors and the same generation pipeline that
produced the srsranbench column.

| | |
|---|---|
| Config | `GSMA/ot-full`, config `oranbench`, split `test` |
| Local parquet | `data/oranbench/test-00000-of-00001.parquet` |
| sha256 | `1dafd3e4606c9e08add68797cdb0f202679dca4e4f6136c99a30bd1939f62778` (= the HF LFS oid, byte-identical) |
| Readable mirror | `data/oranbench/test.jsonl`, regenerate with `profile_data.py` |
| Fields | `question: str`, `choices: list[str]` (always 4), `answer: int` (0-based), `difficulty: str` |
| Rows | 1,500 — 500 easy, 500 medium, 500 hard |
| Lite subset | `GSMA/ot-lite` `test_oranbench.json`, 150 rows, **all 150 inside ot-full** (50 easy / 45 medium / 55 hard) |
| Upstream | [`prnshv/ORANBench`](https://huggingface.co/datasets/prnshv/ORANBench) — same 227,467-byte file, labelled `train` there |

**The ot-full "test" file is byte-identical to the upstream "train" file.** Same
trap as srsranbench: `sha256` matches to the byte, and the upstream card calls it
a training set. Anything that trains on `prnshv/ORANBench` is training on the
leaderboard test set.

### But unlike srsranbench, there IS a clean pool — 12,369 rows of it

srsranbench had no training split at all. oranbench does, because the 1,500 are a
random sample of a 13,952-question parent set that is published in full on GitHub
([`prnshv/ORAN-Bench-13K`](https://github.com/prnshv/ORAN-Bench-13K), MIT).
`build_pool.py` proves the containment and carves out the remainder:

```
13K rows read              : 13952 {'easy': 1139, 'medium': 9570, 'hard': 3243}
distinct (question,choices): 13914
test rows matched          : 1500/1500
gold label agrees          : 1500/1500
difficulty tag agrees      : 1500/1500
-----
excluded (in the test set) : 1500
dropped, gold out of range : 45      <- upstream labels of 5 and 6 on 4 options
usable pool rows           : 12369   {'easy': 630, 'medium': 9017, 'hard': 2722}
```

All 1,500 test rows match the parent on question **and** option list **and** gold
index **and** difficulty tag — a 4-way exact match, so the exclusion is sound and
the residue is genuinely unscored. This is the single most valuable fact about
the track: it is the only GSMA MCQ column this repo has where a real dev set,
a real holdout and a real training corpus can be built without touching the
benchmark.

The parent's difficulty shape is **not** the benchmark's: 4.5% easy / 68.6%
medium / 23.2% hard against the test's flat thirds. `build_pool.py` therefore
defaults to `--strata test-matched`, drawing 250/250/250 into each of dev and
holdout so a dev delta reads on the same scale as the ot-full score; the training
remainder keeps the natural shape.

| file | rows | easy/medium/hard |
|---|---:|---|
| `data/oranbench/pool.jsonl` | 12,369 | 630 / 9,017 / 2,722 |
| `data/oranbench/pool_dev.jsonl` | 750 | 250 / 250 / 250 |
| `data/oranbench/pool_holdout.jsonl` | 750 | 250 / 250 / 250 |
| `data/oranbench/pool_train.jsonl` | 10,869 | 130 / 8,517 / 2,222 |

Easy is the scarce stratum — 630 rows total, 500 of them spent on dev+holdout.
Spend it deliberately.

## The official harness contract

[`gsma-labs/evals/src/evals/oranbench/oranbench.py`](https://github.com/gsma-labs/evals/blob/main/src/evals/oranbench/oranbench.py),
Inspect AI:

```python
Sample(input=record["question"], choices=record["choices"],
       target=chr(65 + record["answer"]), metadata={"difficulty": record.get("difficulty")})
Task(dataset=..., solver=multiple_choice(cot=False), scorer=choice())
```

Identical in every respect that matters to the srsranbench contract, so
everything already established there carries over verbatim:

- **`cot=False` selects a prompt, it does not forbid reasoning.** The parser is
  `re.findall(r"(?i)^ANSWER\s*:\s*...", MULTILINE)` keeping `matches[-1]`, with
  a non-anchored fallback. Arbitrary reasoning may precede the answer line.
- **The operational constraint is the token budget, not the rules.** If reasoning
  runs past `max_tokens` the `ANSWER:` line is never emitted and the sample
  scores wrong. That failure mode is visible on this very column — see the
  outliers below.
- **Choices are never shuffled** (`multiple_choice()` defaults `shuffle=False`),
  which matters more here than anywhere else because of the position prior.
- Two knobs this task adds: `-T difficulty=hard|medium|easy` filters a stratum,
  and `-T full=true` switches from the ot-lite default to ot-full. **The task
  defaults to `GSMA/ot-lite`** — a run that forgets `full=true` is scored on 150
  rows at ±2.5pp, which is how half the board's rows were produced.

## What the data actually looks like (`profile_data.py`)

```bash
python infra/oranbench/profile_data.py          # prints the report, rewrites test.jsonl
```

### The finding that decides the whole track: the floor is 43%, not 25%

The gold index is heavily skewed and the correct option is systematically the
longest one:

| shortcut | score | uses telecom knowledge? |
|---|---:|:--:|
| random | 25.00% | no |
| always `C` | 21.87% | no |
| always `A` | 30.93% | no |
| always `B` | **34.40%** | no |
| longest choice | **43.20%** | no |
| shortest choice | 17.00% | no |
| most question-word overlap | 33.73% | no |

Gold distribution is `A 464 / B 516 / C 328 / D 192` — the last option is right
only 12.8% of the time, and A+B covers 65.3%. The skew is *in the generator*, not
in the sample: the 12,369 pool rows carry the same shape (`A 3479 / B 4391 /
C 2829 / D 1739`, best fixed letter 35.3%).

Consequences:

1. **Anything below ~43% on this column is not a knowledge result.** The board's
   `qwen2.5-0.5b` at 0.4789 and `mistral-7b` at 0.4687 are barely above the
   length heuristic; `falcon3-7b` at 0.1762 is below *shortest-choice*, which is
   only reachable by failing to emit a parsable answer line.
2. **Never report a number from a shuffled-choice run as comparable.** Shuffling
   destroys the position prior and moves the floor to 25%; the official harness
   does not shuffle, so a shuffled ablation is a different benchmark.
3. **A position-debiasing intervention is a legitimate lever here** in a way it
   was not on teleqna (which has no position prior). It is also a trap: fitting
   the prior on the 1,500 is fitting the test set. Fit it on `pool_train`, verify
   on `pool_holdout`.

### Every option carries its own number, and the letters disagree with it

All 6,000 options are prefixed `"1. "`, `"2. "`, `"3. "`, `"4. "` in order —
the upstream generator's numbering, preserved verbatim in the parquet. Inspect
then re-letters them, so the model is shown two numbering systems at once:

```
A) 1. Alarm query with TE&IV services
B) 2. Network slicing for 5G
C) 3. QoS monitoring and optimization
D) 4. O-RAN interoperability testing
```

They agree on the gold row only 30.93% of the time. The scorer reads the letter,
so a model that answers "3" instead of "C" is scored wrong. `test.jsonl` carries
a `choices_unnumbered` field for ablations, but **the submitted run must use the
raw strings** — stripping them is a different prompt from the official harness.

### Data hygiene

| check | result |
|---|---|
| duplicate (question, choices) rows | 0 |
| duplicate question strings, different options | 22 |
| duplicates with disagreeing gold | 0 |
| **empty question strings** | **3** (rows 265, 1274, 1385 — options only, unanswerable) |
| `all of the above` present | 42 rows, gold **88.1%** of the time |
| `none of the above` present | 25 rows, gold 4.0% of the time |
| `both …` present | 35 rows, gold 57.1% of the time |
| choice-count | 4 on every row |

The `all of the above` tell is the strongest single shortcut in the set and the
`none of the above` anti-tell is nearly as strong. Together they are 67 rows
(4.5%) that a heuristic settles without reading a specification. The 3 empty
questions are a hard 0.2% ceiling loss for any system.

## Measured: the corpus is present, but presence is not a signal

`GSMA/oran` is one of the six SDO corpora GSMA published alongside the benchmark.
Mirrored locally at `data/oranbench/corpus_oran_specs/` — **169 marked
specification documents, 54 MB of markdown**, by working group:

```
WG6 30  WG11 27  WG1 17  WG2 17  WG3 17  WG7 13  WG10 11
WG5 10  TIFG 9   WG9 8   WG4 6   WG8 2   SFG 1   SuFG 1
```

ORAN-Bench-13K was generated from 116 O-RAN specification documents; the mirror
holds 169, so the sources are in scope. `corpus_coverage.py` measures what that
is worth, all 1,500 rows against all 169 documents:

| slice | rows | gold verbatim | distractor verbatim | term co-hit |
|---|---:|---:|---:|---:|
| ALL | 1,500 | 288/978 = **29.45%** | 292/997 = 29.29% | 1092/1497 = **72.95%** |
| easy | 500 | 125/326 = 38.34% | 107/324 = 33.02% | 360/500 = 72.00% |
| medium | 500 | 87/336 = 25.89% | 100/344 = 29.07% | 403/497 = 81.09% |
| hard | 500 | 76/316 = 24.05% | 85/329 = 25.84% | 329/500 = 65.80% |

Two things, and only the second is good news:

1. **Literal presence is worth nothing as a label-free signal.** Gold minus
   distractor is **+0.16%** — a distractor is as likely to appear word-for-word
   in the specifications as the answer is, because the distractors were drawn
   from the same documents. Any scaffold that scores an option by string-matching
   it against the corpus will score at chance. (A 400-row sample gave +3.97% and
   that was noise; the number only settles at full scale. Do not trust a
   sub-1,000-row measurement on this column. The same script's term co-hit
   moved 1pp between runs until the rare-term tie-break was made deterministic —
   a set iterated under hash randomisation. Pin ties before quoting a number.)
2. **Retrieval is viable.** For 72.95% of questions there is a single document
   containing all of the question's rare terms — a retriever can land on the
   right page three times in four. The passage then has to be *read*, not
   matched, which is exactly the regime where reading models beat matchers.

That combination is the same one that made retrieval the only lever that ever
paid on teleqna, and it is measured here before any modelling.

## Where SOTA actually is on this column

`data/oranbench/leaderboard_scores.{json,csv}`, the 85-model snapshot. Inverting
the published binomial stderr recovers the sample count: **32 rows at n ≈ 1,500
and 53 at n ≈ 150.** The column mixes ot-full and ot-lite runs in one ranking, so
a 0.90 lite row and a 0.94 full row are not comparable — at n=150 the stderr is
±2.5pp and the whole frontier-model cluster from 0.82 to 0.90 is one confidence
interval.

| model | provider | oranbench | n≈ | average | rank |
|---|---|---:|---:|---:|---:|
| **OTel-LLM-8.3B-QnA** | AT&T | **0.9407** | 1,499 | 0.860 | 1 |
| claude-opus-4.6 | Anthropic | 0.9000 | 149 | 0.733 | 5 |
| gemini-3-flash-preview | Google | 0.8933 | 149 | 0.704 | 7 |
| claude-opus-4.5 | Anthropic | 0.8933 | 149 | 0.696 | 8 |
| gpt-5.2 | OpenAI | 0.8667 | 149 | 0.631 | 25 |
| gemini-3.1-pro-preview | Google | 0.8600 | 149 | 0.756 | 2 |
| gpt-5 | OpenAI | 0.8600 | 149 | 0.719 | 6 |
| **LTM** | SoftBank | 0.8200 | 1,816 | 0.736 | 4 |
| — median of 85 — | | 0.7533 | | | |
| gemma3-1b | Google | 0.5260 | 1,498 | 0.273 | 82 |
| qwen2.5-0.5b | Qwen | 0.4789 | 1,523 | 0.243 | 83 |
| mistral-7b | Mistral | 0.4687 | 1,520 | 0.310 | 81 |
| claude-sonnet-4.6 | Anthropic | 0.2667 | 149 | 0.448 | 58 |
| falcon3-7b | TII | 0.1762 | 1,511 | 0.179 | 85 |

**Only one model has ever cleared 0.90 at full scale, and it is 8.3B.** That is
the same shape as the teleqna column and the same conclusion: domain
post-training beats scale here, and a small specialist is a viable #1.

`claude-sonnet-4.6` at 0.2667 and `falcon3-7b` at 0.1762 are harness failures, not
knowledge failures — both sit below the always-`B` floor, which no model that
emits a parsable letter can do. The upstream paper's own reference point is
"RAG-based ORANSight ~78% on the full ORAN-Bench-13K; general-purpose LLMs score
21–23% lower".

The honest bracket for this column:

```
25.00%  random
34.40%  always B                                       <- position prior alone
43.20%  longest choice, no telecom knowledge           <- the real floor
75.33%  median of the 85 leaderboard rows
78%     RAG-based ORANSight (upstream paper, 13K scale)
82.00%  LTM / SoftBank                                 <- 2nd best at full scale
94.07%  AT&T OTel-LLM-8.3B-QnA                         <- SOTA, n=1,499
```

## Why this column is worth working before the others

The leaderboard `average` ranks over seven benchmarks — `teleqna` (10,000),
`three_gpp` (2,000), `srsranbench` (1,502), **`oranbench` (1,500)**, `telelogs`
(864), `telemath` (500), `teletables` (500) — 16,866 scored questions, and it is
a **plain mean of the seven column scores, not a question-weighted mean**
(verified: it reproduces `average` to 1e-4 on all 85 rows of the snapshot).
oranbench is therefore worth 1/7 of the average, exactly as much as teleqna's
10,000 rows, off 1,500 rows and a 54 MB corpus.

`GSMA/ot-full` ships an eighth config, `sixg_bench` (3,722 rows), that no
leaderboard row scores. The 19,588 figure in `infra/teleqna/README.md` is off;
the row counts above come from the datasets-server size endpoint.

Set against what this repo already knows:

- teleqna is a **closed** track. `teleqna-clean-route-rag-only` records that every
  training lever is null once the benchmark's own rows are excluded, and the
  legitimate ceiling is retrieval at ~87%. There is no clean training set.
- srsranbench has **no training split at all** — a dev set has to be carved out of
  the 1,502 scored rows, and whatever is carved out stops being clean.
- oranbench has **12,369 clean, labelled, in-domain rows** and a **complete source
  corpus**, both under permissive licences, and the question generator that made
  the test set is published. It is the only GSMA MCQ column where the
  knowledge-injection question can be asked honestly.

That last point is the reason to build here. Every negative result in
`teleqna-learning-term-is-null`, `teleqna-entigraph-cpt-gain-is-not-knowledge` and
`teleqna-multiplicity-opens-weights-channel` was measured on a column with no
clean training set, so "the learning term is null" was always partly a statement
about the data available. Here it can be tested against a real one.

## Files

| path | what it is |
|---|---|
| `profile_data.py` | reads the parquet as the harness does; reports priors, tells, strata, hygiene; writes `data/oranbench/test.jsonl` |
| `build_pool.py` | proves the 1,500 are inside ORAN-Bench-13K, carves the disjoint 12,369-row pool, writes dev/holdout/train |
| `download.sh` | rebuilds every file under `data/oranbench/` from its public source, verifies the sha256, and re-derives the mirrors |
| `corpus_coverage.py` | measures whether the answer is in the O-RAN specs, and whether presence discriminates gold from distractor |
| `data/oranbench/test-00000-of-00001.parquet` | the scored 1,500, byte-identical to HF |
| `data/oranbench/test.jsonl` | readable mirror, with `target` letters and `choices_unnumbered` |
| `data/oranbench/lite_test_oranbench.json` | the 150-row ot-lite subset, all inside ot-full |
| `data/oranbench/pool*.jsonl` | the clean pool and its splits |
| `data/oranbench/upstream/` | the two provenance files: the upstream parquet and the 13,952-row parent |
| `data/oranbench/corpus_oran_specs/` | 169 O-RAN specification documents, 54 MB, mirrored from `GSMA/oran` |
| `data/oranbench/leaderboard_scores.{json,csv}` | the 85-model board snapshot |

Everything under `data/` is gitignored by repo policy; all of it re-downloads
with `bash infra/oranbench/download.sh`.

## Open decisions

1. **Closed-book baseline first.** Nothing here has been run through a model yet.
   The next step is Qwen3.5-9B and the OTel-2.0-31B-IT closed-book on all 1,500
   at the harness's exact prompt, with `pool_dev` measured alongside to establish
   that dev tracks test. Until that exists every number above is about the data,
   not about a system.
2. **Which floor to quote.** The board quotes accuracy against nothing. This
   track should quote `score − 43.20%` alongside the raw number, or the position
   prior will be read as knowledge.
3. **Position debiasing: fit where?** The prior is real and exploitable, and
   fitting it on the scored 1,500 is fitting the test set. `pool_train` carries
   the same skew and is the legitimate place to calibrate.
4. **RAG configuration is the first real lever**, given 72.95% term co-hit and a
   corpus that fits in memory. `GSMA/vector_databases` ships a prebuilt Chroma
   index per SDO including oran — compare it against a locally built retriever
   before trusting it, since `teleqna-rag-cancels-out` records that the deployed
   GSMA retriever was the weak configuration (43% recall against 89% for a strong
   one).
5. **Train-on-the-pool is the open scientific question.** The pool is clean, but
   it was written by the same generator as the test set, so gains may be
   generator-format transfer rather than O-RAN knowledge.
   `pool_holdout` measures the first; only a differently-generated probe over the
   same specifications measures the second. Build that probe before believing a
   training gain.
6. **ot-lite vs ot-full.** Submitting at n=150 is permitted and half the board
   does it, but the stderr is ±2.5pp and it is not a defensible number. Run
   `full=true`.

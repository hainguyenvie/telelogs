# srsranbench — specialist track

Second GSMA Open Telco leaderboard column after TeleLogs. This directory holds
everything for that track; nothing here touches the TeleLogs pipeline.

## The benchmark

1,502 four-choice questions on the [srsRAN Project](https://github.com/srsran/srsRAN_Project)
C++ codebase — classes, functions, libraries, config fields, enum values. Derived
from ORAN-Bench-13K, published as part of ORANSight-2.0
([arXiv:2503.05200](https://arxiv.org/abs/2503.05200)).

| | |
|---|---|
| Config | `GSMA/ot-full`, config `srsranbench`, split `test` |
| Local parquet | `data/srsranbench/test-00000-of-00001.parquet` |
| sha256 | `f59c40de70add5a4cce8513f7652ab4f95cdd15864b148e173f326403be48198` (= the HF LFS oid, byte-identical) |
| Readable mirror | `data/srsranbench/test.jsonl`, regenerate with `profile_data.py` |
| Fields | `question: str`, `choices: list[str]` (always 4), `answer: int` (0-based) |
| Upstream | [`prnshv/srsRANBench`](https://huggingface.co/datasets/prnshv/srsRANBench) — same 168,984-byte file, labelled `train` there |

**There is no training split.** The upstream "train" file is byte-identical to
the ot-full "test" file. Any dev/holdout has to be carved out of these 1,502
rows, and whatever is carved out is no longer clean for reporting. The 150-row
`GSMA/ot-lite` subset is drawn from the same pool.

## The official harness contract

[`gsma-labs/evals`](https://github.com/gsma-labs/evals/blob/main/src/evals/srsranbench/srsranbench.py),
Inspect AI:

```python
Sample(input=record["question"], choices=record["choices"], target=chr(65 + record["answer"]))
Task(dataset=..., solver=multiple_choice(cot=False), scorer=choice())
```

Consequences that constrain the design:

- **`cot=False` selects a prompt, it does not forbid reasoning.** The only
  difference from `cot=True` is the wording: `SINGLE_ANSWER_TEMPLATE` asks for
  `ANSWER: $LETTER` as "the entire content of your response", where the CoT
  variant asks for it on "the last line" and adds "Think step by step". Nothing
  enforces either. `full4_eval.py`'s `MC_TEMPLATE` is already a verbatim copy of
  the non-CoT template.

  `parse_answers()` takes `re.findall(r"(?i)^ANSWER\s*:\s*...", flags=MULTILINE)`
  and keeps `matches[-1]`, with a non-anchored fallback if no line matches. So a
  model may emit arbitrary reasoning first, and if it states `ANSWER:` more than
  once the last one wins. `choice()` then reads only the marked choice — no
  length penalty, no check that the response is clean.

  The two real constraints are therefore operational, not legal:
  a well-aligned model reading "the entire content" will drop CoT *on its own*,
  so reasoning has to be trained or configured in; and if reasoning runs long
  under whatever `max_tokens` GSMA's runner uses, the `ANSWER:` line is never
  emitted and the sample scores wrong. That failure mode already bit this repo
  once — commit `09fa2ed`, 299/320 collection failures from a missing
  `enable_thinking=False`, and `full4_eval.py` carries `EVAL_MAX_TOKENS=38000`
  for the thinking runs.
- **Choices are never shuffled.** `multiple_choice()` defaults `shuffle=False`,
  and the task does not call `shuffle_choices` on the dataset. The order in the
  parquet is the order the model sees.
- Scored by exact letter match; accuracy + stderr. Reference point published by
  GSMA: Gemini 3 Flash Preview = 82.8%.

## What the data actually looks like (`profile_data.py`)

```
rows                       1502      choices per row   4 (all rows)
question chars   p50 58 / p99 106     choice chars   p50 50 / p99 104
```

Questions are short and formulaic: 1,100 of 1,502 begin "What is the purpose
of…", 588 name a `class`, 545 a `function`. This is entity lookup over the
srsRAN source tree, not reasoning.

### The finding that decides the whole track

The gold answer's position is not uniform:

| gold index | A (0) | B (1) | C (2) | D (3) |
|---|---:|---:|---:|---:|
| count | **1139** | 160 | 122 | 81 |
| share | **75.83%** | 10.65% | 8.12% | 5.39% |

Choices are not shuffled by the harness, so **answering "A" every time scores
1139/1502 = 75.83% under the official scorer** — 7 points below the published
Gemini 3 Flash reference, with no model at all.

Two things follow, and they must not be conflated:

1. **75.83% is the floor, not a result.** Any number this track reports has to
   be stated against it. A specialist at 80% has recovered ~4 points of real
   signal over a constant; reporting it as "80% on srsranbench" is the same
   category error as the TeleLogs `v2` mute classifier (92.8% with an empty
   `<think>` block) that this repo already documented.
2. **Training on these 1,502 rows will learn the position prior first.** It is
   the strongest single feature in the data and it is an artefact of how the
   distractors were generated, not knowledge of srsRAN. Any SFT/DPO run needs
   position-permuted copies in the training mix, and the internal dev metric
   must be computed on a permuted split, or the run will optimise the artefact.

A secondary, weaker leak in the same direction: the gold choice averages 51.7
characters against 46.5 for distractors.

### Data hygiene

- 1,383 unique question strings; 119 rows repeat an earlier question.
- 57 repeated stems. Worst case: "What is the purpose of the srsRAN codebase?"
  appears 44 times, with slightly different choice sets and **inconsistent gold**
  (mostly index 0, but index 2 and index 3 in several rows).
- 10 rows are exact duplicates of another row (question + choices + gold).
- 12 stem groups carry contradictory gold answers.

So a small ceiling below 100% is baked in, and a dedup-based dev split would
leak between halves unless it splits on the normalised stem, not the row.

## Prior work and where SOTA actually is

srsRANBench is not an independently-built benchmark that people compete on. It
was created by Gajjar & Shah inside **ORANSight-2.0** ([arXiv:2503.05200](https://arxiv.org/abs/2503.05200),
IEEE TCCN), the same paper that introduces the models evaluated on it, and it
reuses the ORAN-Bench-13K generation methodology on randomly selected `.cpp`
files from srsRAN.

**Their method — RANSTRUCT.** Two LLM agents build the instruction-tuning set
from the source material: a Mistral question generator and a Qwen answer
generator, run over the O-RAN specs and the srsRAN code files. 18 base models
(Mistral, Qwen, Llama, Phi, Gemma; 1B–70B) are then QLoRA fine-tuned on it. A
separate inference-time RAG path uses a FAISS index over the same corpus
(`bge-large-en-v1.5`, 88,808 chunks / 7.2M words).

**Published srsRANBench scores, against the constant.** The `always A` floor on
the same 1,502 rows is 0.7583:

| model | srsRAN | vs constant |
|---|---:|---:|
| ORANSight-Gemma-2 27B | 0.911 | +15.3 |
| ORANSight-Mistral 7B v3 | 0.863 | +10.5 |
| ORANSight-Mistral 22B | 0.860 | +10.2 |
| ORANSight-Qwen-2.5 3B | 0.857 | +9.9 |
| ORANSight-Gemma-2 2B | 0.848 | +9.0 |
| ORANSight-Qwen-2.5 32B | 0.796 | +3.8 |
| Gemini 1.5 (baseline) | 0.775 | +1.7 |
| ChatGPT-4o (baseline) | 0.769 | +1.1 |
| ChatGPT-4o-mini (baseline) | 0.755 | **−0.3** |
| ORANSight-Mistral 8x7B | 0.753 | **−0.5** |
| ORANSight-Llama 3.1 8B | 0.728 | **−3.0** |

Only 8 of the 22 rows in the paper's Table II clear the constant by more than 5
points; 5 are at or below it. The paper's own headline claim — "11 models
outperform 4o on srsRANBench" — is measured against a baseline (4o, 0.769) that
is itself 1.1 points above answering A. *Caveat: the paper does not state a
choice-shuffling step, so this assumes it scored the ordering it published.*

**GSMA leaderboard, and a second problem.** Inverting the published binomial
stderr on the `srsranbench` column gives the sample count each model was scored
on. It clusters in two places: **55 of 88 models sit at n ≈ 149** and **33 at
n ≈ 1500**. The leaderboard is mixing `ot-lite` (150 rows) and `ot-full` (1,502
rows) entries in a single ranked column.

That matters because the position prior is *worse* on the lite subset:
`always A` scores **124/150 = 82.67%** there. Consequently:

| model | srsranbench | n≈ | vs its own constant |
|---|---:|---:|---:|
| OTel-2.0-LLM-31B-IT (AT&T) | 0.9154 | 1502 | +15.7 |
| OTel-LLM-8.3B-QnA (AT&T) | 0.8968 | 1502 | +13.9 |
| TeleLLM (China Telecom) | 0.8196 | 1502 | +6.1 |
| mimo-v2-flash (Xiaomi) | 0.8667 | 150 | +4.0 |
| gemini-3.1-pro / claude-opus-4.6 / kimi-k2.5 | 0.8467 | 150 | +2.0 |
| gemini-3-flash-preview | 0.8333 | 150 | +0.7 |
| gpt-5.2, claude-opus-4.5 | 0.8267 | 150 | **0.0 — exactly the constant** |
| several others | 0.82 / 0.8133 / 0.7733 | 150 | **negative** |

The GSMA eval README's "Reference performance: Gemini 3 Flash Preview achieves
82.8% accuracy on the full dataset" is not consistent with its own leaderboard
row, whose stderr implies 150 samples — and 82.8% is, to within noise, the lite
constant.

**What this sets as the target.** The only two published numbers that are
unambiguously doing real work are ORANSight-Gemma-2 27B (0.911) and AT&T's
OTel-2.0 (0.9154), both roughly +15 over the constant. Anything this track
reports should be stated as *points above 75.83%*, and every run should carry
its permuted twin. See `--permute` in `run_baseline.py`.

## Files

| file | what it does |
|---|---|
| `profile_data.py` | reads the parquet the way the harness does, writes `data/srsranbench/test.jsonl`, prints the profile above |

Run it with the local venv (`pyarrow` only):

```bash
.venv-srsran/bin/python infra/srsranbench/profile_data.py --samples 4
```

## Open decisions

1. Baseline first: Qwen3-8B under the exact Inspect template, `cot=False`, to see
   where it sits against the 75.83% constant and the 82.8% reference.
2. Whether the specialist gets retrieval over the srsRAN source tree. The
   questions name real identifiers (`downlink_processor_notifier`,
   `high_rate_bg1_i6`, `make_rlc_entity_creation_message`), so grounding is
   possible — the codebase is public and the answer is in it. This is the honest
   version of the track and the one that would transfer.
3. Whether to report a permuted-choice number alongside the official one, as the
   integrity control. Recommended: it is the only way to show the score is not
   the prior.

# TeleLogs prompt optimization study · Qwen3-8B

## Selected result

| Evaluation | Correct | Total | Accuracy |
|---|---:|---:|---:|
| Raw model official baseline | 315 | 864 | 36.46% |
| Structured zero-shot dev | 223 | 256 | 87.11% |
| Hybrid selected dev | 233 | 256 | 91.02% |
| Hybrid untouched holdout | 435 | 479 | 90.81% |
| Hybrid frozen official | 779 | 864 | 90.16% |
| Calibrated prompt dev (matched, non-GEPA 224) | 209 | 224 | 93.30% |
| Calibrated prompt secondary holdout | 450 | 479 | 93.95% |

The official result remains frozen and was not used to select any later prompt.
The latest prompt work is compared on 224 dev examples that do not overlap the
32 examples used as GEPA validation.

## Latest matched dev-224 comparison

| Method | Correct | Total | Accuracy | Decision |
|---|---:|---:|---:|---|
| Historical hybrid prompt | 204 | 224 | 91.07% | Reference |
| GEPA + DeepSeek V4 Pro, full classifier | 193 | 224 | 86.16% | Rejected |
| GEPA + DeepSeek V4 Flash, full classifier | 194 | 224 | 86.61% | No gain |
| Hybrid GEPA + DeepSeek V4 Flash | 203 | 224 | 90.62% | No gain |
| Four contrastive residual demos | 196 | 224 | 87.50% | Rejected |
| Numeric calibrated residual prompt | 207 | 224 | 92.41% | Improved |
| Boolean calibrated residual prompt | **209** | **224** | **93.30%** | **Selected** |
| Selected prompt + GEPA Flash | 209 | 224 | 93.30% | No further gain |

The selected prompt improves the matched hybrid reference by 5/224 examples,
or 2.23 percentage points. It routes 131 proof-level cases directly and asks
Qwen to diagnose and explain the remaining 93 residual cases.

On the secondary 479-example holdout, it improves the old hybrid from 435 to
450 correct (+15 examples, +3.14 percentage points). This is supporting evidence,
not an untouched final estimate: aggregate threshold stability had already been
inspected on this split. The official test remains frozen and was not rerun.

## Selected prompt: what changed

The C3 threshold (142.5 Mbps) was selected on the train split only. For each
residual example, the calculator adds a boolean `c3_strong_advantage` feature to
the prompt. It does not return the diagnosis. Qwen still chooses C1/C3/C4/C6 and
emits the visible evidence trace. The boolean avoids Qwen's observed arithmetic
errors such as claiming that 39.55 or 18.28 is greater than 142.5.

On dev-224, Qwen followed the calibrated policy in 224/224 cases; on secondary
holdout it did so in 479/479. Every response contains a visible reasoning trace.
The remaining 15 dev errors therefore come from the limits of the current
evidence representation, not from failure to follow the prompt. C1 is 22/28,
C3 26/28, C4 26/28, and C6 23/28; C2/C5/C7/C8 remain 28/28 each.

## DeepSeek reflection comparison

| Reflection model | GEPA calls | Input tokens | Output tokens | Estimated cost | Outcome |
|---|---:|---:|---:|---:|---|
| DeepSeek V4 Pro | 20 | 34,504 | 7,755 | $0.0218 | Cleaner prose, no score gain |
| DeepSeek V4 Flash | 10 | 17,271 | 4,690 | $0.0037 | Same score at much lower cost |
| Flash on selected calibrated prompt | 11 | 19,999 | 7,787 | $0.0050 | Original prompt retained |

Costs are estimates from the public API rates observed for this run. Flash is
the better reflection default here: Pro produced more coherent candidates, but
neither improved validation accuracy, while Flash was around six times cheaper
in the direct full-classifier comparison.

The hybrid program sent 488/864 official cases through deterministic verified
gates and 376/864 through the DSPy Qwen predictor. Mean end-to-end latency was
0.549 seconds per example with 32 workers. There were no request errors.

## Official per-label result

| Label | Correct | Total | Accuracy |
|---|---:|---:|---:|
| C1 | 82 | 108 | 75.93% |
| C2 | 108 | 108 | 100.00% |
| C3 | 54 | 108 | 50.00% |
| C4 | 108 | 108 | 100.00% |
| C5 | 108 | 108 | 100.00% |
| C6 | 103 | 108 | 95.37% |
| C7 | 108 | 108 | 100.00% |
| C8 | 108 | 108 | 100.00% |

One C3 response was semantically a residual C3 decision but failed to emit a
class in the `answer` field; it remains counted wrong. The official result was
not used for further prompt selection.

## Optimizer observations

- LabeledFewShot: 55/64 (85.94%), rejected.
- BootstrapFewShot: 53/64 (82.81%), rejected.
- GEPA: did not beat the original structured prompt; the optimizer retained the
  baseline candidate.
- A residual-only prompt over-selected C3 and scored 216/256 (84.38%), rejected.
- Exact routing plus the validated full signature generalized consistently from
  dev (91.02%) to holdout (90.81%) and official test (90.16%).
- GEPA frequently proposed longer or internally contradictory priority rules.
  The held-out validation gate rejected them and kept the calibrated prompt.
- Contrastive few-shot improved C1 but biased Qwen toward C1, reducing C3 and C4.
- All train C3 cases that collide with the C4 or C6 necessary gates are marked
  `quarantine`, so they are not safe causal demonstrations. The next useful gain
  requires richer verified facts or expert-reviewed contrastive examples rather
  than more prompt wording.

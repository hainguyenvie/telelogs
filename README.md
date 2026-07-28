# TeleLogs reasoning and DSPy experiments

This repository contains the reproducible code for a TeleLogs diagnostic
pipeline built around an untouched `Qwen/Qwen3-8B` served by vLLM on H200.

The selected high-accuracy system is hybrid:

- Python parses the raw drive-test and engineering tables;
- deterministic calculations produce compact verified facts;
- exact/sufficient gates directly resolve C2, C5, C7, C8 and strong C1 cases;
- DSPy/Qwen handles residual C1/C3/C4/C6 cases using a train-calibrated prompt;
- every prediction includes an auditable visible reasoning field.

Reference results:

| Evaluation | Accuracy |
|---|---:|
| Raw Qwen3-8B official baseline | 315/864 = 36.46% |
| Earlier hybrid frozen official | 779/864 = 90.16% |
| Latest calibrated matched dev | 209/224 = 93.30% |
| Latest calibrated secondary holdout | 450/479 = 93.95% |

The 93.95% result is not an official/pristine test score. The calibrated
pipeline has not been rerun on official after selection.

## Start here

- [Exact commands to reproduce the runs](REPRODUCING.md)
- [Detailed answer-trace and reasoning pipeline](artifacts/telelogs-dspy/CURRENT_HIGH_ACCURACY_REASONING_PIPELINE.md)
- [Experiment report](artifacts/telelogs-dspy/report.md)
- [DSPy implementation](infra/telelogs-dspy/telelogs_program.py)
- [H200/vLLM deployment](infra/telelogs-bench4/README.md)

## Data policy

TeleLogs asks users not to publicly share or re-upload its dataset. Benchmark
data, prepared facts and per-example predictions are intentionally excluded
from Git. Obtain the dataset through its official distribution, then rebuild
the deterministic inputs with:

```bash
python infra/telelogs-dspy/prepare_repro_data.py \
  --train-json data/raw_train_2400/train.json \
  --train-output artifacts/telelogs-dspy/data/telelogs_train_splits.jsonl \
  --official-json data/official_test_864/test.json \
  --official-output artifacts/telelogs-dspy/data/telelogs_official_facts.jsonl
```

## Important interpretation

This repository separates three measurements:

1. raw model accuracy;
2. calculator-assisted model accuracy;
3. hybrid code-and-model system accuracy.

The 90–94% results belong to category 3. They should not be reported as Qwen
reading the full raw question and independently reasoning end to end.

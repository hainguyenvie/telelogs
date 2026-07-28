# Reproducing the high-accuracy TeleLogs DSPy runs

## What is being reproduced

The latest selected pipeline is `CalibratedHybridTeleLogsProgram`:

- Qwen checkpoint: untouched `Qwen/Qwen3-8B`;
- inference server: vLLM on one H200;
- DSPy: `3.2.1`;
- temperature: `0.0`;
- native Qwen thinking: disabled;
- visible `reasoning` field: enabled;
- direct code routes: C2, C5, C7, C8 and strong C1;
- residual model labels: C1, C3, C4 and C6;
- train-calibrated C3 advantage threshold: `142.5 Mbps`.

Reference results:

| Run | Result | Meaning |
|---|---:|---|
| Calibrated matched dev | 209/224 = 93.30% | 28 examples/label after excluding the 4 examples/label used by GEPA validation |
| Calibrated secondary holdout | 450/479 = 93.95% | Exploratory holdout, not a pristine final test |
| Earlier hybrid official | 779/864 = 90.16% | Frozen official result; it predates the calibrated boolean |

The calibrated pipeline has not been rerun on official after method selection.
Do not report 93.95% as the official score.

## Dataset requirement

TeleLogs asks users not to redistribute/re-upload the benchmark. This repository
therefore does not contain `train.json`, `test.json`, prepared facts or per-row
predictions.

Obtain the dataset through its official distribution and place it locally as:

```text
data/raw_train_2400/train.json
data/official_test_864/test.json
```

The expected files contain 2,400 train rows and 864 official rows. Each row must
have `question` and `answer`.

## Script map

| Script | Where it runs | Purpose |
|---|---|---|
| `infra/telelogs-dspy/prepare_repro_data.py` | Local machine or H200 dev pod, CPU only | Raw question → deterministic compact facts |
| `infra/telelogs-dspy/calibrate_residual_threshold.py` | Local/H200 dev/client pod, CPU only | Reproduce threshold 142.5 from train residual examples |
| `infra/telelogs-bench4/pod.yaml` | Applied from H200 dev pod | Create H200 model-serving pod |
| `infra/telelogs-bench4/service.yaml` | Applied from H200 dev pod | Internal vLLM service |
| `infra/telelogs-bench4/client_pod.yaml` | Applied from H200 dev pod | Create CPU job-runner for DSPy evaluation |
| `infra/telelogs-bench4/serve_qwen3_8b.sh` | Submitted to GPU pod job directory | Start Qwen3-8B vLLM |
| `infra/telelogs-dspy/setup_dspy.sh` | Submitted to client job directory | Create persistent DSPy environment |
| `infra/telelogs-dspy/run_calibrated_prompt_v2.sh` | Submitted to client job directory | Reproduce dev-224 93.30% run |
| `infra/telelogs-dspy/run_calibrated_prompt_holdout.sh` | Submitted to client job directory | Reproduce holdout-479 93.95% run |
| `infra/telelogs-dspy/run_hybrid_official.sh` | Submitted to client job directory | Reproduce earlier frozen official hybrid run |

## Step 1 — Rebuild deterministic facts

From the repository root:

```bash
python infra/telelogs-dspy/prepare_repro_data.py \
  --train-json data/raw_train_2400/train.json \
  --train-output artifacts/telelogs-dspy/data/telelogs_train_splits.jsonl \
  --official-json data/official_test_864/test.json \
  --official-output artifacts/telelogs-dspy/data/telelogs_official_facts.jsonl
```

This step uses:

- `scripts/exhaustive_review_v11.py` for independent raw table parsing;
- `scripts/generate_reasoning_v13.py` for evidence/gate construction;
- `scripts/generate_reasoning_v14.py` for compact facts;
- the same template-group split logic as `prepare_data.py`.

The gold `answer` is copied only into the scorer target field. It is not passed
to the calculator or Qwen.

Expected split sizes:

```text
train   1,441
dev       480
holdout   479
official  864
```

## Step 2 — Verify the C3 calibration

```bash
python infra/telelogs-dspy/calibrate_residual_threshold.py \
  artifacts/telelogs-dspy/data/telelogs_train_splits.jsonl
```

Expected output:

```json
{
  "calibration_split": "train",
  "residual_examples": 567,
  "grid_step_mbps": 0.5,
  "selected_threshold_mbps": 142.5,
  "correct": 461,
  "accuracy": 0.8130511463844797,
  "tied_best_thresholds_mbps": [142.5]
}
```

## Step 3 — Restore the stopped H200 pods

These commands apply to the project-specific `H200_Tensara` environment. The
TeleLogs pods are intentionally stopped at the time of writing; applying these
manifests starts them again.

First upload the manifests and code to the persistent shared directory:

```bash
ssh H200_Tensara \
  'mkdir -p ~/projects/telelogs-bench4/dspy/{code,data,results} ~/projects/telelogs-bench4/{jobs,client_jobs}'

tar czf - \
  infra/telelogs-dspy \
  infra/telelogs-bench4 \
  scripts/exhaustive_review_v11.py \
  scripts/generate_reasoning_v13.py \
  scripts/generate_reasoning_v14.py \
  | ssh H200_Tensara \
      'mkdir -p ~/projects/telelogs-repro && tar xzf - -C ~/projects/telelogs-repro'

scp artifacts/telelogs-dspy/data/telelogs_train_splits.jsonl \
  H200_Tensara:~/projects/telelogs-bench4/dspy/data/

scp artifacts/telelogs-dspy/data/telelogs_official_facts.jsonl \
  H200_Tensara:~/projects/telelogs-bench4/dspy/data/

ssh H200_Tensara \
  'cp ~/projects/telelogs-repro/infra/telelogs-dspy/*.py ~/projects/telelogs-bench4/dspy/code/'
```

Apply the GPU pod, internal service and CPU client pod:

```bash
scp infra/telelogs-bench4/{pod.yaml,service.yaml,client_pod.yaml} \
  H200_Tensara:~/projects/telelogs-bench4/

ssh H200_Tensara \
  'kubectl apply -f ~/projects/telelogs-bench4/pod.yaml &&
   kubectl apply -f ~/projects/telelogs-bench4/service.yaml &&
   kubectl apply -f ~/projects/telelogs-bench4/client_pod.yaml'
```

The checked-in serving manifest expects the already persistent model cache and
vLLM environment at:

```text
/workspace/telelogs/cache/hf
/workspace/telelogs/venv
```

On another cluster, change these paths in `pod.yaml` and
`serve_qwen3_8b.sh`.

## Step 4 — Start Qwen3-8B

```bash
scp infra/telelogs-bench4/serve_qwen3_8b.sh \
  H200_Tensara:~/projects/telelogs-bench4/jobs/

ssh H200_Tensara \
  'tail -f ~/projects/telelogs-bench4/done/serve_qwen3_8b.log'
```

The endpoint used by DSPy inside the cluster is:

```text
http://telelogs-bench4-vllm:8000/v1
```

The served model name is:

```text
Qwen/Qwen3-8B
```

Do not expose the service publicly.

## Step 5 — Set up DSPy in persistent storage

Submit the setup script to the client runner:

```bash
scp infra/telelogs-dspy/setup_dspy.sh \
  H200_Tensara:~/projects/telelogs-bench4/client_jobs/

ssh H200_Tensara \
  'tail -f ~/projects/telelogs-bench4/client_done/setup_dspy.log'
```

The environment is created at:

```text
/workspace/telelogs-bench4/dspy/.venv
```

## Step 6 — Reproduce 93.30% matched dev

```bash
scp infra/telelogs-dspy/run_calibrated_prompt_v2.sh \
  H200_Tensara:~/projects/telelogs-bench4/client_jobs/

ssh H200_Tensara \
  'tail -f ~/projects/telelogs-bench4/client_done/run_calibrated_prompt_v2.log'
```

The actual inference command inside that wrapper is:

```bash
python run_stage.py calibrated_prompt \
  --run-name calibrated_prompt_boolean_dev224 \
  --eval-per-label 28 \
  --eval-offset-per-label 4 \
  --workers 16 \
  --max-tokens 1200 \
  --c3-advantage-threshold-mbps 142.5
```

Expected summary:

```text
correct = 209
total = 224
accuracy = 0.9330357142857143
deterministic_gate = 131
dspy_calibrated_residual_lm = 93
```

Results are written to:

```text
/workspace/telelogs-bench4/dspy/results/calibrated_prompt_boolean_dev224/
```

## Step 7 — Reproduce 93.95% secondary holdout

```bash
scp infra/telelogs-dspy/run_calibrated_prompt_holdout.sh \
  H200_Tensara:~/projects/telelogs-bench4/client_jobs/

ssh H200_Tensara \
  'tail -f ~/projects/telelogs-bench4/client_done/run_calibrated_prompt_holdout.log'
```

Expected summary:

```text
correct = 450
total = 479
accuracy = 0.9394572025052192
deterministic_gate = 290
dspy_calibrated_residual_lm = 189
```

Results are written to:

```text
/workspace/telelogs-bench4/dspy/results/calibrated_prompt_boolean_holdout479/
```

## Step 8 — Inspect one prediction

Each `predictions.jsonl` row contains:

```text
source_index
target
answer
correct
reasoning
decision_path
elapsed_seconds
error
```

Interpret `decision_path` as:

```text
deterministic_gate
    answer and reasoning were produced by Python

dspy_calibrated_residual_lm
    Python supplied compact facts and the calibrated boolean;
    Qwen produced answer and visible reasoning
```

See `artifacts/telelogs-dspy/CURRENT_HIGH_ACCURACY_REASONING_PIPELINE.md` for
the complete trace logic and limitations.

## Reproducibility caveats

- Exact floating-point output should be stable because the parser/calculator is
  deterministic.
- Qwen inference uses temperature 0, but vLLM/kernel differences can still
  create rare output differences.
- The H200 wrappers are site-specific and use Kubernetes shared-file runners
  because `pods/exec` is blocked.
- The 93.95% holdout is supporting evidence, not an untouched final estimate.
- The high score is code-assisted. It must not be presented as pure raw-model
  reasoning accuracy.

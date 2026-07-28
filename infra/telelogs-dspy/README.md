# DSPy TeleLogs prompt study

This track evaluates the untouched `Qwen/Qwen3-8B` checkpoint served by vLLM.
Qwen native thinking is disabled for predictable latency, while the DSPy output
signature retains a visible, audit-ready `reasoning` field.

## Data hygiene

- The 2,400 training examples are split by normalized question-template group,
  not by row, into train/dev/holdout.
- Optimizer examples and development evaluation never share a template group.
- The official 864-example test is converted to calculator-verified facts and
  evaluated only after the candidate is frozen.
- Official labels are scorer targets only and are never included in model input.

## Selected program

`CalibratedHybridTeleLogsProgram` resolves C2/C5/C7/C8 exact gates and the
sufficient C1 weak-RSRP witness in deterministic code. For residual C1/C3/C4/C6
cases, it adds a train-calibrated C3 boolean feature to the prompt and calls the
structured DSPy predictor. Qwen still emits both the diagnosis and visible
reasoning. The reproducible threshold sweep is in
`calibrate_residual_threshold.py`.

The selected dev-224 run scores 209/224 (93.30%); its secondary holdout run
scores 450/479 (93.95%). The frozen official score, produced by the earlier
hybrid program, remains 779/864 and was not rerun during later prompt work.

Run artifacts live under `artifacts/telelogs-dspy/results/`. The static dashboard
is available through the existing tunnel at:

```text
http://127.0.0.1:18081/dspy.html
```

The server-side environment is persistent at
`/workspace/telelogs-bench4/dspy/.venv`; jobs use the shared-file client runner,
not Kubernetes exec.

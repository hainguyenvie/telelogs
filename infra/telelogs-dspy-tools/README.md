# DSPy label-neutral tool experiments

This track removes answer-producing gates from TeleLogs inference. The raw
question is parsed once into a request-scoped `CaseContext`; six deterministic
tools expose only measurements and row evidence. Qwen3-8B, invoked through
DSPy, remains the only component that selects C1--C8.

Methods:

- `b0_raw`: one DSPy raw-question prediction, no tools;
- `b1_all_tools`: execute all six neutral tools, then one DSPy diagnosis call;
- `b2_planned_tools`: one DSPy planning call selects tools, then one DSPy
  diagnosis call interprets their observations;
- `b3_react_tools`: `dspy.ReAct` agentic function calling — the model chooses
  each tool, reads its observation, decides the next call, then diagnoses.

Prompt optimization (`optimize_tool_program.py`, wrappers `run_opt_bootstrap.sh`
and `run_opt_gepa.sh`) trains and validates only on the train split and saves a
program-state JSON under `results/optimized/<run>/`. Evaluate an optimized state
with the shared runner via
`run_tool_experiment.py --compiled b3_react_tools=<path>/program.json`, keeping
dev for selection and holdout for one final confirmation. The GEPA feedback
names the measurement scope that contains the decisive evidence but explicitly
forbids a fixed C1/C3/C4/C6 priority ranking (the known failure mode).

Run the complete parser/tool leakage audit locally:

```bash
PYTHONPATH=infra/telelogs-dspy-tools \
python infra/telelogs-dspy-tools/audit_tools.py \
  --data data/raw_train_2400/train.json
```

The live dashboard reads `dashboard/data/tool_progress.json` and is available
through the existing private tunnel at `/tools.html`.

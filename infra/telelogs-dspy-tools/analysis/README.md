# Offline analysis of stored predictions

These run against `predictions.jsonl` files pulled from
`~/projects/telelogs/runs/bench4/dspy-tools/results/<run>/` on the H200 host. They keep
no state of their own: every number in the solution report can be regenerated from
the stored per-case predictions plus `symbolic_reference.py`.

Each script expects the prediction files next to it (or edit the `D = Path(...)`
line at the top). Records are keyed by `source_index`; the answer field is
`answer`, the gold field is `target`.

| script | what it computes |
|---|---|
| `mcn_off.py` | accuracy of each official-864 run + exact two-sided McNemar between pairs |
| `ens_vs_spec.py` | rebuilds the precision-weighted 3-ballot vote (weights fitted on dev-96 only) and tests it against the residual specialist; also prints per-class win/loss |
| `budget_spec.py` | error budget of the shipped single program against `symbolic_reference` and every other run on file, plus the residual-zone table and the all-runs oracle |

Two invariants worth re-checking whenever these are touched: `ens_vs_spec.py` must
reproduce **717/864 = 82.99%** for the ensemble, and `budget_spec.py` must
reproduce **777/864 = 89.93%** for the symbolic reference. Both are the published
numbers; if either drifts, the loader or the weight fit is wrong, not the result.

A failed ballot (empty `answer`) casts no vote — it is not counted as a wrong vote.

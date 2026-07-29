# TeleLogs tool-calling migration · Qwen3-8B

## Selected result

| Evaluation | Correct | Accuracy |
|---|---:|---:|
| b3 ReAct bare, dev-96 | 19/96 | 19.79% |
| **Selected (seed4 checklist + 2 bootstrapped C4 trajectory demos), dev-96** | 67/96 | **69.79%** |
| **Selected, untouched holdout-96 (single confirmation)** | 72/96 | **75.00%** |

Holdout per-label: C2/C4/C5/C7/C8 = 12/12 each, C3 7/12, C1 5/12, C6 0/12.
The program state is `results/optimized/bootstrap46_seeded4_b3/program.json`;
reproduce the two evaluations with `run_dev96_compiled.sh` /
`run_holdout96_compiled.sh`. The official 864 was not touched.

Track goal: replace the answer-producing if/else gates with **label-neutral
measurement tools that the LM itself must call**, so every diagnosis is genuine
model reasoning with an auditable trajectory — the property the gate-routed
hybrid (93.30% matched dev) lacks for technical-report generation.

All splits are hash-based train/dev/holdout (60/20/20) over the raw 2,400-row
train.json, balanced per label. Selection happens on dev-96 (12 per label);
holdout is reserved for one final confirmation; the official 864 stays frozen.
All LM calls go to the self-hosted vLLM Qwen3-8B; GEPA reflection uses the
authorized DeepSeek endpoint only.

## Dev-96 scoreboard (2026-07-29, zero request errors in every run)

| Variant | Correct | Accuracy | Note |
|---|---:|---:|---|
| b0 raw question | 15/96 | 15.62% | no tools |
| b3 ReAct bare | 19/96 | 19.79% | uninstructed agent, C2 over-prediction |
| b2 plan-once | 24/96 | 25.00% | |
| b1 all tools, bare | 32/96 | 33.33% | |
| b1 + seed1 | 37/96 | 38.54% | cannot read mobility fields from the 6-blob prompt (C5 1/12, C7 0/12) |
| b3 + seed2 | 39/96 | 40.62% | REJECTED: val-32 said +12.5pt, dev-96 said −12.5pt |
| **b3 + seed1** | **51/96** | **53.12%** | selected optimizer starting point |
| b3 + seed1 + GEPA (qwen reflection) | 44/96 | 45.83% | improved own val-64 (43.8→48.4%) without transferring to dev |
| b3 + seed3 | 46/96 | 47.92% | C1-direction fix works (C1 1→8/12) but exact gates see-saw down (C5 10→4, C7 8→5) |
| b3 + seed4 checklist | 55/96 | 57.29% | forced 4-line verification stabilizes gates AND C1 (C7 12/12, C5 11/12, C1 8/12); C4 1/12 |
| b3 + seed1 + bootstrap-2 (demos C2,C5) | 57/96 | 59.38% | biased: C5/C8 12/12 but C4/C6 0/12 |
| b3 + seed1 + bootstrap-2 (demos C6,C6) | 57/96 | 59.38% | demo-priority C4,C6; healthiest distribution (min class C3 3/12, C6 0→10, C4 0→5) |
| **b3 + seed4 + bootstrap (demos C4,C4) — SELECTED** | **67/96** | **69.79%** | exact gates perfect 48/48, C4 11/12; the C4 demos absorb C6 (0/12) |
| b3 + seed4 + balanced bootstrap (1 demo each C4, C6) | 59/96 | 61.46% | even one C6 demo dominates: C4 falls to 2/12, C8 to 5/12 |

The demo mechanism is now characterized: whichever residual class appears in
the bootstrapped trajectories wins that class and absorbs its neighbors
(C2/C5 demos → C4/C6 die; C6 demos → C6 10/12 but pull C1/C4; C4 demos → C6
dies). Demo composition acts as a hard prior on the residual zone — exactly the
per-class see-saw the instruction text alone also shows. Selection went to the
dev-96 maximum per protocol; the holdout confirmation (75.00%) shows it
generalizes.

## Next steps

1. C6 is the open structural weakness of the selected program (0/12 with C4
   demos present). Candidate fixes: a C6-specific instruction clause tied to
   the mod-30 measured residues quoted in the checklist; or demo pairs chosen
   as (C4-correct, C6-correct) from the SAME drive geometry so the contrast is
   inside the demos rather than between them.
2. GEPA with a strong reflector (DeepSeek balance is exhausted; the Viettel
   gateway `Qwen/Qwen3.5-122B-A10B-FP8` is the data-safe candidate) on top of
   the selected program, with dev-96 (not val-32/64) as the selection gate.
3. The 53→75% arc came from instruction + 2 demos only; the model still makes
   threshold slips inside trajectories. A verifier pass (the 8-gate schema as
   a checker, never as the answer path) over the emitted reasoning would catch
   the remaining contradiction-style errors for report generation.

The GEPA + DeepSeek Flash attempt aborted: the DeepSeek account balance is
exhausted, and GEPA silently burns rollouts when every reflection call fails
(it swallows the per-iteration exception). The run was killed; Qwen3-8B
self-reflection was used instead and did not transfer.

## What the numbers established

1. **Tools help, agency helps more — but only with a calibrated instruction.**
   The seed transplants the audited gate schema as *verification text* (the four
   exact criteria override narrative; C1/C3/C4/C6 witnesses without a priority
   order, per the known GEPA/v3 trap) and takes the ReAct agent from 19.79% to
   53.12%, while the same text on one-shot b1 reaches only 38.54%.
2. **Small validation slices mislead.** seed2 (C1-witness moved into the
   decisive list + "select and stop") improved val-32 from 43.75% to 56.25% and
   *dropped* dev-96 from 53.12% to 40.62% (C5 10→5, C7 8→2). Candidate
   selection therefore uses dev-96 exclusively.
3. **Known open defects of b3+seed1** (in the trajectories, not the harness):
   C1 recall (1/12; seed1 wrongly lets witness-absence exclude C1) and the C8
   override pathology (model states 105.57 < 160 then picks another class).
4. **Few-shot demos import class bias.** BootstrapFewShot picked two exact-gate
   demos (C2, C5) and erased C4/C6 entirely — net +6.2pt but structurally
   unacceptable; mirrors the contrastive-few-shot result in the prompt-study
   track.

## Infrastructure notes

- The GPU pod must stay on `hgx046`: `/mnt/registry/tensara-home` is node-local
  storage; on `hgx45` the hostPath resolves to an empty directory (busybox probe,
  2026-07-29) and the pod hangs in ContainerCreating on the read-only
  `projects/telelogs` mount.
- Full pipeline: `infra/telelogs-dspy-tools/` (tools, programs, runner,
  optimizer); results mirrored here under `results/`; live dashboard at
  `/tools.html` on the private tunnel.

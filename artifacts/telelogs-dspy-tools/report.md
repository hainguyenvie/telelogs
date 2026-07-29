# TeleLogs tool-calling migration · Qwen3-8B

> **Round-2 note (2026-07-29, seeds v5–v11).** A second round ported the old
> hybrid's full decision flow (exact gates → strong-C1 → calibrated residual
> tie-break) into the ReAct instruction and upgraded the tools with one-step
> aggregates ("tools v2", see below). Best round-2 candidate
> (`bootstrap26_seeded11_b3`) reached **72.92% on dev-96** but only **66.67% on
> the holdout confirmation**, so the round-1 program below **remains selected**
> (69.79% dev / 75.00% holdout). The round-1 numbers were produced with tools
> v1 (commit `0e7c7d7`); from seed8 onward all runs use tools v2. Details in
> "Round 2" below.

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

## Round 2 (2026-07-29): porting the 93% decision flow into the agent

Goal, per the user's direction: the gate-then-LM hybrid's logic held ~90%+, so
recreate that *flow of thinking* inside the tool-calling track instead of
leaving the residual zone to free-form reasoning.

### Symbolic ceiling (train-fit only, no dev/holdout fitting)

Running the old policy as pure Python over the neutral tools:

- Exact gates + strong-C1 witness: **874/874 = 100%** on the hash-split train
  population — every C2/C5/C7/C8 case is caught, none falls to residual.
- The old residual order (advantage≥142.5 → C3, then C4 > C6 > C1) is
  split-dependent: on this split it scores 71.60% on the 567-case train
  residual pool and collapses C6 on dev-96 (1/12; total 86.46%).
- Refit on the train residual pool only: best order is **C6 > C4 > C1 with the
  142.5 threshold unchanged** → 74.96% train residual and **93/96 = 96.88% on
  dev-96** (C3 10, C4 11, everything else 12/12). This is the faithful-execution
  ceiling for the current tools.

### Tools v2

Qwen3-8B cannot reliably scan lists or subtract decimals inside a trajectory,
so the tools now precompute one-step, label-neutral aggregates (no thresholds,
no classes, audit unchanged): `maximum_distance_km`,
`segment_minimum_comparison.minimum_difference_mbps`, `best_noncolocated_gap`,
`equal_residue_pairs`, `rows_below_main_lobe_lower_edge`.

### Execution-bug taxonomy (each cost 10–25 points until fixed)

| # | Bug | Fix that worked |
|---|---|---|
| 1 | "pass/fail" verdicts polarity-invert ("2.774 km … verdict: fail") | say "triggered / not triggered" (the old pipeline's word) |
| 2 | Conditional tool calls skipped; residual observations **fabricated** (0/96 calls to overlap/pci tools while the reasoning "quotes" them) | integrity clause + structural staging (see 3) |
| 3 | All six blobs at once → misreads plus motivated arithmetic ("100.98 is not below 160" to dodge C8; "2.977 > 1 → triggered" then answers C3) | **two-stage calling**: 4 decisive tools, stop-and-answer on trigger; only residual cases call the last two tools. C8 3/12→12/12, C2→12/12 |
| 4 | Boundary slip ("changes 3 times, equal to 3 → not triggered") | "a count of exactly 3 already triggers" |
| 5 | Negative-dB comparisons flip ("−2.19 not worse than −3 → no C4"; "−88.89 weaker than −90 → C1") | signed-inequality coaching; ban "worse/weaker/better" |
| 6 | "Write all four residual lines, first satisfied wins" → model answers the **last** satisfied line (geometry/C1, true in ~51% of cases) | stop at first satisfied rule; later rules must not be mentioned (seed10 → seed11) |

Even after all fixes, two slips persist at low rate: repetition momentum (a
correct inequality followed by the wrong verdict word after seven "not
triggered" lines) and stage-2 skipping (imitating the gated demo's 4-call
trajectory shape). These are the direct targets for a consistency-verifier
retry layer.

### Round-2 dev-96 scoreboard (zero request errors everywhere)

| Variant | Accuracy | Note |
|---|---:|---|
| seed5 (policy port, "pass/fail") | 44.79% | verdict polarity flips break C2/C8 |
| seed5 + bootstrap (C6,C1 demos) | 67.71% | C6 0→9/12 — the ported order works |
| seed6 (triggered/not) | 37.50% | pure-run variance; C5 collapsed |
| seed6 + bootstrap (C4,C6 demos) | 69.79% | C2/C5/C6/C7 12/12; C4 0 — residual block fabricated |
| seed7 (all six tools forced) | 44.79% | b1 syndrome returns; C2,C7 demos → 42.71% |
| seed8 (tools v2, named fields) | 62.50% | best pure so far; C8 flips remain |
| seed8 + bootstrap | 38.54% | demo lottery: a C3-answering demo floods C3 |
| seed9 (two-stage calling, C6>C4>C1) | 62.50% | gates fixed; stage-2 skipping appears |
| **seed9 + bootstrap (C2,C6 demos)** | **72.92%** | gates 48/48, C1 11/12 |
| seed10 (write-all-lines) | 62.50% / 65.62% / 34.38% | recency bias → last satisfied line wins |
| seed11 (stop-at-first + signed inequalities) | 63.54% | |
| **seed11 + bootstrap (C2,C6 demos)** | **72.92%** | ties seed9; healthier C3 (8/12) |
| seed11 + balanced 4 residual demos | 65.62% | no correct C4 trajectory bootstrappable; C1 1/12 |

### Holdout confirmation and selection decision

`bootstrap26_seeded11_b3` (dev-96 72.92%) was confirmed once on holdout-96:
**64/96 = 66.67%** (C2/C5/C7 12/12, C8 11, C1 10 — but C3 4, C6 3, C4 0). The
dev advantage did not transfer; the round-1 program (69.79% dev / **75.00%**
holdout) generalizes better and **remains selected**. Residual-zone execution
is the unstable component: dev↔holdout swings of ±6 points come almost
entirely from C1/C3/C4/C6 execution slips, not from the gates, which held
47–48/48 on every confirmed run.

## Next steps

1. **Consistency-verifier retry layer** — now the highest-leverage move. The
   two persistent bugs (inequality written correctly but verdict word flipped;
   stage-2 concluded without both residual observations) are text-internal or
   trajectory-structural contradictions. A checker that flags only such
   self-contradictions and asks the model to re-decide (never supplying a
   label, never comparing against the symbolic answer) should recover much of
   the 72.9→96.9 dev gap AND cut the dev↔holdout variance.
2. Native-thinking probe: all runs so far use `enable_thinking: False`; a
   thinking-mode variant of the seed11 program is the cheapest test of whether
   procedure-following, not knowledge, is the binding constraint.
3. GEPA with a strong reflector (DeepSeek balance is exhausted; the Viettel
   gateway `Qwen/Qwen3.5-122B-A10B-FP8` is the data-safe candidate) on top of
   the selected program, with dev-96 (not val-32/64) as the selection gate.
4. The symbolic refit (C6>C4>C1 @ 142.5, 96.88% dev-96) doubles as the natural
   process-reward/verifier reference for report generation — usable to audit
   emitted reasoning, never as the answer path.

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

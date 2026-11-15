# Stage 2 synthetic ablation, variant V_no_refine

Written by `evaluation/experiments/Experiment_3_synthetic_ablation/scripts/run_stage2_synthetic_ablation.py`. One folder of the ablation in `..` (see `../README.md`).

- GLASS commit: `821cc000c5f7f9da3197401c15020b539a600045` (v3.3, no local changes under `goal_to_parameters/`)
- Switches: `{"use_log_profile": true, "use_context": true, "use_kb": false, "max_iterations": 1, "legacy_refinement": false}`
- Configurations: LoanApp_G1, LoanApp_G2, Procure2Pay_G1, LoanApp_G1G2, Procure2Pay_G2, Procure2Pay_G1G2; 5 repetitions; evaluation seeds 42..51
- Records: 30; first / last run started 2026-11-11T10:35:54+00:00 / 2026-11-11T15:29:13+00:00
- This invocation: 2026-11-11T10:20:59+00:00 to 2026-11-11T15:49:23+00:00
- Each run is `run_stage2_batch.run_rep` with the switches above (see the header of `evaluation/experiments/Experiment_3_synthetic_ablation/scripts/run_stage2_synthetic_ablation.py`); `stage2_summary.csv` and `stage2_per_config.csv` are the v3.2 batch's summaries plus `stage2_reporting.py`'s columns.
- Check: `python verify_stage2_results.py --results evaluation/experiments/Experiment_3_synthetic_ablation/results/stage2/V_no_refine --only LoanApp_G1,LoanApp_G2,LoanApp_G1G2,Procure2Pay_G1,Procure2Pay_G2,Procure2Pay_G1G2` (verify_stage2_results.py is not part of this release; `verify_synthetic_ablation.py` checks all variants)

## Finding: GLASS utilization metric

This batch ran on a GLASS version whose `Resource Utilization` already uses each resource's available working time and is evaluated for the role the KPI names (commit 5112fbc). The earlier definition, busy time divided by the whole simulated span averaged over all resources, rose when a backlog was cleared; that made the loop discard correct fixes in batch v1. Discarded-better-initial counts below have other causes here.

The records keep both values: GLASS's in `evaluation.<phase>.kpis[*].baseline_mean / proposed_mean`, the batch's pool utilization of working time in `evaluation.<phase>.utilization_rule`. The summary CSV shows them side by side (`glass_utilization_*`, `utilization_*`).

`loop_discarded_better_initial` (summary columns; per configuration: `reps_loop_discarded_better_initial`) marks runs whose loop kept a final worse than iteration 1. Across all runs: 2 run(s); in 0 of them GLASS's utilization metric rose while the pool's utilization fell.

## Finding: utilization KPI without numeric target

The validated utilization KPI tells GLASS to *minimize* utilization ("bring its utilization down to a sustainable level without idling the team") but carries no target value. GLASS's rule for minimize counts any decrease as improved, and its loop stops at the first iteration in which all target KPIs move the right way.

The batch's limit (below 0.85 of available working time, and lower than the baseline) is an evaluation rule that GLASS never received. Both verdicts are reported side by side:
- `util_improved_glass`: GLASS's own verdict for the utilization KPI, computed with GLASS's utilization value in this batch.
- `util_improved_batch`: the batch's verdict.
- `all_kpis_improved_glass_rule`: next to `all_kpis_improved`, using GLASS's rules for every KPI.

Runs with a utilization KPI (status ok):

| Phase | Runs | Improved, GLASS's rule | Improved, batch rule (< 0.85) | GLASS improved, batch not |
|---|---|---|---|---|
| initial | 0 | 0 | 0 | 0 |
| final | 0 | 0 | 0 | 0 |

A numeric target in the KPI text (e.g. below 85 % of available working time) is a protocol variant for a possible later batch; it is not applied here.

## Sensitivity: utilization limit (reported, not scored)

Utilization verdicts "improved" (below the limit and lower than the baseline), from the stored per-seed pool utilizations, with the scored limit 0.85 and with 0.90:

| Phase | Runs with a utilization KPI | Improved at 0.85 (scored) | Improved at 0.90 |
|---|---|---|---|
| initial | 0 | 0 | 0 |
| final | 0 | 0 | 0 |

## Finding: constraints re-derived in every iteration

GLASS rebuilds the hard-constraints block of the patch prompt in every iteration: its summariser (`build_context_summary`, temperature 0.1) runs on the whole chat, and `inject_feedback_message` adds each iteration's simulation feedback to that chat as a user message. The feedback therefore also reaches the summariser. In 0 of 0 later iterations the block differs from iteration 1, mostly in wording.

In 3 of 30 blocks (3 of them in iteration 1), the summariser still read "+3 per internal role" as a global cap of 3 additional people across all roles, despite the rule added in `bb6cd17`.

## Post-processing

1. `complete_stage2_records.py` (not part of this release): for runs whose iteration 1 produced no scenario, the batch had stopped before evaluating the final (the loop's best iteration). This step evaluates that final with the batch's own phase evaluation (same functions, seeding and cached baseline; no LLM calls), scores the initial as not valid, and marks the record (`status_at_run`, `initial_proposal: false`, `completed_by`). Completed runs: none.
2. `evaluation/experiments/experiment_2_valid_improving_kpis/scripts/stage2_reporting.py`: the reporting columns and the two sections above.
3. `verify_stage2_results.py` (not part of this release): independent verification (`verification_report.md`).

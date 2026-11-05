# Loop replications experiment: 1 vs 5 seeds per refinement iteration (LoanApp)

GLASS's refinement loop decides after each iteration whether the proposal improved the KPIs, met the targets and
should be refined further. Until now it judged each iteration on one simulation run of the baseline and one of the
proposal (seed 42). This experiment compares that with judging each iteration on the mean of five paired runs.

| | seeds1 | seeds5 |
|---|---|---|
| Folder | `seeds1/` | `seeds5/` |
| Loop seeds per iteration | 1 (seed 42, the default) | 5 (seeds 42–46, paired) |
| GLASS commit (records) | [`5643d04`](../../versions/v3.2_to_loop_seeds.patch) | [`5643d04`](../../versions/v3.2_to_loop_seeds.patch) |
| Run (UTC) | 2026-11-04 21:25–21:56 | 2026-11-04 21:56–22:26 |

- **GLASS:** [`5643d04`](../../versions/v3.2_to_loop_seeds.patch) = v3.2 plus the option `run_iterative_evaluation(..., loop_seeds=k)`.
  - **What changes with k > 1:** the loop simulates the baseline and each proposal on k paired seeds and
    averages their KPIs (value and numeric details, e.g. the per-role utilization).
  - **What uses the means:** improved / worsened / unchanged, achieved targets, the score, the stop rule, the best
    iteration and the feedback, with the same thresholds. The feedback says that its values are means.
  - **Default:** `loop_seeds=1` is the single-run loop. `tests/test_loop_seeds.py` checks the calls, results,
    feedback and selection against it. A side-by-side run of the previous and the new
    `iterative_evaluator.py` on 200 random KPI series also gave identical results.
- **Batch:** `scripts/run_stage2_batch.py`, SHA-256 `480af8b8e869da1840a36dbe471c79733660de9c593a2cf78706ce908375e152`.
  - It is the v3.2 RAG comparison's script (`69111005…`) plus plumbing only: `--loop-seeds N` passes N to the
    loop, and the record gets `loop_seeds`.
  - With N > 1, the record's `loop.baseline_kpis_single_run` and the iterations' `single_run_evaluation` hold the
    means (each KPI's `details.n_seeds` says over how many seeds).
- **Inputs:**
  - **Configurations:** the seven LoanApp configurations of `setup/stage2_config.json` (SHA-256 `847334ec…`, as
    in v3, v3.1 and v3.2), 5 repetitions each.
  - **KPIs:** the same KPI objects as before.
  - **Final evaluation:** the same, on 10 seeds (42–51).
- **Runs:** 4 workers in both arms, one arm after the other, on the same machine without standby (Windows System
  log). All 70 runs ended with status `ok`; no run needed the completion step.

## Steps

From the repository root (`ONLY` = the seven LoanApp configuration ids, comma-separated):

```
python evaluation/loanapp_p2p/scripts/run_stage2_batch.py --only $ONLY --workers 4 --loop-seeds 1 --out evaluation/loanapp_p2p/results/stage2_loop_seeds/seeds1
python evaluation/loanapp_p2p/scripts/run_stage2_batch.py --only $ONLY --workers 4 --loop-seeds 5 --out evaluation/loanapp_p2p/results/stage2_loop_seeds/seeds5
python evaluation/loanapp_p2p/scripts/stage2_reporting.py --results evaluation/loanapp_p2p/results/stage2_loop_seeds/<arm>
python evaluation/loanapp_p2p/scripts/verify_stage2_results.py --only $ONLY --results evaluation/loanapp_p2p/results/stage2_loop_seeds/<arm>
python evaluation/loanapp_p2p/scripts/compare_loop_seeds.py
python evaluation/loanapp_p2p/scripts/make_loop_seeds_workbook.py
```

- **Verifier:** every check is OK in both arms (`<arm>/verification_report.md`). It got `--only` (plumbing only)
  so that it checks the configurations the batch ran.
- **Averaging check:** in seeds5, 480 of the 525 baseline KPI values in the loop are means over 5 seeds. The
  others are per-activity waiting times averaged over the seeds in which the activity occurred (1–3). All 14
  seeds5 feedback texts state that their values are means.

## Results

Per configuration and tests: `loop_seeds_comparison.md` / `.csv`; per run: `loop_seeds_runs.csv`; workbook
`../GLASS_loop_seeds.xlsx`.

| Measure (35 runs per arm) | seeds1 | seeds5 | Fisher p |
|---|---|---|---|
| Valid final | 34 | 34 | 1.000 |
| All KPIs improved final | 32 | 33 | 1.000 |
| Valid and improved final | 32 | 32 | 1.000 |
| Utilization target achieved final (20 runs with a utilization KPI) | 19 | 19 | 1.000 |
| Loop discarded a better initial | 7 | 6 | 1.000 |
| Early stops after iteration 1 | 21 | 25 | 0.450 |
| of which the 10-seed final has not all KPIs improved | 1 of 21 | 1 of 25 | 1.000 |
| Iterations used (mean) | 1.69 | 1.40 | sign test p 0.250 |
| LLM calls per run (mean) | 6.97 | 5.43 | sign test p 0.688 |
| Run time, minutes (median) | 2.36 | 2.41 | sign test p 1.000 |
| Refinement loop time, minutes (median) | 0.69 | 1.10 | sign test p 0.453 |

- **Success with 1 loop seed as a percentage of success with 5: 100 %.** Valid-and-improved finals are 32 in
  both arms; all KPIs improved finals are 32 vs 33 (97 %). No measure differs at the 5 % level, by Fisher's test
  on the runs or the sign test over the configurations.
- **Run time ratio (seeds5 / seeds1): 1.02 for the whole run, 1.59 for the loop alone.** Five runs per iteration
  cost more simulation. But seeds5 stopped after iteration 1 more often (25 vs 21 runs), so it used fewer
  iterations and LLM calls. The 10-seed final evaluation, identical in both arms, takes most of a run.
- **Early stops that the 10-seed evaluation does not confirm are rare in both arms:** 1 of 21 (seeds1) and 1 of 25
  (seeds5), both in LoanApp_G2. A single loop seed did not make the loop stop on a scenario that fails the 10-seed
  evaluation more often than five did.
- **Where the arms differ:**
  - **LoanApp_G2 (the timer problem):** seeds1 used 2.4 iterations on average, seeds5 1.0; 3 vs 4 runs were valid
    and improved.
    - In seeds1, three of the five runs continued after iteration 1, because their first proposal scored 0 or
      below on seed 42 (status "worsened").
    - Seeds1 rep 1 found no improving iteration in four and kept the baseline. That is seeds1's only invalid
      final.
    - In seeds5, all five runs stopped after iteration 1.
  - **LoanApp_G1G2G3:** 5 vs 4 valid finals. The invalid seeds5 final (rep 5) raised the hourly staff cost by
    39.5 % against the business context's limit of +35 %.
- **Consistency with earlier batches:** seeds1 repeats the v3.2 batch (RAG comparison, V_full) on the LoanApp
  configurations, where 33 of 35 finals were valid and improved; here 32.
- **Caveat:** the LLM is not seeded; each arm is one draw of 5 repetitions per configuration. With 35 runs per arm,
  only large differences would be detectable.

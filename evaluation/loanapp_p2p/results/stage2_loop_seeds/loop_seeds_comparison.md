# Loop replications experiment: 1 vs 5 seeds per refinement iteration (LoanApp)

- **Arms:** seeds1 = the refinement loop judges each iteration on one simulation run (seed 42, the default); seeds5 = on the mean KPIs of five paired runs (seeds 42–46) of the baseline and the proposal (`run_iterative_evaluation(..., loop_seeds=5)`). Everything else as in the Stage-2 batch on v3.2: the seven LoanApp configurations of `setup/stage2_config.json`, 5 repetitions, the same KPI objects, up to 4 iterations, the same final evaluation on 10 seeds (42–51).
- **Code:** GLASS 5643d04 / 5643d04; batch script 480af8b8 / 480af8b8.
- **Cells:** seeds1 / seeds5. Counts are out of 5 runs per configuration (utilization target: only the configurations with a utilization KPI).
- **Early stop:** the loop stopped after iteration 1 because its own check (one run, or the mean of five) found every target met. *Disagreeing:* the 10-seed final evaluation of that scenario does not have all KPIs improved (batch rule).

| Configuration | Utilization KPI | Valid final | All KPIs improved final | Valid and improved final | Utilization target achieved final, GLASS | Utilization target achieved final, batch | Loop discarded better initial | Early stops after iteration 1 | Early stops whose 10-seed final has not all KPIs improved | Iterations used (mean) | Run time, minutes (median) | Refinement loop time, minutes (median) | LLM calls per run (mean) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| LoanApp_G1 | no | 5 / 5 | 5 / 5 | 5 / 5 | – | – | 0 / 0 | 5 / 5 | 0 / 0 | 1 / 1 | 2.37 / 2.33 | 0.89 / 0.84 | 5.8 / 4.2 |
| LoanApp_G2 | no | 4 / 5 | 3 / 4 | 3 / 4 | – | – | 0 / 0 | 2 / 5 | 1 / 1 | 2.4 / 1 | 3.92 / 2.65 | 1.47 / 1.25 | 13.8 / 5.4 |
| LoanApp_G3 | yes | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 0 / 0 | 5 / 5 | 0 / 0 | 1 / 1 | 1.77 / 2.18 | 0.38 / 0.8 | 3.2 / 3.2 |
| LoanApp_G1G2 | no | 5 / 5 | 5 / 5 | 5 / 5 | – | – | 1 / 0 | 4 / 5 | 0 / 0 | 1.4 / 1 | 2.13 / 2.41 | 0.67 / 1.17 | 6.6 / 5 |
| LoanApp_G1G3 | yes | 5 / 5 | 4 / 4 | 4 / 4 | 4 / 4 | 4 / 4 | 3 / 2 | 0 / 0 | 0 / 0 | 3 / 2.8 | 3.77 / 3.8 | 1.16 / 1.34 | 9.6 / 9.4 |
| LoanApp_G2G3 | yes | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 0 / 0 | 5 / 5 | 0 / 0 | 1 / 1 | 1.99 / 2.24 | 0.58 / 0.96 | 3.8 / 4.2 |
| LoanApp_G1G2G3 | yes | 5 / 4 | 5 / 5 | 5 / 4 | 5 / 5 | 5 / 5 | 3 / 4 | 0 / 0 | 0 / 0 | 2 / 2 | 3.93 / 3.91 | 0.78 / 1.37 | 6 / 6.6 |
| **TOTAL (35 runs per arm)** |  | 34 / 34 | 32 / 33 | 32 / 32 | 19 / 19 | 19 / 19 | 7 / 6 | 21 / 25 | 1 / 1 | 1.69 / 1.4 | 2.36 / 2.41 | 0.69 / 1.1 | 6.97 / 5.43 |
| **configurations with a utilization KPI** |  | 20 / 19 | 19 / 19 | 19 / 18 | 19 / 19 | 19 / 19 | 6 / 6 | 10 / 10 | 0 / 0 | 1.75 / 1.7 | 2.47 / 2.92 | 0.68 / 1.09 | 5.65 / 5.85 |
| **configurations without a utilization KPI (control)** |  | 14 / 15 | 13 / 14 | 13 / 14 | – | – | 1 / 0 | 11 / 15 | 1 / 1 | 1.6 / 1 | 2.36 / 2.41 | 0.85 / 1.1 | 8.73 / 4.87 |

## Success and run time

- **Success with 1 loop seed as a percentage of success with 5:** 100.0 % (valid and all KPIs improved final: 32 vs 32 of 35); all KPIs improved final: 97.0 %.
- **Run time:** median 2.36 min (seeds1) vs 2.41 min (seeds5), ratio 1.02 (seeds5 / seeds1; 4 workers in both arms). The refinement loop alone: median 0.69 vs 1.1 min, ratio 1.59; the rest of a run is mostly the 10-seed final evaluation, the same in both arms. Five runs per iteration cost more simulation, but seeds5 used fewer iterations and LLM calls.

## Tests (seeds1 vs seeds5)

| Group | Measure | seeds1 | seeds5 | Fisher p | Sign test: configurations seeds1 > seeds5 of non-tied | Sign test p |
|---|---|---|---|---|---|---|
| all 7 configurations | Valid final | 34/35 | 34/35 | 1.000 | 1 of 2 | 1.000 |
| all 7 configurations | All KPIs improved final | 32/35 | 33/35 | 1.000 | 0 of 1 | 1.000 |
| all 7 configurations | Valid and improved final | 32/35 | 32/35 | 1.000 | 1 of 2 | 1.000 |
| all 7 configurations | Utilization target achieved final, GLASS | 19/20 | 19/20 | 1.000 | 0 of 0 | 1.000 |
| all 7 configurations | Utilization target achieved final, batch | 19/20 | 19/20 | 1.000 | 0 of 0 | 1.000 |
| all 7 configurations | Loop discarded better initial | 7/35 | 6/35 | 1.000 | 2 of 3 | 1.000 |
| all 7 configurations | Early stops after iteration 1 | 21/35 | 25/35 | 0.450 | 0 of 2 | 0.500 |
| all 7 configurations | Early stops whose 10-seed final has not all KPIs improved | 1/21 | 1/25 | 1.000 | 0 of 0 | 1.000 |
| all 7 configurations | Iterations used (mean) | 1.69 | 1.4 | – | 3 of 3 | 0.250 |
| all 7 configurations | Run time, minutes (median) | 2.36 | 2.41 | – | 3 of 7 | 1.000 |
| all 7 configurations | Refinement loop time, minutes (median) | 0.69 | 1.1 | – | 2 of 7 | 0.453 |
| all 7 configurations | LLM calls per run (mean) | 6.97 | 5.43 | – | 4 of 6 | 0.688 |
| with a utilization KPI | Valid final | 20/20 | 19/20 | 1.000 | 1 of 1 | 1.000 |
| with a utilization KPI | All KPIs improved final | 19/20 | 19/20 | 1.000 | 0 of 0 | 1.000 |
| with a utilization KPI | Valid and improved final | 19/20 | 18/20 | 1.000 | 1 of 1 | 1.000 |
| with a utilization KPI | Utilization target achieved final, GLASS | 19/20 | 19/20 | 1.000 | 0 of 0 | 1.000 |
| with a utilization KPI | Utilization target achieved final, batch | 19/20 | 19/20 | 1.000 | 0 of 0 | 1.000 |
| with a utilization KPI | Loop discarded better initial | 6/20 | 6/20 | 1.000 | 1 of 2 | 1.000 |
| with a utilization KPI | Early stops after iteration 1 | 10/20 | 10/20 | 1.000 | 0 of 0 | 1.000 |
| with a utilization KPI | Early stops whose 10-seed final has not all KPIs improved | 0/10 | 0/10 | 1.000 | 0 of 0 | 1.000 |
| with a utilization KPI | Iterations used (mean) | 1.75 | 1.7 | – | 1 of 1 | 1.000 |
| with a utilization KPI | Run time, minutes (median) | 2.47 | 2.92 | – | 1 of 4 | 0.625 |
| with a utilization KPI | Refinement loop time, minutes (median) | 0.68 | 1.09 | – | 0 of 4 | 0.125 |
| with a utilization KPI | LLM calls per run (mean) | 5.65 | 5.85 | – | 1 of 3 | 1.000 |
| without a utilization KPI (control) | Valid final | 14/15 | 15/15 | 1.000 | 0 of 1 | 1.000 |
| without a utilization KPI (control) | All KPIs improved final | 13/15 | 14/15 | 1.000 | 0 of 1 | 1.000 |
| without a utilization KPI (control) | Valid and improved final | 13/15 | 14/15 | 1.000 | 0 of 1 | 1.000 |
| without a utilization KPI (control) | Loop discarded better initial | 1/15 | 0/15 | 1.000 | 1 of 1 | 1.000 |
| without a utilization KPI (control) | Early stops after iteration 1 | 11/15 | 15/15 | 0.100 | 0 of 2 | 0.500 |
| without a utilization KPI (control) | Early stops whose 10-seed final has not all KPIs improved | 1/11 | 1/15 | 1.000 | 0 of 0 | 1.000 |
| without a utilization KPI (control) | Iterations used (mean) | 1.6 | 1 | – | 2 of 2 | 0.500 |
| without a utilization KPI (control) | Run time, minutes (median) | 2.36 | 2.41 | – | 2 of 3 | 1.000 |
| without a utilization KPI (control) | Refinement loop time, minutes (median) | 0.85 | 1.1 | – | 2 of 3 | 1.000 |
| without a utilization KPI (control) | LLM calls per run (mean) | 8.73 | 4.87 | – | 3 of 3 | 0.250 |

# Stage 2: batch v1 vs v2

- **v1:** GLASS `8039058`; Resource Utilization = busy time ÷ simulated span, averaged over all resources.
- **v2:** GLASS `5112fbc`; Resource Utilization = busy time ÷ available working time, evaluated for the role the KPI names.
- **Same in both:** batch script, config, seeds and baseline logic (baselines recomputed for v2).
- **Cells:** v1 / v2. Counts are out of 5 runs; recall and CTD ratio are means over the evaluated runs.
- **Groups:** configurations with a utilization KPI are those the GLASS change targets; the others are the control.
- **Hour values applied as seconds:** in the runs flagged `unit_defect` (`stage2_v1_vs_v2_runs.csv`; list in `unit_defect_runs_v1_v2.csv`, see `VERSIONS.md`) a proposed duration meant in hours was applied in seconds; their scores are kept as recorded and are included in all counts below.

- **Two rules for utilization KPIs:** "batch rule" = target pool below 0.85 and lower than the baseline (the scored rule); "GLASS rule" = GLASS's own verdict (any decrease of GLASS's utilization value: all-resource mean of busy time ÷ span in v1, role-resolved working-time utilization in v2). "All KPIs improved (GLASS rule)" applies GLASS's verdict to every KPI.

| Configuration | Utilization KPI | Valid initial | Valid final | All KPIs improved initial | All KPIs improved final | All KPIs improved initial (GLASS rule) | All KPIs improved final (GLASS rule) | Utilization improved final (GLASS rule) | Utilization improved final (batch rule) | Valid and improved final | Loop discarded better initial | Recall final | CTD ratio final (mean) | CTD ratio final (median) | Runs affected by the unit defect (v1 / v2) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| LoanApp_G1 | no | 5 / 5 | 5 / 5 | 5 / 4 | 5 / 4 | 5 / 4 | 5 / 4 | – | – | 5 / 4 | 0 / 0 | 0.11 / 0.09 | 0.11 / 0.11 | 0.12 / 0.10 | 0 / 1 |
| LoanApp_G2 | no | 5 / 5 | 5 / 5 | 3 / 4 | 4 / 5 | 3 / 4 | 4 / 5 | – | – | 4 / 5 | 1 / 1 | 0.25 / 0.25 | 0.06 / 0.23 | 0.23 / 0.22 | 0 / 0 |
| LoanApp_G3 | yes | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 0 / 0 | 1.00 / 1.00 | 0.87 / 0.82 | 0.88 / 0.82 | 0 / 0 |
| LoanApp_G1G2 | no | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | – | – | 5 / 5 | 0 / 0 | 0.18 / 0.17 | 0.12 / 0.11 | 0.16 / 0.10 | 1 / 1 |
| LoanApp_G1G3 | yes | 5 / 5 | 5 / 5 | 0 / 0 | 0 / 0 | 0 / 5 | 1 / 5 | 1 / 5 | 0 / 0 | 0 / 0 | 5 / 0 | 0.08 / 0.22 | 0.01 / 0.81 | 0.01 / 0.81 | 0 / 0 |
| LoanApp_G2G3 | yes | 5 / 5 | 5 / 5 | 5 / 2 | 5 / 4 | 2 / 2 | 2 / 4 | 2 / 4 | 5 / 4 | 5 / 4 | 0 / 1 | 0.24 / 0.32 | 0.17 / -8.90 | 0.19 / 0.33 | 0 / 0 |
| LoanApp_G1G2G3 | yes | 5 / 5 | 5 / 5 | 2 / 1 | 0 / 1 | 0 / 5 | 1 / 5 | 1 / 5 | 0 / 1 | 0 / 1 | 5 / 0 | 0.11 / 0.17 | -0.00 / 0.78 | 0.00 / 0.80 | 0 / 0 |
| Procure2Pay_G1 | no | 2 / 5 | 4 / 5 | 1 / 2 | 3 / 3 | 1 / 2 | 3 / 3 | – | – | 3 / 3 | 0 / 0 | 0.07 / 0.06 | 0.00 / -0.02 | 0.01 / 0.01 | 0 / 0 |
| Procure2Pay_G2 | no | 3 / 2 | 3 / 2 | 5 / 4 | 5 / 5 | 5 / 4 | 5 / 5 | – | – | 3 / 2 | 3 / 1 | 0.14 / 0.14 | 0.32 / 0.43 | 0.23 / 0.49 | 0 / 0 |
| Procure2Pay_G3 | yes | 5 / 5 | 5 / 5 | 0 / 0 | 2 / 1 | 0 / 0 | 2 / 1 | 2 / 1 | 2 / 1 | 2 / 1 | 0 / 0 | 0.40 / 0.00 | 0.25 / 0.04 | 0.00 / 0.00 | 0 / 1 |
| Procure2Pay_G1G2 | no | 4 / 5 | 4 / 5 | 1 / 1 | 3 / 1 | 1 / 1 | 3 / 1 | – | – | 3 / 1 | 3 / 1 | 0.06 / 0.10 | 0.06 / -0.04 | 0.15 / -0.05 | 0 / 0 |
| Procure2Pay_G1G3 | yes | 2 / 0 | 5 / 2 | 0 / 0 | 0 / 2 | 0 / 0 | 1 / 2 | 1 / 2 | 0 / 2 | 0 / 2 | 2 / 0 | 0.15 / 0.21 | 0.11 / 0.56 | 0.14 / 0.56 | 0 / 0 |
| Procure2Pay_G2G3 | yes | 5 / 5 | 5 / 5 | 3 / 2 | 3 / 3 | 3 / 2 | 3 / 3 | 3 / 4 | 3 / 4 | 3 / 3 | 0 / 0 | 0.17 / 0.20 | 0.13 / 0.18 | 0.25 / 0.25 | 0 / 0 |
| Procure2Pay_G1G2G3 | yes | 5 / 5 | 5 / 5 | 0 / 0 | 0 / 0 | 1 / 0 | 1 / 0 | 3 / 5 | 1 / 5 | 0 / 0 | 3 / 1 | 0.08 / 0.14 | 0.25 / 0.47 | 0.07 / 0.59 | 2 / 4 |
| TOTAL (70 runs per version) |  | 61 / 62 | 66 / 64 | 35 / 30 | 40 / 39 | 31 / 39 | 41 / 48 | 18 / 31 | 16 / 22 | 38 / 36 | 22 / 5 | 0.22 / 0.22 | 0.17 / -0.32 | 0.13 / 0.24 | 3 / 7 |
| configurations with a utilization KPI |  | 37 / 35 | 40 / 37 | 15 / 10 | 15 / 16 | 11 / 19 | 16 / 25 | 18 / 31 | 16 / 22 | 15 / 16 | 15 / 2 | 0.28 / 0.28 | 0.22 / -0.65 | 0.10 / 0.56 | 2 / 5 |
| configurations without a utilization KPI (control) |  | 24 / 27 | 26 / 27 | 20 / 20 | 25 / 23 | 20 / 20 | 25 / 23 | 0 / 0 | 0 / 0 | 23 / 20 | 7 / 3 | 0.14 / 0.13 | 0.11 / 0.14 | 0.14 / 0.10 | 1 / 2 |

Runs flagged `unit_defect`: v1 LoanApp_G1G2 rep 2; v1 Procure2Pay_G1G2G3 rep 1; v1 Procure2Pay_G1G2G3 rep 3; v2 LoanApp_G1 rep 1; v2 LoanApp_G1G2 rep 2; v2 Procure2Pay_G3 rep 1; v2 Procure2Pay_G1G2G3 rep 1; v2 Procure2Pay_G1G2G3 rep 2; v2 Procure2Pay_G1G2G3 rep 3; v2 Procure2Pay_G1G2G3 rep 5.

**Per-group delta (v2 − v1)**

| Group | Valid and improved final | Loop discarded better initial | Recall final | CTD ratio final (mean) | CTD ratio final (median) |
|---|---|---|---|---|---|
| With a utilization KPI | +1 | -13 | +0.002 | -0.877 | +0.454 |
| Without (control) | -3 | -4 | -0.002 | +0.025 | -0.033 |

**Control check** (configurations without a utilization KPI, outcome: valid and all KPIs improved, final):

- **Sign test** over the 6 configurations: 4 differ, 1 of them higher in v2; two-sided p = 0.625.
- **Pooled run-level Fisher exact test:** 23/30 in v1 vs 20/30 in v2; p = 0.567.
- **Reading:** the control configurations are statistically indistinguishable between v1 and v2 at the 5 % level. With 6 configurations, the sign test can detect only large systematic shifts. The LLM is not seeded, so run-to-run variation alone also differs between versions.

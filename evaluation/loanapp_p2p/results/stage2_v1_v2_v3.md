# Stage 2: batches v1, v2 and v3

- **Versions:** see `VERSIONS.md` (GLASS commits, code, input and scoring changes, affected runs).
- **Cells:** v1 / v2 / v3. Counts are out of 5 runs per configuration; recall is the mean over the evaluated runs, the CTD ratio the median over the runs (one outlier run in v2 LoanApp_G2G3, see the v2 README).
- **Utilization target achieved (v3 only):** GLASS = its role-resolved utilization (mean over the 10 seeds) <= 0.85; batch = the target pool's utilization of working time < 0.85. v1 and v2 had no threshold.
- **Rules:** "batch rule" = the scored rule (utilization: target pool below 0.85 and lower than the baseline); "GLASS rule" = GLASS's improved verdict for every KPI (any decrease for utilization).
- **Groups:** configurations with a utilization KPI are those the utilization changes (v2 metric, v3 target) address; the others are the control group.
- **Hour values applied as seconds (v1, v2):** in the runs flagged `unit_defect` (`stage2_v1_v2_v3_runs.csv`; see `VERSIONS.md`) a proposed duration meant in hours was applied in seconds; their scores are kept as recorded and are included in all counts below. v3 rejects such modifications.

| Configuration | Utilization KPI | Valid final | All KPIs improved final (batch rule) | All KPIs improved final (GLASS rule) | Utilization target achieved final, GLASS (v3) | Utilization target achieved final, batch (v3) | Valid and improved final | Loop discarded better initial | Recall final (mean) | CTD ratio final (median) | Runs affected by the unit defect (v1 / v2) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| LoanApp_G1 | no | 5 / 5 / 5 | 5 / 4 / 5 | 5 / 4 / 5 | – | – | 5 / 4 / 5 | 0 / 0 / 0 | 0.11 / 0.09 / 0.20 | 0.12 / 0.10 / 0.09 | 0 / 1 |
| LoanApp_G2 | no | 5 / 5 / 5 | 4 / 5 / 4 | 4 / 5 / 4 | – | – | 4 / 5 / 4 | 1 / 1 / 0 | 0.25 / 0.25 / 0.15 | 0.23 / 0.22 / 0.11 | 0 / 0 |
| LoanApp_G3 | yes | 5 / 5 / 5 | 5 / 5 / 5 | 5 / 5 / 5 | 5 | 5 | 5 / 5 / 5 | 0 / 0 / 0 | 1.00 / 1.00 / 1.00 | 0.88 / 0.82 / 0.81 | 0 / 0 |
| LoanApp_G1G2 | no | 5 / 5 / 5 | 5 / 5 / 5 | 5 / 5 / 5 | – | – | 5 / 5 / 5 | 0 / 0 / 0 | 0.18 / 0.17 / 0.14 | 0.16 / 0.10 / 0.17 | 1 / 1 |
| LoanApp_G1G3 | yes | 5 / 5 / 5 | 0 / 0 / 5 | 1 / 5 / 5 | 5 | 5 | 0 / 0 / 5 | 5 / 0 / 1 | 0.08 / 0.22 / 0.16 | 0.01 / 0.81 / 0.99 | 0 / 0 |
| LoanApp_G2G3 | yes | 5 / 5 / 5 | 5 / 4 / 5 | 2 / 4 / 5 | 5 | 5 | 5 / 4 / 5 | 0 / 1 / 0 | 0.24 / 0.32 / 0.32 | 0.19 / 0.33 / 0.34 | 0 / 0 |
| LoanApp_G1G2G3 | yes | 5 / 5 / 5 | 0 / 1 / 5 | 1 / 5 / 5 | 5 | 5 | 0 / 1 / 5 | 5 / 0 / 1 | 0.11 / 0.17 / 0.13 | 0.00 / 0.80 / 0.99 | 0 / 0 |
| Procure2Pay_G1 | no | 4 / 5 / 3 | 3 / 3 / 3 | 3 / 3 / 3 | – | – | 3 / 3 / 2 | 0 / 0 / 0 | 0.07 / 0.06 / 0.06 | 0.01 / 0.01 / 0.14 | 0 / 0 |
| Procure2Pay_G2 | no | 3 / 2 / 5 | 5 / 5 / 4 | 5 / 5 / 4 | – | – | 3 / 2 / 4 | 3 / 1 / 0 | 0.14 / 0.14 / 0.11 | 0.23 / 0.49 / 0.21 | 0 / 0 |
| Procure2Pay_G3 | yes | 5 / 5 / 5 | 2 / 1 / 2 | 2 / 1 / 2 | 5 | 5 | 2 / 1 / 2 | 0 / 0 / 0 | 0.40 / 0.00 / 0.40 | 0.00 / 0.00 / 0.00 | 0 / 1 |
| Procure2Pay_G1G2 | no | 4 / 5 / 5 | 3 / 1 / 1 | 3 / 1 / 1 | – | – | 3 / 1 / 1 | 3 / 1 / 1 | 0.06 / 0.10 / 0.06 | 0.15 / -0.05 / -0.02 | 0 / 0 |
| Procure2Pay_G1G3 | yes | 5 / 2 / 5 | 0 / 2 / 5 | 1 / 2 / 5 | 5 | 5 | 0 / 2 / 5 | 2 / 0 / 3 | 0.15 / 0.21 / 0.15 | 0.14 / 0.56 / 0.58 | 0 / 0 |
| Procure2Pay_G2G3 | yes | 5 / 5 / 5 | 3 / 3 / 5 | 3 / 3 / 5 | 5 | 5 | 3 / 3 / 5 | 0 / 0 / 0 | 0.17 / 0.20 / 0.25 | 0.25 / 0.25 / 0.27 | 0 / 0 |
| Procure2Pay_G1G2G3 | yes | 5 / 5 / 5 | 0 / 0 / 0 | 1 / 0 / 0 | 5 | 5 | 0 / 0 / 0 | 3 / 1 / 2 | 0.08 / 0.14 / 0.07 | 0.07 / 0.59 / 0.58 | 2 / 4 |
| TOTAL (70 runs per version) |  | 66 / 64 / 68 | 40 / 39 / 54 | 41 / 48 / 54 | 40 | 40 | 38 / 36 / 53 | 22 / 5 / 8 | 0.22 / 0.22 / 0.23 | 0.13 / 0.24 / 0.27 | 3 / 7 |
| configurations with a utilization KPI |  | 40 / 37 / 40 | 15 / 16 / 32 | 16 / 25 / 32 | 40 | 40 | 15 / 16 / 32 | 15 / 2 / 7 | 0.28 / 0.28 / 0.31 | 0.10 / 0.56 / 0.58 | 2 / 5 |
| configurations without a utilization KPI (control) |  | 26 / 27 / 28 | 25 / 23 / 22 | 25 / 23 / 22 | – | – | 23 / 20 / 21 | 7 / 3 / 1 | 0.14 / 0.13 / 0.12 | 0.14 / 0.10 / 0.10 | 1 / 2 |

**Target already met by the baseline:** the target pool's baseline utilization (mean over the 10 seeds) is below 0.85 in LoanApp_G2G3 (0.849), Procure2Pay_G3 (0.838), Procure2Pay_G2G3 (0.827). There, "utilization target achieved" holds without any change; the scored rule also requires the utilization to be lower than the baseline (column `baseline_pool_utilization` in the CSV).

Runs flagged `unit_defect`: v1 LoanApp_G1G2 rep 2; v1 Procure2Pay_G1G2G3 rep 1; v1 Procure2Pay_G1G2G3 rep 3; v2 LoanApp_G1 rep 1; v2 LoanApp_G1G2 rep 2; v2 Procure2Pay_G3 rep 1; v2 Procure2Pay_G1G2G3 rep 1; v2 Procure2Pay_G1G2G3 rep 2; v2 Procure2Pay_G1G2G3 rep 3; v2 Procure2Pay_G1G2G3 rep 5. v3: 0 run(s) (same search over the v3 records).

**Per-group change (v3 − v2, and v3 − v1 in brackets)**

| Group | Valid final | All KPIs improved final (batch rule) | Valid and improved final | Loop discarded better initial | Recall final (mean) | CTD ratio final (median) |
|---|---|---|---|---|---|---|
| With a utilization KPI | +3 (+0) | +16 (+17) | +16 (+17) | +5 (-8) | +0.028 (+0.030) | +0.026 (+0.480) |
| Without (control) | +1 (+2) | -1 (-3) | +1 (-2) | -2 (-6) | -0.015 (-0.018) | -0.005 (-0.038) |

**Control check v2 → v3** (configurations without a utilization KPI, outcome: valid and all KPIs improved, final):

- **Sign test** over the 6 configurations: 4 differ, 2 of them higher in v3; two-sided p = 1.000.
- **Pooled run-level Fisher exact test:** 20/30 in v2 vs 21/30 in v3; p = 1.000.
- **Reading:** the control configurations are statistically indistinguishable between v2 and v3 at the 5 % level. Unlike v1 → v2, the v3 changes can act on these configurations too (rejected mismatched modifications, id-stem mapping, best-iteration order), so this is a check that they did not shift them, not a pure null comparison. With 6 configurations the sign test detects only large systematic shifts; the LLM is not seeded, so run-to-run variation also differs between batches.

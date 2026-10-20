# Stage 2: batches v1, v2, v3 and v3.1

- **Versions:** see `VERSIONS.md`. v3.1 = v3 plus four implementation fixes (direction filter by KPI category, relative budget enforced, calendar changes given as text parsed or reported, calendar items in Prosimos period form accepted); same script, configuration and seeds.
- **Cells:** v1 / v2 / v3 / v3.1 (target and fix columns: v3 / v3.1). Counts are out of 5 runs per configuration; recall is the mean over the evaluated runs, the CTD ratio the median over the runs.
- **Rules:** "batch rule" = the scored rule (utilization: target pool below 0.85 and lower than the baseline); "GLASS rule" = GLASS's improved verdict for every KPI.
- **Hour values applied as seconds (v1, v2):** runs flagged `unit_defect` keep their recorded scores.
- **Fix columns:** runs in which each v3.1 fix acted (see the script's docstring; per run in `stage2_v1_v2_v3_v31_runs.csv`).

| Configuration | Utilization KPI | Valid final | All KPIs improved final (batch rule) | All KPIs improved final (GLASS rule) | Utilization target achieved final, GLASS (v3 / v3.1) | Utilization target achieved final, batch (v3 / v3.1) | Valid and improved final | Loop discarded better initial | Recall final (mean) | CTD ratio final (median) | Runs affected by the unit defect (v1 / v2) | Runs with a relative-budget rejection (v3 / v3.1) | Runs with a calendar change read from text or Prosimos form (v3 / v3.1) | Runs with a calendar change rejected with feedback (v3 / v3.1) | Runs with a calendar change skipped without feedback (v3 / v3.1) | Runs shown utilization-raising strategies for a utilization problem KPI (v3 / v3.1) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| LoanApp_G1 | no | 5 / 5 / 5 / 5 | 5 / 4 / 5 / 4 | 5 / 4 / 5 / 4 | – | – | 5 / 4 / 5 / 4 | 0 / 0 / 0 / 0 | 0.11 / 0.09 / 0.20 / 0.16 | 0.12 / 0.10 / 0.09 / 0.12 | 0 / 1 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| LoanApp_G2 | no | 5 / 5 / 5 / 5 | 4 / 5 / 4 / 4 | 4 / 5 / 4 / 4 | – | – | 4 / 5 / 4 / 4 | 1 / 1 / 0 / 2 | 0.25 / 0.25 / 0.15 / 0.15 | 0.23 / 0.22 / 0.11 / 0.22 | 0 / 0 | 0 / 3 | 0 / 4 | 0 / 1 | 1 / 0 | 0 / 0 |
| LoanApp_G3 | yes | 5 / 5 / 5 / 5 | 5 / 5 / 5 / 5 | 5 / 5 / 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 / 5 / 5 | 0 / 0 / 0 / 0 | 1.00 / 1.00 / 1.00 / 1.00 | 0.88 / 0.82 / 0.81 / 0.87 | 0 / 0 | 0 / 0 | 0 / 2 | 0 / 1 | 1 / 0 | 0 / 0 |
| LoanApp_G1G2 | no | 5 / 5 / 5 / 5 | 5 / 5 / 5 / 5 | 5 / 5 / 5 / 5 | – | – | 5 / 5 / 5 / 5 | 0 / 0 / 0 / 0 | 0.18 / 0.17 / 0.14 / 0.08 | 0.16 / 0.10 / 0.17 / 0.13 | 1 / 1 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| LoanApp_G1G3 | yes | 5 / 5 / 5 / 5 | 0 / 0 / 5 / 4 | 1 / 5 / 5 / 5 | 5 / 4 | 5 / 4 | 0 / 0 / 5 / 4 | 5 / 0 / 1 / 3 | 0.08 / 0.22 / 0.16 / 0.14 | 0.01 / 0.81 / 0.99 / 0.99 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| LoanApp_G2G3 | yes | 5 / 5 / 5 / 5 | 5 / 4 / 5 / 5 | 2 / 4 / 5 / 5 | 5 / 5 | 5 / 5 | 5 / 4 / 5 / 5 | 0 / 1 / 0 / 0 | 0.24 / 0.32 / 0.32 / 0.28 | 0.19 / 0.33 / 0.34 / 0.16 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| LoanApp_G1G2G3 | yes | 5 / 5 / 5 / 5 | 0 / 1 / 5 / 5 | 1 / 5 / 5 / 5 | 5 / 5 | 5 / 5 | 0 / 1 / 5 / 5 | 5 / 0 / 1 / 1 | 0.11 / 0.17 / 0.13 / 0.11 | 0.00 / 0.80 / 0.99 / 0.99 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Procure2Pay_G1 | no | 4 / 5 / 3 / 5 | 3 / 3 / 3 / 3 | 3 / 3 / 3 / 3 | – | – | 3 / 3 / 2 / 3 | 0 / 0 / 0 / 1 | 0.07 / 0.06 / 0.06 / 0.13 | 0.01 / 0.01 / 0.14 / 0.01 | 0 / 0 | 0 / 3 | 0 / 2 | 0 / 1 | 1 / 0 | 0 / 0 |
| Procure2Pay_G2 | no | 3 / 2 / 5 / 5 | 5 / 5 / 4 / 4 | 5 / 5 / 4 / 4 | – | – | 3 / 2 / 4 / 4 | 3 / 1 / 0 / 0 | 0.14 / 0.14 / 0.11 / 0.11 | 0.23 / 0.49 / 0.21 / 0.21 | 0 / 0 | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 0 | 0 / 0 |
| Procure2Pay_G3 | yes | 5 / 5 / 5 / 5 | 2 / 1 / 2 / 5 | 2 / 1 / 2 / 5 | 5 / 5 | 5 / 5 | 2 / 1 / 2 / 5 | 0 / 0 / 0 / 0 | 0.40 / 0.00 / 0.40 / 0.20 | 0.00 / 0.00 / 0.00 / 0.48 | 0 / 1 | 0 / 0 | 0 / 5 | 0 / 1 | 5 / 0 | 5 / 0 |
| Procure2Pay_G1G2 | no | 4 / 5 / 5 / 4 | 3 / 1 / 1 / 2 | 3 / 1 / 1 / 2 | – | – | 3 / 1 / 1 / 2 | 3 / 1 / 1 / 0 | 0.06 / 0.10 / 0.06 / 0.08 | 0.15 / -0.05 / -0.02 / -0.02 | 0 / 0 | 0 / 2 | 0 / 2 | 0 / 1 | 1 / 0 | 0 / 0 |
| Procure2Pay_G1G3 | yes | 5 / 2 / 5 / 5 | 0 / 2 / 5 / 5 | 1 / 2 / 5 / 5 | 5 / 5 | 5 / 5 | 0 / 2 / 5 / 5 | 2 / 0 / 3 / 4 | 0.15 / 0.21 / 0.15 / 0.12 | 0.14 / 0.56 / 0.58 / 0.57 | 0 / 0 | 0 / 0 | 0 / 2 | 0 / 3 | 1 / 0 | 0 / 0 |
| Procure2Pay_G2G3 | yes | 5 / 5 / 5 / 5 | 3 / 3 / 5 / 4 | 3 / 3 / 5 / 4 | 5 / 5 | 5 / 5 | 3 / 3 / 5 / 4 | 0 / 0 / 0 / 0 | 0.17 / 0.20 / 0.25 / 0.20 | 0.25 / 0.25 / 0.27 / 0.25 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 2 / 0 | 0 / 0 |
| Procure2Pay_G1G2G3 | yes | 5 / 5 / 5 / 5 | 0 / 0 / 0 / 0 | 1 / 0 / 0 / 0 | 5 / 5 | 5 / 5 | 0 / 0 / 0 / 0 | 3 / 1 / 2 / 0 | 0.08 / 0.14 / 0.07 / 0.07 | 0.07 / 0.59 / 0.58 / 0.56 | 2 / 4 | 0 / 1 | 0 / 3 | 0 / 3 | 1 / 0 | 0 / 0 |
| TOTAL (70 runs per version) |  | 66 / 64 / 68 / 69 | 40 / 39 / 54 / 55 | 41 / 48 / 54 / 56 | 40 / 39 | 40 / 39 | 38 / 36 / 53 / 55 | 22 / 5 / 8 / 11 | 0.22 / 0.22 / 0.23 / 0.20 | 0.13 / 0.24 / 0.27 / 0.25 | 3 / 7 | 0 / 10 | 0 / 21 | 0 / 12 | 13 / 0 | 5 / 0 |
| configurations with a utilization KPI |  | 40 / 37 / 40 / 40 | 15 / 16 / 32 / 33 | 16 / 25 / 32 / 34 | 40 / 39 | 40 / 39 | 15 / 16 / 32 / 33 | 15 / 2 / 7 / 8 | 0.28 / 0.28 / 0.31 / 0.27 | 0.10 / 0.56 / 0.58 / 0.56 | 2 / 5 | 0 / 1 | 0 / 12 | 0 / 8 | 10 / 0 | 5 / 0 |
| configurations without a utilization KPI (control) |  | 26 / 27 / 28 / 29 | 25 / 23 / 22 / 22 | 25 / 23 / 22 / 22 | – / – | – / – | 23 / 20 / 21 / 22 | 7 / 3 / 1 / 3 | 0.14 / 0.13 / 0.12 / 0.12 | 0.14 / 0.10 / 0.10 / 0.07 | 1 / 2 | 0 / 9 | 0 / 9 | 0 / 4 | 3 / 0 | 0 / 0 |

**Utilization target achieved (v3, after refinement):** target achieved where the baseline was overloaded (>0.85): 25 of 25; all 40 utilization runs: 40 of 40, of which 15 already below 0.85 at baseline.

**Utilization target achieved (v3.1, after refinement):** target achieved where the baseline was overloaded (>0.85): 24 of 25; all 40 utilization runs: 39 of 40, of which 15 already below 0.85 at baseline.

**Per-group change (v3.1 − v3, and v3.1 − v2 in brackets)**

| Group | Valid final | All KPIs improved final (batch rule) | Valid and improved final | Loop discarded better initial | Recall final (mean) | CTD ratio final (median) |
|---|---|---|---|---|---|---|
| With a utilization KPI | +0 (+3) | +1 (+17) | +1 (+17) | +1 (+6) | -0.045 (-0.016) | -0.019 (+0.007) |
| Without (control) | +1 (+2) | +0 (-1) | +1 (+2) | +2 (+0) | -0.002 (-0.017) | -0.031 (-0.036) |

**Control check v3 → v3.1** (configurations without a utilization KPI, outcome: valid and all KPIs improved, final):

- **Sign test** over the 6 configurations: 3 differ, 2 of them higher in v3.1; two-sided p = 1.000.
- **Pooled run-level Fisher exact test:** 21/30 in v3 vs 22/30 in v3.1; p = 1.000.
- **Reading:** the control configurations are statistically indistinguishable between v3 and v3.1 at the 5 % level. The v3.1 fixes can act on them too (relative budget, calendar changes), so this checks that they did not shift them. With 6 configurations the sign test detects only large systematic shifts; the LLM is not seeded.
- **For reference, configurations with a utilization KPI:** sign test 3 differ, 1 higher in v3.1, p = 1.000.

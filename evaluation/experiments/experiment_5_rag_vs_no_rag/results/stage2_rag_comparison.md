# Stage 2: RAG comparison on GLASS v3.2 (V_full vs V_no_kb)

- **Arms:** V_full = the Stage-2 batch on v3.2, knowledge base on; V_no_kb = the same batch with the knowledge base off in generation and feedback (the real-life ablation's switch). Records: GLASS fb11b64 / fb11b64; batch script 69111005 / 69111005. Same configuration, KPI objects, 5 repetitions and seeds 42–51 in both.
- **Cells:** V_full v3.2 / V_no_kb v3.2. Counts are out of 5 runs per configuration; recall is the mean over the evaluated runs, the CTD ratio the median over the runs, rejections per run the mean.
- **Kept baseline (v3.2):** when no iteration reaches a positive score, GLASS keeps the baseline; the run has no final scenario and is scored not valid. It counts as "loop discarded better initial" only if the initial was valid and improved at least one KPI on 10 seeds.
- **direction_inconsistent:** v3.2 rejects a modification whose stated direction contradicts its values.

| Configuration | Utilization KPI | Valid final | All KPIs improved final | Valid and improved final | Utilization target achieved final, GLASS | Utilization target achieved final, batch | Loop discarded better initial | Loop kept the baseline | Runs with a direction_inconsistent rejection | Recall final (mean) | CTD ratio to healthy final (median) | direction_inconsistent rejections per run |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| LoanApp_G1 | no | 5 / 5 | 5 / 5 | 5 / 5 | – | – | 0 / 0 | 0 / 0 | 0 / 0 | 0.20 / 0.04 | 0.10 / 0.07 | 0.00 / 0.00 |
| LoanApp_G2 | no | 5 / 5 | 4 / 5 | 4 / 5 | – | – | 0 / 0 | 0 / 0 | 0 / 0 | 0.20 / 0.20 | 0.23 / 0.23 | 0.00 / 0.00 |
| LoanApp_G3 | yes | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 0 / 0 | 0 / 0 | 0 / 0 | 1.00 / 1.00 | 0.79 / 0.87 | 0.00 / 0.00 |
| LoanApp_G1G2 | no | 5 / 5 | 5 / 5 | 5 / 5 | – | – | 0 / 0 | 0 / 0 | 0 / 0 | 0.17 / 0.09 | 0.17 / 0.13 | 0.00 / 0.00 |
| LoanApp_G1G3 | yes | 5 / 5 | 4 / 5 | 4 / 5 | 4 / 5 | 4 / 5 | 2 / 0 | 0 / 0 | 0 / 0 | 0.16 / 0.10 | 0.99 / 0.99 | 0.00 / 0.00 |
| LoanApp_G2G3 | yes | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 5 / 5 | 0 / 0 | 0 / 0 | 0 / 0 | 0.28 / 0.40 | 0.16 / 0.33 | 0.00 / 0.00 |
| LoanApp_G1G2G3 | yes | 5 / 5 | 5 / 4 | 5 / 4 | 5 / 4 | 5 / 4 | 3 / 2 | 0 / 0 | 0 / 0 | 0.13 / 0.10 | 0.99 / 0.99 | 0.00 / 0.00 |
| Procure2Pay_G1 | no | 5 / 5 | 4 / 1 | 4 / 1 | – | – | 0 / 1 | 0 / 0 | 0 / 0 | 0.04 / 0.14 | 0.01 / -0.03 | 0.00 / 0.00 |
| Procure2Pay_G2 | no | 5 / 5 | 5 / 5 | 5 / 5 | – | – | 0 / 0 | 0 / 0 | 0 / 0 | 0.14 / 0.11 | 0.23 / 0.25 | 0.00 / 0.00 |
| Procure2Pay_G3 | yes | 5 / 4 | 5 / 4 | 5 / 4 | 5 / 4 | 5 / 4 | 0 / 0 | 0 / 1 | 0 / 0 | 0.20 / 0.25 | 0.48 / 0.48 | 0.00 / 0.00 |
| Procure2Pay_G1G2 | no | 5 / 5 | 2 / 1 | 2 / 1 | – | – | 0 / 1 | 0 / 0 | 0 / 0 | 0.06 / 0.11 | -0.01 / -0.02 | 0.00 / 0.00 |
| Procure2Pay_G1G3 | yes | 5 / 5 | 5 / 3 | 5 / 3 | 5 / 3 | 5 / 3 | 3 / 1 | 0 / 0 | 0 / 0 | 0.15 / 0.08 | 0.56 / 0.55 | 0.00 / 0.00 |
| Procure2Pay_G2G3 | yes | 5 / 5 | 4 / 5 | 4 / 5 | 5 / 5 | 5 / 5 | 0 / 1 | 0 / 0 | 0 / 0 | 0.23 / 0.23 | 0.25 / 0.27 | 0.00 / 0.00 |
| Procure2Pay_G1G2G3 | yes | 0 / 2 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 4 / 3 | 5 / 3 | 0 / 0 | – / 0.05 | – / 0.03 | 0.00 / 0.00 |
| TOTAL (70 runs per arm) |  | 65 / 66 | 58 / 53 | 58 / 53 | 34 / 31 | 34 / 31 | 12 / 9 | 5 / 4 | 0 / 0 | 0.23 / 0.21 | 0.25 / 0.27 | 0.00 / 0.00 |
| configurations with a utilization KPI |  | 35 / 36 | 33 / 31 | 33 / 31 | 34 / 31 | 34 / 31 | 12 / 7 | 5 / 4 | 0 / 0 | 0.31 / 0.28 | 0.58 / 0.49 | 0.00 / 0.00 |
| configurations without a utilization KPI (control) |  | 30 / 30 | 25 / 22 | 25 / 22 | – / – | – / – | 0 / 2 | 0 / 0 | 0 / 0 | 0.13 / 0.12 | 0.14 / 0.12 | 0.00 / 0.00 |

**Utilization target achieved (GLASS, after refinement):**
- V_full: where the baseline was overloaded (>0.85) 19 of 25; all 40 utilization runs 34 of 40, of which 15 already below 0.85 at baseline.
- V_no_kb: where the baseline was overloaded (>0.85) 17 of 25; all 40 utilization runs 31 of 40, of which 15 already below 0.85 at baseline.

**V_full vs V_no_kb (v3.2).** Counts: pooled run-level Fisher exact test (V_full / V_no_kb of the runs). All measures: two-sided sign test over the configurations (configurations that differ, of them higher in V_full).

| Group | Measure | V_full | V_no_kb | Fisher p | Sign test | Sign p |
|---|---|---|---|---|---|---|
| all 14 configurations | Valid final | 65/70 | 66/70 | 1.000 | 1 of 2 | 1.000 |
| all 14 configurations | All KPIs improved final | 58/70 | 53/70 | 0.404 | 5 of 8 | 0.727 |
| all 14 configurations | Valid and improved final | 58/70 | 53/70 | 0.404 | 5 of 8 | 0.727 |
| all 14 configurations | Utilization target achieved final, GLASS | 34/40 | 31/40 | 0.568 | 3 of 4 | 0.625 |
| all 14 configurations | Utilization target achieved final, batch | 34/40 | 31/40 | 0.568 | 3 of 4 | 0.625 |
| all 14 configurations | Loop discarded better initial | 12/70 | 9/70 | 0.637 | 4 of 7 | 1.000 |
| all 14 configurations | Loop kept the baseline | 5/70 | 4/70 | 1.000 | 1 of 2 | 1.000 |
| all 14 configurations | Runs with a direction_inconsistent rejection | 0/70 | 0/70 | 1.000 | 0 of 0 | 1.000 |
| all 14 configurations | Recall final (mean) | 0.23 | 0.21 | – | 6 of 10 | 0.754 |
| all 14 configurations | CTD ratio to healthy final (median) | 0.25 | 0.27 | – | 6 of 10 | 0.754 |
| all 14 configurations | direction_inconsistent rejections per run | 0.00 | 0.00 | – | 0 of 0 | 1.000 |
| with a utilization KPI | Valid final | 35/40 | 36/40 | 1.000 | 1 of 2 | 1.000 |
| with a utilization KPI | All KPIs improved final | 33/40 | 31/40 | 0.781 | 3 of 5 | 1.000 |
| with a utilization KPI | Valid and improved final | 33/40 | 31/40 | 0.781 | 3 of 5 | 1.000 |
| with a utilization KPI | Utilization target achieved final, GLASS | 34/40 | 31/40 | 0.568 | 3 of 4 | 0.625 |
| with a utilization KPI | Utilization target achieved final, batch | 34/40 | 31/40 | 0.568 | 3 of 4 | 0.625 |
| with a utilization KPI | Loop discarded better initial | 12/40 | 7/40 | 0.293 | 4 of 5 | 0.375 |
| with a utilization KPI | Loop kept the baseline | 5/40 | 4/40 | 1.000 | 1 of 2 | 1.000 |
| with a utilization KPI | Runs with a direction_inconsistent rejection | 0/40 | 0/40 | 1.000 | 0 of 0 | 1.000 |
| with a utilization KPI | Recall final (mean) | 0.31 | 0.28 | – | 3 of 5 | 1.000 |
| with a utilization KPI | CTD ratio to healthy final (median) | 0.58 | 0.49 | – | 2 of 5 | 1.000 |
| with a utilization KPI | direction_inconsistent rejections per run | 0.00 | 0.00 | – | 0 of 0 | 1.000 |
| without a utilization KPI (control) | Valid final | 30/30 | 30/30 | 1.000 | 0 of 0 | 1.000 |
| without a utilization KPI (control) | All KPIs improved final | 25/30 | 22/30 | 0.532 | 2 of 3 | 1.000 |
| without a utilization KPI (control) | Valid and improved final | 25/30 | 22/30 | 0.532 | 2 of 3 | 1.000 |
| without a utilization KPI (control) | Loop discarded better initial | 0/30 | 2/30 | 0.492 | 0 of 2 | 0.500 |
| without a utilization KPI (control) | Loop kept the baseline | 0/30 | 0/30 | 1.000 | 0 of 0 | 1.000 |
| without a utilization KPI (control) | Runs with a direction_inconsistent rejection | 0/30 | 0/30 | 1.000 | 0 of 0 | 1.000 |
| without a utilization KPI (control) | Recall final (mean) | 0.13 | 0.12 | – | 3 of 5 | 1.000 |
| without a utilization KPI (control) | CTD ratio to healthy final (median) | 0.14 | 0.12 | – | 4 of 5 | 0.375 |
| without a utilization KPI (control) | direction_inconsistent rejections per run | 0.00 | 0.00 | – | 0 of 0 | 1.000 |

- **Significant at 5 %:** none.
- The LLM is not seeded; each arm is one draw of 5 repetitions per configuration. With 14 configurations the sign test detects only large systematic shifts.

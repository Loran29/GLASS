# Experiments

Experiment 0 checks the BPS models the experiments start from; experiments 1 to 5 evaluate GLASS. Experiments 2 to 5 report GLASS v3.2 with the knowledge base (V_full) and without it
(V_no_kb).

| # | Question | Data | GLASS | Headline | Folder |
|---|---|---|---|---|---|
| 0 | Do the modified BPS models contain the changes the paper describes? | LoanApp, Procure2Pay: original and 4 variants each | – | Workbook per change type (resources, durations, timers, calendars) | `experiment_0_synthetic_variants_vs_original/` |
| 1 | How often does Stage 1 return the expected KPIs, and how many refinement rounds are needed? | LoanApp, Procure2Pay: 14 goal configurations x 5 runs; refinement pilot on 9 cases | Stage 1 | All expected KPIs in 24 of 70 runs (strict), 26 of 70 (lenient) | `experiment_1_expected_kpi_generation/` |
| 2 | How many Stage 2 runs give a valid proposal that improves all KPIs? | LoanApp, Procure2Pay: 14 configurations x 5 runs | v3.2 V_full, V_no_kb | Valid and improved: 58 of 70 (V_full), 53 of 70 (V_no_kb) | `experiment_2_valid_improving_kpis/` |
| 3 | GLASS vs Process Improvement Copilot: do the manual changes from the Copilot's ideas beat GLASS's proposals? | LoanApp, Procure2Pay: 16 manual models in 13 configurations, 10 seeds | v3.2 V_full, V_no_kb | Rep comparisons GLASS better / no difference / manual better: 51 / 23 / 76 (V_full), 65 / 14 / 71 (V_no_kb) | `experiment_3_glass_vs_process_improvement_copilot/` |
| 4 | What does each GLASS component contribute on real event logs? | AcademicCredentials, BPIC_2012_W, BPIC_2017_W: 3 goals x 6 variants x 5 runs | v3.2, six variants | All KPIs improved: 18 of 45 (V_full), 28 of 45 (V_no_kb) | `experiment_4_real_life_ablation/` |
| 5 | Does the knowledge base (RAG) change the Stage 2 results? | Experiment 2's runs | v3.2 V_full vs V_no_kb | No measure differs significantly at 5 % (valid and improved 58 vs 53 of 70, Fisher p = 0.404) | `experiment_5_rag_vs_no_rag/` |

- `shared/loanapp_p2p/`: inputs used by experiments 1, 2, 3 and 5 (setup files, event logs, BPS models, Stage 2
  baselines), the BIMP-to-Prosimos converters, and `lp_paths.py`, which gives every script its paths.
- Simulated event logs of GLASS's runs are not in git. Experiment 3 ships the logs it compares as zip files (`inputs/archives/`); the two largest are published separately (see its README).
- Older GLASS versions (v1 to v3.1) are not part of the experiments; they are in the `main` branch.

Run the scripts from the repository root, for example
`python evaluation/experiments/experiment_2_valid_improving_kpis/scripts/verify_stage2_results.py --results <folder>`.

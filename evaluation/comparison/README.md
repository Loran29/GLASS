# Comparison: my Prosimos results vs GLASS Stage 2 v3.1

## Purpose

This folder compares business process simulation models that I built by hand with the output of GLASS Stage 2 (v3.1) on
the LoanApp and Procure2Pay processes. For each configuration (a process and its goals G1, G2, G3) it answers:

- Does my model improve the expected KPIs of the goal compared with the unchanged baseline?
- How does it compare with what GLASS proposed (5 independent runs, each with its best iteration)?

All three sources (mine, GLASS, baseline) were simulated with the same Prosimos settings (1000 cases, start
2023-02-09T10:00:00+02:00, seeds 42-51, Prosimos 2.1.0, Python 3.11) and the KPIs are computed from the event logs with
the same code (GLASS's `kpi_computation.py`).

Read `comparison_notes.md` first: it explains every column of the table, the definition of each KPI (cycle time, waiting
time, utilization) and the caveats.

## Files

| File | Content |
|---|---|
| `comparison.xlsx` | The curated results workbook (hand-edited; the script never writes this file). Main sheet `Summary`: one row per config x variant x expected KPI |
| `comparison_summary.csv` | The same table as the `Summary` sheet, with extra columns: paired and Welch p-values and the utilization target flags (`baseline/mine/glass_target_met`) |
| `comparison_all.csv` | Every comparison: mine vs baseline, GLASS vs baseline, mine vs GLASS, each GLASS rep separately and the mean of the 5 reps |
| `kpi_values_per_log.csv` | The KPI value of every log (source, config, model, rep, seed, KPI, value, note): 1680 values |
| `comparison_notes.md` | Column dictionary, KPI definitions step by step, statistics, caveats, checks |
| `scripts/compar_results_with_glass.py` | Computes the KPIs, the statistics and all the files above |
| `scripts/check_prosimos_results.py` | Checks my Prosimos result folders before the comparison |
| `scripts/write_excel_notes.py` | Rewrites the `Notes` sheet of `comparison.xlsx` so it describes only what the workbook contains (other sheets are not touched) |

## What is compared

- **Mine:** `evaluation/my_prosimos_results_v2/{extended_hours, extra_resources}/<model>/`. 16 models in 13 configs, each
  with the Prosimos JSON, the BPMN and 10 logs (`seed_42..51_log.csv`).
  - `extended_hours`: working hours of the named roles extended from 09:00-17:00 to 09:00-19:00.
  - `extra_resources`: headcount of the named roles increased (by 2 in most cases).
  - Source of the ideas and the changes: `evaluation/loanapp_p2p/bps_models_improved/` (`CHANGES.md`, `change_log.csv`).
- **GLASS:** `evaluation/stage2_v3_1_final_logs/final_logs/<config>/rep_<k>/iter<n>_seed<s>.csv.gz`: 5 reps per
  config; each rep is an independent GLASS run and the logs are those of its best iteration `n`.
- **Baseline:** `evaluation/stage2_v3_1_final_logs/baseline_logs/<config>/seed_<s>.csv.gz`: the unchanged (injected-problem)
  model of the config, same seeds.
- **Matching:** by config. A config found in both of my folders gets two rows. A config I have no model for is not compared:
  **Procure2Pay_G1 is not compared.**
- **Expected KPIs** per config come from `evaluation/EXPECTED_KPIS.md`: Average Cycle Time (G1), the waiting time of
  Assess loan risk / Release Supplier's Invoice (G2), the utilization of Loan Officer / Purchasing Agent (G3).

Other inputs the script reads:
- the Prosimos parameters of GLASS's best iteration: `evaluation/loanapp_p2p/results/stage2_results_v3_1/raw/<config>/rep_<k>.json`
  (`loop.iterations[best_iteration - 1].simulated_scenario_prosimos`);
- the Prosimos parameters of the baseline: `evaluation/loanapp_p2p/stage2/baselines/<config>/sim_params.json`;
- the GLASS KPI code: `goal_to_parameters/second_llm/kpi_computation.py` (last commit `5112fbc`, SHA-256
  `de4102dcae91b09fb676705b1706f3db4d8eba764f2c025196d635bce3e0163f`). It is imported from the repository, not copied.

## How to run

From the repository root, with Python 3.11 or newer (pandas, numpy, scipy, openpyxl):

```
python -I evaluation/comparison/scripts/check_prosimos_results.py          # checks my result folders
python -I evaluation/comparison/scripts/compar_results_with_glass.py --workers 8   # about 20 minutes
python -I evaluation/comparison/scripts/compar_results_with_glass.py --reuse       # seconds, reuses kpi_values_per_log.csv
```

- Without `--reuse` the script reads all 940 logs (160 mine, 130 baseline, 650 GLASS) and recomputes the KPIs; with
  `--reuse` it only recomputes the statistics and the text files from `kpi_values_per_log.csv`.
- The script overwrites `comparison_summary.csv`, `comparison_all.csv`, `kpi_values_per_log.csv` (without `--reuse`) and
  `comparison_notes.md`. It writes the workbook as `comparison_generated.xlsx` (Summary sheet only) and does not touch
  `comparison.xlsx`.

## Checks that were done

- `check_prosimos_results.py`: all 16 models have 10 logs with 1000 cases each, every resource name in the logs is in the
  model's resource list, the role headcounts and the 09:00-19:00 calendars match `change_log.csv`. 0 problems.
- No utilization fell back to the span-based formula (every resource had a calendar).
- Cross-check of my baseline KPI series against the baseline values stored in the GLASS run records: 30 of 31 series match.
  The one exception is Procure2Pay_G1G2G3: there GLASS optimised the case-level KPI "Average Wait Time Before Invoice
  Release" (summed waiting per case), while this comparison uses the expected KPI "Release Supplier's Invoice Waiting Time".

## Things to keep in mind

- Seeds are the same for all sources, but the pairing by seed is weak between different models (the random streams
  drift after the first change), so read the Welch p-value in the CSV next to the confidence interval in the table.
- LoanApp G1G3 and G1G2G3 have an overloaded baseline (cycle time about 2250 hours, a single Loan Officer): times and
  percentages there are dominated by the growing queue.
- For utilization, a lower value can also mean idle capacity. GLASS's target rule (below 0.85 and below the baseline)
  is in the `*_target_met` columns of `comparison_summary.csv`. The reason for the 0.85 limit is not documented in the
  repository.
- The roles chosen for the `extended_hours` and `extra_resources` ideas are partly analyst assumptions (see
  `bps_models_improved/CHANGES.md`); for example, for Procure2Pay G1G2 the hours were extended for Financial Manager
  (and Purchasing Agent in `hours_2`), while "Release Supplier's Invoice" is performed by Supplier.

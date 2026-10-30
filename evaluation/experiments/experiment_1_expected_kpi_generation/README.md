# Experiment 1: how often GLASS generates the expected KPIs

For each of the 14 goal configurations (LoanApp and Procure2Pay; goals G1, G2, G3 and their combinations) GLASS's
Stage 1 turned the goal sentence into KPIs, 5 times. The question is in how many runs the expected KPIs appear, and,
in a refinement pilot on 9 cases, how many feedback rounds are needed when they do not.

## Results

All expected KPIs present (out of 5 runs per configuration), `results/stage1_results_normalised/stage1_per_config.csv`:

| Configuration | Strict | Lenient |
|---|---|---|
| LoanApp_G1 | 5 | 5 |
| LoanApp_G2 | 5 | 2 |
| LoanApp_G3 | 5 | 5 |
| LoanApp_G1G2 | 0 | 0 |
| LoanApp_G1G3 | 5 | 5 |
| LoanApp_G2G3 | 0 | 0 |
| LoanApp_G1G2G3 | 0 | 1 |
| Procure2Pay_G1 | 4 | 5 |
| Procure2Pay_G2 | 0 | 2 |
| Procure2Pay_G3 | 0 | 0 |
| Procure2Pay_G1G2 | 0 | 0 |
| Procure2Pay_G1G3 | 0 | 0 |
| Procure2Pay_G2G3 | 0 | 1 |
| Procure2Pay_G1G2G3 | 0 | 0 |
| **Total (of 70)** | **24** | **26** |

`stage1_results/` holds the same 70 runs before GLASS's later `measurable_as` normalisation (strict 0, lenient 26);
the scoring rules are in the README of each results folder.

Refinement pilot (`results/refinement_pilot/pilot_summary.csv`): per case and round, whether all expected KPIs are
present (`reached_without_refinement`, `reached_after_N_round(s)`, `not_reached_after_N_round(s)`).

## Files

| Path | Content |
|---|---|
| `results/stage1_results/` | Raw records of the 70 runs, summary CSVs, README, verification report |
| `results/stage1_results_normalised/` | The same runs with the normalisation applied |
| `results/refinement_pilot/` | Refinement records (9 cases, up to 3 rounds), `pilot_summary.csv`, README |
| `results/GLASS_Stage1_results.xlsx`, `GLASS_Stage1_refinement_pilot.xlsx` | Workbooks |
| `scripts/run_stage1_batch.py`, `run_refinement_pilot.py` | Run the batch and the pilot (LLM calls) |
| `scripts/reprocess_stage1_normalised.py` | Rebuilds `stage1_results_normalised/` |
| `scripts/verify_stage1_results.py`, `verify_refinement_pilot.py` | Independent checks |

Inputs: `../shared/loanapp_p2p/setup/` (configurations, expected KPIs, pilot feedback) and
`../shared/loanapp_p2p/input_logs/`.

The READMEs inside `results/` were written at run time and give the paths of that time (`evaluation/loanapp_p2p/...`).
The scripts' path changes for this folder are in `../shared/loanapp_p2p/scripts/relocation_experiments.patch`.

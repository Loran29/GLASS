# Experiment 3: GLASS vs Process Improvement Copilot (manual changes)

The Process Improvement Copilot proposed improvement ideas for the LoanApp and Procure2Pay problems. The ideas that
can be applied directly (extended working hours, extra resources) were applied by hand to the BPS models and simulated
with Prosimos. This experiment compares those manual models with GLASS v3.2's proposals, with the knowledge base
(V_full) and without it (V_no_kb), and with the unchanged baseline.

All sources use the same Prosimos settings (1000 cases, start 2023-02-09T10:00:00+02:00, seeds 42-51, Prosimos 2.1.0,
Python 3.11). The KPIs are computed from the event logs with GLASS's own code
(`goal_to_parameters/second_llm/kpi_computation.py`):

- Average Cycle Time (G1): last event end minus first event start per case, mean in hours.
- Waiting time of Assess loan risk / Release Supplier's Invoice (G2): start of the activity minus the end of the
  previous event in the case, positive gaps only, mean in hours.
- Utilization of Loan Officer / Purchasing Agent (G3): busy time within working hours / available working time, per
  person, mean over the role.

## Results

`results/comparison_new.xlsx`, one summary sheet and one rep sheet per arm.

| | V_full v3.2 | V_no_kb v3.2 |
|---|---|---|
| Summary rows: manual lower / GLASS lower / no significant difference | 13 / 16 / 1 | 18 / 12 / 0 |
| Rep comparisons: GLASS better / no difference / manual better (of 150) | 51 / 23 / 76 | 65 / 14 / 71 |

- Summary verdict: 95 % confidence interval of the per-seed difference (manual minus the mean of GLASS's 5 reps).
- Rep sheets: each GLASS rep against the manual model. Time KPIs use the same interval rule. Utilization uses the
  means: GLASS is better when manual < rep < baseline (a very low manual value can mean idle capacity).
- In GLASS v3.2 the loop keeps the baseline when no iteration scores above 0, so these reps have no final log and the
  baseline values are used (orange cells): V_full Procure2Pay_G1G2G3 reps 1-5; V_no_kb Procure2Pay_G1G2G3 reps 2, 3,
  4 and Procure2Pay_G3 rep 3.
- 16 manual models in 13 configurations. Procure2Pay_G1 has no manual model and is not compared.

## Files

| Path | Content |
|---|---|
| `results/comparison_new.xlsx` | Curated workbook |
| `results/comparison_generated.xlsx` | Summary sheets as written by the script |
| `results/comparison_summary_<arm>.csv`, `comparison_all_<arm>.csv` | Summary per arm (with p-values) and every comparison (each rep) |
| `results/kpi_values_per_log.csv` | KPI value of every log; cache of the script |
| `scripts/compar_results_with_glass.py` | Computes the KPIs and the comparison |
| `scripts/check_prosimos_results.py` | Checks the manual model folders |
| `inputs/manual_models/` | The 16 manual models: Prosimos JSON, BPMN and 10 event logs each |
| `inputs/manual_models_source/` | Modified BPMN models with `CHANGES.md` and `change_log.csv`, the script that applied the changes, the Copilot's ideas (`applicability_updated.xlsx`) |
| `inputs/glass_event_logs/v3.2_rag_comparison/` | GLASS's selected-iteration logs of both arms, the baseline logs, `manifest.csv` with SHA-256 values |

The scripts also read GLASS's run records (`../experiment_2_valid_improving_kpis/results/stage2_results_v32_rag/<arm>/raw/`)
and the baseline parameters (`../shared/loanapp_p2p/stage2/baselines/`).

## Event logs

The event logs are not stored as single files in git. They are in four zip files in `inputs/archives/`; each zip keeps
the paths relative to `inputs/`.

| Zip | Content | Size | Where |
|---|---|---|---|
| `manual_models_logs.zip` | The 160 logs of the manual models and the Prosimos statistics (`_stats/`) | 27.7 MB | in git |
| `glass_baseline_logs.zip` | Baseline logs, 14 configurations x 10 seeds | 24.5 MB | in git |
| `glass_V_full_logs.zip` | GLASS V_full selected-iteration logs (650) | 107.2 MB | not in git (over GitHub's 100 MB limit): download it from the location below |
| `glass_V_no_kb_logs.zip` | GLASS V_no_kb selected-iteration logs (660) | 110.5 MB | not in git: download it from the location below |

Download location of the two large zips: *to be added*.

SHA-256 of the zips:

```
28196fd1aacc997cbdd2a901f925c45990c4b9b07d21c2e3158b9edb21cd99e3  manual_models_logs.zip
e652d48827c48672b559fdfe35efd1089d4da9ef979face331735cee95f9d420  glass_baseline_logs.zip
8f847c20700364dd905cfd245c98d6851cd31a8a4d32ba41d9a732ad12c7e2e0  glass_V_full_logs.zip
a396b3eb51d06a67d42cfdfceb375029077cfae9b19bf0ae875f13a13f701c39  glass_V_no_kb_logs.zip
```

Put the two large zips into `inputs/archives/`, then unzip all four into `inputs/` (from this experiment's folder):

```
cd evaluation/experiments/experiment_3_glass_vs_process_improvement_copilot/inputs
python -c "import zipfile, glob; [zipfile.ZipFile(z).extractall('.') for z in glob.glob('archives/*.zip')]"
```

The comparison script needs the logs only to compute KPI values that are not yet in `results/kpi_values_per_log.csv`
(the cache in git has all of them), but it checks that every log file exists, so unzip all four before running it.
The file `inputs/glass_event_logs/v3.2_rag_comparison/manifest.csv` lists the SHA-256 of every GLASS log (with the
package's original folder names `V_full/` and `V_no_kb/`).

## How to run

From the repository root, after unzipping the event logs:

```
python -I evaluation/experiments/experiment_3_glass_vs_process_improvement_copilot/scripts/check_prosimos_results.py
python -I evaluation/experiments/experiment_3_glass_vs_process_improvement_copilot/scripts/compar_results_with_glass.py --workers 8
```

`--rep-sheets` and `--update-workbook` (re)write the rep sheets and the summary sheets of `comparison_new.xlsx`
(close it in Excel first). KPI values in `kpi_values_per_log.csv` are reused; `--recompute` reads every log again.

## Notes

- The manual models were simulated outside this repository, with the same per-person Prosimos layout as the GLASS
  baselines (`../shared/loanapp_p2p/scripts/merge_ends.py`, `qbp2prosimos.py`).
- The pairing by seed is weak between different models; the CSV files also give the Welch p-value.
- LoanApp G1G3 and G1G2G3 have an overloaded baseline (cycle time about 2250 hours), so times there are dominated by
  the queue.
- The roles chosen for the manual changes are partly assumptions (see `CHANGES.md`).

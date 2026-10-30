# Experiment 0: synthetic variants vs original

Checks whether each modified BPS model of the LoanApp and Procure2Pay processes really contains the change described in
"A Framework for Measuring the Quality of Business Process Simulation Models", by comparing it with its original model.
The results are written to an Excel workbook.

## Results

`results/synthetic_vs_original_comparison.xlsx`:

| Sheet | Content |
|---|---|
| Resources | Resource counts, original vs `resource_cont` |
| Durations | Mean activity durations, original vs `durations` |
| Extraneous Timers | Each timer in the `extraneous` models: between which elements it sits and its delay |
| Calendars | "Business Hours" timetable in the original and `calendars` models |
| Quality Measures | The paper's quality measures (from `inputs/quality_measures.csv`, written by hand) |

Two findings: `Check credit history` (LoanApp) has only its `mean` attribute changed, so its effective duration does
not change; `Pay invoice` (Procure2Pay) goes from 20 to 15 min.

## Files

| Path | Content |
|---|---|
| `inputs/bps_models/` | `<Dataset>_original.bpmn` and the variants `resource_cont`, `durations`, `extraneous`, `calendars` (LoanApp, Procure2Pay) |
| `inputs/quality_measures.csv` | Table for the "Quality Measures" sheet |
| `scripts/bpmn_parser.py` | Reads the simulation parameters from a BIMP/QBP BPMN file |
| `scripts/build_workbook.py` | Runs every comparison and writes the workbook |

## Run

From the repository root (Python 3, `openpyxl`):

```
python evaluation/experiments/experiment_0_synthetic_variants_vs_original/scripts/build_workbook.py
python evaluation/experiments/experiment_0_synthetic_variants_vs_original/scripts/bpmn_parser.py <model.bpmn>
```

Run without `-I`: `build_workbook.py` imports `bpmn_parser.py` from its own folder.

## Notes

- Times in the BPMN files are in seconds; the workbook shows minutes or hours. The effective mean follows the BIMP
  convention: FIXED and NORMAL use `mean`, EXPONENTIAL uses `arg1`.
- Three gateway labels in the Extraneous Timers sheet are set by hand in `build_workbook.py` (`GATEWAY_LABELS`).
- The Resources and Durations sheets contain Excel formulas; Excel recalculates them when the file is opened.
- Copied from the "Glass parameter change" project (`analysis/` and the BPMN files of `eventlogs/synthetic/`).
  `Procure2Pay_original.bpmn` there writes the arrival distribution in a longer form than the copy in
  `../shared/loanapp_p2p/bps_models/` (same values: FIXED, 1800 s); the other originals and variants are identical.

# Stage 2 baselines (LoanApp / Procure2Pay)

This folder holds the baseline GLASS receives in Stage 2 for each of the 14 configurations in
`../setup/stage1_config.json`: a BPMN model and a Prosimos simulation-parameter file. It also holds a
validation showing that each baseline, loaded through GLASS, reproduces the configuration's event log.

## Layout

```
stage2/
  README.md                       this file
  baselines/
    <config_id>/model.bpmn        BPMN given to GLASS (one end event, see below)
    <config_id>/sim_params.json   Prosimos parameters given to GLASS
    validation_report.md          per-configuration comparison and PASS/FAIL
    validation_metrics.json       all measured values behind the report
  healthy/
    LoanApp/, Procure2Pay/        model.bpmn + sim_params.json of the two original (healthy) models
```

`healthy/` is a scoring reference only and is never given to GLASS. It is converted, not validated.

## How the baselines were produced

For each configuration, `../scripts/build_stage2_baselines.py` does the following:

1. It takes the BIMP model named in the "Evaluation cases" sheet of
   `../setup/GLASS_expected_KPIs_and_evaluation_cases.xlsx`, matched on the event-log column, from
   `../bps_models/`.
2. `../scripts/merge_ends.py` writes `model.bpmn`. It merges several end events into one through a
   converging exclusive gateway, because Prosimos needs a single end event; LoanApp has three. For
   simulation this makes no difference, since an end event only terminates the case. Procure2Pay
   models have one end event and are copied unchanged.
3. `../scripts/qbp2prosimos.py` writes `sim_params.json` from the BIMP simulation info: distributions,
   gateway probabilities, calendars, resource pools and timer events. It was validated in Step 1
   against the paper's logs.

Both helper scripts can also be run on their own:
`python merge_ends.py <in.bpmn> <out.bpmn>` and `python qbp2prosimos.py <in.bpmn> <out.json>`.

## Validation

Each baseline is loaded through GLASS's own path:
`accept_simod_raw_input` → `build_baseline_scenario` → `scenario_to_prosimos_json` → `run_prosimos_simulation`.
It is then simulated once with Prosimos (1000 cases from 2023-02-09T10:00:00+02:00, seed 20230209) and
compared with two references. A configuration passes only if both comparisons pass:

- **(i) Direct run.** The same `sim_params.json` is simulated directly with Prosimos with the same seed.
  GLASS must not change what the converter produced. Duration medians, events per case, mean cycle time
  and the KPI waiting median are compared within noise-aware tolerances.
- **(ii) BIMP input log.** This compares different engines; end-of-day handling differs, as documented in
  Step 1. Mean cycle time and the KPI waiting median must be within a factor of 2. Cases and resources
  per pool must match exactly. Timers must be present with their original means, and normal
  distributions must keep their std.

The exact criteria are at the top of `baselines/validation_report.md`. Result: all 14 configurations pass.

**GLASS version.** The validation needs GLASS at or after commit `5b3f717`. Earlier versions lose timer
delays (fixed in `7c93a1c`), treat a normal distribution's std as its variance (`893db82`, `cb9cb17`),
and replace arrival and resource calendars with 24/7 (`5b3f717`). With those versions, the baselines do
not reproduce the logs.

## Environment

Prosimos needs an older Python than GLASS's default. Prosimos 2.1.0 requires Python 3.11 or 3.12
(`>=3.11,<3.13`), and earlier Prosimos versions require Python < 3.12. The validation was run in the
repository's `.venv` (gitignored) with these versions:

| Package | Version |
|---|---|
| Python | 3.11.13 |
| prosimos | 2.1.0 |
| pix-framework | 0.15.0 |
| numpy | 2.4.6 |
| pandas | 2.3.3 |
| scipy | 1.17.1 |

Setup and run, from the repository root:

```bash
<python3.11> -m venv .venv                     # any Python 3.11 interpreter
.venv/Scripts/pip install -r requirements.txt prosimos==2.1.0   # .venv/bin/pip on Linux/macOS
.venv/Scripts/python evaluation/loanapp_p2p/scripts/build_stage2_baselines.py
```

The script rewrites `baselines/`, `healthy/`, `validation_report.md` and `validation_metrics.json`.
Prosimos has no seed parameter, so the script seeds Python's `random` and NumPy's global RNG before
each in-process run. With the package versions above, a rerun reproduces the stored values.

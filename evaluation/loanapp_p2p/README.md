# LoanApp / Procure2Pay Stage 1 evaluation

This folder holds everything needed to rerun and check the GLASS Stage 1 evaluation on two simulated
processes, a loan application (LoanApp) and procure-to-pay (Procure2Pay). Stage 1 is the step from a
natural-language goal to SMART KPIs. The question is whether GLASS proposes the KPIs a process analyst
would expect for a goal. There are 14 goal configurations (goals G1, G2, G3 and their combinations,
per process), each run 5 times. A small refinement pilot then gives feedback once on 9 of the first
proposals.

The thesis evaluation on BPIC 2012/2017 and Sepsis is separate: see `evaluation/README.md`.

## Folder map

```
evaluation/loanapp_p2p/
  README.md            this file
  .gitattributes       stores every file byte for byte (recorded SHA-256 values depend on it)
  setup/
    GLASS_expected_KPIs_and_evaluation_cases.xlsx   expected KPIs per goal; which model/log each case uses
    stage1_config.json      the 14 configurations, process descriptions and run settings (used by the scripts)
    pilot_config.json       the 9 refinement-pilot cases: accept/reject decisions and fixed feedback
    process_descriptions.md the two process descriptions, copied verbatim from stage1_config.json
  input_logs/          16 event logs (CSV): 14 given to GLASS, 2 baseline logs
  bps_models/          12 BPS models (BPMN): 2 baselines + 10 models of the combined/resource cases
  results/
    GLASS_Stage1_results.xlsx           scored results per configuration and per returned KPI
    GLASS_Stage1_refinement_pilot.xlsx  scored refinement pilot
    stage1_results/              raw outputs of the 70 Stage 1 runs (+ README, patch, verification report)
    stage1_results_normalised/   the same 70 outputs with GLASS's later measurable_as normalisation applied
    refinement_pilot/            the 9 round-1 refinement outputs
  scripts/
    run_stage1_batch.py            runs the 14 configurations x N repetitions through GLASS's Stage 1
    verify_stage1_results.py       independent check of a Stage 1 result folder
    reprocess_stage1_normalised.py re-applies the post-processing that produced stage1_results_normalised/
    run_refinement_pilot.py        runs one refinement round for the pilot cases
    verify_refinement_pilot.py     independent check of the pilot folder
    relocation.patch               how these scripts differ from the versions used at run time (see Notes)
```

## The 14 configurations

Each configuration gives GLASS one process description, one goal sentence and one event log. The
expected KPIs use the names from the setup workbook; `measurable_as` is the string GLASS should return
for them (both from `setup/stage1_config.json`).

| ID | Goal sentence given to GLASS | Event log (`input_logs/`) | Expected KPIs | Process description |
|---|---|---|---|---|
| LoanApp_G1 | I want customers to receive a final decision on their loan application sooner. | `LoanApp_durations_0.csv` | Average Cycle Time (`measurable_as`: Average Cycle Time) | [LoanApp](setup/process_descriptions.md#loanapp) |
| LoanApp_G2 | Once all the background checks on an application are done, it should not sit around waiting before someone assesses it. | `LoanApp_extraneous_0.csv` | Assess loan risk Waiting Time (`measurable_as`: Assess loan risk Waiting Time) | [LoanApp](setup/process_descriptions.md#loanapp) |
| LoanApp_G3 | We want to make better use of our loan officers without overloading them. | `LoanApp_resource_target_bimp.csv` | Loan Officer Utilization (`measurable_as`: Resource Utilization) | [LoanApp](setup/process_descriptions.md#loanapp) |
| LoanApp_G1G2 | We want customers to receive a final decision sooner, and applications should not sit around after their background checks are completed. | `LoanApp_G1G2_bimp.csv` | Average Cycle Time + Assess loan risk Waiting Time (`measurable_as`: Assess loan risk Waiting Time, Average Cycle Time) | [LoanApp](setup/process_descriptions.md#loanapp) |
| LoanApp_G1G3 | We want customers to receive a final decision sooner while making better use of our loan officers without overloading them. | `LoanApp_G1G3_bimp.csv` | Average Cycle Time + Loan Officer Utilization (`measurable_as`: Average Cycle Time, Resource Utilization) | [LoanApp](setup/process_descriptions.md#loanapp) |
| LoanApp_G2G3 | Applications should not sit around after their background checks are completed, while our loan officers should be used effectively without being overloaded. | `LoanApp_G2G3_bimp.csv` | Assess loan risk Waiting Time + Loan Officer Utilization (`measurable_as`: Assess loan risk Waiting Time, Resource Utilization) | [LoanApp](setup/process_descriptions.md#loanapp) |
| LoanApp_G1G2G3 | We want customers to receive a final decision sooner, avoid unnecessary delays after background checks are completed, and make better use of our loan officers without overloading them. | `LoanApp_G1G2G3_bimp.csv` | Average Cycle Time + Assess loan risk Waiting Time + Loan Officer Utilization (`measurable_as`: Assess loan risk Waiting Time, Average Cycle Time, Resource Utilization) | [LoanApp](setup/process_descriptions.md#loanapp) |
| Procure2Pay_G1 | We want to pay our suppliers sooner after a purchase is requested. | `Procure2Pay_durations_0.csv` | Average Cycle Time (`measurable_as`: Average Cycle Time) | [Procure2Pay](setup/process_descriptions.md#procure2pay) |
| Procure2Pay_G2 | Supplier invoices should not stay unattended before someone processes them. | `Procure2Pay_extraneous_0.csv` | Release Supplier's Invoice Waiting Time (`measurable_as`: Release Supplier's Invoice Waiting Time) | [Procure2Pay](setup/process_descriptions.md#procure2pay) |
| Procure2Pay_G3 | We want to make better use of our purchasing agents without overloading them. | `Procure2Pay_resource_target_bimp.csv` | Purchasing Agent Utilization (`measurable_as`: Resource Utilization) | [Procure2Pay](setup/process_descriptions.md#procure2pay) |
| Procure2Pay_G1G2 | We want to pay our suppliers sooner, and supplier invoices should not stay unattended before someone processes them. | `Procure2Pay_G1G2_bimp.csv` | Average Cycle Time + Release Supplier's Invoice Waiting Time (`measurable_as`: Average Cycle Time, Release Supplier's Invoice Waiting Time) | [Procure2Pay](setup/process_descriptions.md#procure2pay) |
| Procure2Pay_G1G3 | We want to pay our suppliers sooner while making better use of our purchasing agents without overloading them. | `Procure2Pay_G1G3_bimp.csv` | Average Cycle Time + Purchasing Agent Utilization (`measurable_as`: Average Cycle Time, Resource Utilization) | [Procure2Pay](setup/process_descriptions.md#procure2pay) |
| Procure2Pay_G2G3 | Supplier invoices should not stay unattended before processing, while our purchasing agents should be used effectively without being overloaded. | `Procure2Pay_G2G3_bimp.csv` | Release Supplier's Invoice Waiting Time + Purchasing Agent Utilization (`measurable_as`: Release Supplier's Invoice Waiting Time, Resource Utilization) | [Procure2Pay](setup/process_descriptions.md#procure2pay) |
| Procure2Pay_G1G2G3 | We want to pay our suppliers sooner, avoid unnecessary delays in processing supplier invoices, and make better use of our purchasing agents without overloading them. | `Procure2Pay_G1G2G3_bimp.csv` | Average Cycle Time + Release Supplier's Invoice Waiting Time + Purchasing Agent Utilization (`measurable_as`: Average Cycle Time, Release Supplier's Invoice Waiting Time, Resource Utilization) | [Procure2Pay](setup/process_descriptions.md#procure2pay) |

## Running

### Setup

```bash
pip install -r requirements.txt
cp goal_to_parameters/.env.example goal_to_parameters/.env   # then set one key in goal_to_parameters/.env:
#   OPENROUTER_API_KEY=...   (used for the stored runs)
#   OPENAI_API_KEY=...       (takes precedence when set)
```

The scripts read the key from `goal_to_parameters/.env`, which is git-ignored; never commit it.

Settings, all taken from `setup/stage1_config.json` and the GLASS code:

- **Model:** `gpt-4o-mini`, called as `openai/gpt-4o-mini` through OpenRouter (or as `gpt-4o-mini`
  directly with an OpenAI key). The provider string recorded in every run is `openrouter/openai/gpt-4o-mini`.
- **Temperature:** 0.2, the value `goal_to_parameters/app.py` hard-codes. The batch refuses to start if
  the config and app.py disagree.
- **JSON mode:** on. **Number of KPIs:** chosen by GLASS ("Auto"). **Log evidence:** on.
- **Repetitions:** 5 per configuration (`--reps` overrides), giving 14 x 5 = 70 runs.
- **Python and packages used:** Python 3.13.14, openai 3.22.1, pydantic 2.13.2.

Both scripts run GLASS's own code from `goal_to_parameters/app.py`, compiled from the file's source,
so a run always uses the GLASS version that is checked out. Two keyword checks of the web UI are
recorded but not enforced, because they would otherwise block most inputs. The goal check
(`validate_generation_scope`) would reject 12 of the 14 goal sentences. The feedback check
(`validate_refinement_scope`) would reject 7 of the 9 pilot feedback texts.

### Stage 1 batch

Run from the repository root:

```bash
# one call, LoanApp_G1 rep 1, to check the setup
python evaluation/loanapp_p2p/scripts/run_stage1_batch.py --smoke --out evaluation/loanapp_p2p/results/my_run
# all 14 configurations x 5 repetitions (70 calls), 4 parallel workers
python evaluation/loanapp_p2p/scripts/run_stage1_batch.py --workers 4 --out evaluation/loanapp_p2p/results/my_run
python evaluation/loanapp_p2p/scripts/verify_stage1_results.py --results evaluation/loanapp_p2p/results/my_run
```

Use a new `--out` folder. The default is `results/stage1_results`; the script resumes and skips every
stored run. A run writes, per configuration and repetition, `raw/<id>/rep_<k>.json`, holding the
prompts, every LLM answer, the parsed and post-processed KPIs and the run metadata. It also writes
`stage1_summary.csv`, `stage1_per_config.csv` and a `README.md` with the replication record.

If `goal_to_parameters/` has uncommitted changes, the batch records them as a patch. It only starts
once that patch is saved as `<out>/glass_local_fixes.patch`, and it prints the `git diff` command to
do so.

### Refinement pilot

```bash
python evaluation/loanapp_p2p/scripts/run_refinement_pilot.py --out evaluation/loanapp_p2p/results/my_pilot
```

That is 9 LLM calls, one refinement round each. The first proposals are read from
`results/stage1_results/` and `results/stage1_results_normalised/`; decisions and feedback come from
`setup/pilot_config.json`. Undecided KPIs are treated as accepted, as the UI requires a decision on
every KPI before refining. `--reprocess` re-applies only the deterministic post-processing to the
stored answers, with no LLM call. `verify_refinement_pilot.py` checks the stored pilot folder
`results/refinement_pilot/`.

### Checking the stored results

```bash
python evaluation/loanapp_p2p/scripts/verify_stage1_results.py
python evaluation/loanapp_p2p/scripts/verify_stage1_results.py --results evaluation/loanapp_p2p/results/stage1_results_normalised
python evaluation/loanapp_p2p/scripts/verify_refinement_pilot.py
```

The verifiers make no LLM calls. They re-derive everything they can from the raw outputs with their own
code and write `verification_report.md` into the folder. The Stage 1 verifier rebuilds each folder's
GLASS code from the base commit and the folder's patch. The pilot verifier re-executes the stored
post-processing with the checked-out code, which must equal the pilot's post-processing patch (true at
HEAD, see the table below). Current status:

- Both Stage 1 folders: PASS.
- Pilot: PASS except checks 4a/4b. In two cases (LoanApp_G2G3, Procure2Pay_G1G3) the model dropped
  an accepted KPI during refinement. This is a recorded finding, not an error in the data.

### Reproducing a result folder

LLM answers are not deterministic, so a rerun gives new samples. What can be reproduced exactly is
the code, the inputs and the settings of each folder; the stored raw outputs are the record. To rerun
with the exact GLASS code of a folder, use a separate worktree at the base commit, take this folder
from your clone and apply the folder's patch:

```bash
git worktree add ../glass-c787694 c787694
cd ../glass-c787694
git checkout <commit that added this folder> -- evaluation/loanapp_p2p   # from your clone: git log -1 --format=%H -- evaluation/loanapp_p2p
git apply evaluation/loanapp_p2p/results/stage1_results/glass_local_fixes.patch
mkdir -p evaluation/loanapp_p2p/results/rerun
git diff --output=evaluation/loanapp_p2p/results/rerun/glass_local_fixes.patch -- goal_to_parameters
# copy goal_to_parameters/.env from your clone, then:
python evaluation/loanapp_p2p/scripts/run_stage1_batch.py --workers 4 --out evaluation/loanapp_p2p/results/rerun
```

- **`stage1_results_normalised/`:** apply its own patch instead and run
  `scripts/reprocess_stage1_normalised.py`. It re-processes the stored outputs (no LLM call) and refuses
  to run on any commit other than the base commit.
- **Pilot LLM calls:** use `results/refinement_pilot/glass_local_fixes_llm_run.patch`.
- **Pilot post-processing:** this equals the current HEAD (table below), so
  `run_refinement_pilot.py --reprocess` reproduces it in a normal checkout.

## How the results were scored

The reported results were scored by hand, per returned KPI, by its name, scope and formula. The
rules are in sheet **"Settings and scoring"** of `results/GLASS_Stage1_results.xlsx`:

- **Expected KPI:** cycle time of the whole case; waiting time before the target activity; or
  utilization/workload of the target resource.
- **Near miss:** the cycle time of a segment only, or the waiting time at the neighbouring activity.
- **Not asked for:** everything else.

The same workbook has every assessment ("All KPIs returned by GLASS") and the totals per configuration
("Results per configuration"). The pilot is scored in `results/GLASS_Stage1_refinement_pilot.xlsx`.

The CSV files in the result folders also contain automatic strict/lenient columns. Their rules are in
the header of `scripts/run_stage1_batch.py` and are copied into each result README. They are analysis
aids only. Where they differ from the workbooks, the workbooks are the scoring to use. For example,
the lenient rule credits a waiting time at the neighbouring activity when its formula names the target
activity, so the pilot's automatic `outcome` column differs from the workbook for Procure2Pay G2 and
G2+G3.

## GLASS code versions

All result folders were produced from base commit `c787694272e40e96d3a217a76c8d46cc4f59bbca`, plus a
local patch stored in the folder. The latest of those changes is now committed as
`4c8dbafa2765c530047947945130415b4415bbf3` ("Stage 1 robustness: normalise evidence_basis labels, keep
KPIs with unsupported segmentation, canonical measurable_as").

| Result folder | GLASS code used | Patch SHA-256 | Where those changes are now |
|---|---|---|---|
| `results/stage1_results/` | base + `glass_local_fixes.patch` | `73166a37c3c430c0b015f154e496363f725c2d133f9b82cfd39c91ac46508ba5` | Both file changes unchanged in `4c8dbaf`; `4c8dbaf` adds later changes to `app.py` and `models/smart_kpi.py`. |
| `results/stage1_results_normalised/` | base + `glass_local_fixes.patch` | `d43ca746c1cfe464ceb3a8860b325bcbbfc19fa4842e5941cf75c736031232dc` | `utils/` and `models/smart_kpi.py` unchanged in `4c8dbaf`. `app.py` in `4c8dbaf` also has the `measurable_as_raw` fix (written only on change, first value wins). |
| `results/refinement_pilot/`, LLM calls | base + `glass_local_fixes_llm_run.patch` | `d43ca746c1cfe464ceb3a8860b325bcbbfc19fa4842e5941cf75c736031232dc` | As the row above. |
| `results/refinement_pilot/`, stored post-processing | base + `glass_local_fixes.patch` | `f45ac72c281282ac6c4016f431b6f0886893042444eb6a9a84f15726966827be` | Exactly commit `4c8dbaf`: `git diff c787694 4c8dbaf -- goal_to_parameters` is byte-identical to this patch. |

## Inputs for baseline comparison

Per configuration, GLASS Stage 1 received exactly three inputs:

1. **The process description**, verbatim. The app's input sanitizer changes only triple quotes, and the
   texts contain none.
2. **The goal sentence.**
3. **The event log CSV.** GLASS profiles it into log and context evidence inside the prompt.

The fixed settings above applied to every run. GLASS received no BPS model, no expected KPIs and no
workbook. The exact system prompt, few-shot messages and user prompt of every run are stored in
`results/stage1_results/raw/<id>/rep_<k>.json`, in the fields `system_prompt`, `few_shot_messages` and
`user_prompt`.

| ID | Process description | Goal sentence | Event log | BPS model of the case (not given to GLASS) |
|---|---|---|---|---|
| LoanApp_G1 | `setup/process_descriptions.md` § LoanApp | `setup/stage1_config.json` (`configurations[].simulation_goal`) | `input_logs/LoanApp_durations_0.csv` | `LoanApp_durations.bpmn` (paper's Zenodo package; not in this repo) |
| LoanApp_G2 | `setup/process_descriptions.md` § LoanApp | `setup/stage1_config.json` (`configurations[].simulation_goal`) | `input_logs/LoanApp_extraneous_0.csv` | `LoanApp_extraneous.bpmn` (paper's Zenodo package; not in this repo) |
| LoanApp_G3 | `setup/process_descriptions.md` § LoanApp | `setup/stage1_config.json` (`configurations[].simulation_goal`) | `input_logs/LoanApp_resource_target_bimp.csv` | `bps_models/LoanApp_resource_target.bpmn` |
| LoanApp_G1G2 | `setup/process_descriptions.md` § LoanApp | `setup/stage1_config.json` (`configurations[].simulation_goal`) | `input_logs/LoanApp_G1G2_bimp.csv` | `bps_models/LoanApp_G1G2.bpmn` |
| LoanApp_G1G3 | `setup/process_descriptions.md` § LoanApp | `setup/stage1_config.json` (`configurations[].simulation_goal`) | `input_logs/LoanApp_G1G3_bimp.csv` | `bps_models/LoanApp_G1G3.bpmn` |
| LoanApp_G2G3 | `setup/process_descriptions.md` § LoanApp | `setup/stage1_config.json` (`configurations[].simulation_goal`) | `input_logs/LoanApp_G2G3_bimp.csv` | `bps_models/LoanApp_G2G3.bpmn` |
| LoanApp_G1G2G3 | `setup/process_descriptions.md` § LoanApp | `setup/stage1_config.json` (`configurations[].simulation_goal`) | `input_logs/LoanApp_G1G2G3_bimp.csv` | `bps_models/LoanApp_G1G2G3.bpmn` |
| Procure2Pay_G1 | `setup/process_descriptions.md` § Procure2Pay | `setup/stage1_config.json` (`configurations[].simulation_goal`) | `input_logs/Procure2Pay_durations_0.csv` | `Procure2Pay_durations.bpmn` (paper's Zenodo package; not in this repo) |
| Procure2Pay_G2 | `setup/process_descriptions.md` § Procure2Pay | `setup/stage1_config.json` (`configurations[].simulation_goal`) | `input_logs/Procure2Pay_extraneous_0.csv` | `Procure2Pay_extraneous.bpmn` (paper's Zenodo package; not in this repo) |
| Procure2Pay_G3 | `setup/process_descriptions.md` § Procure2Pay | `setup/stage1_config.json` (`configurations[].simulation_goal`) | `input_logs/Procure2Pay_resource_target_bimp.csv` | `bps_models/Procure2Pay_resource_target.bpmn` |
| Procure2Pay_G1G2 | `setup/process_descriptions.md` § Procure2Pay | `setup/stage1_config.json` (`configurations[].simulation_goal`) | `input_logs/Procure2Pay_G1G2_bimp.csv` | `bps_models/Procure2Pay_G1G2.bpmn` |
| Procure2Pay_G1G3 | `setup/process_descriptions.md` § Procure2Pay | `setup/stage1_config.json` (`configurations[].simulation_goal`) | `input_logs/Procure2Pay_G1G3_bimp.csv` | `bps_models/Procure2Pay_G1G3.bpmn` |
| Procure2Pay_G2G3 | `setup/process_descriptions.md` § Procure2Pay | `setup/stage1_config.json` (`configurations[].simulation_goal`) | `input_logs/Procure2Pay_G2G3_bimp.csv` | `bps_models/Procure2Pay_G2G3.bpmn` |
| Procure2Pay_G1G2G3 | `setup/process_descriptions.md` § Procure2Pay | `setup/stage1_config.json` (`configurations[].simulation_goal`) | `input_logs/Procure2Pay_G1G2G3_bimp.csv` | `bps_models/Procure2Pay_G1G2G3.bpmn` |

The BPS model column comes from sheet "Evaluation cases" of
`setup/GLASS_expected_KPIs_and_evaluation_cases.xlsx`. The models of the G1 and G2 cases come from the
paper's Zenodo package and are not part of this repository: `LoanApp_durations.bpmn`, `LoanApp_extraneous.bpmn`, `Procure2Pay_durations.bpmn`, `Procure2Pay_extraneous.bpmn`. Their logs
(`*_durations_0.csv`, `*_extraneous_0.csv`) are in `input_logs/`. The workbook also lists the
modification behind each case and the average cycle time of each log.

- LoanApp baseline (healthy, no modification): `bps_models/LoanApp_original.bpmn` with log `input_logs/LoanApp_ground_truth_0.csv`. Not used as input to GLASS; kept as the reference for baseline comparisons.
- Procure2Pay baseline (healthy, no modification): `bps_models/Procure2Pay_original.bpmn` with log `input_logs/Procure2Pay_ground_truth_0.csv`. Not used as input to GLASS; kept as the reference for baseline comparisons.

## Notes

- **Config version.** `setup/stage1_config.json` is the configuration the runs used: temperature 0.2,
  SHA-256 `31eae7b67543d7485f2e31558888c332767c5f6c18e2a5f0017d05ed56586e88`. An earlier distributed
  version is identical except for temperature 0.3 (SHA-256
  `ce94197574a362a184412d367dfbb23d8f0b2c492b1d11281dd9edc0e97c1e3e`). It was changed on 2026-10-01 to
  match the temperature GLASS itself uses.
- **Script versions.** The scripts were moved here after the runs. Their paths now follow this folder
  layout, and the Stage 1 verifier rebuilds each folder's GLASS code from base commit + patch, so it no
  longer needs the working tree to equal it. `scripts/relocation.patch` records every difference. Applied in
  reverse (`git apply -R` in a scratch checkout), it restores the run-time versions whose SHA-256 values
  the result READMEs list.
- **Paths inside raw records.** Paths stored in the raw JSON records use the layout at run time
  (`evaluation/stage1_results/...`), for example `source_file` in the pilot records and
  `normalisation.source_file` in the normalised records. The records are kept byte-identical; the
  verifiers map these paths to `results/`.
- **Line endings.** `.gitattributes` turns off end-of-line conversion for this folder. The recorded
  SHA-256 values of logs, configs, raw records and patches refer to the exact bytes, so the files must
  not be converted on checkout.

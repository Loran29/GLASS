# GLASS evaluation experiments

One folder per experiment; paths below are relative to `evaluation/experiments/`. GLASS has two stages:
Stage 1 (Goal-to-KPI Translation) and Stage 2 (What-if Scenario Generation and Refinement, including the
Prosimos simulation, the KPI evaluation and the refinement loop).

**Rerunning.** Run the scripts from the repository root, in the environment and with the API key described in
the main `README.md`. The batch scripts resume: a run whose record already exists with a final status is
skipped, so pass `--out` (or `--results`) with a new folder to rerun next to the stored results. The LLM is not
seeded, so a rerun gives new samples; the stored records are the reported results. `shared/loanapp_p2p/lp_paths.py`
maps the paths stored in setup files and run records to the folders of this release.

## experiment_0_synthetic_variants_vs_original/

The modified LoanApp and Procure2Pay models compared with the original models, and the expected KPIs per goal.
- **Inputs:** `inputs/bps_models/` (original and modified models), `inputs/quality_measures.csv`.
- **Results:** `results/synthetic_vs_original_comparison.xlsx`, `results/Expected_KPIs.xlsx`,
  `results/Expected_KPIs_Author_A_Author_B_with_resolutions.xlsx`.
- **Scripts:** `scripts/build_workbook.py` (uses `scripts/bpmn_parser.py`).
- **Rerun:** from this folder, `python scripts/build_workbook.py` (no LLM).

## experiment_1_expected_kpi_generation/

Stage 1 on the 14 LoanApp/Procure2Pay goal configurations, 5 repetitions each (RQ1), and the KPI refinement pilot.
- **Inputs:** `shared/loanapp_p2p/setup/stage1_config.json` (process descriptions, goals), `shared/loanapp_p2p/input_logs/`.
- **Results:** `results/stage1_results_intial/` (70 run records in `raw/<configuration>/rep_<k>.json`),
  `results/refinement_pilot/`, `results/GLASS_Stage1_results.xlsx`, `results/GLASS_Stage1_refinement_pilot.xlsx`.
- **Scripts:** `scripts/run_stage1_batch.py`, `scripts/run_refinement_pilot.py`.
- **Rerun:** `python evaluation/experiments/experiment_1_expected_kpi_generation/scripts/run_stage1_batch.py --out <folder>`
  (`--smoke` for one configuration).

## experiment_2_valid_improving_kpis/

Stage 2 on the 14 configurations, 5 repetitions each, without the knowledge base (RQ2): the main Stage 2 results.
- **Inputs:** `shared/loanapp_p2p/setup/stage2_config.json`, the As-Is models in `shared/loanapp_p2p/stage2/baselines/`,
  the reference models in `shared/loanapp_p2p/stage2/healthy/`, and the Stage 1 KPIs of
  `experiment_1_expected_kpi_generation/results/` (raw records are normalised on loading, see
  `run_stage2_batch.read_kpi_record`).
- **Results:** `results/stage2_results_v32/V_no_kb/`: 70 run records (`raw/`), cached baseline evaluations
  (`baseline_logs/`), `stage2_summary.csv`, `stage2_per_config.csv`, `GLASS_Stage2_evaluation_plan.xlsx`,
  `GLASS_Stage2_with_modification_strategies.xlsx`.
- **Scripts:** `scripts/run_stage2_batch.py` (one run = one Stage 2 run with its 10-seed evaluation),
  `scripts/stage2_reporting.py`.
- **Rerun:** `python evaluation/experiments/experiment_2_valid_improving_kpis/scripts/run_stage2_batch.py --workers 4 --out <folder>`,
  then `python evaluation/experiments/experiment_2_valid_improving_kpis/scripts/stage2_reporting.py --results <folder>`.

## experiment_3_glass_vs_process_improvement_copilot/

GLASS compared with Process Improvement Copilot (RQ3): the Copilot's directly applicable recommendations were built
into simulation models by hand and simulated like GLASS's scenarios.
- **Inputs:** `inputs/repeat_runs/run_1/` to `run_5/` (Copilot outputs), `inputs/manual_models_source/` (applicability
  of each recommendation, hand-built models), `inputs/manual_models/`, `inputs/glass_event_logs/v3.2_rag_comparison/manifest.csv`.
  The GLASS side uses the run records of `experiment_2_valid_improving_kpis/` and of the Experiment 4 folder.
- **Results:** `results/comparison_repeat_runs_Summary.xlsx`, `results/kpi_values_manual_runs.csv`,
  `results/kpi_values_glass_extra_configs.csv`, `results/kpi_values_per_log.csv`, `results/GLASS_runtimes.xlsx`.
- **Scripts:** `scripts/build_repeat_run_models.py`, `scripts/simulate_repeat_runs.py`,
  `scripts/compare_repeat_runs_with_glass.py` (uses `scripts/kpi_comparison.py`).
- **Rerun:** from this folder, `python scripts/build_repeat_run_models.py jobs`, `python scripts/build_repeat_run_models.py build`,
  `python scripts/simulate_repeat_runs.py`, then `python -I scripts/compare_repeat_runs_with_glass.py` (no LLM). The simulated GLASS logs are not in the
  repository (see `manifest.csv`).

## Experiment_3_real_life_ablation/

Ablation on real-life logs (RQ4): AcademicCredentials and BPIC 2017 W, goals G1, G2 and G1+G2, variants V_full,
V_no_log, V_no_context and V_no_refine, 5 repetitions (120 Stage 2 runs), and a Stage 1 log-evidence ablation.
- **Inputs:** `data/` (models and test logs from https://zenodo.org/records/10435849; `data/BPIC_2012_W/` is input
  data only, without runs), `baselines/`, `setup/`.
- **Results:** `results/stage2/` (run records, `ablation_summary.csv`, `ablation_per_cell.csv`,
  `ablation_per_variant.csv`), `results/stage1/`, `results/GLASS_ablation_results.xlsx`.
- **Scripts:** `scripts/run_ablation_batch.py`, `scripts/ablation_reporting.py`, `scripts/run_stage1_ablation.py`,
  `scripts/build_config.py`, `scripts/select_baselines.py`, `scripts/compute_waiting_times.py`.
- **Rerun:** `python evaluation/experiments/Experiment_3_real_life_ablation/scripts/run_ablation_batch.py --workers 4 --out <folder>`,
  then `python evaluation/experiments/Experiment_3_real_life_ablation/scripts/ablation_reporting.py --results <folder>`.

## Experiment_3_synthetic_ablation/

The same ablation on LoanApp/Procure2Pay (RQ4): LoanApp_G1, LoanApp_G2, LoanApp_G1G2, Procure2Pay_G1,
Procure2Pay_G2, Procure2Pay_G1G2; variants V_main, V_no_log, V_no_context, V_no_refine, V_old_refine; 5 repetitions
(150 runs); knowledge base off in every variant.
- **Inputs:** those of `experiment_2_valid_improving_kpis/` for the six configurations.
- **Results:** `results/stage2/` (one folder per variant with its run records and summaries;
  `synthetic_ablation_summary.csv`, `synthetic_ablation_per_cell.csv`, `synthetic_ablation_per_variant.csv`),
  `results/GLASS_synthetic_ablation.xlsx`.
- **Scripts:** `scripts/run_stage2_synthetic_ablation.py`, `scripts/synthetic_ablation_reporting.py`,
  `scripts/verify_synthetic_ablation.py`.
- **Rerun:** `python evaluation/experiments/Experiment_3_synthetic_ablation/scripts/run_stage2_synthetic_ablation.py --workers 4 --out <folder>`,
  then `synthetic_ablation_reporting.py --results <folder>`; `verify_synthetic_ablation.py` checks the stored results.

## Refinement_replications_Sensitvity_Analysis/

Sensitivity analysis: the refinement loop judged on 1 or on 5 paired simulation seeds per iteration, on the 7 LoanApp
configurations, 5 repetitions each.
- **Inputs:** those of `experiment_2_valid_improving_kpis/` for LoanApp.
- **Results:** `results/seeds1/`, `results/seeds5/` (35 run records each, summaries), `results/loop_seeds_comparison.md`,
  `results/loop_seeds_comparison.csv`, `results/loop_seeds_runs.csv`, `results/GLASS_loop_seeds.xlsx`.
- **Scripts:** `scripts/compare_loop_seeds.py`.
- **Rerun:** `run_stage2_batch.py --only <LoanApp configurations> --loop-seeds 1 --out <folder>` and the same with
  `--loop-seeds 5` (these runs used the knowledge base: add `--use-knowledge-base`); `compare_loop_seeds.py` compares
  `results/seeds1/` and `results/seeds5/`.

## Experiment 4 Adding RAG to Stage 2 (not reported in the paper)/

Stage 2 with knowledge-base retrieval (RAG) on the 14 configurations, 5 repetitions each; the runs without it are those
of `experiment_2_valid_improving_kpis/`.
- **Inputs:** those of `experiment_2_valid_improving_kpis/`; `Papers/CaseStudy/README.md` lists the case-study
  references of the knowledge base.
- **Results:** `V_full_with RAG/` (70 run records, `baseline_logs/`, `stage2_summary.csv`, `stage2_per_config.csv`),
  `additional_experiment_rag_impact.md.txt` (write-up).
- **Scripts:** those of `experiment_2_valid_improving_kpis/`.
- **Rerun:** `run_stage2_batch.py --use-knowledge-base --workers 4 --out <folder>`.

## shared/

- `shared/loanapp_p2p/`: configurations (`setup/`), event logs (`input_logs/`), process models (`bps_models/`), As-Is
  and reference models (`stage2/`), the scripts that built them (`scripts/`) and `lp_paths.py`.
- `shared/analyses/`: run times of all batches (`results/runtimes.*`, `scripts/summarize_runtimes.py`).
- `shared/glass_versions/`: patches between the GLASS code versions named in the run records.

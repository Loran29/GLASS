# GLASS evaluation experiments

This folder holds the configurations, run records, results and scripts of the GLASS evaluation.

## Experimental setup

The implementation uses GPT-4o mini (`openai/gpt-4o-mini`), accessed through OpenRouter, for LLM-based generation and
Prosimos for business process simulation. During iterative refinement, each candidate scenario is evaluated using a
single simulation replication, with a maximum of four refinement iterations. The final evaluation uses 10 simulation
replications (seeds 42-51), paired with the *As-Is* baseline on the same seeds.

| Exp. | RQ | Evaluation | Processes | Config. | Runs | Folder |
|---|---|---|---|---|---|---|
| 1 | RQ1 | Stage 1: goal-to-KPI translation | LoanApp, P2P | 14 | 5/config. | `experiment_1_expected_kpi_generation/` |
| 2 | RQ2 | Stage 2: scenario validity, KPI improvement, refinement | LoanApp, P2P | 14 | 5/config. | `experiment_2_valid_improving_kpis/` |
| 2 | RQ3 | Stage 2 vs Process Improvement Copilot | LoanApp, P2P | 14 | 5/config. | `experiment_3_glass_vs_process_improvement_copilot/` |
| 3 | RQ4 | Ablation study | AcademicCredentials, BPIC 2017 W, BPIC 2012 W | 9 | 5/config. | `experiment_4_real_life_ablation/` |

Supporting analyses: `experiment_0_synthetic_variants_vs_original/` (the model modifications behind the evaluation
cases) and `experiment_5_loop_replications/` (the refinement loop with 1 vs 5 simulation replications per iteration). Shared
inputs are in `shared/loanapp_p2p/`, analyses across experiments in `shared/analyses/`, and the GLASS code versions the
runs used in `shared/glass_versions/`. Each experiment keeps its results (Excel workbooks, summary CSVs and the raw run
records) in its own `results/` folder.

## Evaluation cases

We selected two business processes, LoanApp and Procure2Pay, from an existing simulation-based process evaluation
study (Chapela-Campa et al., "A Framework for Measuring the Quality of Business Process Simulation Models"). The study
provides process models, event logs, and modified versions of the *As-Is* models in which activity durations, waiting
times and resource availability were deliberately altered. We reused these modified models to construct evaluation
cases with known underlying problems: for each modification we formulated a natural-language improvement goal and the
expected KPI. For example, a longer activity duration gives the goal "Complete loan applications sooner" and the KPI
average cycle time.

This gives three goals per process, reducing cycle time (G1), reducing waiting time (G2) and improving resource
utilization (G3), plus the combinations G1+G2, G1+G3, G2+G3 and G1+G2+G3: seven configurations per process, 14 in
total. Goals, expected KPIs and evaluation cases: `shared/loanapp_p2p/setup/GLASS_expected_KPIs_and_evaluation_cases.xlsx`.

For the ablation study we additionally used real-life datasets from an existing reproducibility package
(https://zenodo.org/records/10435849).

## Experiment 0 (supporting): model modifications

`experiment_0_synthetic_variants_vs_original/`

Documents what was changed in each modified LoanApp and Procure2Pay model compared with its original (resources,
activity durations, extraneous timers, calendars). This analysis is the basis for the goals and expected KPIs of the
evaluation cases. Result: `results/synthetic_vs_original_comparison.xlsx`.

## Experiment 1: Goal-to-KPI translation (RQ1)

`experiment_1_expected_kpi_generation/`

Evaluates Stage 1 of GLASS: its ability to translate natural-language improvement goals into measurable KPIs, and the
refinement effort required to align them with the intended goals. For each configuration, GLASS received the
improvement goal, the process description and the event log; the generated KPIs were compared with the expected ones.

- **Initial KPI generation:** 14 configurations x 5 runs, without refinement.
- **Human-guided refinement:** starting from a configuration's first run, relevant KPIs are accepted and unsuitable
  ones rejected with predefined natural-language feedback that describes the intended business requirement without
  naming KPIs or formulas. Later rounds clarify the relevant activities and measurement boundaries.

Results: `results/GLASS_Stage1_results.xlsx`, `results/GLASS_Stage1_refinement_pilot.xlsx`.

## Experiment 2: What-if scenario generation, refinement and comparison (RQ2, RQ3)

### Scenario validity, effectiveness and refinement (RQ2)

`experiment_2_valid_improving_kpis/`

Evaluates Stage 2 of GLASS: its ability to automatically generate valid, executable what-if scenarios that improve
the target KPIs, and the contribution of simulation-guided refinement. To isolate Stage 2, it uses the expected KPI
sets from experiment 1. Each run covers scenario generation, simulation and refinement; the initial scenario is
compared with the best scenario after refinement. A scenario is valid when it passes GLASS's validator, simulates, and
respects the business constraints (staffing, budget, working hours, immutable steps).

Results: `results/stage2_results_v32_rag/<arm>/` (summary CSVs and run records). The two arms are GLASS with the
knowledge base on (`V_full`) and off (`V_no_kb`).

### GLASS vs Process Improvement Copilot (RQ3)

`experiment_3_glass_vs_process_improvement_copilot/`

Investigates whether GLASS can automatically generate improvement scenarios with performance comparable to scenarios
derived from an existing recommendation-based approach, the Process Improvement Copilot (PI Copilot). The PI Copilot
was run on the same 14 configurations, process descriptions and constraints, five runs per configuration. Its
recommendations were classified as *directly applicable*, *applicable with assumptions* or *not applicable*; the
directly applicable ones were manually translated into executable what-if scenarios, simulated with the same settings
as GLASS, and compared with GLASS's scenarios on the target KPIs.

- `inputs/repeat_runs/`: per PI Copilot run its output, the classified recommendations, and the resulting models.
- `results/repeat_runs/comparison_repeat_runs.xlsx`: the KPI comparison.

The event logs are zip files in `inputs/archives/`; unzip them into `inputs/` before running the comparison. The two
largest (`glass_V_full_logs.zip`, `glass_V_no_kb_logs.zip`) exceed GitHub's 100 MB limit and are published separately
(download location: *to be added*).

```
cd evaluation/experiments/experiment_3_glass_vs_process_improvement_copilot/inputs
python -c "import zipfile, glob; [zipfile.ZipFile(z).extractall('.') for z in glob.glob('archives/*.zip')]"
```

## Experiment 3: Ablation study (RQ4)

`experiment_4_real_life_ablation/`

Investigates the contribution of individual framework components to the effectiveness of scenario generation and
refinement, and examines GLASS on real-life processes to strengthen external validity. Goals: reduce cycle time (G1),
reduce the waiting time of the activity with the longest mean wait (G2), and both (G1G2).

- **Stage 1:** with and without the event log.
- **Stage 2:** six variants, each switching off one component: V_full (all on), V_no_log (no log evidence),
  V_no_context (no business context), V_no_kb (no knowledge base), V_no_refine (no refinement), V_old_refine
  (earlier refinement rules).

Results: `results/GLASS_ablation_results.xlsx`.

## Experiment 5 (supporting): loop replications

`experiment_5_loop_replications/`

Repeats experiment 2's batch on the seven LoanApp configurations with the refinement loop judging each iteration on
one simulation replication (`seeds1`, the default) or on the mean of five paired replications (`seeds5`, seeds 42-46).
Everything else is unchanged, including the final evaluation on 10 seeds. Run records: `results/seeds1/`,
`results/seeds5/`; comparison: `results/loop_seeds_comparison.md`, `results/GLASS_loop_seeds.xlsx`.

## Analyses across experiments

`shared/analyses/`:
- `results/runtimes.md`: run times of every batch (`scripts/summarize_runtimes.py`).

It was computed including the Stage 2 batches v1-v3.1, which are not part of this package.

## Shared inputs

`shared/loanapp_p2p/`:
- `setup/`: configurations, process descriptions, goals and expected KPIs, refinement feedback.
- `input_logs/`, `bps_models/`: the event logs given to GLASS and the BIMP models behind them.
- `stage2/`: the Stage 2 baselines (BPMN + Prosimos JSON).
- `scripts/`: building the baselines and converting BIMP models to Prosimos input.

`shared/glass_versions/`: patches between the GLASS code versions the runs used (original commit IDs in the run
records).

## Running

Run the scripts from the repository root. Two Python environments are needed:
- GLASS: `pip install -r requirements.txt`; put `OPENROUTER_API_KEY` or `OPENAI_API_KEY` in
  `goal_to_parameters/.env` (git-ignored). Used for Stage 1.
- Prosimos: Python 3.11 with `prosimos==2.1.0` and GLASS's requirements. Used for Stage 2 and all simulations.

Each script's docstring gives its usage. Re-running a batch resumes: runs with a final status are skipped.

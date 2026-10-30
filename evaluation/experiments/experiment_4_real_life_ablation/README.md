# GLASS real-life ablation study

**Experiment 4.** GLASS v3.2 on three real-life event logs, six variants, 45 runs each. The full system (V_full) next to
the variant without the knowledge base (V_no_kb), from `results/stage2/ablation_per_variant.csv`:

| Measure                                 | V_full | V_no_kb |
| --------------------------------------- | ------ | ------- |
| Valid                                   | 35/45  | 40/45   |
| All KPIs improved                       | 18/45  | 28/45   |
| Admissible and all KPIs improved        | 17/45  | 28/45   |
| At least one KPI improved with p < 0.05 | 7/45   | 21/45   |
| Loop kept the baseline                  | 9/45   | 5/45    |

The other variants and the tests are in the sections below. The LoanApp / Procure2Pay scripts this study reuses are
in `../experiment_1_expected_kpi_generation/scripts/`, `../experiment_2_valid_improving_kpis/scripts/` and
`../shared/loanapp_p2p/scripts/`.

The study measures what each part of GLASS contributes on real event logs. Stage 1 runs with and without the
event log. Stage 2 runs in six variants, each switching off one component, on the three real-life datasets of
the Zenodo package 10.5281/zenodo.10435849.

- Stage 1: 3 datasets × 3 goals × 2 variants × 5 repetitions = 90 LLM calls.
- Stage 2: 3 datasets × 3 goals × 6 variants × 5 repetitions = 270 runs: repetitions 1–3 (162 runs), then
  repetitions 4–5 (108 runs) as a second pass with the same script, settings and seeds.

Contents:
- [Layout](#layout)
- [Settings](#settings)
- [Data and baselines](#data-and-baselines)
- [The activity X](#the-activity-x)
- [Process descriptions, goals and KPIs](#process-descriptions-goals-and-kpis)
- [Business context](#business-context)
- [Stage 1 ablation](#stage-1-ablation)
- [Stage 2 variants](#stage-2-variants)
- [Scoring](#scoring)
- [GLASS version and the runs before the batch](#glass-version-and-the-runs-before-the-batch)
- [Stage 2 results (five repetitions)](#stage-2-results-five-repetitions)
- [GLASS behaviour observed](#glass-behaviour-observed)
- [Reproduction](#reproduction)

## Layout

| Path | Content |
|---|---|
| `data/<dataset>/` | Test log, chosen baseline BPMN and Prosimos JSON, the package's timers file for that variant |
| `baselines/` | Baseline selection (CTD) and validation through GLASS's direct-input path (`validation_report.md`) |
| `setup/` | `ablation_config.json` (settings, goals, expected KPIs, business context), `process_descriptions.md`, `waiting_time_per_activity.csv`, `x_activity.json`, `stage2_kpis.json` (KPI objects passed to Stage 2) |
| `scripts/` | All scripts below |
| `results/stage1/` | Stage 1 records (`raw/`), `stage1_summary.csv`, `stage1_per_cell.csv`, `run_info.json` |
| `results/stage2/` | Stage 2 records (`raw/`), `ablation_summary.csv`, `ablation_per_cell.csv`, `ablation_per_variant.csv`, `verification.md`; the 10-seed baseline cache (`baseline_logs/<dataset>/baseline.json`). Simulated logs are kept out of git; their SHA-256 values are in the records. |
| `results/GLASS_ablation_results.xlsx` | Workbook (overview, per cell, all runs, Stage 1, setup) |

## Settings

| Setting | Value |
|---|---|
| Model | gpt-4o-mini (OpenAI) |
| Stage 1 temperature | 0.2, GLASS app's value, read from `app.py` as in the Step 1 batch |
| Stage 2 temperatures | GLASS's loop schedule min(0.3 + 0.1 × (iteration − 1), 0.7); context-summary and compliance calls 0.1 |
| Refinement | `max_iterations` 4 (V_no_refine: 1) |
| Evaluation | 10 seeds (42–51), paired with a cached 10-seed baseline per dataset; the loop runs at seed 42 |
| Cases and start | Test log's case count and first start: AcademicCredentials 398 / 2016-04-15 23:47:55; BPIC_2012_W 2,976 / 2012-01-07 09:04:12.635; BPIC_2017_W 7,376 / 2016-09-17 06:07:22 (UTC) |
| Repetitions | 5; first pass 1–3, second pass 4–5 |

## Data and baselines

From the Zenodo package, per dataset: the test log and the baseline model. The baseline is the timer-enhanced
variant whose 10 simulated logs in the package have the lowest mean cycle-time distribution distance (CTD) to
the test log. `metrics.csv` reports only event-distribution distances, so CTD was computed from the package's
own logs (`scripts/select_baselines.py`).

| Dataset | Chosen variant | CTD, package logs vs test log | Timers kept | Mean cycle time, one GLASS run vs test log | vs package's simulation |
|---|---|---|---|---|---|
| AcademicCredentials | `complex_hyperopt_holdout_after_enhanced` | 52.07 | 15 / 15 | +15.7 % | +1.9 % |
| BPIC_2012_W | same | 23.88 | 6 / 6 | −11.0 % | −3.3 % |
| BPIC_2017_W | same | 21.69 | 7 / 7 | +0.1 % | −6.4 % |

Each baseline was validated through GLASS's direct-input path, as in Stage 2 (`baselines/validation_report.md`).

- **Calendar rounding.** GLASS stores calendar times in whole hours, so the models' 15- and 12-minute
  boundaries are rounded. Weekly available hours change by +6.84 %, +1.48 % and +1.60 %. Arrival calendars are
  unchanged.
- **Events per case.** In BPIC_2012_W and BPIC_2017_W the model produces fewer events per case than the test
  log (3.93 vs 6.24; 3.69 vs 7.05). The package's own simulated logs show the same, so this is a property of the
  discovered models, not of the import.
- **Every resource profile has one person**: 306, 47 and 105 profiles.

## The activity X

X is the activity with the largest mean waiting time in the test log, among activities that occur in at least
10 % of its cases (`scripts/compute_waiting_times.py`). A waiting time is an event's start minus the end of the
preceding event in the case. The minimum share keeps X from resting on a handful of events. The same X results
under the clipped rule and the latest-ended-predecessor rule.

| Dataset | X | Mean waiting | Cases with X | Largest without the 10 % minimum |
|---|---|---|---|---|
| AcademicCredentials | Notificacion estudiante cancelacion soli | 111.11 h | 208 of 398 | Validacion final (342.29 h, 28 cases) |
| BPIC_2012_W | W_Nabellen offertes | 73.44 h | 1,397 of 2,976 | (same) |
| BPIC_2017_W | W_Validate application | 75.40 h | 4,847 of 7,376 | (same) |

The full table is `setup/waiting_time_per_activity.csv`.

## Process descriptions, goals and KPIs

`setup/process_descriptions.md` describes each model's activities, order, choices and parallel branches, written
from the BPMN alone. It speaks of "the staff" (the models have no roles), gives each step in plain English
followed by the model's activity name, and contains no numbers or KPI hints. Timers are not described.

| Goal | Sentence | Expected KPIs |
|---|---|---|
| G1 | "We want credential requests / loan applications to be completed sooner." | Average Cycle Time |
| G2 | "Credential requests / Loan applications should not sit waiting before ⟨X as a manager would say it⟩." | ⟨X⟩ Waiting Time |
| G1G2 | both sentences | both |

The manager's wording of X: AcademicCredentials "the student is notified of the cancellation"; BPIC_2012_W "the
follow-up calls on offers"; BPIC_2017_W "the application validation".

## Business context

Three clarification answers are sent as user turns (all variants except V_no_context):

| Topic | Answer (as sent in the batch) |
|---|---|
| Staffing | "Each role may grow by one additional person with the same skills (for example, a role with one person may have two). Working hours may be extended by up to two hours per day." |
| Budget | "Staff cost may increase by at most 35 %; hourly cost 20 EUR for everyone." |
| Immutable | "The order of the steps must stay, and no activity may be removed or added. Activity durations, staffing, working hours and waiting times between steps may be changed." |

**Wording history.** The staffing and immutable answers were reworded after the smoke runs and before the
batch. Each earlier wording was misread by GLASS:

| Topic | Earlier wording | What happened |
|---|---|---|
| Staffing 1 | "Each resource group may be increased by up to 20 % of its current size, at least one person; working hours may be extended by up to two hours per day." | Fractional headcounts (1 → 1.2) on the one-person profiles, which the merger skips |
| Staffing 2 | "Any person's role may be reinforced by one additional person with the same skills; overall, staff may grow by at most 20 %. Working hours …" | GLASS's compliance check read the 20 % as a per-role cap (1 → 2 = +100 %) and rejected +1. The overall cap was dropped: GLASS's constraint model has per-role maxima and a budget only, with no global headcount cap distinct from them (known gap, next version). The +35 % cost limit is the binding total constraint. |
| Staffing 3 | "Each role may be reinforced by at most one additional person with the same skills. Working hours …" | The compliance check still judged 1 → 2 a violation |
| Immutable 1 | "The order of the steps and the activities themselves must stay; who does what, how long steps take and how long cases wait between steps can be improved." | GLASS's feasibility check treated the activities' durations as immutable |

**What GLASS keeps of the context.** GLASS's context summary is what reaches the prompt. It drops parts of the
answers: the staffing rule (in the first wordings) and the 20 EUR rate. This is GLASS behaviour and was left
unchanged. The batch's validity check enforces the real rules (see Scoring).

## Stage 1 ablation

`scripts/run_stage1_ablation.py` reuses the Step 1 batch's functions unchanged (app loader, recording provider,
scoring). The two variants:

- **full:** the test log is passed as an uploaded log: log profile, log evidence and context evidence.
- **no_log:** no log is passed.

Scoring is as in Step 1: an expected KPI is found strictly (exact `measurable_as`) or leniently (name, scope and
formula rules), and the remaining KPIs are extras. Consistency per cell is reported three ways, none of them
primary: the mean pairwise Jaccard of the KPI sets, category stability (thesis M4) and the distinct
`measurable_as` values.

| Dataset | Goal | full: strict, all expected found | no_log: strict, all expected found | full / no_log: lenient | Extras (strict), full / no_log | Jaccard, full / no_log |
|---|---|---|---|---|---|---|
| AcademicCredentials | G1 | 5/5 | 5/5 | 5 / 4 | 1.6 / 2.6 | 0.48 / 0.80 |
| | G2 | 5/5 | 0/5 | 4 / 0 | 1.6 / 1.2 | 0.40 / 0.60 |
| | G1G2 | 0/5 | 0/5 | 5 / 0 | 2.2 / 1.6 | 0.25 / 0.34 |
| BPIC_2012_W | G1 | 5/5 | 5/5 | 5 / 3 | 2.2 / 3.4 | 0.57 / 0.69 |
| | G2 | 5/5 | 0/5 | 5 / 4 | 1.2 / 3.0 | 0.87 / 0.76 |
| | G1G2 | 2/5 | 0/5 | 5 / 2 | 1.0 / 2.8 | 0.46 / 0.77 |
| BPIC_2017_W | G1 | 5/5 | 5/5 | 5 / 5 | 1.4 / 3.4 | 0.31 / 0.69 |
| | G2 | 1/5 | 0/5 | 5 / 3 | 1.0 / 2.2 | 0.55 / 0.29 |
| | G1G2 | 1/5 | 0/5 | 3 / 1 | 0.8 / 1.8 | 0.28 / 0.53 |

Summary:
- **Every expected KPI found, strictly:** full 29 of 45 repetitions, no_log 15 of 45.
- **X's waiting time, strictly:** full 14 of 30, no_log 0 of 30. Without the log the model never names the
  activity's waiting time as GLASS computes it.
- **Cycle time:** found equally often in both variants.
- **Extras:** no_log proposes more extra KPIs in 8 of 9 cells.
- **Lenient and strict are separate rules, as in Step 1.** The lenient rule also requires scope and category, so
  a lenient count can be lower than the strict one.

**KPI objects for Stage 2** (`setup/stage2_kpis.json`), taken from variant full:
1. the strict match in repetition 1, else the first repetition with a strict match;
2. else the strict match from the dataset's single-goal configuration that expects that KPI (lead author's
   decision);
3. only then a lenient match.

All nine configurations got strict matches. Exceptions to "repetition 1 of the own cell":
- AcademicCredentials_G1G2: cycle time from repetition 3. X from AcademicCredentials_G2, repetition 1. Its only
  X match in its own cell was lenient, with a `measurable_as` that GLASS resolves to the process-wide Average
  Waiting Time.
- BPIC_2012_W_G1G2: X from repetition 3.
- BPIC_2017_W_G2: X from repetition 3.

The Stage 1 calls ran under the configuration before the business-context answers were reworded (SHA-256
`1e234bfc…` in `run_info.json`). Stage 1 does not use those answers, and its goals and descriptions are unchanged.

## Stage 2 variants

`scripts/run_ablation_batch.py` derives from the LoanApp/P2P Stage 2 batch and reuses its recorder, seed shim,
capture wrappers, KPI table and log preprocessing. Each variant is a switch on the same code path, recorded in
every run record as `variant` and `switches`:

| Variant | Switch |
|---|---|
| V_full | log profile, clarification context, knowledge base, up to 4 iterations, current refinement rules |
| V_no_log | `log_profile = None` for the draft payload and the loop (no log or context evidence in the Stage 2 prompt) |
| V_no_context | no clarification turns (GLASS defaults: no operational-constraint summary) |
| V_no_kb | no knowledge-base retrieval and no knowledge-base material, in generation and feedback. `knowledge.retrieval.retrieve_for_second_llm` is replaced by a stub that retrieves nothing (calls counted), the evidence's `kb_json` is emptied so the prompt has no knowledge-base section, and the feedback's alternative-intervention retrieval returns nothing. |
| V_no_refine | `max_iterations = 1` |
| V_old_refine | `run_iterative_evaluation(legacy_refinement=True)`: stop at the first iteration whose overall status is "improved", best iteration by score alone (the v2 rules) |

Per run, the records hold:
- every iteration: the LLM calls, patches, validator results and merged scenarios, the loop's single-run
  evaluation, and the feedback;
- the 10-seed evaluation of the initial (iteration 1) and final (the loop's best iteration) scenario, paired
  with the cached baseline;
- which sections each generation prompt contained.

## Scoring

The scoring order: scenario produced → valid → executable → merge stability > 0 → all KPIs improved → DHR →
NIS → secondary measures. Each measure applies to the final scenario, and the initial's is recorded too.

**Primary measures**

- **scenario produced:** the final has a merged scenario. A run whose loop kept the baseline
  (`no_improving_scenario`, below) has none.
- **valid:** the patch validator passed and the business context holds on the merged scenario:
  - at most +1 resource per resource profile;
  - at most +2 h of working time per resource and weekday;
  - weekly staff cost at most +35 %: 20 EUR × each resource's weekly calendar hours, as GLASS's
    `staff_cost_per_week` computes it, but with the context's fixed rate. Stage 2 on LoanApp/P2P used the hourly
    headcount cost; calendar extensions now count.
  - every role's hourly rate stays 20 EUR (`rate_changed` otherwise);
  - the same activities as the baseline;
  - start-event arrival unchanged (`demand_changed` otherwise). Customer demand is not a company lever. Only
    applied changes count; a proposed `inter_arrival_time` change that the merger skipped is reported as
    `demand_proposed`.
  - Branching probabilities may change: they change how often a path is taken, not the order.
- **executable:** the 10-seed evaluation simulated on all seeds.
- **merge stability:** applied ÷ proposed modifications of the attempt GLASS accepted. "Proposed" counts the
  modifications in the LLM's answer before GLASS's reject steps; no proposal gives no value, counted as not > 0.
  GLASS's own value (applied ÷ modifications reaching the merger, 0/0 = 1.0) is recorded as
  `glass_merge_stability`.
- **admissible:** valid, executable and merge stability > 0.
- **KPIs:** per KPI, the 10-seed means, 95 % CI, paired t-test p-value, significance (p < 0.05) and GLASS's
  direction rule for "improved". `all_kpis_improved` means every KPI improved.
- **DHR** (thesis): the share of KPIs that improved.
- **NIS** (thesis): the mean over KPIs of |% change|, positive if improved, negative otherwise.

**Secondary measures**

- **Change profile:** modifications per parameter type, and the share aimed at X or at a role or resource that
  performs X in the baseline.
- **Distance to the real test log:** CTD, RED and CWD. The simulated logs get the midnight-end mapping of the
  Stage 2 batch; the test log is unchanged.
- **`loop_discarded_better_initial`:** the final differs from the initial, the initial is admissible, and one of:
  - (a) the final is not admissible;
  - (b) DHR_initial > DHR_final;
  - (c) equal DHR and NIS_initial − NIS_final > 1 percentage point.

  A kept baseline counts only if the initial was better than the baseline over 10 seeds (DHR > 0 or NIS > +1
  percentage point): "a: final not admissible (baseline kept)".
- **`no_improving_scenario`:** the loop kept the baseline (GLASS v3.2).
- **`direction_inconsistent_rejections`:** modifications GLASS v3.2 rejected because their stated direction
  contradicts their values.
- **Iterations used.**

**Per-variant comparison** (`ablation_per_variant.csv`): V_full minus each other variant. Rates are compared in
percentage points with Fisher's exact test. Means are compared as the mean paired difference (same configuration
and repetition) with a sign test.

**Completion step** (`scripts/complete_ablation_records.py`). The batch, like the Stage 2 batch it derives from,
stops evaluating a run when iteration 1 produced no scenario. For those runs (`status_at_run` =
`no_initial_proposal`), the final is evaluated afterwards with the batch's own phase evaluation and no LLM calls.
If the loop kept the baseline instead, that is recorded.

**Verification** (`scripts/verify_ablation.py`) independently recomputes, from the records, logs and inputs:
- validity, executability (log SHA-256 values) and merge stability;
- the KPI means, % changes, improved flags and p-values;
- DHR, NIS, the change profile and the distance means, with a recomputation from the logs for a sample;
- the kept-baseline rule, the direction rejections, `loop_discarded_better_initial` and the switches;
- the per-variant table.

## GLASS version and the runs before the batch

The batch ran on GLASS `c2f7f53` (v3.2; `evaluation/experiments/experiment_2_valid_improving_kpis/results/VERSIONS.md`). Commits made during this
study:

| Commit | Change |
|---|---|
| `c328453` | `legacy_refinement` flag: the earlier stop and selection rules, off by default; used only by V_old_refine |
| `7748a58` | The loop's KPI targets carry `measurable_as`. Before, the loop measured a waiting-time KPI by name, e.g. as the process-wide Average Waiting Time. The LoanApp/P2P batches had the same gap in 5 of 14 configurations; it is recorded in `VERSIONS.md`. |
| `19c8478` | A modification whose stated direction contradicts its values is rejected (`direction_inconsistent`), not merged with a warning |
| `c2f7f53` | The loop keeps the baseline when no iteration reaches a positive score (`no_improving_scenario`) |

Runs before the batch, none of them reported:
- **Smoke runs:** all six variants on BPIC_2012_W_G1G2 rep 1, on `c328453` and `7748a58`.
- **Three check runs:** V_full on the same cell, for the reworded context.
- **A first batch attempt on `7748a58`:** stopped after 23 runs, when two final scenarios with +243 % and +686 %
  cycle time exposed the direction and keep-baseline issues. It is kept locally only, in the gitignored
  `results/ablation_aborted_local/`.

The batch then restarted from scratch on v3.2; only the cached baseline runs were reused.

During the first pass the computer went into standby twice. Runs paused and continued; two runs failed with
connection errors (`llm_error`) and were re-run.

**Second pass (repetitions 4–5).** Same script, configuration, KPI objects and seeds; GLASS code unchanged. The
records show `glass_commit` `07fa5f7`, the commit that added this study's first-pass results; `goal_to_parameters/`
is identical to `c2f7f53` (`git diff c2f7f53 07fa5f7 -- goal_to_parameters` is empty). The pass was interrupted
by the session ending and by standby (lid closed) and resumed each time; runs paused, none failed. After 76 of
the 108 runs it was restarted with 4 workers instead of 2. Each run is an independent process with its own fixed
seeds, and the Python Prosimos backend has no timeout, so the worker count changes only the run order and
`elapsed_s`.

## Stage 2 results (five repetitions)

The two passes produced 270 records, all with status `ok` after the completion step. Twelve runs had
`status_at_run = no_initial_proposal` (7 in repetitions 1–3, 5 in 4–5): nine were completed with their final
(iteration 2) and three with a kept baseline. The verifier recomputes everything listed under Scoring and finds
0 mismatches (`results/stage2/verification.md`), including the distances recomputed from the logs for a sample of
three runs. A tampered copy (a validity flag, an NIS value and a switch changed) was detected.

**Per variant** (`results/stage2/ablation_per_variant.csv`; 45 runs per variant). The p-value compares each
variant with V_full: Fisher's exact test for rates, a paired sign test for means. Rates count all runs. Means
(DHR, NIS, distances, merge stability) are over the runs with a final scenario.

| Measure | V_full | V_no_log | V_no_context | V_no_kb | V_no_refine | V_old_refine |
|---|---|---|---|---|---|---|
| Scenario produced | 36/45 | 32/45 (p 0.462) | 38/45 (p 0.784) | 40/45 (p 0.384) | 31/45 (p 0.334) | 41/45 (p 0.230) |
| Valid | 35/45 | 32/45 (p 0.629) | 38/45 (p 0.591) | 40/45 (p 0.258) | 31/45 (p 0.475) | 41/45 (p 0.144) |
| Executable | 36/45 | 32/45 (p 0.462) | 38/45 (p 0.784) | 40/45 (p 0.384) | 31/45 (p 0.334) | 41/45 (p 0.230) |
| Merge stability > 0 | 36/45 | 32/45 (p 0.462) | 38/45 (p 0.784) | 40/45 (p 0.384) | 31/45 (p 0.334) | 41/45 (p 0.230) |
| All KPIs improved | 18/45 | 16/45 (p 0.828) | 19/45 (p 1.000) | 28/45 (p 0.057) | 19/45 (p 1.000) | 19/45 (p 1.000) |
| DHR | 0.54 | 0.53 (p 1.000) | 0.53 (p 1.000) | 0.75 (p 0.035) | 0.69 (p 0.219) | 0.52 (p 1.000) |
| NIS | 2.67 | 1.48 (p 1.000) | 3.54 (p 0.093) | 11.02 (p 0.728) | 4.48 (p 0.503) | 3.02 (p 1.000) |
| Admissible (valid, executable, merge stability > 0) | 35/45 | 32/45 (p 0.629) | 38/45 (p 0.591) | 40/45 (p 0.258) | 31/45 (p 0.475) | 41/45 (p 0.144) |
| Admissible and all KPIs improved | 17/45 | 16/45 (p 1.000) | 19/45 (p 0.830) | 28/45 (p 0.034) | 19/45 (p 0.830) | 19/45 (p 0.830) |
| Average Cycle Time improved (G1, G1G2) | 13/30 | 10/30 (p 0.596) | 15/30 (p 0.796) | 23/30 (p 0.017) | 15/30 (p 0.796) | 12/30 (p 1.000) |
| X waiting time improved (G2, G1G2) | 15/30 | 14/30 (p 1.000) | 15/30 (p 1.000) | 19/30 (p 0.435) | 17/30 (p 0.796) | 18/30 (p 0.604) |
| At least one KPI improved with p < 0.05 | 7/45 | 5/45 (p 0.758) | 6/45 (p 1.000) | 21/45 (p 0.003) | 11/45 (p 0.430) | 9/45 (p 0.784) |
| Loop kept the baseline (no_improving_scenario) | 9/45 | 13/45 (p 0.462) | 7/45 (p 0.784) | 5/45 (p 0.384) | 14/45 (p 0.334) | 4/45 (p 0.230) |
| Loop discarded a better initial | 1/45 | 3/45 (p 0.616) | 1/45 (p 1.000) | 0/45 (p 1.000) | 0/45 (p 1.000) | 2/45 (p 1.000) |
| At least one direction_inconsistent rejection | 14/45 | 14/45 (p 1.000) | 13/45 (p 1.000) | 3/45 (p 0.006) | 10/45 (p 0.475) | 11/45 (p 0.638) |
| direction_inconsistent rejections per run | 1.33 | 1.04 (p 1.000) | 1.09 (p 0.344) | 0.18 (p 0.007) | 0.40 (p 0.003) | 0.71 (p 0.180) |
| Merge stability (applied / proposed) | 0.89 | 0.85 (p 0.289) | 0.82 (p 0.508) | 0.97 (p 0.180) | 0.88 (p 1.000) | 0.85 (p 0.727) |
| Merge stability, GLASS's value | 0.98 | 1.00 (p 1.000) | 0.96 (p 1.000) | 1.00 (p 0.500) | 1.00 (p 0.500) | 1.00 (p 0.500) |
| Share of modifications on X | 0.53 | 0.40 (p 0.031) | 0.56 (p 0.754) | 0.31 (p 0.041) | 0.54 (p 0.549) | 0.54 (p 0.790) |
| Modifications | 2.22 | 2.09 (p 1.000) | 2.00 (p 0.070) | 2.23 (p 1.000) | 2.32 (p 0.754) | 2.05 (p 0.424) |
| Iterations used | 2.18 | 2.22 (p 0.839) | 1.98 (p 0.405) | 1.71 (p 0.296) | 1.00 (p 0.000) | 1.91 (p 0.572) |
| CTD to the test log | 39.54 | 38.46 (p 0.839) | 39.35 (p 0.405) | 52.42 (p 0.035) | 45.08 (p 0.263) | 42.35 (p 0.557) |
| RED to the test log | 66.55 | 63.52 (p 0.839) | 63.78 (p 0.678) | 67.05 (p 0.163) | 62.95 (p 1.000) | 63.05 (p 0.845) |
| CWD to the test log | 1.93 | 1.86 (p 0.541) | 1.98 (p 1.000) | 1.98 (p 0.296) | 1.97 (p 0.115) | 2.08 (p 0.845) |
| inter_arrival_time proposed (skipped by the merger) | 0/45 | 0/45 (p 1.000) | 0/45 (p 1.000) | 0/45 (p 1.000) | 0/45 (p 1.000) | 0/45 (p 1.000) |
| Hourly rate changed | 0/45 | 0/45 (p 1.000) | 0/45 (p 1.000) | 0/45 (p 1.000) | 0/45 (p 1.000) | 0/45 (p 1.000) |

**Reading the table**

- **Primary measures.** No variant is significantly worse than V_full on any primary measure, and V_full is not
  ahead on any of them.
  - V_no_refine and V_no_log produce a scenario least often: 31 and 32 of 45, against 36 for V_full. The loop
    kept the baseline in 14 and 13 runs. Neither difference is significant.
  - V_no_kb improves most often: all KPIs in 28 of 45 runs against 18 (p = 0.057), admissible and all KPIs
    improved 28 against 17 (p = 0.034), at least one KPI significantly in 21 against 7 (p = 0.003), DHR 0.75
    against 0.54 (p = 0.035). It also has the highest NIS (+11.0 against +2.7; p = 0.728).
  - With knowledge-base material, the model more often states changes whose direction contradicts their values:
    1.33 rejections per run for V_full against 0.18 for V_no_kb (p = 0.007). V_no_kb also aims fewer changes at
    X: a share of 0.31 against 0.53 (p = 0.041).
  - V_no_kb's final logs are further from the real test log in control flow (CTD 52.4 against 39.5, p = 0.035).
- **Log evidence aims changes at X.** Without it (V_no_log), the share of modifications on X falls from 0.53 to
  0.40 (p = 0.031).
- **Refinement.**
  - V_old_refine (the v2 rules) ends with an admissible scenario most often (41 of 45), with no more
    improvements than V_full.
  - "Loop discarded a better initial" is rare in all variants (0–3 of 45).
- **Validity.** Only one final scenario that was produced is not valid, the same as after repetitions 1–3:
  V_full, AcademicCredentials_G1G2 rep 2. The model extended working hours on 10183_profile. That profile's
  calendar is shared by 289 of the 306 resources, so the change applied to all of them and staff cost rose by
  +311 %. GLASS's budget check rejected the scenario, but GLASS returned it after three attempts as the best
  available proposal. The batch scores it not valid. Every produced final was executable.
- **Constraints never broken.** No final changed demand or an hourly rate.
- **By dataset.** Runs with a significant KPI improvement: AcademicCredentials 39 of 90, BPIC_2012_W 12 of 90,
  BPIC_2017_W 8 of 90. On AcademicCredentials, the levers that work shorten the timer before X; activity
  durations of seconds barely move cycle times of days. The loop kept the baseline in 27, 20 and 5 runs.
- **The two passes agree** on V_no_kb's lead: DHR 0.75 in both; NIS +11.5 and +10.2; at least one KPI
  significantly improved in 13 of 27 and 8 of 18 runs, against 5 of 27 and 2 of 18 for V_full. The other
  variants change places between passes. V_no_log kept the baseline in 11 of 27 runs in repetitions 1–3 and in 2
  of 18 in repetitions 4–5; its NIS was +3.5 and −0.6.
- **Power.** With 45 runs per variant, only V_no_kb's differences reach p < 0.05 on the primary measures. The
  other differences among V_full, V_no_log, V_no_context and V_old_refine are within run-to-run variation.

## GLASS behaviour observed

These are reported as findings; GLASS was changed only where noted.

- **Context summary and compliance check misread clear answers** (see Business context). GLASS's semantic
  compliance check, an LLM call at temperature 0.1, judged the same "1 → 2" as compliant in one attempt and as
  exceeding "one additional person" in another. It also flagged duration decreases as violating "Activity
  durations … may be changed". The batch's validity check enforces the real rules.
- **Timer waiting confused with processing time.** On AcademicCredentials the model quoted a timer's duration
  (e.g. 235,566 s) as an activity's processing time (825.5 s). After GLASS's baseline check corrected the quote,
  it kept its target value, so a "decrease" became an increase of up to 218×. This was found in the first batch
  attempt; since `19c8478` such a modification is rejected (`direction_inconsistent`).
- **Why iteration 1 produced no scenario.** GLASS rejected every attempt of iteration 1 in the 12
  `no_initial_proposal` runs:
  - in 8, the model changed a timer's duration with the following activity's name as the target ("W_Call after
    offers"), although the prompt lists the timers, some named "Timer before W_Call after offers";
  - in 3 (AcademicCredentials_G2), it "decreased" an instantaneous activity's duration from 0 s to 0 s (no-op);
  - in 1, it used direction "differentiate" without a context condition.
- **The loop's single seed misleads.** The loop judges a proposal on one run (seed 42); the final evaluation uses
  10 seeds. Adding one person to a one-person profile often "improves" at seed 42 and is slightly worse over 10
  seeds.
- **Shared calendars spread a calendar change**, and GLASS returns a proposal its own budget check rejected when
  all three attempts fail (see Validity above).

## Reproduction

From the repository root, in GLASS's environment with Prosimos and log-distance-measures:

```
python evaluation/experiments/experiment_4_real_life_ablation/scripts/select_baselines.py <Zenodo package folder>
python evaluation/experiments/experiment_4_real_life_ablation/scripts/validate_baselines.py <Zenodo package folder>
python evaluation/experiments/experiment_4_real_life_ablation/scripts/compute_waiting_times.py
python evaluation/experiments/experiment_4_real_life_ablation/scripts/build_config.py
python evaluation/experiments/experiment_4_real_life_ablation/scripts/run_stage1_ablation.py
python evaluation/experiments/experiment_4_real_life_ablation/scripts/run_ablation_batch.py --reps 1,2,3 --workers 2
python evaluation/experiments/experiment_4_real_life_ablation/scripts/run_ablation_batch.py --reps 4,5 --workers 2
python evaluation/experiments/experiment_4_real_life_ablation/scripts/complete_ablation_records.py
python evaluation/experiments/experiment_4_real_life_ablation/scripts/ablation_reporting.py
python evaluation/experiments/experiment_4_real_life_ablation/scripts/verify_ablation.py
python evaluation/experiments/experiment_4_real_life_ablation/scripts/make_ablation_workbook.py
```

The batch needs `OPENAI_API_KEY` (or `OPENROUTER_API_KEY`) and a clean `goal_to_parameters/`. It is resumable:
runs with a final status are skipped and `llm_error` runs are redone.

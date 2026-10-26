# GLASS real-life ablation study

The study measures what each part of GLASS contributes on real event logs. Stage 1 runs with and without the
event log. Stage 2 runs in six variants, each switching off one component, on the three real-life datasets of
the Zenodo package 10.5281/zenodo.10435849.

- Stage 1: 3 datasets × 3 goals × 2 variants × 5 repetitions = 90 LLM calls.
- Stage 2: 3 datasets × 3 goals × 6 variants × 3 repetitions = 162 runs. Repetitions 4–5 (108 runs) follow as a
  second pass with the same script and settings.

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
- [Stage 2 results (repetitions 1–3)](#stage-2-results-repetitions-13)
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
| Repetitions | 5; first pass 1–3 (this commit), second pass 4–5 |

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

The batch ran on GLASS `c2f7f53` (v3.2; `evaluation/loanapp_p2p/results/VERSIONS.md`). Commits made during this
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

During the batch the computer went into standby twice. Runs paused and continued; two runs failed with
connection errors (`llm_error`) and were re-run. All 162 records have a final status.

## Stage 2 results (repetitions 1–3)

The batch produced 162 records, all with status `ok` after the completion step. Seven runs had
`status_at_run = no_initial_proposal`: six were completed with their final (iteration 2) and one with a kept
baseline. The verifier recomputes everything listed under Scoring and finds 0 mismatches
(`results/stage2/verification.md`), including the distances recomputed from the logs for a sample of three runs.
A tampered copy (a validity flag, an NIS value and a switch changed) was detected.

**Per variant** (`results/stage2/ablation_per_variant.csv`; 27 runs per variant). The p-value compares each
variant with V_full: Fisher's exact test for rates, a paired sign test for means. Rates count all runs. Means
(DHR, NIS, distances, merge stability) are over the runs with a final scenario.

| Measure | V_full | V_no_log | V_no_context | V_no_kb | V_no_refine | V_old_refine |
|---|---|---|---|---|---|---|
| Scenario produced | 22/27 | 16/27 (p 0.135) | 24/27 (p 0.704) | 24/27 (p 0.704) | 16/27 (p 0.135) | 25/27 (p 0.420) |
| Valid | 21/27 | 16/27 (p 0.241) | 24/27 (p 0.467) | 24/27 (p 0.467) | 16/27 (p 0.241) | 25/27 (p 0.250) |
| Executable | 22/27 | 16/27 (p 0.135) | 24/27 (p 0.704) | 24/27 (p 0.704) | 16/27 (p 0.135) | 25/27 (p 0.420) |
| Merge stability > 0 | 22/27 | 16/27 (p 0.135) | 24/27 (p 0.704) | 24/27 (p 0.704) | 16/27 (p 0.135) | 25/27 (p 0.420) |
| All KPIs improved | 11/27 | 9/27 (p 0.779) | 11/27 (p 1.000) | 17/27 (p 0.173) | 10/27 (p 1.000) | 12/27 (p 1.000) |
| DHR | 0.57 | 0.59 (p 1.000) | 0.48 (p 1.000) | 0.75 (p 0.109) | 0.72 (p 0.625) | 0.54 (p 0.754) |
| NIS | 2.63 | 3.50 (p 0.790) | 4.22 (p 0.332) | 11.54 (p 0.263) | 4.95 (p 0.774) | 3.63 (p 1.000) |
| Admissible (valid, executable, merge stability > 0) | 21/27 | 16/27 (p 0.241) | 24/27 (p 0.467) | 24/27 (p 0.467) | 16/27 (p 0.241) | 25/27 (p 0.250) |
| Admissible and all KPIs improved | 10/27 | 9/27 (p 1.000) | 11/27 (p 1.000) | 17/27 (p 0.102) | 10/27 (p 1.000) | 12/27 (p 0.782) |
| Average Cycle Time improved (G1, G1G2) | 8/18 | 5/18 (p 0.489) | 8/18 (p 1.000) | 13/18 (p 0.176) | 7/18 (p 1.000) | 8/18 (p 1.000) |
| X waiting time improved (G2, G1G2) | 10/18 | 9/18 (p 1.000) | 9/18 (p 1.000) | 12/18 (p 0.733) | 10/18 (p 1.000) | 12/18 (p 0.733) |
| At least one KPI improved with p < 0.05 | 5/27 | 4/27 (p 1.000) | 4/27 (p 1.000) | 13/27 (p 0.042) | 6/27 (p 1.000) | 6/27 (p 1.000) |
| Loop kept the baseline (no_improving_scenario) | 5/27 | 11/27 (p 0.135) | 3/27 (p 0.704) | 3/27 (p 0.704) | 11/27 (p 0.135) | 2/27 (p 0.420) |
| Loop discarded a better initial | 1/27 | 3/27 (p 0.610) | 1/27 (p 1.000) | 0/27 (p 1.000) | 0/27 (p 1.000) | 2/27 (p 1.000) |
| At least one direction_inconsistent rejection | 8/27 | 9/27 (p 1.000) | 7/27 (p 1.000) | 2/27 (p 0.076) | 5/27 (p 0.526) | 6/27 (p 0.757) |
| direction_inconsistent rejections per run | 1.48 | 1.11 (p 0.727) | 1.04 (p 0.219) | 0.22 (p 0.070) | 0.30 (p 0.008) | 0.74 (p 0.289) |
| Merge stability (applied / proposed) | 0.88 | 0.86 (p 1.000) | 0.84 (p 1.000) | 0.98 (p 0.375) | 0.86 (p 1.000) | 0.87 (p 0.625) |
| Merge stability, GLASS's value | 0.98 | 1.00 | 0.99 (p 1.000) | 1.00 (p 1.000) | 1.00 (p 1.000) | 1.00 (p 1.000) |
| Share of modifications on X | 0.56 | 0.35 (p 0.039) | 0.56 (p 0.688) | 0.31 (p 0.039) | 0.54 (p 0.375) | 0.51 (p 1.000) |
| Modifications | 2.18 | 2.00 (p 1.000) | 2.00 (p 0.375) | 2.21 (p 1.000) | 2.19 (p 1.000) | 2.12 (p 1.000) |
| Iterations used | 2.15 | 2.56 (p 0.302) | 1.89 (p 0.607) | 1.52 (p 0.238) | 1.00 (p 0.000) | 1.74 (p 0.302) |
| CTD to the test log | 42.32 | 39.59 (p 0.424) | 40.40 (p 0.143) | 53.50 (p 0.503) | 45.94 (p 0.774) | 41.63 (p 1.000) |
| RED to the test log | 65.26 | 61.16 (p 0.790) | 65.20 (p 0.332) | 68.18 (p 0.263) | 60.73 (p 0.388) | 65.27 (p 1.000) |
| CWD to the test log | 2.03 | 1.85 (p 0.424) | 1.99 (p 1.000) | 1.98 (p 0.503) | 2.00 (p 0.146) | 2.01 (p 1.000) |
| inter_arrival_time proposed (skipped by the merger) | 0/27 | 0/27 (p 1.000) | 0/27 (p 1.000) | 0/27 (p 1.000) | 0/27 (p 1.000) | 0/27 (p 1.000) |
| Hourly rate changed | 0/27 | 0/27 (p 1.000) | 0/27 (p 1.000) | 0/27 (p 1.000) | 0/27 (p 1.000) | 0/27 (p 1.000) |

**Reading the table (repetitions 1–3 only)**

- **Primary measures.** No variant is significantly worse than V_full on any primary measure, and V_full is not
  ahead on any of them.
  - V_no_log and V_no_refine produce a scenario least often: 16 of 27, against 22 for V_full. In 11 runs each,
    the loop kept the baseline because no iteration improved on it. Neither difference is significant (p = 0.135).
  - V_no_kb improves most often: all KPIs in 17 of 27 runs, at least one KPI significantly in 13 of 27 against
    5 for V_full (p = 0.042). It also has the highest NIS (+11.5 against +2.6).
  - With knowledge-base material, the model more often states changes whose direction contradicts their values:
    1.48 rejections per run for V_full against 0.22 for V_no_kb (p = 0.070). V_no_kb also aims fewer changes at
    X: a share of 0.31 against 0.56 (p = 0.039).
- **Log evidence aims changes at X.** Without it (V_no_log), the share of modifications on X falls from 0.56 to
  0.35 (p = 0.039).
- **Refinement.**
  - V_old_refine (the v2 rules) ends with an admissible scenario most often (25 of 27), with no more
    improvements than V_full.
  - "Loop discarded a better initial" is rare in all variants (0–3 of 27).
- **Validity.** Only one final scenario that was produced is not valid: V_full, AcademicCredentials_G1G2 rep 2.
  The model extended working hours on 10183_profile. That profile's calendar is shared by 289 of the 306
  resources, so the change applied to all of them and staff cost rose by +311 %. GLASS's budget check rejected
  the scenario, but GLASS returned it after three attempts as the best available proposal. The batch scores it
  not valid.
- **Constraints never broken.** No final changed demand or an hourly rate.
- **By dataset.** Runs with a significant KPI improvement: AcademicCredentials 25 of 54, BPIC_2012_W 6 of 54,
  BPIC_2017_W 7 of 54. On AcademicCredentials, the levers that work shorten the timer before X; activity
  durations of seconds barely move cycle times of days. The loop kept the baseline in 17, 14 and 4 runs.
- **Power.** With 27 runs per variant, most differences are not significant. Repetitions 4–5 (Phase 7) add 18
  runs per variant.

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
- **Timer changes aimed at the activity name.** In the 7 `no_initial_proposal` runs, iteration 1 changed a
  timer's duration with the following activity's name as the target ("W_Call after offers"), although the
  prompt lists the timers, some named "Timer before W_Call after offers". GLASS rejected every attempt.
- **The loop's single seed misleads.** The loop judges a proposal on one run (seed 42); the final evaluation uses
  10 seeds. Adding one person to a one-person profile often "improves" at seed 42 and is slightly worse over 10
  seeds.
- **Shared calendars spread a calendar change**, and GLASS returns a proposal its own budget check rejected when
  all three attempts fail (see Validity above).

## Reproduction

From the repository root, in GLASS's environment with Prosimos and log-distance-measures:

```
python evaluation/real_life_ablation/scripts/select_baselines.py <Zenodo package folder>
python evaluation/real_life_ablation/scripts/validate_baselines.py <Zenodo package folder>
python evaluation/real_life_ablation/scripts/compute_waiting_times.py
python evaluation/real_life_ablation/scripts/build_config.py
python evaluation/real_life_ablation/scripts/run_stage1_ablation.py
python evaluation/real_life_ablation/scripts/run_ablation_batch.py --reps 1,2,3 --workers 2
python evaluation/real_life_ablation/scripts/complete_ablation_records.py
python evaluation/real_life_ablation/scripts/ablation_reporting.py
python evaluation/real_life_ablation/scripts/verify_ablation.py
python evaluation/real_life_ablation/scripts/make_ablation_workbook.py
```

The batch needs `OPENAI_API_KEY` (or `OPENROUTER_API_KEY`) and a clean `goal_to_parameters/`. It is resumable:
runs with a final status are skipped and `llm_error` runs are redone.

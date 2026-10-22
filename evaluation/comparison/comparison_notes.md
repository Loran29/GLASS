# Comparison notes

## What the table shows
`comparison.xlsx` (sheet `Summary`) has one row per config x variant x expected KPI. Cycle time and waiting time are in hours, utilization is a ratio. Lower is better for all three.

| Column | Meaning |
|---|---|
| `config` | GLASS configuration: process and goals (G1 end-to-end time, G2 local waiting, G3 resource workload) |
| `variant` | Which of my model sets: `extended_hours` or `extra_resources` |
| `model` | Folder name of my model (Procure2Pay_G1G2 has `hours_1` and `hours_2`) |
| `kpi_expected` | Expected KPI of the config (`EXPECTED_KPIS.md`) |
| `baseline_mean` | Unchanged baseline, mean over the 10 seeds (42-51) |
| `mine_mean` | My model, mean over the 10 seeds |
| `glass_mean_5reps` | GLASS Stage 2 v3.1: mean over the 5 reps (each rep = its best iteration) and the 10 seeds |
| `glass_rep_min`, `glass_rep_max` | Lowest and highest of the 5 reps' own 10-seed means |
| `mine_vs_baseline_pct` | (baseline - mine) / baseline x 100. Positive = lower than the baseline |
| `glass_vs_baseline_pct` | The same for `glass_mean_5reps` |
| `diff_mine_minus_glass` | Mean over the seeds of (mine - GLASS 5-rep mean). Negative = mine is lower |
| `ci_low`, `ci_high` | 95% confidence interval of that difference (t distribution, n = 10 seeds) |
| `verdict` | `mine lower` if the interval is below zero, `GLASS mean of 5 reps lower` if above zero, `no significant difference` if it contains zero |

More detail is in the CSV files: `comparison_summary.csv` (also p-values paired and Welch, and the utilization target flags `baseline/mine/glass_target_met`), `comparison_all.csv` (every comparison, each GLASS rep separately, mine vs baseline, GLASS vs baseline) and `kpi_values_per_log.csv` (the value of every KPI in every log).

## Scope
- 16 models of mine in 13 configs (a config in both `extended_hours` and `extra_resources` gets two rows).
- Each is compared with the same config in the GLASS Stage 2 v3.1 final logs (`stage2_v3_1_final_logs`, 5 reps) and with the unchanged baseline, on seeds 42-51.
- **Procure2Pay_G1 is not compared**: there is no model of it in `my_prosimos_results_v2/`.

## KPI definitions (GLASS `kpi_computation.py`, the same code for all sources)
- Average Cycle Time: first event start to last event end per case, mean.
- `<activity>` Waiting Time: gap between the previous event's end and this event's start in the same case (positive gaps only), mean. Activity: Assess loan risk (LoanApp), Release Supplier's Invoice (Procure2Pay).
- `<role>` Utilization: busy time clipped to each resource's working calendar / available working time, from the simulation start to the last event of the log; mean over the role's resources. Role: Loan Officer (LoanApp), Purchasing Agent (Procure2Pay). GLASS target rule: below 0.85 and below the baseline.

## How the KPIs are computed, step by step
Every KPI is computed per log (one log = one model or one rep on one seed), which gives 10 values per source (seeds 42-51). The table reports the mean of these 10 values.

**Utilization of a role** (`_compute_resource_utilization` in `kpi_computation.py`):
1. Period: from the simulation start (2023-02-09 10:00) to the last `end_time` in the log. Times are taken as local wall-clock time (time zone removed), because the calendars are written that way. Each log uses its own period.
2. Available time of a person: the person's calendar (e.g. Mon-Fri 09:00-17:00, or 09:00-19:00 for the roles of the `extended_hours` models) cut to the period; the lengths of all working windows inside the period are added up. The first day starts at 10:00 because the period does.
3. Busy time of a person: every activity the person performed, cut to the working windows. The part of an activity outside working hours is not counted.
4. Utilization of a person = busy time / available time.
5. Utilization of the role = mean over the people of that role (Loan Officer for LoanApp, Purchasing Agent for Procure2Pay). The person-to-role link comes from `resource_profiles` of each log's own Prosimos JSON.
6. Repeated for seeds 42-51.

Example (made-up numbers): two Loan Officers. A: busy 700 h, available 800 h = 0.875. B: busy 500 h, available 800 h = 0.625. Role utilization = (0.875 + 0.625) / 2 = 0.75.

- `extended_hours`: the affected role has more available time (10 h a day instead of 8) and the window for busy time is longer too, so utilization can drop just from the longer day.
- `extra_resources`: the same work is shared by more people, so the mean per person drops.
- A person without a calendar would fall back to busy time / log length. This never happened (see 'KPI computation problems').
- The period ends with the log, so a model that finishes earlier is measured over a shorter period. A very overloaded baseline (e.g. 0.997) means the person is almost always busy until the end of the log.
- Target rule used by GLASS: role utilization below 0.85 and below the baseline (`mine_target_met`, `glass_target_met`, `baseline_target_met` in `comparison_summary.csv`). The 0.85 limit comes from the GLASS Stage 2 configuration; its reason is not documented in the repository.

**Average Cycle Time:** per case, last `end_time` minus first `start_time` in hours; mean over all 1000 cases of the log.

**Waiting time of an activity:** in each case the events are sorted by `start_time`. For every occurrence of the activity, the wait is its `start_time` minus the previous event's `end_time`. Only positive gaps count, and the value is the mean over all such occurrences in the log (in hours).

**Statistics of the table:** `diff_mine_minus_glass` is the mean of the 10 per-seed differences (my value for seed s minus the GLASS 5-rep mean for seed s, where that mean is taken over the 5 reps for the same seed). `ci_low`/`ci_high` are mean difference +- t(0.975, 9) x standard error. `mine_vs_baseline_pct` and `glass_vs_baseline_pct` use the baseline mean of the same config.

## Reading the table
- For utilization, a lower value can also mean idle capacity; read it together with the 0.85 target rule.
- Where the baseline is overloaded (LoanApp G1G3 and G1G2G3: cycle time about 2250 hours) the queue grows during the simulation, so times and percentages there are dominated by the queue.
- Seed pairing is weak between different models (their random streams drift after the first change), so the CI is computed on the per-seed differences but should be read with the Welch p-value in the CSV.

## KPI computation problems
- 0 log/KPI values with a problem (NaN, utilization falling back to the span-based formula, role or activity not found).

## Cross-check against the GLASS records (baseline per-seed values)
- 30 of 31 record series match my baseline series.
  - Procure2Pay_G1G2G3: Average Wait Time Before Invoice Release (time) has no match (closest max abs diff 1.353).
    Reason: in this config GLASS's Stage 2 optimised the case-level KPI 'Average Wait Time Before Invoice Release' (measurable_as case_wait_time_hours, the summed waiting per case), not the expected activity waiting time 'Release Supplier's Invoice Waiting Time'. This comparison uses the expected KPI from EXPECTED_KPIS.md.

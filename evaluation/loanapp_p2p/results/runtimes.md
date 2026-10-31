# Run times of the evaluation batches

Run time of every run from its record (`timestamp` = start, `elapsed_s` = wall clock in its worker), in minutes. Per-run values: `runtimes_runs.csv`; per batch and dataset/variant: `runtimes.csv` (`scripts/summarize_runtimes.py`).

- **Statistics** (median, mean, min, max) leave out the runs that overlapped a standby window of the machine; they are listed below. `Runs` counts all runs. Any overlap counts, also with the short Modern Standby windows. Not every window paused the runs (Stage 2 v2's Procure2Pay runs inside the 93-minute window of 2026-10-15 took their usual 7–16 min); those that did show run times of hours (up to 1,222 min). `Median, all runs` keeps every run: per batch the exclusion moves the median by at most 0.66 min (Stage 2 v3.1); per dataset by up to 3.07 min (Stage 2 v2 Procure2Pay, 15 of 35 runs left out).
- **LLM vs. other time.** LLM time is the sum of the recorded LLM calls (`elapsed_s` per call, with retries and back-offs). The records do not time the simulation separately: "other" is everything else in the run, i.e. Prosimos simulation (Stage 2: the first proposal and the final on 10 seeds each, plus one seed per loop iteration), KPI and distance computation, log writing and the 1 s pause between LLM calls. Stage 1 and the pilot make LLM calls only, so their "other" is parsing and validation. LLM share = LLM time / run time over the runs used.
- **Workers** run in parallel, so run times include contention for the CPU (simulation) and the API; a batch's wall clock is shorter than the sum of its runs.
- **Stage 2 completion step.** Runs whose iteration 1 had no scenario were completed later with their final evaluation (`completed_by` in the record); that evaluation is not part of `elapsed_s` and was not timed (33 runs, column `completed_later`).
- **Not included:** `stage1_results_normalised` (Stage 1 records re-processed without LLM calls) and the aborted local batches.

## Per batch

| Batch | Dataset | Variant | Workers | Runs | Standby, excluded | Median | Mean | Min | Max | Median, all runs | Median LLM | Median other | LLM share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Stage 1 (LoanApp/P2P) | all | all | 4 | 70 | 0 | 0.29 | 0.31 | 0.23 | 0.81 | 0.29 | 0.29 | 0.0 | 0.998 |
| Refinement pilot | all | round 1 | 1 | 9 | 0 | 0.24 | 0.31 | 0.2 | 0.64 | 0.24 | 0.24 | 0.0 | 0.999 |
| Refinement pilot | all | round 2 | 1 | 2 | 0 | 0.25 | 0.25 | 0.25 | 0.26 | 0.25 | 0.25 | 0.0 | 1.0 |
| Refinement pilot | all | round 3 | 1 | 1 | 0 | 0.21 | 0.21 | 0.21 | 0.21 | 0.21 | 0.21 | 0.0 | 1.0 |
| Refinement pilot | all | all | 1 | 12 | 0 | 0.25 | 0.29 | 0.2 | 0.64 | 0.25 | 0.25 | 0.0 | 0.999 |
| Stage 2 v1 | all | all | 2 | 70 | 4 | 6.43 | 7.77 | 1.41 | 19.45 | 6.63 | 1.25 | 5.08 | 0.17 |
| Stage 2 v2 | all | all | 2 | 70 | 15 | 4.35 | 5.36 | 1.59 | 13.15 | 4.67 | 0.6 | 3.81 | 0.208 |
| Stage 2 v3 | all | all | 2 | 70 | 6 | 6.69 | 7.57 | 1.5 | 19.9 | 6.77 | 0.8 | 6.31 | 0.163 |
| Stage 2 v3.1 | all | all | 2 | 70 | 6 | 4.9 | 5.94 | 1.75 | 14.95 | 5.56 | 0.65 | 4.17 | 0.168 |
| Stage 2 v3.2 RAG comparison | all | V_full | 4 | 70 | 0 | 2.51 | 2.87 | 1.36 | 6.69 | 2.51 | 0.74 | 1.87 | 0.337 |
| Stage 2 v3.2 RAG comparison | all | V_no_kb | 4 | 70 | 0 | 2.52 | 3.09 | 0.82 | 7.97 | 2.52 | 0.73 | 1.7 | 0.34 |
| Stage 2 v3.2 RAG comparison | all | all | 4 | 140 | 0 | 2.51 | 2.98 | 0.82 | 7.97 | 2.51 | 0.73 | 1.8 | 0.339 |
| Real-life ablation Stage 1 | all | full | 2 | 45 | 0 | 0.25 | 0.26 | 0.18 | 0.83 | 0.25 | 0.25 | 0.0 | 0.998 |
| Real-life ablation Stage 1 | all | no_log | 2 | 45 | 0 | 0.24 | 0.26 | 0.17 | 0.81 | 0.24 | 0.24 | 0.0 | 0.999 |
| Real-life ablation Stage 1 | all | all | 2 | 90 | 0 | 0.25 | 0.26 | 0.17 | 0.83 | 0.25 | 0.25 | 0.0 | 0.999 |
| Real-life ablation Stage 2 | all | V_full | 2/4 | 45 | 3 | 6.88 | 9.43 | 1.15 | 31.93 | 6.96 | 0.81 | 6.29 | 0.123 |
| Real-life ablation Stage 2 | all | V_no_context | 2/4 | 45 | 4 | 6.92 | 10.56 | 0.92 | 33.12 | 7.73 | 0.66 | 6.11 | 0.091 |
| Real-life ablation Stage 2 | all | V_no_kb | 2/4 | 45 | 1 | 7.12 | 10.94 | 1.23 | 39.02 | 7.36 | 0.53 | 6.74 | 0.07 |
| Real-life ablation Stage 2 | all | V_no_log | 2/4 | 45 | 2 | 7.24 | 10.54 | 1.5 | 37.59 | 7.27 | 1.0 | 6.61 | 0.105 |
| Real-life ablation Stage 2 | all | V_no_refine | 2/4 | 45 | 1 | 6.59 | 8.4 | 1.48 | 19.23 | 6.69 | 0.47 | 6.18 | 0.059 |
| Real-life ablation Stage 2 | all | V_old_refine | 2/4 | 45 | 3 | 10.1 | 9.89 | 1.1 | 31.55 | 11.18 | 0.67 | 8.23 | 0.095 |
| Real-life ablation Stage 2 | all | all | 2/4 | 270 | 14 | 6.96 | 9.96 | 0.92 | 39.02 | 7.21 | 0.63 | 6.45 | 0.091 |

## Per batch and dataset/variant

| Batch | Dataset | Variant | Workers | Runs | Standby, excluded | Median | Mean | Min | Max | Median, all runs | Median LLM | Median other | LLM share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Stage 1 (LoanApp/P2P) | LoanApp | - | 4 | 35 | 0 | 0.3 | 0.34 | 0.24 | 0.81 | 0.3 | 0.3 | 0.0 | 0.996 |
| Stage 1 (LoanApp/P2P) | Procure2Pay | - | 4 | 35 | 0 | 0.28 | 0.29 | 0.23 | 0.41 | 0.28 | 0.28 | 0.0 | 1.0 |
| Stage 1 (LoanApp/P2P) | all | all | 4 | 70 | 0 | 0.29 | 0.31 | 0.23 | 0.81 | 0.29 | 0.29 | 0.0 | 0.998 |
| Refinement pilot | LoanApp | round 1 | 1 | 3 | 0 | 0.32 | 0.3 | 0.24 | 0.35 | 0.32 | 0.32 | 0.0 | 0.998 |
| Refinement pilot | Procure2Pay | round 1 | 1 | 6 | 0 | 0.23 | 0.32 | 0.2 | 0.64 | 0.23 | 0.23 | 0.0 | 0.999 |
| Refinement pilot | Procure2Pay | round 2 | 1 | 2 | 0 | 0.25 | 0.25 | 0.25 | 0.26 | 0.25 | 0.25 | 0.0 | 1.0 |
| Refinement pilot | Procure2Pay | round 3 | 1 | 1 | 0 | 0.21 | 0.21 | 0.21 | 0.21 | 0.21 | 0.21 | 0.0 | 1.0 |
| Refinement pilot | all | round 1 | 1 | 9 | 0 | 0.24 | 0.31 | 0.2 | 0.64 | 0.24 | 0.24 | 0.0 | 0.999 |
| Refinement pilot | all | round 2 | 1 | 2 | 0 | 0.25 | 0.25 | 0.25 | 0.26 | 0.25 | 0.25 | 0.0 | 1.0 |
| Refinement pilot | all | round 3 | 1 | 1 | 0 | 0.21 | 0.21 | 0.21 | 0.21 | 0.21 | 0.21 | 0.0 | 1.0 |
| Refinement pilot | all | all | 1 | 12 | 0 | 0.25 | 0.29 | 0.2 | 0.64 | 0.25 | 0.25 | 0.0 | 0.999 |
| Stage 2 v1 | LoanApp | - | 2 | 35 | 0 | 4.33 | 4.88 | 1.41 | 11.2 | 4.33 | 0.64 | 3.77 | 0.18 |
| Stage 2 v1 | Procure2Pay | - | 2 | 35 | 4 | 11.25 | 11.02 | 1.52 | 19.45 | 12.22 | 1.85 | 9.44 | 0.165 |
| Stage 2 v1 | all | all | 2 | 70 | 4 | 6.43 | 7.77 | 1.41 | 19.45 | 6.63 | 1.25 | 5.08 | 0.17 |
| Stage 2 v2 | LoanApp | - | 2 | 35 | 0 | 4.07 | 4.48 | 1.59 | 10.04 | 4.07 | 0.44 | 3.58 | 0.148 |
| Stage 2 v2 | Procure2Pay | - | 2 | 35 | 15 | 5.97 | 6.9 | 1.85 | 13.15 | 9.04 | 1.48 | 5.63 | 0.276 |
| Stage 2 v2 | all | all | 2 | 70 | 15 | 4.35 | 5.36 | 1.59 | 13.15 | 4.67 | 0.6 | 3.81 | 0.208 |
| Stage 2 v3 | LoanApp | - | 2 | 35 | 4 | 4.47 | 5.44 | 1.5 | 10.09 | 4.47 | 0.52 | 3.77 | 0.161 |
| Stage 2 v3 | Procure2Pay | - | 2 | 35 | 2 | 7.93 | 9.57 | 2.34 | 19.9 | 7.93 | 1.72 | 6.8 | 0.165 |
| Stage 2 v3 | all | all | 2 | 70 | 6 | 6.69 | 7.57 | 1.5 | 19.9 | 6.77 | 0.8 | 6.31 | 0.163 |
| Stage 2 v3.1 | LoanApp | - | 2 | 35 | 2 | 4.62 | 5.01 | 2.16 | 9.09 | 4.63 | 0.55 | 4.14 | 0.124 |
| Stage 2 v3.1 | Procure2Pay | - | 2 | 35 | 4 | 7.02 | 6.93 | 1.75 | 14.95 | 7.11 | 0.89 | 5.06 | 0.202 |
| Stage 2 v3.1 | all | all | 2 | 70 | 6 | 4.9 | 5.94 | 1.75 | 14.95 | 5.56 | 0.65 | 4.17 | 0.168 |
| Stage 2 v3.2 RAG comparison | LoanApp | V_full | 4 | 35 | 0 | 1.91 | 2.39 | 1.36 | 4.64 | 1.91 | 0.73 | 1.29 | 0.335 |
| Stage 2 v3.2 RAG comparison | Procure2Pay | V_full | 4 | 35 | 0 | 2.61 | 3.34 | 2.11 | 6.69 | 2.61 | 0.75 | 2.01 | 0.338 |
| Stage 2 v3.2 RAG comparison | LoanApp | V_no_kb | 4 | 35 | 0 | 1.92 | 2.16 | 0.82 | 4.45 | 1.92 | 0.52 | 1.42 | 0.268 |
| Stage 2 v3.2 RAG comparison | Procure2Pay | V_no_kb | 4 | 35 | 0 | 4.24 | 4.02 | 1.55 | 7.97 | 4.24 | 1.26 | 2.28 | 0.379 |
| Stage 2 v3.2 RAG comparison | all | V_full | 4 | 70 | 0 | 2.51 | 2.87 | 1.36 | 6.69 | 2.51 | 0.74 | 1.87 | 0.337 |
| Stage 2 v3.2 RAG comparison | all | V_no_kb | 4 | 70 | 0 | 2.52 | 3.09 | 0.82 | 7.97 | 2.52 | 0.73 | 1.7 | 0.34 |
| Stage 2 v3.2 RAG comparison | all | all | 4 | 140 | 0 | 2.51 | 2.98 | 0.82 | 7.97 | 2.51 | 0.73 | 1.8 | 0.339 |
| Real-life ablation Stage 1 | AcademicCredentials | full | 2 | 15 | 0 | 0.28 | 0.31 | 0.18 | 0.83 | 0.28 | 0.28 | 0.0 | 0.996 |
| Real-life ablation Stage 1 | BPIC_2012_W | full | 2 | 15 | 0 | 0.25 | 0.25 | 0.2 | 0.3 | 0.25 | 0.25 | 0.0 | 1.0 |
| Real-life ablation Stage 1 | BPIC_2017_W | full | 2 | 15 | 0 | 0.24 | 0.24 | 0.18 | 0.27 | 0.24 | 0.24 | 0.0 | 1.0 |
| Real-life ablation Stage 1 | AcademicCredentials | no_log | 2 | 15 | 0 | 0.25 | 0.28 | 0.17 | 0.81 | 0.25 | 0.25 | 0.0 | 1.0 |
| Real-life ablation Stage 1 | BPIC_2012_W | no_log | 2 | 15 | 0 | 0.25 | 0.27 | 0.19 | 0.39 | 0.25 | 0.25 | 0.0 | 0.999 |
| Real-life ablation Stage 1 | BPIC_2017_W | no_log | 2 | 15 | 0 | 0.23 | 0.23 | 0.17 | 0.35 | 0.23 | 0.23 | 0.0 | 1.0 |
| Real-life ablation Stage 1 | all | full | 2 | 45 | 0 | 0.25 | 0.26 | 0.18 | 0.83 | 0.25 | 0.25 | 0.0 | 0.998 |
| Real-life ablation Stage 1 | all | no_log | 2 | 45 | 0 | 0.24 | 0.26 | 0.17 | 0.81 | 0.24 | 0.24 | 0.0 | 0.999 |
| Real-life ablation Stage 1 | all | all | 2 | 90 | 0 | 0.25 | 0.26 | 0.17 | 0.83 | 0.25 | 0.25 | 0.0 | 0.999 |
| Real-life ablation Stage 2 | AcademicCredentials | V_full | 2 | 15 | 0 | 4.1 | 4.05 | 1.15 | 7.56 | 4.1 | 1.78 | 2.27 | 0.422 |
| Real-life ablation Stage 2 | BPIC_2012_W | V_full | 2 | 15 | 2 | 6.96 | 7.93 | 5.06 | 14.0 | 7.56 | 0.64 | 6.5 | 0.11 |
| Real-life ablation Stage 2 | BPIC_2017_W | V_full | 2/4 | 15 | 1 | 16.06 | 16.57 | 2.73 | 31.93 | 16.24 | 0.53 | 15.45 | 0.051 |
| Real-life ablation Stage 2 | AcademicCredentials | V_no_context | 2 | 15 | 0 | 3.69 | 3.56 | 0.92 | 5.59 | 3.69 | 0.56 | 2.91 | 0.289 |
| Real-life ablation Stage 2 | BPIC_2012_W | V_no_context | 2 | 15 | 2 | 11.55 | 10.96 | 6.01 | 20.4 | 11.55 | 1.24 | 9.72 | 0.121 |
| Real-life ablation Stage 2 | BPIC_2017_W | V_no_context | 2/4 | 15 | 2 | 16.64 | 18.25 | 2.15 | 33.12 | 18.84 | 0.44 | 16.37 | 0.028 |
| Real-life ablation Stage 2 | AcademicCredentials | V_no_kb | 2 | 15 | 0 | 2.83 | 3.08 | 1.23 | 6.25 | 2.83 | 0.56 | 2.09 | 0.276 |
| Real-life ablation Stage 2 | BPIC_2012_W | V_no_kb | 2 | 15 | 0 | 7.36 | 8.95 | 2.05 | 17.35 | 7.36 | 0.71 | 6.85 | 0.094 |
| Real-life ablation Stage 2 | BPIC_2017_W | V_no_kb | 2/4 | 15 | 1 | 17.72 | 21.5 | 15.63 | 39.02 | 18.07 | 0.49 | 17.27 | 0.028 |
| Real-life ablation Stage 2 | AcademicCredentials | V_no_log | 2 | 15 | 0 | 3.87 | 3.94 | 2.01 | 7.24 | 3.87 | 1.56 | 2.42 | 0.37 |
| Real-life ablation Stage 2 | BPIC_2012_W | V_no_log | 2 | 15 | 1 | 7.24 | 6.91 | 1.5 | 19.98 | 7.27 | 0.82 | 6.64 | 0.157 |
| Real-life ablation Stage 2 | BPIC_2017_W | V_no_log | 2/4 | 15 | 1 | 18.4 | 21.24 | 9.24 | 37.59 | 18.45 | 0.51 | 17.96 | 0.036 |
| Real-life ablation Stage 2 | AcademicCredentials | V_no_refine | 2 | 15 | 0 | 2.76 | 2.57 | 1.48 | 3.67 | 2.76 | 0.65 | 2.12 | 0.244 |
| Real-life ablation Stage 2 | BPIC_2012_W | V_no_refine | 2 | 15 | 0 | 6.69 | 6.0 | 2.11 | 7.2 | 6.69 | 0.36 | 6.23 | 0.066 |
| Real-life ablation Stage 2 | BPIC_2017_W | V_no_refine | 2/4 | 15 | 1 | 17.08 | 17.24 | 15.66 | 19.23 | 17.1 | 0.49 | 16.58 | 0.027 |
| Real-life ablation Stage 2 | AcademicCredentials | V_old_refine | 2 | 15 | 2 | 2.78 | 3.12 | 1.1 | 6.97 | 2.83 | 0.49 | 2.19 | 0.353 |
| Real-life ablation Stage 2 | BPIC_2012_W | V_old_refine | 2 | 15 | 0 | 9.24 | 9.43 | 1.54 | 14.77 | 9.24 | 1.19 | 7.78 | 0.115 |
| Real-life ablation Stage 2 | BPIC_2017_W | V_old_refine | 2/4 | 15 | 1 | 16.47 | 16.68 | 11.18 | 31.55 | 16.87 | 0.44 | 15.58 | 0.038 |
| Real-life ablation Stage 2 | all | V_full | 2/4 | 45 | 3 | 6.88 | 9.43 | 1.15 | 31.93 | 6.96 | 0.81 | 6.29 | 0.123 |
| Real-life ablation Stage 2 | all | V_no_context | 2/4 | 45 | 4 | 6.92 | 10.56 | 0.92 | 33.12 | 7.73 | 0.66 | 6.11 | 0.091 |
| Real-life ablation Stage 2 | all | V_no_kb | 2/4 | 45 | 1 | 7.12 | 10.94 | 1.23 | 39.02 | 7.36 | 0.53 | 6.74 | 0.07 |
| Real-life ablation Stage 2 | all | V_no_log | 2/4 | 45 | 2 | 7.24 | 10.54 | 1.5 | 37.59 | 7.27 | 1.0 | 6.61 | 0.105 |
| Real-life ablation Stage 2 | all | V_no_refine | 2/4 | 45 | 1 | 6.59 | 8.4 | 1.48 | 19.23 | 6.69 | 0.47 | 6.18 | 0.059 |
| Real-life ablation Stage 2 | all | V_old_refine | 2/4 | 45 | 3 | 10.1 | 9.89 | 1.1 | 31.55 | 11.18 | 0.67 | 8.23 | 0.095 |
| Real-life ablation Stage 2 | all | all | 2/4 | 270 | 14 | 6.96 | 9.96 | 0.92 | 39.02 | 7.21 | 0.63 | 6.45 | 0.091 |

## Runs left out: overlap with a standby window (45)

Windows from `standby_windows.csv` (Windows System log: sleep, Power-Troubleshooter event 1; Modern Standby, Kernel-Power 506/507). Overlap = seconds of the run inside a window.

| Batch | Configuration | Variant | Rep | Start (UTC) | Run time (min) | Overlap (s) | Windows |
|---|---|---|---|---|---|---|---|
| Stage 2 v1 | Procure2Pay_G2 |  | 1 | 2026-10-12T15:11:06Z | 141.19 | 8033 | 2026-10-12 15:15:51–15:15:52 UTC (modern_standby); 2026-10-12 15:15:52–15:15:57 UTC (modern_standby); 2026-10-12 15:15:57–17:29:31 UTC (modern_standby); 2026-10-12 17:30:03–17:30:16 UTC (modern_standby) |
| Stage 2 v1 | Procure2Pay_G2 |  | 2 | 2026-10-12T15:15:35Z | 152.85 | 8033 | 2026-10-12 15:15:51–15:15:52 UTC (modern_standby); 2026-10-12 15:15:52–15:15:57 UTC (modern_standby); 2026-10-12 15:15:57–17:29:31 UTC (modern_standby); 2026-10-12 17:30:03–17:30:16 UTC (modern_standby) |
| Stage 2 v1 | Procure2Pay_G2G3 |  | 1 | 2026-10-12T20:08:59Z | 22.43 | 882 | 2026-10-12 20:15:15–20:29:57 UTC (modern_standby) |
| Stage 2 v1 | Procure2Pay_G2G3 |  | 2 | 2026-10-12T20:09:00Z | 22.46 | 882 | 2026-10-12 20:15:15–20:29:57 UTC (modern_standby) |
| Stage 2 v2 | Procure2Pay_G1G2 |  | 1 | 2026-10-15T12:14:16Z | 15.72 | 943 | 2026-10-15 11:55:02–13:28:48 UTC (modern_standby) |
| Stage 2 v2 | Procure2Pay_G1G2 |  | 2 | 2026-10-15T12:23:28Z | 7.13 | 428 | 2026-10-15 11:55:02–13:28:48 UTC (modern_standby) |
| Stage 2 v2 | Procure2Pay_G1G2 |  | 3 | 2026-10-15T12:29:59Z | 14.26 | 856 | 2026-10-15 11:55:02–13:28:48 UTC (modern_standby) |
| Stage 2 v2 | Procure2Pay_G1G2 |  | 4 | 2026-10-15T12:30:36Z | 10.07 | 604 | 2026-10-15 11:55:02–13:28:48 UTC (modern_standby) |
| Stage 2 v2 | Procure2Pay_G1G2 |  | 5 | 2026-10-15T12:40:41Z | 9.6 | 576 | 2026-10-15 11:55:02–13:28:48 UTC (modern_standby) |
| Stage 2 v2 | Procure2Pay_G1G2G3 |  | 1 | 2026-10-15T11:49:59Z | 9.04 | 240 | 2026-10-15 11:55:01–11:55:02 UTC (modern_standby); 2026-10-15 11:55:02–13:28:48 UTC (modern_standby) |
| Stage 2 v2 | Procure2Pay_G1G2G3 |  | 2 | 2026-10-15T11:51:05Z | 14.2 | 616 | 2026-10-15 11:55:01–11:55:02 UTC (modern_standby); 2026-10-15 11:55:02–13:28:48 UTC (modern_standby) |
| Stage 2 v2 | Procure2Pay_G1G2G3 |  | 3 | 2026-10-15T11:59:02Z | 9.59 | 575 | 2026-10-15 11:55:02–13:28:48 UTC (modern_standby) |
| Stage 2 v2 | Procure2Pay_G1G2G3 |  | 4 | 2026-10-15T12:05:17Z | 8.98 | 539 | 2026-10-15 11:55:02–13:28:48 UTC (modern_standby) |
| Stage 2 v2 | Procure2Pay_G1G2G3 |  | 5 | 2026-10-15T12:08:37Z | 14.85 | 891 | 2026-10-15 11:55:02–13:28:48 UTC (modern_standby) |
| Stage 2 v2 | Procure2Pay_G2G3 |  | 1 | 2026-10-15T12:44:15Z | 6.91 | 414 | 2026-10-15 11:55:02–13:28:48 UTC (modern_standby) |
| Stage 2 v2 | Procure2Pay_G2G3 |  | 2 | 2026-10-15T12:50:17Z | 40.17 | 2311 | 2026-10-15 11:55:02–13:28:48 UTC (modern_standby) |
| Stage 2 v2 | Procure2Pay_G2G3 |  | 3 | 2026-10-15T12:51:09Z | 42.64 | 2259 | 2026-10-15 11:55:02–13:28:48 UTC (modern_standby) |
| Stage 2 v2 | Procure2Pay_G3 |  | 1 | 2026-10-15T11:14:57Z | 11.92 | 2 | 2026-10-15 11:21:31–11:21:32 UTC (modern_standby); 2026-10-15 11:21:32–11:21:33 UTC (modern_standby) |
| Stage 2 v2 | Procure2Pay_G3 |  | 2 | 2026-10-15T11:17:38Z | 9.41 | 2 | 2026-10-15 11:21:31–11:21:32 UTC (modern_standby); 2026-10-15 11:21:32–11:21:33 UTC (modern_standby) |
| Stage 2 v3 | LoanApp_G1G2 |  | 4 | 2026-10-16T14:19:42Z | 4.19 | 24 | 2026-10-16 14:23:29–14:23:30 UTC (modern_standby); 2026-10-16 14:23:30–14:24:20 UTC (modern_standby) |
| Stage 2 v3 | LoanApp_G1G2 |  | 5 | 2026-10-16T14:22:25Z | 1221.7 | 73201 | 2026-10-16 14:23:29–14:23:30 UTC (modern_standby); 2026-10-16 14:23:30–14:24:20 UTC (modern_standby); 2026-10-16 14:24:20–15:52:39 UTC (modern_standby); 2026-10-16 15:52:39–16:05:07 UTC (modern_standby); 2026-10-16 16:05:07–20:52:39 UTC (modern_standby); 2026-10-16 20:52:39–10:43:01 UTC (sleep); 2026-10-17 10:43:00–10:43:01 UTC (modern_standby); 2026-10-17 10:43:38–10:44:17 UTC (modern_standby) |
| Stage 2 v3 | LoanApp_G2G3 |  | 1 | 2026-10-16T14:23:54Z | 1221.56 | 73186 | 2026-10-16 14:23:30–14:24:20 UTC (modern_standby); 2026-10-16 14:24:20–15:52:39 UTC (modern_standby); 2026-10-16 15:52:39–16:05:07 UTC (modern_standby); 2026-10-16 16:05:07–20:52:39 UTC (modern_standby); 2026-10-16 20:52:39–10:43:01 UTC (sleep); 2026-10-17 10:43:00–10:43:01 UTC (modern_standby); 2026-10-17 10:43:38–10:44:17 UTC (modern_standby) |
| Stage 2 v3 | LoanApp_G2G3 |  | 2 | 2026-10-17T10:44:07Z | 3.51 | 10 | 2026-10-17 10:43:38–10:44:17 UTC (modern_standby) |
| Stage 2 v3 | Procure2Pay_G1G3 |  | 2 | 2026-10-17T11:59:55Z | 15.17 | 50 | 2026-10-17 12:10:50–12:10:51 UTC (modern_standby); 2026-10-17 12:10:51–12:11:40 UTC (modern_standby) |
| Stage 2 v3 | Procure2Pay_G1G3 |  | 3 | 2026-10-17T12:08:14Z | 7.64 | 50 | 2026-10-17 12:10:50–12:10:51 UTC (modern_standby); 2026-10-17 12:10:51–12:11:40 UTC (modern_standby) |
| Stage 2 v3.1 | LoanApp_G1G2G3 |  | 4 | 2026-10-18T22:49:20Z | 925.56 | 55076 | 2026-10-18 22:53:46–23:01:49 UTC (modern_standby); 2026-10-18 23:01:49–03:54:14 UTC (modern_standby); 2026-10-19 03:54:14–14:11:40 UTC (sleep); 2026-10-19 14:11:40–14:11:42 UTC (modern_standby) |
| Stage 2 v3.1 | LoanApp_G1G2G3 |  | 5 | 2026-10-18T22:49:34Z | 925.67 | 55076 | 2026-10-18 22:53:46–23:01:49 UTC (modern_standby); 2026-10-18 23:01:49–03:54:14 UTC (modern_standby); 2026-10-19 03:54:14–14:11:40 UTC (sleep); 2026-10-19 14:11:40–14:11:42 UTC (modern_standby) |
| Stage 2 v3.1 | Procure2Pay_G1G2G3 |  | 2 | 2026-10-20T09:33:15Z | 9.85 | 1 | 2026-10-20 09:35:16–09:35:17 UTC (modern_standby) |
| Stage 2 v3.1 | Procure2Pay_G1G2G3 |  | 3 | 2026-10-20T09:33:59Z | 9.8 | 1 | 2026-10-20 09:35:16–09:35:17 UTC (modern_standby) |
| Stage 2 v3.1 | Procure2Pay_G2 |  | 1 | 2026-10-19T14:14:54Z | 1102.52 | 65749 | 2026-10-19 14:16:41–14:19:46 UTC (modern_standby); 2026-10-19 14:19:46–19:16:41 UTC (modern_standby); 2026-10-19 19:16:41–08:32:28 UTC (sleep); 2026-10-20 08:32:28–08:32:30 UTC (modern_standby) |
| Stage 2 v3.1 | Procure2Pay_G2 |  | 2 | 2026-10-19T14:15:14Z | 1102.64 | 65749 | 2026-10-19 14:16:41–14:19:46 UTC (modern_standby); 2026-10-19 14:19:46–19:16:41 UTC (modern_standby); 2026-10-19 19:16:41–08:32:28 UTC (sleep); 2026-10-20 08:32:28–08:32:30 UTC (modern_standby) |
| Real-life ablation Stage 2 | AcademicCredentials_G1 | V_old_refine | 4 | 2026-10-26T13:03:21Z | 134.21 | 7805 | 2026-10-26 13:04:30–13:04:32 UTC (modern_standby); 2026-10-26 13:04:32–15:14:35 UTC (modern_standby) |
| Real-life ablation Stage 2 | AcademicCredentials_G1 | V_old_refine | 5 | 2026-10-26T13:03:28Z | 133.93 | 7805 | 2026-10-26 13:04:30–13:04:32 UTC (modern_standby); 2026-10-26 13:04:32–15:14:35 UTC (modern_standby) |
| Real-life ablation Stage 2 | BPIC_2012_W_G1G2 | V_full | 4 | 2026-10-27T22:53:50Z | 14.15 | 180 | 2026-10-27 23:04:59–23:10:43 UTC (modern_standby) |
| Real-life ablation Stage 2 | BPIC_2012_W_G1G2 | V_full | 5 | 2026-10-27T23:01:06Z | 583.33 | 34473 | 2026-10-27 23:04:59–23:10:43 UTC (modern_standby); 2026-10-27 23:10:43–04:10:31 UTC (modern_standby); 2026-10-28 04:10:31–08:39:30 UTC (sleep); 2026-10-28 08:39:30–08:39:32 UTC (modern_standby) |
| Real-life ablation Stage 2 | BPIC_2012_W_G1G2 | V_no_context | 1 | 2026-10-24T11:57:20Z | 19.67 | 2 | 2026-10-24 12:00:23–12:00:25 UTC (modern_standby) |
| Real-life ablation Stage 2 | BPIC_2012_W_G1G2 | V_no_context | 2 | 2026-10-24T12:00:01Z | 9.54 | 2 | 2026-10-24 12:00:23–12:00:25 UTC (modern_standby) |
| Real-life ablation Stage 2 | BPIC_2012_W_G1G2 | V_no_log | 4 | 2026-10-27T23:07:59Z | 574.87 | 34293 | 2026-10-27 23:04:59–23:10:43 UTC (modern_standby); 2026-10-27 23:10:43–04:10:31 UTC (modern_standby); 2026-10-28 04:10:31–08:39:30 UTC (sleep); 2026-10-28 08:39:30–08:39:32 UTC (modern_standby) |
| Real-life ablation Stage 2 | BPIC_2017_W_G1G2 | V_full | 3 | 2026-10-25T12:49:41Z | 255.16 | 14647 | 2026-10-25 13:00:14–13:00:15 UTC (modern_standby); 2026-10-25 13:00:15–13:01:54 UTC (modern_standby); 2026-10-25 13:01:54–17:04:21 UTC (modern_standby) |
| Real-life ablation Stage 2 | BPIC_2017_W_G1G2 | V_no_refine | 3 | 2026-10-25T18:25:43Z | 23.23 | 577 | 2026-10-25 18:36:20–18:45:54 UTC (modern_standby); 2026-10-25 18:45:54–18:45:57 UTC (modern_standby) |
| Real-life ablation Stage 2 | BPIC_2017_W_G1G2 | V_old_refine | 1 | 2026-10-25T18:27:00Z | 22.62 | 577 | 2026-10-25 18:36:20–18:45:54 UTC (modern_standby); 2026-10-25 18:45:54–18:45:57 UTC (modern_standby) |
| Real-life ablation Stage 2 | BPIC_2017_W_G2 | V_no_context | 4 | 2026-10-28T11:16:50Z | 126.43 | 6652 | 2026-10-28 11:32:17–13:23:09 UTC (modern_standby) |
| Real-life ablation Stage 2 | BPIC_2017_W_G2 | V_no_context | 5 | 2026-10-28T11:30:26Z | 128.97 | 6652 | 2026-10-28 11:32:17–13:23:09 UTC (modern_standby) |
| Real-life ablation Stage 2 | BPIC_2017_W_G2 | V_no_kb | 4 | 2026-10-28T11:31:44Z | 129.06 | 6652 | 2026-10-28 11:32:17–13:23:09 UTC (modern_standby) |
| Real-life ablation Stage 2 | BPIC_2017_W_G2 | V_no_log | 5 | 2026-10-28T11:16:46Z | 126.51 | 6652 | 2026-10-28 11:32:17–13:23:09 UTC (modern_standby) |

## Workers

- Stage 1 (LoanApp/P2P): 4 (thread pool; `stage1_results/README.md`).
- Refinement pilot: 1 (the script makes its calls one after another).
- Stage 2 v1, v2, v3, v3.1: 2 processes; v3.2 RAG comparison: 4 processes (the batch READMEs).
- Real-life ablation Stage 1: 2 (the records' `worker` field: worker_0, worker_1).
- Real-life ablation Stage 2: 2 for repetitions 1–3 and for the first 76 runs of repetitions 4–5; 4 for the last 32 runs, which started after the restart at 2026-10-28 10:25:53 UTC (`real_life_ablation/README.md`).

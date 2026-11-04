# How many evaluation seeds? A post-hoc analysis

For every Stage 2 run with a 10-seed evaluation, every KPI comparison (the initial and, if different, the final scenario against the cached baseline, paired by seed) was re-evaluated on every k-subset of the 10 seeds (k = 1 … 9; all C(10, k) subsets). Agreement = share of subsets whose verdict equals the 10-seed verdict, averaged over comparisons. Script: `scripts/seed_analysis.py`; per batch and k: `seed_analysis.csv`; plot: `seed_analysis.png`.

- **Batch rule (primary)** — the rule the batches stored as `improved`: direction of the mean change (GLASS), and for the LoanApp/P2P utilization KPIs the utilization rule (target-pool utilization below 0.85 and below the baseline). Recomputed at 10 seeds it matches the stored verdict in 1403 of 1403 comparisons.
- **Stated rule (secondary)** — improved = mean better in the KPI's direction and paired t-test p < 0.05 (k ≥ 2); the sign of the single difference for k = 1. The 10-seed reference uses the same rule. Maintain KPIs are left out of this rule.
- **CI width** — width of the paired 95 % t-interval of the mean change, in % of the comparison's 10-seed baseline mean; per comparison the mean over its subsets, then the median (IQR) over comparisons.

## Comparisons

| Batch | KPI comparisons | 10-seed verdict improved (batch rule) |
|---|---|---|
| Stage 2 v1 | 180 | 117 |
| Stage 2 v2 | 137 | 88 |
| Stage 2 v3 | 174 | 125 |
| Stage 2 v3.1 | 164 | 128 |
| v3.2 RAG comparison | 319 | 244 |
| Real-life ablation | 429 | 208 |
| All batches | 1403 | 910 |

## Agreement with the 10-seed verdict, batch rule (%)

| Batch | k=1 | k=2 | k=3 | k=4 | k=5 | k=6 | k=7 | k=8 | k=9 |
|---|---|---|---|---|---|---|---|---|---|
| Stage 2 v1 | 89.9 | 91.6 | 93.2 | 94.1 | 95.0 | 95.7 | 96.5 | 97.4 | 98.3 |
| Stage 2 v2 | 92.3 | 93.9 | 95.0 | 95.8 | 96.4 | 96.9 | 97.3 | 97.9 | 98.4 |
| Stage 2 v3 | 89.4 | 92.2 | 93.5 | 94.5 | 95.3 | 96.0 | 96.6 | 97.4 | 98.0 |
| Stage 2 v3.1 | 92.8 | 94.2 | 95.1 | 95.8 | 96.4 | 96.9 | 97.4 | 97.9 | 98.5 |
| v3.2 RAG comparison | 91.5 | 93.2 | 94.5 | 95.4 | 96.1 | 96.8 | 97.5 | 97.9 | 98.5 |
| Real-life ablation | 71.8 | 74.5 | 77.9 | 80.9 | 83.8 | 86.4 | 89.1 | 92.1 | 95.3 |
| All batches | 85.2 | 87.4 | 89.3 | 90.8 | 92.2 | 93.4 | 94.7 | 96.0 | 97.4 |

Split by the 10-seed verdict (all batches):

| 10-seed verdict | k=1 | k=2 | k=3 | k=4 | k=5 | k=6 | k=7 | k=8 | k=9 |
|---|---|---|---|---|---|---|---|---|---|
| improved | 89.4 | 91.7 | 93.4 | 94.5 | 95.5 | 96.3 | 97.0 | 97.7 | 98.3 |
| not improved | 77.6 | 79.3 | 81.7 | 84.0 | 86.0 | 88.0 | 90.3 | 92.9 | 95.7 |

## Agreement with the 10-seed verdict, stated rule (%)

| Batch | k=1 | k=2 | k=3 | k=4 | k=5 | k=6 | k=7 | k=8 | k=9 |
|---|---|---|---|---|---|---|---|---|---|
| Stage 2 v1 | 83.2 | 71.5 | 82.7 | 89.2 | 92.6 | 95.6 | 98.3 | 98.9 | 99.6 |
| Stage 2 v2 | 90.4 | 78.2 | 92.9 | 97.0 | 98.3 | 98.9 | 99.5 | 99.6 | 99.8 |
| Stage 2 v3 | 90.2 | 75.5 | 91.7 | 94.7 | 96.7 | 98.0 | 98.9 | 98.9 | 99.1 |
| Stage 2 v3.1 | 91.5 | 75.7 | 93.0 | 96.2 | 97.4 | 98.2 | 99.0 | 99.3 | 99.7 |
| v3.2 RAG comparison | 89.2 | 76.7 | 92.9 | 96.1 | 97.5 | 98.3 | 98.9 | 99.1 | 99.5 |
| Real-life ablation | 62.9 | 83.1 | 87.9 | 91.1 | 92.5 | 93.0 | 94.4 | 94.8 | 97.4 |
| All batches | 80.9 | 77.9 | 89.9 | 93.6 | 95.3 | 96.3 | 97.5 | 97.8 | 98.9 |

## Width of the 95 % CI of the mean KPI change (% of the baseline mean, median [IQR])

| Batch | k=2 | k=3 | k=4 | k=5 | k=6 | k=7 | k=8 | k=9 | k=10 |
|---|---|---|---|---|---|---|---|---|---|
| Stage 2 v1 | 66.76 [42.37–127.59] | 20.46 [13.08–39.5] | 13.57 [8.73–26.32] | 10.77 [7.0–20.91] | 9.19 [6.03–17.84] | 8.15 [5.44–15.82] | 7.39 [4.96–14.35] | 6.82 [4.59–13.22] | 6.36 [4.31–12.33] |
| Stage 2 v2 | 53.8 [23.95–142.13] | 16.61 [7.41–43.43] | 11.18 [4.97–28.7] | 8.94 [3.97–22.71] | 7.64 [3.4–19.33] | 6.77 [3.03–17.11] | 6.15 [2.75–15.51] | 5.68 [2.55–14.29] | 5.3 [2.38–13.32] |
| Stage 2 v3 | 45.05 [22.36–92.25] | 13.75 [6.85–28.33] | 9.11 [4.6–18.87] | 7.22 [3.7–15.09] | 6.16 [3.19–12.97] | 5.46 [2.86–11.55] | 4.96 [2.61–10.53] | 4.57 [2.42–9.74] | 4.27 [2.26–9.11] |
| Stage 2 v3.1 | 42.64 [22.58–91.06] | 13.09 [6.93–28.1] | 8.67 [4.65–18.73] | 6.86 [3.73–14.88] | 5.89 [3.21–12.7] | 5.26 [2.87–11.28] | 4.79 [2.62–10.25] | 4.42 [2.43–9.46] | 4.12 [2.27–8.83] |
| v3.2 RAG comparison | 45.52 [23.65–96.26] | 13.93 [7.23–30.03] | 9.21 [4.79–20.12] | 7.3 [3.8–16.12] | 6.24 [3.24–13.86] | 5.53 [2.88–12.38] | 5.01 [2.62–11.26] | 4.62 [2.43–10.37] | 4.31 [2.27–9.66] |
| Real-life ablation | 49.21 [21.89–122.04] | 15.2 [6.71–37.37] | 10.17 [4.5–24.81] | 8.13 [3.57–19.67] | 6.98 [3.07–16.78] | 6.21 [2.74–14.87] | 5.64 [2.49–13.5] | 5.21 [2.29–12.44] | 4.86 [2.14–11.61] |
| All batches | 49.71 [24.37–114.4] | 15.32 [7.55–35.07] | 10.21 [5.03–23.42] | 8.17 [4.0–18.7] | 7.01 [3.43–16.0] | 6.23 [3.05–14.22] | 5.69 [2.78–12.96] | 5.27 [2.57–11.96] | 4.91 [2.41–11.15] |

## Findings

- **One seed decides like ten in 85.2 % of the comparisons** (batch rule, all batches); five seeds in 92.2 %, nine in 97.4 %. The LoanApp/P2P batches agree more (k = 1: 89–93 %) than the real-life ablation (k = 1: 71.8 %), where fewer comparisons are improved at 10 seeds (208 of 429).
- **Disagreements fall mostly on comparisons that are not improved at 10 seeds:** at k = 1, 77.6 % agreement there against 89.4 % for improved ones; a single seed tends to report an improvement that the 10-seed mean does not confirm.
- **With the significance test (stated rule) k = 2 is worse than k = 1:** a paired t-test on two seeds has one degree of freedom and almost never reaches p < 0.05, so nearly every comparison is 'not improved'. From k = 3 on, the stated rule agrees more often than the batch rule (all batches).
- **The CI of the mean change narrows from 49.71 % of the baseline at k = 2 to 8.17 % at k = 5 and 4.91 % at k = 10** (medians, all batches).

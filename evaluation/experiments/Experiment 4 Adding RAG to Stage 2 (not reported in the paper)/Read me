# Additional Experiment: Impact of Retrieval-Augmented Generation (RAG) on GLASS

## 1. Motivation

As an additional experiment, we investigated whether incorporating external process improvement knowledge through Retrieval-Augmented Generation (RAG) could improve GLASS's ability to generate executable business process simulation (BPS) scenarios.

We extended GLASS with an optional knowledge-retrieval component that provides literature-derived process improvement strategies to the LLM during scenario generation and refinement.

We compared GLASS with and without RAG, using the same framework version (v3.2) and keeping the remaining experimental settings unchanged.

## 2. Results on LoanApp and Procure2Pay

The experiment included 14 goal configurations across LoanApp and Procure2Pay, with five repetitions per configuration. Each final scenario was evaluated using 10 simulation seeds, resulting in 70 runs per variant.

| Evaluation measure | With RAG | Without RAG |
|---|---:|---:|
| Valid scenarios | 65/70 (92.9%) | 66/70 (94.3%) |
| All target KPIs improved | 58/70 (82.9%) | 53/70 (75.7%) |
| Utilization target achieved | 34/40 (85.0%) | 31/40 (77.5%) |

The RAG-enabled variant achieved more runs in which all target KPIs improved (58 versus 53). However, the difference was not statistically significant (p = 0.40). Similarly, the difference in utilization-target achievement was not statistically significant (p = 0.57).

These results suggest that retrieved improvement knowledge may help in some configurations, but does not demonstrate a statistically reliable overall advantage on the synthetic processes.

## 3. Results on Real-Life Event Logs

We further investigated RAG using two real-life datasets: **AcademicCredentials and BPIC 2017 W**, consistent with the datasets reported in the paper.

For each dataset, we evaluated three goal configurations (G1, G2, and G1+G2), with five repetitions each, resulting in 30 runs per variant. Final scenarios were evaluated using 10 simulation seeds.

### Scenario Validity and KPI Improvements

| Evaluation measure | With RAG | Without RAG |
|---|---:|---:|
| Total runs | 30 | 30 |
| Valid and executable scenarios | 22/30 (73.3%) | 27/30 (90.0%) |
| All target KPIs improved | 17/30 (56.7%) | 26/30 (86.7%) |

The variant without RAG achieved a higher scenario validity rate and improved all target KPIs in more runs.

The difference in all-KPI improvement rates was 30 percentage points. A two-sided Fisher's exact test on these aggregated counts gives p = 0.0204. The validity difference was not statistically significant (p = 0.1806).

### Per-Dataset Results

| Dataset | Variant | Valid scenarios | All KPIs improved |
|---|---|---:|---:|
| AcademicCredentials | With RAG | 8/15 | 4/15 |
| AcademicCredentials | Without RAG | 12/15 | 12/15 |
| BPIC 2017 W | With RAG | 14/15 | 13/15 |
| BPIC 2017 W | Without RAG | 15/15 | 14/15 |

The largest difference occurred for AcademicCredentials, where the number of runs improving all target KPIs increased from 4 to 12 when RAG was disabled. For BPIC 2017 W, both variants performed similarly.

### Observed Intervention Behavior

Analysis of the generated scenarios suggested that retrieved improvement strategies could sometimes direct the LLM toward general interventions rather than modifications targeting the bottlenecks observed in the event logs.

For example, RAG-supported recommendations sometimes encouraged reducing activity processing times, whereas the underlying improvement opportunities involved scheduled delays represented by timer events.

Without RAG, GLASS appeared to rely more directly on event-log evidence and process-specific information when selecting interventions.

The no-RAG variant also produced fewer proposals in which the stated modification direction contradicted the proposed parameter values.

However, removing RAG sometimes resulted in larger modifications that moved simulated process behavior further from the observed event logs. This suggests a potential trade-off between improving target KPIs and preserving similarity to the observed process.

## 4. Discussion and Implications

The results indicate that the effectiveness of RAG depends on the process characteristics and improvement goals.

For LoanApp and Procure2Pay, incorporating RAG resulted in slightly higher KPI improvement rates, although the differences were not statistically significant.

For AcademicCredentials and BPIC 2017 W, GLASS without RAG achieved higher validity and KPI improvement rates. The difference in the overall proportion of runs improving all target KPIs was statistically significant under the aggregated Fisher's exact test, although repeated runs within configurations may not be independent.

These observations suggest that external process improvement knowledge does not necessarily improve intervention selection. Retrieved recommendations may be useful, but they may also steer the LLM toward general strategies rather than modifications that directly address process-specific bottlenecks.

Consequently, RAG is retained as an optional component of GLASS and is disabled by default.

The paper reports the results obtained without RAG. This additional experiment documents the effect of enabling the knowledge-retrieval component and provides further evidence about its potential benefits and limitations.

## 5. Limitations

The experiment was conducted using GPT-4o-mini and a limited number of datasets and improvement goals. Therefore, the findings should not be generalized to other LLMs, knowledge bases, or process improvement settings without further evaluation.

The statistical tests reported above treat runs as independent observations. Since repetitions within the same process and goal configuration may be related, their p-values should be interpreted cautiously.

Future work could investigate selective retrieval approaches that introduce external knowledge only when it is relevant to the process context and improvement goal.

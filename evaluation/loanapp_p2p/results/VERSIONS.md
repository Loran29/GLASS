# Stage 2 batch versions

All three batches use the same 14 configurations × 5 repetitions, the same model (gpt-4o-mini), the same temperature schedule, the same seeds (42–51, paired with a cached 10-seed baseline per configuration) and the same baselines. They differ only as listed below. "Affected" means the change can act on these runs; the counts from earlier batches show how often the targeted situation occurred.

| Version | GLASS commit(s) | Code changes (GLASS) | Input changes | Scoring and reporting changes | Configurations / runs that can be affected | Batch folder |
|---|---|---|---|---|---|---|
| v1 | `8039058` (on top of `cb9cb17`: `5b3f717`, `bb6cd17`, `8039058`) | `5b3f717` arrival and resource calendars exported as they are (no 24/7). `bb6cd17` clarification summary reads staffing and overtime limits correctly. `8039058` each resource keeps its own calendar; calendar patches apply to the whole role. | Config `e28bf0c5…`, script `1def6a5f…`. Stage-1 KPI objects from the normalised records. Utilization KPIs passed with `minimize` and the overload sentence. Business context as three chat turns. | Primary: valid (validator, simulation, business limits) and all KPIs improved; utilization scored by the batch rule (target pool < 0.85 and lower than the baseline). Secondary: addressed, precision, recall, CTD/RED/CWD/NGD ratios. Reporting: `loop_discarded_better_initial`, 0.90 sensitivity. | Reference batch. | `stage2_results/` |
| v2 | `5112fbc` | Resource Utilization = busy time within working hours ÷ available working time (per resource calendar), evaluated for the role the KPI names. Previously: busy time ÷ simulated span, averaged over all resources. | None (same config and script SHA-256 as v1). | Unchanged scored rules. Reporting adds `util_improved_glass`, `util_improved_batch` and `all_kpis_improved_glass_rule` (GLASS's own verdicts next to the batch's). | The 8 configurations with a utilization KPI (40 runs): GLASS's utilization value drives its loop (stop rule, best iteration, feedback). The 6 control configurations are not touched by the change. | `stage2_results_v2/` |
| v3 | `33cf3cb`, `9c6df0c`, `9aa1dd4`, `26617b7`, `3f722a5` (records: `3f722a5`; `f6f1d30` in between changes only the config and the batch scripts) | `33cf3cb` a modification whose quoted baseline value is > 25 % from the model's actual value (or names other roles for an assignment) is rejected, and the retry prompt states the actual value; previously it was applied with a warning. `9c6df0c` a role target given as a resource-id stem, a resource id or an indexed resource name is mapped to its role, and the prompt shows SIMOD resources by name instead of id. `9aa1dd4` for a KPI with a numeric threshold, `achieved` is reported next to `improved`; the loop stops only when every threshold is achieved and every other KPI improved, the best iteration is chosen by achieved targets, then improved KPIs, then score, and the feedback says "improved but target not yet reached (current X, target Y)" or "target achieved". `26617b7` for such a KPI, the feedback also states the remaining gap (absolute and relative), that a larger change is needed, and the allowed range of the levers targeting it from the constraint summary (for capacity: the allowed additions, what the proposal adds, and how many remain). `3f722a5` the knowledge-base strategies in the refinement feedback are filtered by the KPI's direction: an entry whose stated effect is the opposite (or mixed) is dropped, and one without a derivable direction is ranked last. | Config `847334ec…`: every utilization KPI gets `kpi_inputs` with `target_threshold: 0.85` and the description sentence "Target: below 85 % of the available working time." (8 configurations). Script `3c595a55…`: plumbing only (the sentence goes into the KPI description, the threshold into `KPITarget.threshold` for the loop and the multi-seed evaluation). | Unchanged scored rules. Reporting adds `kpi<i>_target_achieved` (GLASS's verdict per KPI), `util_target_achieved_glass` (GLASS's utilization ≤ 0.85) and `util_target_achieved_batch` (target pool < 0.85), plus per-configuration counts. The verifier recomputes the achieved verdicts and checks the thresholds against the config. | Threshold and sentence, stop rule, gap and range feedback: the 8 utilization configurations (40 runs). Direction-aware retrieval: every run whose refinement feedback lists strategies for a problem KPI; in v1 and v2, strategies raising utilization were shown for a minimize-utilization KPI in 61 feedback texts (28 runs) and 18 feedback texts (10 runs). Baseline-mismatch rejection: any configuration; a > 25 % mismatch occurred in 16 runs (v1) and 22 runs (10 configurations, v2). Id-stem mapping: any configuration; an id-stem role error occurred in at least one patch attempt in 25 runs (v1) and 20 runs (v2), and cost a whole iteration in 5 runs in each batch. Best-iteration order: any run with more than one iteration (v1 42, v2 31 runs); applied to the stored single-run evaluations, it would have picked a different iteration in 3 v1 runs and no v2 run. | `stage2_results_v3/` |

**Rationale for the refinement changes in `26617b7` and `3f722a5` (from the v1 and v2 records).**
- **Gap and allowed range.**
  - *What the records show:* first proposals add exactly one resource to the overloaded role, and the loop stops after iteration 1. This happened in 5 v1 runs and 9 v2 runs, including all five v2 LoanApp_G1G3 runs. In v2 LoanApp_G1G3 rep 1, the first proposal sets Loan Officer 1 → 2, GLASS rates it "improved", and the pool's utilization stays at 0.973.
  - *Why the change:* the LLM was never told how far it still was from a sustainable level, or how much the constraints allowed (+3 per internal role).
- **Direction-aware retrieval.**
  - *What the records show:* refinement feedback for a KPI to *minimize* utilization recommends raising utilization. This happened in 61 feedback texts in 28 v1 runs and 18 feedback texts in 10 v2 runs. Examples: v1 LoanApp_G1G3 rep 1, iteration 1: "Improve operator utilization rates to achieve more balanced workload distribution", "raised overall average utilization by 8.5%", "utilization improves as a side-effect"; v2 LoanApp_G2G3 rep 5: "Increase the utilization rate of MRI scanners to reduce idle time".

**Known defect:** direction filter reads KPI name instead of category (affects Procure2Pay_G3 only; baseline already below 0.85; no score change; to be fixed in the next version). Scoring does not use the filter, so no score was altered; but the LLM in those 5 runs was shown unfiltered advice, so whether their outcomes would differ with the fix can only be determined by a re-run in the next version. In detail: the direction filter (`3f722a5`) reads a problem KPI's metric from the KPI's *name*. Three of the eight utilization KPIs do not contain "utilization" in their name (LoanApp_G3, Procure2Pay_G2G3, Procure2Pay_G3), so no entry was filtered for them. This mattered only in Procure2Pay_G3: in 12 feedback texts (all five runs), strategies raising utilization were still shown. In the 26 other feedback texts where utilization was a problem KPI, none was shown. Fix, not applied: use the KPI's category. See the v3 README.

**Utilization target achieved (v3, after refinement):**
- target achieved where the baseline was overloaded (>0.85): 25 of 25 (LoanApp_G3, LoanApp_G1G3, LoanApp_G1G2G3, Procure2Pay_G1G3, Procure2Pay_G1G2G3);
- all 40 utilization runs: 40 of 40, of which 15 already below 0.85 at baseline (LoanApp_G2G3, Procure2Pay_G3, Procure2Pay_G2G3).

Both figures hold under GLASS's flag (≤ 0.85) and under our rule (< 0.85).

**Target already met by the baseline (v3).** The target pool's baseline utilization is below 0.85 in LoanApp_G2G3 (0.849), Procure2Pay_G3 (0.838) and Procure2Pay_G2G3 (0.827). There, "utilization target achieved" holds without any change; the scored rule also requires a decrease.

**Full SHA-256 values.**
- Config: v1 and v2 `e28bf0c5d02c79d438dc96853c3a5a31e63536fd8294f345077b966916364c20`; v3 `847334ec99cd50555aa7956ee81b2e7c74667959650ba5374296fa8a8272f728`.
- Batch script: v1 and v2 `1def6a5fae8cb6c81a2156a846314acf7f440aba31d31598aba43151563e093d`; v3 `3c595a55fa9dbb6d18e203431d7b569fa7a305e211252cd9211c1359f33a38fa`.

**Hour values applied as seconds (v1 and v2; exposed by the strict baseline check in v3).** Before `33cf3cb`, the merger applied a proposed duration as a plain number in the element's own time unit (seconds in these baselines), whatever unit the LLM meant. `scripts/find_unit_defect_runs.py` searches the stored patches and merged scenarios for duration modifications whose merged value is the proposed number unconverted. It finds two patterns:
1. **Hours quoted correctly.** The quoted baseline is the actual value in hours (a unit factor of 60, 3600 or 86400), or the proposal names hours. Example: "1.1667 hours" for 4200 s, with the proposal "0.5 hours", set the activity to a constant 0.5 s.
2. **Hour-scale number that is wrong.** The quote is on an hour scale but matches no unit factor, e.g. "2.5833" for 2100 s, and the proposal ("1.5") was applied as 1.5 s, below 1/50 of the actual value.

The results are in `unit_defect_runs_v1_v2.csv` (column `class`: `unit_factor` = pattern 1, `scale_only` = pattern 2): 34 modifications in 10 runs. In every affected run the final scenario contains the defect.

| Batch | Affected runs | Pattern | In the first proposal (iteration 1) |
|---|---|---|---|
| v1 | LoanApp_G1G2 rep 2 | 1 and 2 | yes |
| v1 | Procure2Pay_G1G2G3 rep 1 | 1 | yes |
| v1 | Procure2Pay_G1G2G3 rep 3 | 2 | no |
| v2 | LoanApp_G1 rep 1 | 2 | yes |
| v2 | LoanApp_G1G2 rep 2 | 1 and 2 | yes |
| v2 | Procure2Pay_G1G2G3 rep 1 | 1 | yes |
| v2 | Procure2Pay_G1G2G3 rep 2 | 1 and 2 | yes |
| v2 | Procure2Pay_G1G2G3 rep 3 | 1 and 2 | yes |
| v2 | Procure2Pay_G1G2G3 rep 5 | 1 | no |
| v2 | Procure2Pay_G3 rep 1 | 2 | no |

These runs simulated near-zero durations for the affected activities. That shortens cycle times and can count as "improved" while moving the model away from the healthy one. v1 and v2 are not re-scored: these runs' scores are kept as recorded. The comparisons flag them (`unit_defect` in the per-run CSVs, a count per configuration).

**Achieved: ≤ vs <.**
- GLASS's `achieved` flag for a minimize KPI is value ≤ threshold.
- The batch rule, and with it `util_target_achieved_batch`, is pool utilization < 0.85.
- The two can differ only for a value of exactly 0.85.
- They are also computed on different values: GLASS's role-resolved utilization, and the batch's target-pool utilization of working time. Both are means over the 10 seeds and are normally close.

**Re-verifying an earlier batch.** `scripts/verify_stage2_results.py` re-merges the stored patches with the GLASS code checked out at the time it runs.
- With the v3 code, 35 v2 scenarios no longer re-merge identically, because the mismatched modifications are now rejected. All other checks pass.
- Re-verify v1 or v2 with GLASS at its own commit (`8039058` or `5112fbc`).
- The KPI-object check applies the config's `kpi_inputs` only to records that carry `kpi_thresholds` (v3), so v1 and v2 records are not held to the v3 input.

**Comparisons.**
- `stage2_v1_vs_v2.csv` / `.md`: v1 vs v2 (`scripts/compare_stage2_v1_v2.py`).
- `stage2_v1_v2_v3.csv` / `.md`: all three batches (`scripts/compare_stage2_versions.py`); per run: `stage2_v1_v2_v3_runs.csv` (with `unit_defect`).
- `stage2_v1_vs_v2_runs.csv`: per run for v1 and v2, with `unit_defect`.
- `unit_defect_runs_v1_v2.csv`, `unit_defect_runs_v3.csv`: hour values applied as seconds (`scripts/find_unit_defect_runs.py`; v3: none).

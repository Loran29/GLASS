# Stage 2 LoanApp/P2P: RAG comparison on GLASS v3.2

The LoanApp/P2P Stage-2 batch, run twice on GLASS v3.2: once with the knowledge base (V_full) and once without
it (V_no_kb). Both arms use the v3.1 batch's configuration, KPI objects, 14 configurations, 5 repetitions and
seeds 42–51. v3.1 (`../stage2_results_v3_1/`) is shown for reference. The question is whether the knowledge base's
effect in the real-life ablation (where V_no_kb did best) shows up on the synthetic LoanApp/P2P problems.

| | V_full | V_no_kb |
|---|---|---|
| Folder | `V_full/` | `V_no_kb/` |
| GLASS commit (records) | `fb11b64` | `fb11b64` |
| Knowledge base | on | off in generation and feedback |
| Run | 2026-10-28, 19:43–20:47 (local time) | 2026-10-28 20:47 – 2026-10-29 01:02, standby 20:54–00:06 |

- **GLASS:** `fb11b64`, whose `goal_to_parameters/` is identical to `c2f7f53` (v3.2; `../VERSIONS.md`).
  Relative to v3.1 this adds the direction rejection (`19c8478`), keeping the baseline (`c2f7f53`) and the loop's
  `measurable_as` targets (`7748a58`).
- **Inputs:** `setup/stage2_config.json` SHA-256 `847334ec…`, as in v3 and v3.1. The baseline and healthy logs
  recomputed for each arm are identical in content to v3.1's; only the gzip header timestamp differs.
- **Batch script:** `scripts/run_stage2_batch.py`, SHA-256
  `69111005703c785105dc714e54ce46727f194b808e53499c1aceed94f746e17f`: the v3/v3.1 script (`3c595a55…`) plus
  plumbing only:
  - `--variant V_full|V_no_kb`. V_no_kb calls `disable_kb` from
    `evaluation/real_life_ablation/scripts/run_ablation_batch.py`: `knowledge.retrieval.retrieve_for_second_llm`
    returns an empty result, the generation evidence's `kb_json` is empty, and the feedback's literature section
    is empty.
  - The record gets `variant`, `switches` and `kb_switch` (the stub's call counts).
  - The record's loop gets v3.2's `no_improving_scenario` and `best_candidate_iteration`.
  - With V_full the batch runs exactly as before.
- **Switch check:** V_no_kb's stub was called in all 70 runs (204 retrieval calls, 56 feedback sections
  suppressed), and none of its prompts contains the knowledge-base section. All 70 V_full runs have it.
- **Workers:** 4.

## Steps

Per arm, from the repository root:

```
python evaluation/loanapp_p2p/scripts/run_stage2_batch.py --variant <arm> --workers 4 --out evaluation/loanapp_p2p/results/stage2_results_v32_rag/<arm>
python evaluation/loanapp_p2p/scripts/complete_stage2_records.py --results evaluation/loanapp_p2p/results/stage2_results_v32_rag/<arm>
python evaluation/loanapp_p2p/scripts/stage2_reporting.py --results evaluation/loanapp_p2p/results/stage2_results_v32_rag/<arm>
python evaluation/loanapp_p2p/scripts/verify_stage2_results.py --results evaluation/loanapp_p2p/results/stage2_results_v32_rag/<arm>
```

Then, once:

```
python evaluation/loanapp_p2p/scripts/compare_stage2_rag.py
python evaluation/loanapp_p2p/scripts/make_stage2_rag_workbook.py
```

- **Batch:** no `llm_error` or `app_error` runs. Runs with no scenario in iteration 1: 1 in V_full, 6 in
  V_no_kb.
- **Completion step:** completed 5 V_no_kb runs with their final. Two runs keep `no_initial_proposal` because
  their loop then kept the baseline: V_full Procure2Pay_G1G2G3 rep 3 and V_no_kb Procure2Pay_G3 rep 3.
- **Verifier:** every check OK in both arms (`<arm>/verification_report.md`).

## Kept baseline (v3.2)

When no iteration reaches a positive score, GLASS v3.2 keeps the baseline (`no_improving_scenario`).
- **Scoring:** the run has no final scenario and is scored not valid, without improved KPIs and without an
  achieved target.
- **"Loop discarded a better initial":** such a run counts only if its initial was valid and improved at least
  one KPI on 10 seeds. The reason is recorded as "primary: final not valid (baseline kept)". This is the rule of
  the real-life ablation, added to `stage2_reporting.py` and the verifier; it acts only on v3.2 records.

## Results

Per configuration: `../stage2_rag_comparison.md` / `.csv`; per run: `../stage2_rag_comparison_runs.csv`; workbook
`../GLASS_Stage2_RAG_comparison.xlsx`.

| Measure (70 runs per arm) | V_full v3.2 | V_no_kb v3.2 | v3.1 | V_full vs V_no_kb, Fisher p |
|---|---|---|---|---|
| Valid final | 65 | 66 | 69 | 1.000 |
| All KPIs improved final | 58 | 53 | 55 | 0.404 |
| Valid and improved final | 58 | 53 | 55 | 0.404 |
| Utilization target achieved (40 runs with a utilization KPI) | 34 | 31 | 39 | 0.568 |
| Utilization target achieved where the baseline was overloaded (25 runs) | 19 | 17 | 24 | |
| Loop discarded a better initial | 12 | 9 | 11 | 0.637 |
| Loop kept the baseline | 5 | 4 | 0 | 1.000 |
| Runs with a direction_inconsistent rejection | 0 | 0 | 0 (warnings) | |
| Recall final, mean | 0.23 | 0.21 | 0.20 | sign test p 0.754 |
| CTD ratio to healthy final, median | 0.25 | 0.27 | 0.25 | sign test p 0.754 |

- **The knowledge base makes no significant difference here.**
  - V_full has 5 more valid-and-improved finals than V_no_kb (58 against 53, p = 0.404). By group: 33 against 31 in
    the configurations with a utilization KPI, and 25 against 22 in the controls.
  - No measure differs at the 5 % level, by Fisher's test on the runs or by the sign test over the configurations.
  - The real-life ablation's V_no_kb lead does not carry over to the synthetic problems.
- **Procure2Pay_G1G2G3 accounts for the losses against v3.1.**
  - In almost every run of all three batches, every iteration scores negative. The proposals either raise the
    average waiting time by 200–900 % or leave the Purchasing Agent's utilization unchanged.
  - v3.1 returned the least-bad iteration as its final: valid, not all KPIs improved, utilization target achieved.
  - v3.2 keeps the baseline in all 5 V_full runs and in 3 of 5 V_no_kb runs. V_no_kb's other two runs found
    iterations with a positive score.
  - Without this configuration, valid is 65 / 64 / 64 of 65 and the target is achieved in 34 / 31 / 34 of 35
    (V_full / V_no_kb / v3.1).
  - Four of the five V_full runs count as "loop discarded a better initial". Their initial was valid and cut
    cycle time and utilization, but raised the waiting time by 204–811 %.
- **The other kept baseline:** V_no_kb Procure2Pay_G3 rep 3, where iterations 2–4 all scored exactly 0.
- **Direction rejections:** none in either arm. LoanApp/P2P proposals never stated a direction that contradicts
  their values; v3.1 recorded no such warning either. In the real-life ablation this was frequent (1.33 per run in
  V_full).
- **Caveat:** the LLM is not seeded; each arm is one draw of 5 repetitions per configuration.

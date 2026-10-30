# Experiment 2: valid proposals that improve the KPIs

GLASS's Stage 2 ran on the 14 LoanApp / Procure2Pay configurations, 5 runs each (70 runs), on GLASS v3.2, once with
the knowledge base (V_full) and once without it (V_no_kb). A run counts when its final proposal is valid (validator,
simulation, business limits) and improves all KPIs. Each proposal is evaluated with Prosimos on 10 seeds (42-51),
paired with the baseline.

## Results

Out of 70 runs, from `../experiment_5_rag_vs_no_rag/results/stage2_rag_comparison.md` (TOTAL row):

| Measure | V_full v3.2 | V_no_kb v3.2 |
|---|---|---|
| Valid final | 65 | 66 |
| All KPIs improved final | 58 | 53 |
| Valid and improved final | 58 | 53 |

Per configuration: `results/stage2_results_v32_rag/<arm>/stage2_per_config.csv`. In 5 V_full runs and 4 V_no_kb runs
the loop kept the baseline (no iteration scored above 0); these runs have no final scenario.

## Files

| Path | Content |
|---|---|
| `results/stage2_results_v32_rag/V_full/`, `V_no_kb/` | Run records (`raw/`), summaries, verification reports, README per arm |
| `results/stage2_results_v32_rag/README.md` | Setup of the two arms |
| `results/VERSIONS.md` | What changed between the GLASS versions (v1 to v3.2) |
| `scripts/run_stage2_batch.py` | Runs the batch (`--variant V_full` or `V_no_kb`; LLM calls and simulations) |
| `scripts/complete_stage2_records.py`, `stage2_reporting.py` | Complete the records and write the summaries |
| `scripts/verify_stage2_results.py` | Independent check of a result folder |

Inputs: `../shared/loanapp_p2p/setup/stage2_config.json`, the baselines in `../shared/loanapp_p2p/stage2/`, and the
Stage 1 KPIs from `../experiment_1_expected_kpi_generation/results/stage1_results_normalised/`.

The simulated event logs are not in git; the selected-iteration logs of both arms are in experiment 3's
`inputs/glass_event_logs/v3.2_rag_comparison/`. The READMEs inside `results/` give the paths at run time.

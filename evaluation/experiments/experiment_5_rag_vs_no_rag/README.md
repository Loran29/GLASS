# Experiment 5: RAG vs no RAG

Compares experiment 2's two arms on GLASS v3.2: V_full (knowledge base on) and V_no_kb (knowledge base off in
generation and feedback). Same 14 configurations, 5 runs each, same seeds and inputs.

## Results

From `results/stage2_rag_comparison.md` (V_full vs V_no_kb, 70 runs each):

| Measure | V_full | V_no_kb | Fisher p |
|---|---|---|---|
| Valid final | 65 | 66 | 1.000 |
| All KPIs improved final | 58 | 53 | 0.404 |
| Valid and improved final | 58 | 53 | 0.404 |
| Utilization target achieved (of 40) | 34 | 31 | 0.568 |
| Loop kept the baseline | 5 | 4 | 1.000 |

No measure differs significantly at 5 %. The LLM is not seeded, so each arm is one draw of 5 runs per configuration.

The comparison covers only the two v3.2 arms.

## Files

| Path | Content |
|---|---|
| `results/stage2_rag_comparison.md`, `.csv` | Comparison per configuration and the tests |
| `results/stage2_rag_comparison_runs.csv` | One row per run and arm |
| `results/GLASS_Stage2_RAG_comparison.xlsx` | Workbook |
| `scripts/compare_stage2_rag.py` | Writes the comparison files |
| `scripts/make_stage2_rag_workbook.py` | Writes the workbook |

Inputs: `../experiment_2_valid_improving_kpis/results/stage2_results_v32_rag/`.

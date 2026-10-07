# Refinement pilot verification: FAIL

Produced by `evaluation/loanapp_p2p/scripts/verify_refinement_pilot.py`; findings are reported, never patched.

| check | checks | failures | result |
|---|---|---|---|
| 1. raw files (9/9; handle_refinement {'temperature': 0.2, 'json_mode': True}) | 17 | 0 | PASS |
| 2. source files byte-identical, first proposal = normalised result of the same answer | 45 | 0 | PASS |
| 3. decisions and prompt contents | 36 | 0 | PASS |
| 4a. accepted KPIs byte-identical: model's answer vs KPI shown in previous_kpis_json (16/18 identical) | 18 | 2 | FAIL |
| 4b. accepted KPIs byte-identical: delivered result vs first proposal (16/18 identical) | 18 | 2 | FAIL |
| 5. pilot_summary.csv rebuilt independently (9 rows) | 9 | 0 | PASS |
| 6. GLASS = commit + patch (LLM run: stage1_results_normalised patch; post-processing: current patch) | 4 | 0 | PASS |
| 7. stored result = current post-processing of the stored raw answer (no LLM call) | 18 | 0 | PASS |

## 4a. accepted KPIs byte-identical: model's answer vs KPI shown in previous_kpis_json (16/18 identical)

- LoanApp_G2G3_rep1: accepted KPI 'Average Wait Time Before Loan Offer Approval' missing from the model's answer
- Procure2Pay_G1G3_rep1: accepted KPI 'Average Duration of Settling Disputes with Suppliers' missing from the model's answer

## 4b. accepted KPIs byte-identical: delivered result vs first proposal (16/18 identical)

- LoanApp_G2G3_rep1: accepted KPI 'Average Wait Time Before Loan Offer Approval' missing from the delivered result
- Procure2Pay_G1G3_rep1: accepted KPI 'Average Duration of Settling Disputes with Suppliers' missing from the delivered result

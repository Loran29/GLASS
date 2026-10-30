# Refinement pilot verification: FAIL

Produced by `evaluation/loanapp_p2p/scripts/verify_refinement_pilot.py`; findings are reported, never patched.

| check | checks | failures | result |
|---|---|---|---|
| 1. raw files (12/12 records, max 3 rounds; handle_refinement {'temperature': 0.2, 'json_mode': True}) | 20 | 0 | PASS |
| 2. first proposals: round 1 = normalised result of the stored answer (source files byte-identical); round n = round n-1's delivered result | 54 | 0 | PASS |
| 3. decisions and prompt contents | 48 | 0 | PASS |
| 4a. accepted KPIs byte-identical: model's answer vs KPI shown in previous_kpis_json (21/23 identical) | 23 | 2 | FAIL |
| 4b. accepted KPIs byte-identical: delivered result vs first proposal (21/23 identical) | 23 | 2 | FAIL |
| 5. pilot_summary.csv rebuilt independently (12 rows) | 12 | 0 | PASS |
| 6. GLASS = commit + patch (round-1 LLM calls: stage1_results_normalised patch; later rounds and all post-processing: current patch) | 5 | 0 | PASS |
| 7. stored result = current post-processing of the stored raw answer (no LLM call) | 24 | 0 | PASS |

## 4a. accepted KPIs byte-identical: model's answer vs KPI shown in previous_kpis_json (21/23 identical)

- LoanApp_G2G3_rep1_round1: accepted KPI 'Average Wait Time Before Loan Offer Approval' missing from the model's answer
- Procure2Pay_G1G3_rep1_round1: accepted KPI 'Average Duration of Settling Disputes with Suppliers' missing from the model's answer

## 4b. accepted KPIs byte-identical: delivered result vs first proposal (21/23 identical)

- LoanApp_G2G3_rep1_round1: accepted KPI 'Average Wait Time Before Loan Offer Approval' missing from the delivered result
- Procure2Pay_G1G3_rep1_round1: accepted KPI 'Average Duration of Settling Disputes with Suppliers' missing from the delivered result

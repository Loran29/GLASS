# Stage-1 results verification: PASS

Produced by `evaluation/loanapp_p2p/scripts/verify_stage1_results.py`; findings are reported, never patched.

| check | checks | failures | result |
|---|---|---|---|
| 1. raw files (70/70; statuses {'ok': 70}) | 75 | 0 | PASS |
| 1b. same LLM outputs as stage1_results (only measurable_as re-derived) | 210 | 0 | PASS |
| 2. app.py Stage-1 code identical (30 definitions, app.py@c787694 + glass_local_fixes.patch) | 97 | 0 | PASS |
| 2b. GLASS = commit c787694 + glass_local_fixes.patch; HEAD has later GLASS changes | 5 | 0 | PASS |
| 3. normalisation + fill step re-run (273 checks, 137 measurable_as normalised, 0 measurable_as filled by the app) | 273 | 0 | PASS |
| 4. attempt records consistent with parse_with_retries | 73 | 0 | PASS |
| 5. event logs and prompts match stage1_config.json | 211 | 0 | PASS |
| 6. stage1_summary.csv rebuilt independently (70 rows) | 70 | 0 | PASS |

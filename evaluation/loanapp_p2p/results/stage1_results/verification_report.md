# Stage-1 results verification: PASS

Produced by `evaluation/loanapp_p2p/scripts/verify_stage1_results.py`; findings are reported, never patched.

| check | checks | failures | result |
|---|---|---|---|
| 1. raw files (70/70; statuses {'ok': 70}) | 75 | 0 | PASS |
| 2. app.py Stage-1 code identical (15 definitions, app.py@c787694 + glass_local_fixes.patch) | 53 | 0 | PASS |
| 2b. GLASS = commit c787694 + glass_local_fixes.patch; HEAD has later GLASS changes | 3 | 0 | PASS |
| 3. fill step re-run (136 checks, 26 measurable_as filled by the app) | 136 | 0 | PASS |
| 4. attempt records consistent with parse_with_retries | 73 | 0 | PASS |
| 5. event logs and prompts match stage1_config.json | 211 | 0 | PASS |
| 6. stage1_summary.csv rebuilt independently (70 rows) | 70 | 0 | PASS |

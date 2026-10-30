# GLASS Stage-1 batch evaluation — replication record

> Moved to `evaluation/loanapp_p2p/results/stage1_results/` in the commit that added `evaluation/loanapp_p2p/`. Paths in this README were
> updated to that layout; all SHA-256 values are unchanged. Script SHA-256 values describe the scripts as
> they were at run time; `evaluation/loanapp_p2p/scripts/relocation.patch` turns the current scripts back into those
> versions. To reproduce this folder see `evaluation/loanapp_p2p/README.md`, section "Reproducing a result folder".

Generated automatically by `evaluation/loanapp_p2p/scripts/run_stage1_batch.py` at the end of the run.

## Reproduce

See `evaluation/loanapp_p2p/README.md`, section "Reproducing a result folder": base commit
and this folder's `glass_local_fixes.patch` (both listed below), scripts from `evaluation/loanapp_p2p/scripts/`.

## Run

- Repository commit: `c787694272e40e96d3a217a76c8d46cc4f59bbca`
- Uncommitted changes at run time: the GLASS changes now recorded in `glass_local_fixes.patch`
  (`goal_to_parameters/utils/parsing.py`, `goal_to_parameters/utils/semantic_validation.py`) and the
  evaluation files, which were untracked then and are now committed under `evaluation/loanapp_p2p/`.
- Command line: `python.exe evaluation/loanapp_p2p/scripts/run_stage1_batch.py --workers 4`
- Provider / model string: `OpenRouterProvider` / `openrouter/openai/gpt-4o-mini`
- Temperature: 0.2 (app.py _render_goal_to_parameters_page: `temperature = 0.2`), json_mode: True
- num_kpis: None, use_log_evidence: True
- Repetitions: 5; configurations: 14
- This invocation: start 2026-10-01T12:07:41+00:00, end 2026-10-01T12:13:43+00:00
- Earliest / latest rep timestamp on disk: 2026-10-01T12:07:48+00:00 / 2026-10-01T12:13:22+00:00
- Python 3.13.14; openai 3.22.1; pydantic 2.13.2

## GLASS code: base commit + local patch

- Base commit: `c787694272e40e96d3a217a76c8d46cc4f59bbca`
- Local patch: `evaluation/loanapp_p2p/results/stage1_results/glass_local_fixes.patch` (= `git diff -- goal_to_parameters`), SHA-256 `73166a37c3c430c0b015f154e496363f725c2d133f9b82cfd39c91ac46508ba5`
- Parallel workers: 4 (thread pool; one provider instance per worker)

## Deviation from the UI: scope gate

The app's Generate button runs `validate_generation_scope` (a keyword gate) before
`handle_generation`. The batch records its verdict per rep (`scope_gate_passed`,
`scope_gate_message`) but always runs the Stage-1 LLM path. Configurations whose goal fails
the gate (LoanApp_G1, LoanApp_G2, LoanApp_G3, LoanApp_G1G2, LoanApp_G1G3, LoanApp_G2G3, Procure2Pay_G1, Procure2Pay_G2, Procure2Pay_G3, Procure2Pay_G1G2, Procure2Pay_G1G3, Procure2Pay_G2G3) would get an error and no KPIs in the UI.

## Files (SHA-256)

- `evaluation/loanapp_p2p/setup/stage1_config.json`: `31eae7b67543d7485f2e31558888c332767c5f6c18e2a5f0017d05ed56586e88`
- `evaluation/loanapp_p2p/scripts/run_stage1_batch.py`: `612071320f139e8516d4e7193780585b990d2e94bb1ae10a30b6a47c71adf4b7` (run-time version)
- `goal_to_parameters/app.py`: `1b7bb0625a2aa8a3ffba4bde7e014811cf66e0106e4187bda9349c904a06759d`
- `evaluation/loanapp_p2p/input_logs/LoanApp_G1G2G3_bimp.csv`: `ec8a17519ce7999cae27be6a7a1bc53fde43b35be8cb17a7cce3220305d12bd8`
- `evaluation/loanapp_p2p/input_logs/LoanApp_G1G2_bimp.csv`: `92fd10eb5eb85544912556d198f20dc3f82036014de1c6eb3f0b1a108c9ff1c9`
- `evaluation/loanapp_p2p/input_logs/LoanApp_G1G3_bimp.csv`: `77e5b7b2d41197e87528af1ea705ec80efddea00ceab9f4a5dcd013b74096ca4`
- `evaluation/loanapp_p2p/input_logs/LoanApp_G2G3_bimp.csv`: `d790617613f831a0fd8f19470dc26ec4aea4ce6e9df7a360aee8cb6241078712`
- `evaluation/loanapp_p2p/input_logs/LoanApp_durations_0.csv`: `344cfc91a9737064c5aaf36db72727ac141cfee2fa1d94113f771be247292b88`
- `evaluation/loanapp_p2p/input_logs/LoanApp_extraneous_0.csv`: `4846522bb55967bd9aaa63c0012062ff96caa7e4a9bd2568ef0da9a204606d3a`
- `evaluation/loanapp_p2p/input_logs/LoanApp_resource_target_bimp.csv`: `c3ba7170dc9465f9720747f68770cacef0104c5c00c5515d522aa5353f122198`
- `evaluation/loanapp_p2p/input_logs/Procure2Pay_G1G2G3_bimp.csv`: `825ffda980b90701d8f5834e9e6d1b752ea8fa39e89c43ae6441eace60d60fce`
- `evaluation/loanapp_p2p/input_logs/Procure2Pay_G1G2_bimp.csv`: `1cb5e289777ed7728f8a6d5e9a64bf842cb2e180d86bb860d6de139451af1c02`
- `evaluation/loanapp_p2p/input_logs/Procure2Pay_G1G3_bimp.csv`: `8b7ae08958e5cae1da98d4fe8ed5578e0f7b7896e30d543acef204034665a9a9`
- `evaluation/loanapp_p2p/input_logs/Procure2Pay_G2G3_bimp.csv`: `146eacf9e39033a4e86ea61c4184a580a6d061d7f75358afa8a3e2615385f65c`
- `evaluation/loanapp_p2p/input_logs/Procure2Pay_durations_0.csv`: `d328ce6a7f4fea8a7225816e7800647baf884691b165ec18c8fd5b819ceead65`
- `evaluation/loanapp_p2p/input_logs/Procure2Pay_extraneous_0.csv`: `eaf9fdaf2abc734b2a63b1a001125fbccc66e2369effcc42c8ae6f9d854d8cec`
- `evaluation/loanapp_p2p/input_logs/Procure2Pay_resource_target_bimp.csv`: `24d8e5e47d1de600473d66131d13389ec7f7a680a095722a6ce6a606cb67ce42`

## app.py Stage-1 code executed (compiled from app.py source; SHA-256 of each source text)

- `BPM_GOAL_HINTS`: `d7c835459b06f827c2c3b71531c49d302eb4f8736b4f3dcfda36581ba25ce948`
- `BPM_PROCESS_HINTS`: `7e15d4c99c1374fd1d1a030b64b9cb8bef551367d6a1a1d4667c7ae051484ec7`
- `OUT_OF_SCOPE_PATTERNS`: `cbd905375b9dd18b95ec7c8ffcae8b62917a5db5be59abfef754c39167a4d400`
- `_FORMULA_ACTIVITY_RE`: `9131ebd2c5748f1c9bbe5cdf2d0e35591f459b7747acc1efa2a1ed90a8ed1223`
- `_accepted_relationship_support`: `a6b199a8fc49ea53b55bc274bfab213b593b6f8ee162ce1424fa4a4820825c24`
- `_enrich_context_segment`: `b6fdee4fd659106e3adbf2ee7be27705a0b125d48e089a6ca5018b4a0ccc6a22`
- `_fill_missing_activity_measurable_as`: `6f3a9046551e958c505e81f5c9089dbf3a2aea81cf88924e3aa2034e26ab5b14`
- `_finalize_generated_result`: `44d25ebb9f9aae2875c2b2530aa9cf66f253a8dfd7b5dda886c03636930cc6c0`
- `_sanitize_kpi_grounding_claims`: `c0b54790258a57990c39edf546d940c9d27eaee83523ee3627fd7d49bbde15d9`
- `contains_any_keyword`: `229f053be1562f9a6962b07391cb7ca668571bdc40956fc9771fe510adb45177`
- `detect_out_of_scope_text`: `03d50eb73f37fe20f0637048e1e8b3a233b439c67f4cce16a293262c3a7fffd7`
- `extract_log_artifacts`: `7e29108e17dd512a25335c725c04c4a98e76154f8ef5b551b2ba0cb86c5c5755`
- `parse_with_retries`: `7866bf8468e8c24a5f54d75ff996b849e85ccc81d92df48bd56c1d7c26f5036e`
- `sanitize_user_input`: `1193446d1980ad04a2cfa61284fba81664db08cbd7f9a618ea138a799db6b508`
- `validate_generation_scope`: `6ff46a48c7a9dc8d023ca5f740c39efeace95f3c8534a94f65489545dd862e3d`

## Scoring rules (copied from the script header)

``` (analysis only; recording is never changed by scoring)
  strict_*  : an expected measurable_as string is found if it equals, case-insensitively and
              exactly, the measurable_as of a returned KPI after GLASS's fill step.
              strict_extra = returned non-null measurable_as values not in the expected set.
  lenient_* : an expected KPI is found if at least one returned KPI satisfies its rule. Text is
              compared after normalisation: lower-case, "\'" and "’" -> "'", "_" -> " ".
    "Average Cycle Time"      process_scope == "end_to_end" and category == "time" and
                              "cycle time" in (name or suggested_formula)
    "<Activity> Waiting Time" process_scope == "activity_level" and "<activity>" in (name or
                              suggested_formula) and "wait" in (name or suggested_formula);
                              an activity-duration KPI (no "wait") does not count
    "Resource Utilization"    category == "utilization" or "utilization" in name
              lenient_extra = names of returned KPIs that satisfy none of the expected rules.
  n_null_measurable_as = returned KPIs whose measurable_as is null after the fill step.
```

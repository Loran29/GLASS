# Stage 2 LoanApp/P2P, GLASS v3.2 RAG comparison: simulated event logs of the final iterations

Source: `evaluation/loanapp_p2p/results/stage2_results_v32_rag/` in https://github.com/Loran29/GLASS: two arms,
V_full (knowledge base on) and V_no_kb (knowledge base off), each 14 configurations x 5 runs. The files are copies
of the logs written by the batch, byte for byte. `manifest.csv` lists each file with its SHA-256 and the SHA-256
recorded by the batch; `matches_record` is true for all 1450 files.

## Contents
- `<arm>/<config_id>/rep_<k>/iter<n>_seed<s>.csv.gz`: for each run, the 10 simulated logs (seeds 42-51) of the final
  scenario, i.e. the refinement loop's selected (best) iteration `n`. For runs whose selected iteration is 1,
  these are the logs of the first proposal. V_full 650 files, V_no_kb 660 files.
- Runs whose loop kept the baseline have no final scenario and therefore no final logs. They are listed in
  `manifest.csv` with `final_scenario` = `none` and the reason (no iteration scored > 0; the best candidate iteration
  is attached to the record for inspection only):
  - V_full: 5 (Procure2Pay_G1G2G3 rep 1; Procure2Pay_G1G2G3 rep 2; Procure2Pay_G1G2G3 rep 3; Procure2Pay_G1G2G3 rep 4; Procure2Pay_G1G2G3 rep 5)
  - V_no_kb: 4 (Procure2Pay_G1G2G3 rep 2; Procure2Pay_G1G2G3 rep 3; Procure2Pay_G1G2G3 rep 4; Procure2Pay_G3 rep 3)
- `baseline_logs/<config_id>/seed_<s>.csv.gz`: the 10 logs of the unchanged baseline per configuration, simulated
  once per configuration with the same seeds; every run of the configuration is compared against them (paired by
  seed). 140 files, included once. Each arm recomputed its baseline logs; the files here are V_full's, and
  their SHA-256 values are those in `V_full/baseline_logs/<config_id>/baseline.json` and in every V_full run record.
  Decompressed, all 140 are identical to V_no_kb's and to the v3.1 batch's baseline logs; the .gz files differ
  only in the gzip header's timestamp (V_no_kb's records carry the SHA-256 of V_no_kb's own files).
- `manifest.csv`: kind (final / baseline), arm, config_id, rep, final_iteration, final_scenario, reason, seed,
  file_path, sha256, sha256_recorded, recorded_in, matches_record, note.

## Versions
- GLASS commit: `fb11b6404d66553be16057e05f8b447f98ad8ee0` (`fb11b64`); its `goal_to_parameters/` is identical to `c2f7f53`, i.e. v3.2.
- v3.2 differs from v3.1 (`01f76d6`) by the three fixes listed in `evaluation/loanapp_p2p/results/VERSIONS.md`:
  `19c8478` a modification whose stated direction contradicts its values is rejected (`direction_inconsistent`);
  `c2f7f53` when no iteration reaches a positive score the loop keeps the baseline (`no_improving_scenario`);
  `7748a58` the loop's KPI targets carry `measurable_as`. (`c328453` only adds the `legacy_refinement` flag, off.)
- Batch script `evaluation/loanapp_p2p/scripts/run_stage2_batch.py` SHA-256: `69111005703c785105dc714e54ce46727f194b808e53499c1aceed94f746e17f`
  (the v3/v3.1 script plus the `--variant` plumbing; V_no_kb switches the knowledge base off with
  `disable_kb` from `evaluation/real_life_ablation/scripts/run_ablation_batch.py`).
- Configuration `evaluation/loanapp_p2p/setup/stage2_config.json` SHA-256: `847334ec99cd50555aa7956ee81b2e7c74667959650ba5374296fa8a8272f728` (as in v3.1).
- Prosimos 2.1.0, Python 3.11.13.

## Simulation settings
- 1000 cases per log, simulation start 2023-02-09T10:00:00+02:00.
- Seeds 42-51 (log k of a model uses seed 42 + k; baseline, first proposal and final
  use the same seeds, so logs with the same seed are paired).
- Model of the LLM: gpt-4o-mini, temperature 0.3 (refinement: up to
  4 iterations). The LLM is not seeded.

## Log format
gzip-compressed CSV as written by Prosimos 2.1.0, one row per activity instance:
`case_id, activity, enable_time, start_time, end_time, resource`. Times are ISO timestamps with time zone;
`resource` is the resource id (e.g. `QBP_DEFAULT_RESOURCE_3`, `qbp_..._1`) of the simulated scenario.

## How the seed is applied
GLASS passes a seed to `run_prosimos_simulation` but its runner does not apply it. The batch therefore wraps
that function (a "seed shim"): before each call it seeds Python's `random` and NumPy's global random generator
with the seed passed, so a log is reproducible from its scenario, its seed and the settings above. The baseline
logs were simulated once per configuration and reproduced (the first seed re-run and compared) before use.

## Matching Prosimos inputs (in the repository)
- Every iteration of a run: `evaluation/loanapp_p2p/results/stage2_results_v32_rag/<arm>/raw/<config_id>/rep_<k>.json`,
  `loop.iterations[n-1].simulated_scenario_prosimos` (the Prosimos JSON parameters of iteration `n`; with
  `simulated_scenario` = the same in GLASS's scenario format). The final scenario is iteration
  `evaluation.final.iteration`. For a run that kept the baseline, `loop.best_candidate_iteration` names the
  iteration attached for inspection. The BPMN model is the configuration's baseline model below (patches do not
  change the model structure).
- Baseline: `evaluation/loanapp_p2p/stage2/baselines/<config_id>/sim_params.json` and `model.bpmn`; the batch sets
  `cost_per_hour` from the business context (`setup/stage2_config.json`), as recorded in each run record under
  `sim_params_with_costs`.
- Per-run evaluation, scores and KPIs: `evaluation.final` in the same run record; summaries in
  `<arm>/stage2_summary.csv` / `stage2_per_config.csv`; the comparison of both arms with v3.1 in
  `evaluation/loanapp_p2p/results/stage2_rag_comparison.md`.

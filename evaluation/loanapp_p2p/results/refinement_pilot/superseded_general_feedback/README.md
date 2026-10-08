# Superseded: refinement rounds 2–3 with general feedback

Earlier protocol: rounds 2–3 with general clarifications, run on 7–8 Oct 2026. Replaced by the concrete-feedback protocol on the lead author's decision. Kept for completeness; not part of the main results.

## Contents

- `raw/`: the four records, byte-identical to commit `af9c763` (LLM call times, UTC: `Procure2Pay_G1G2_rep1_round2.json` 2026-10-07T13:33:46+00:00; `Procure2Pay_G1G2_rep1_round3.json` 2026-10-07T13:40:39+00:00; `Procure2Pay_G2G3_rep1_round2.json` 2026-10-07T13:34:01+00:00; `Procure2Pay_G2G3_rep1_round3.json` 2026-10-07T13:40:53+00:00).
- `pilot_summary_general.csv`: their rows of the former `pilot_summary.csv`, unchanged.
- `pilot_rounds_general.json`: the rounds file these rounds were run with (general sentences).

## Note on stored paths

The records store the path of the round they refined (`previous_round_file`) as it was when they ran. For the two round-3 records that path names `raw/<case>_rep1_round2.json` of the main pilot folder, which now holds the concrete-feedback round 2. Their previous round is the round-2 record in this folder: it matches the stored `previous_round_sha256`. The main verifier does not check this folder.

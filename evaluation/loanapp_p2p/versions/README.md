# GLASS code versions

The run records name the GLASS code they ran on by its original commit ID (`glass_commit`) or by the base commit
plus a local patch (Stage 1 and the refinement pilot). This folder rebuilds every one of those versions from the
code in this tree.

## Versions

| Version | Original commit | Batch script (`run_stage2_batch.py`) SHA-256 | Used by | Changes (details: `../results/VERSIONS.md`) |
|---|---|---|---|---|
| base | `c787694272e40e96d3a217a76c8d46cc4f59bbca` | – (the script did not exist yet) | Stage 1, normalised Stage 1 and refinement pilot, each with its folder's `glass_local_fixes.patch` | – |
| v1 | `80390586dcd770513b025efdf0f4e14c521ed270` | `1def6a5fae8cb6c81a2156a846314acf7f440aba31d31598aba43151563e093d` | Stage 2 v1 (`results/stage2_results/`) | timers, calendars, normal std, context summary, role calendars |
| v2 | `5112fbc89dd71a498d2336178fdd6b3992399f6a` | `1def6a5f…` (as v1) | Stage 2 v2 | calendar-based, role-aware Resource Utilization |
| v3 | `3f722a58d10923f5efc923f9bbe5485f14a6d86c` | `3c595a55fa9dbb6d18e203431d7b569fa7a305e211252cd9211c1359f33a38fa` | Stage 2 v3 | baseline-value check, resource-id stems, improved vs achieved, gap feedback, direction-aware retrieval |
| v3.1 | `01f76d6429f725e2a9730005c4d8f60a99ac5e02` | `3c595a55…` (as v3) | Stage 2 v3.1; real-life ablation Stage 1 (`4e73788`, same GLASS code) | relative budget, calendar text and Prosimos periods, retrieval metric from the KPI category |
| v3.2 | `c2f7f535dda139449bcda780e9f1bf59694a622b` | `69111005703c785105dc714e54ce46727f194b808e53499c1aceed94f746e17f` | real-life ablation Stage 2 (`c2f7f53`, `07fa5f7`); RAG comparison (`fb11b64`); all three with the GLASS code of `c2f7f53` | direction rejection, keeping the baseline, loop targets with `measurable_as`, `legacy_refinement` flag |
| loop_seeds | `5643d0495c6db2013cade1be29cb47dc0cafcd0c` | `480af8b8e869da1840a36dbe471c79733660de9c593a2cf78706ce908375e152` | loop replications (`results/stage2_loop_seeds/`) | option `loop_seeds` of the refinement loop (default 1 = the v3.2 loop) |
| v3.3 | `821cc000c5f7f9da3197401c15020b539a600045` | `f11cf907deaa012977df039f867c52315b0c189e6a389bb07153c53b33a9a4de` | – (this tree) | knowledge base optional, off by default (`use_knowledge_base`); no change to any other component |

The code in this tree is v3.3.

## Patch files

| File | From → to | Contents |
|---|---|---|
| [`base_to_v1.patch`](base_to_v1.patch) | base → v1 | `goal_to_parameters/` (15 files) and the batch script (added) |
| [`v1_to_v2.patch`](v1_to_v2.patch) | v1 → v2 | `goal_to_parameters/` (7 files) |
| [`v2_to_v3.patch`](v2_to_v3.patch) | v2 → v3 | `goal_to_parameters/` (9 files) and the batch script |
| [`v3_to_v3.1.patch`](v3_to_v3.1.patch) | v3 → v3.1 | `goal_to_parameters/` (7 files) |
| [`v3.1_to_v3.2.patch`](v3.1_to_v3.2.patch) | v3.1 → v3.2 | `goal_to_parameters/` (4 files) and the batch script |
| [`v3.2_to_loop_seeds.patch`](v3.2_to_loop_seeds.patch) | v3.2 → loop_seeds | `goal_to_parameters/` (1 file) and the batch script |
| [`loop_seeds_to_v3.3.patch`](loop_seeds_to_v3.3.patch) | loop_seeds → v3.3 | `goal_to_parameters/` (5 files) and the batch script |

[`run_ablation_batch_recorded_to_v3.3.patch`](run_ablation_batch_recorded_to_v3.3.patch) takes
`evaluation/real_life_ablation/scripts/run_ablation_batch.py` from the version the ablation's records name (SHA-256
`6650172191bae872106016f90d48e552065b7d35ddb93c7f00f7c6190cfc9277`, apart from the wording edits below) to this
tree: in v3.3 its V_no_kb uses GLASS's option instead of a monkey-patch of the same three points. Apply it in reverse
to get the recorded version.

To rebuild a version, apply the patches in reverse, newest first, from the root of a copy of this tree. Use a copy
without end-of-line conversion (an archive, or a clone with `core.autocrlf=false`):

```bash
git apply -R evaluation/loanapp_p2p/versions/loop_seeds_to_v3.3.patch   # now loop_seeds
git apply -R evaluation/loanapp_p2p/versions/v3.2_to_loop_seeds.patch   # now v3.2
git apply -R evaluation/loanapp_p2p/versions/v3.1_to_v3.2.patch   # now v3.1
git apply -R evaluation/loanapp_p2p/versions/v3_to_v3.1.patch     # now v3
git apply -R evaluation/loanapp_p2p/versions/v2_to_v3.patch       # now v2
git apply -R evaluation/loanapp_p2p/versions/v1_to_v2.patch       # now v1
git apply -R evaluation/loanapp_p2p/versions/base_to_v1.patch     # now base
```

`git apply` also works outside a git repository. For Stage 1 and the refinement pilot, then apply the result
folder's own patch to the base (`git apply evaluation/loanapp_p2p/results/<folder>/glass_local_fixes.patch`);
all four of those patches apply to the rebuilt base.

**Checked when the patches were made:** applying them in reverse from v3.3 reproduces each version's
`goal_to_parameters/` and batch script exactly (after the text edits below). At every version, the batch script's
SHA-256 equals the value its run records list.

## Text edits relative to the recorded bytes

This tree, and every version rebuilt from it, differs from the code the records were produced with in two ways.
Both are comments only and do not change behaviour:

- **Stage tag.** Each module under `goal_to_parameters/` starts with one comment line naming its stage
  (`../../../STAGES.md`).
- **Wording.** 14 comment and docstring lines in `goal_to_parameters/` (`knowledge/__init__.py`, `knowledge/kb_data.py`,
  `knowledge/hybrid_retrieval.py`, `knowledge/models.py`, `second_llm/output_schema.py`, `second_llm/simod_input.py`,
  `second_llm/prosimos_runner.py`), three comment lines in `evaluation/real_life_ablation/scripts/run_ablation_batch.py`
  and one in `evaluation/real_life_ablation/scripts/run_stage1_ablation.py` were reworded.

Hashes the records keep of these files therefore refer to the original bytes. That includes `app_py_sha256` in the
Stage 1 and pilot records, and `script_sha256` of the real-life ablation scripts (`run_ablation_batch.py` also changed in v3.3, see
[`run_ablation_batch_recorded_to_v3.3.patch`](run_ablation_batch_recorded_to_v3.3.patch)). The batch script
`run_stage2_batch.py` and every other evaluation script, configuration, input and result are unchanged.

## Commit IDs cited in the documentation

The result READMEs and `../results/VERSIONS.md` cite the original commit IDs. Each one is in exactly one place
below.

| Commit | Where its GLASS code is |
|---|---|
| `c787694` | base |
| `4c8dbaf` | base + `results/refinement_pilot/glass_local_fixes.patch` (byte-identical to that commit's change); `base_to_v1.patch` contains it |
| `af9c763`, `e396427` | refinement pilot, later rounds: GLASS code as `4c8dbaf` |
| `bddc958`, `5b3f717`, `893db82`, `7c93a1c`, `bb6cd17`, `cb9cb17` | between base and v1: [`base_to_v1.patch`](base_to_v1.patch) |
| `8039058` | v1 |
| `5112fbc` | v2: [`v1_to_v2.patch`](v1_to_v2.patch) |
| `33cf3cb`, `26617b7`, `9aa1dd4`, `9c6df0c`, `f6f1d30` | between v2 and v3: [`v2_to_v3.patch`](v2_to_v3.patch) (`f6f1d30`: the batch script and configuration for v3) |
| `3f722a5` | v3 |
| `009f26e`, `01f7df1`, `1cf24c2` | between v3 and v3.1: [`v3_to_v3.1.patch`](v3_to_v3.1.patch) |
| `01f76d6` | v3.1 |
| `897544c`, `4e73788` | GLASS code as v3.1 (documentation and a test only) |
| `7748a58`, `19c8478`, `c328453` | between v3.1 and v3.2: [`v3.1_to_v3.2.patch`](v3.1_to_v3.2.patch) |
| `c2f7f53` | v3.2 |
| `07fa5f7`, `fb11b64` | GLASS code as v3.2 (evaluation files only) |
| `5643d04` | loop_seeds: [`v3.2_to_loop_seeds.patch`](v3.2_to_loop_seeds.patch) |
| `821cc00` | v3.3: [`loop_seeds_to_v3.3.patch`](loop_seeds_to_v3.3.patch) |

A commit "between" two versions is contained in the patch from the earlier to the later version. The patches
cover whole versions, not single commits.

# GLASS modules by stage

GLASS has three stages. Every module under `goal_to_parameters/` carries its stage as a comment in its first line
(`# GLASS Stage 1: goal → KPI identification`, `# GLASS Stage 2: scenario generation`,
`# GLASS Stage 3: simulation-based evaluation and refinement`, `# GLASS shared/UI`).

- **Stage 1: goal → KPI identification.** From a process description, a simulation goal and an event log, an LLM
  proposes SMART KPIs grounded in a profile of the log; the user accepts, rejects or refines them.
- **Stage 2: scenario generation.** From the accepted KPIs, a discovered baseline simulation model (BPMN +
  SIMOD parameters) and a clarification chat on operational constraints (optionally also retrieved
  knowledge-base evidence, off by default), an LLM proposes a delta patch of parameter changes; validators check it and a deterministic merger applies it to
  the baseline.
- **Stage 3: simulation-based evaluation and refinement.** Prosimos simulates baseline and scenario, KPIs are
  computed from the simulated logs, and a refinement loop feeds the comparison back to Stage 2 until the targets
  are met; the final scenario is evaluated on several seeds with 95 % confidence intervals.

## Stage 1: goal → KPI identification

| Module | Purpose (first sentence of its docstring) |
|---|---|
| `goal_to_parameters/models/__init__.py` | Package exports of the KPI data models. |
| `goal_to_parameters/models/smart_kpi.py` | Pydantic models of the SMART KPI set returned by Stage 1 (KPIs, categories, measurable_as, target direction). |
| `goal_to_parameters/prompts/refinement_prompt.py` | Prompt builder for refining a KPI set from the user's feedback on accepted and rejected KPIs. |
| `goal_to_parameters/prompts/smart_kpi_prompt.py` | Prompt builder for SMART KPI generation with explicit schema and multi-turn few-shot examples. |
| `goal_to_parameters/utils/__init__.py` | Package exports of the log-profiling, parsing and validation helpers. |
| `goal_to_parameters/utils/context_analysis.py` | Statistical context-performance association screening with traceable filtering. |
| `goal_to_parameters/utils/log_processing.py` | Structured event-log profiling and KPI grounding helpers. |
| `goal_to_parameters/utils/parsing.py` | Utilities for parsing LLM JSON output into structured KPI models. |
| `goal_to_parameters/utils/semantic_validation.py` | Rule-based semantic validation for generated KPI sets. |

## Stage 2: scenario generation

| Module | Purpose (first sentence of its docstring) |
|---|---|
| `goal_to_parameters/knowledge/__init__.py` | Parameter knowledge base for the second LLM step. |
| `goal_to_parameters/knowledge/bm25.py` | Okapi BM25 lexical scorer. |
| `goal_to_parameters/knowledge/cross_encoder.py` | Cross-encoder re-ranking for the hybrid RAG system. |
| `goal_to_parameters/knowledge/direction.py` | Direction of a knowledge-base entry's stated effect on a metric. |
| `goal_to_parameters/knowledge/embeddings.py` | Dense-embedding backends for RAG retrieval. |
| `goal_to_parameters/knowledge/evidence_filters.py` | Goal-conditional filtering of SIMOD, log, and context evidence. |
| `goal_to_parameters/knowledge/goal_matching.py` | Goal-category and context-rule matching shared across retrieval stages. |
| `goal_to_parameters/knowledge/hybrid_retrieval.py` | Hybrid dense + lexical retrieval over the parameter knowledge base. |
| `goal_to_parameters/knowledge/kb_data.py` | Structured knowledge base data for the second LLM step. |
| `goal_to_parameters/knowledge/models.py` | Pydantic data models for the simulation parameter knowledge base. |
| `goal_to_parameters/knowledge/pdf_indexer.py` | PDF full-text chunk indexer for the hybrid RAG system. |
| `goal_to_parameters/knowledge/retrieval.py` | RAG-style retrieval for the second LLM step. |
| `goal_to_parameters/knowledge/retrieval_result.py` | Data containers returned by the retrieval stages. |
| `goal_to_parameters/knowledge/switch.py` | Whether GLASS uses its knowledge base (RAG) in Stage 2. |
| `goal_to_parameters/knowledge/test_rag_improvements.py` | Standalone test for PDF indexing + cross-encoder re-ranking. |
| `goal_to_parameters/prompts/scenario_patch_prompt.py` | Prompt builder for the second LLM step — PATCH-ONLY variant. |
| `goal_to_parameters/prompts/scenario_proposal_prompt.py` | Prompt builder for the second LLM step |
| `goal_to_parameters/second_llm/chatbot.py` | Clarification chatbot for the Scenario Studio. |
| `goal_to_parameters/second_llm/comparison.py` | Step 8 |
| `goal_to_parameters/second_llm/compatibility_adapter.py` | Back-compat adapter |
| `goal_to_parameters/second_llm/context_summary.py` | Structured operational-context summariser. |
| `goal_to_parameters/second_llm/cost_estimation.py` | Computational cost and impact estimation for scenario proposals. |
| `goal_to_parameters/second_llm/models.py` | Pydantic data models for the Scenario Studio. |
| `goal_to_parameters/second_llm/orchestrator.py` | Orchestrator that wires the second-LLM workspace components together. |
| `goal_to_parameters/second_llm/output_schema.py` | Formal output schema for the second LLM step. |
| `goal_to_parameters/second_llm/output_schema_patch.py` | Patch-only (delta) output schema for the second LLM step. |
| `goal_to_parameters/second_llm/patch_validator.py` | Patch-level validation for :class:`ScenarioPatch` outputs. |
| `goal_to_parameters/second_llm/payload_builder.py` | Draft payload builder for the second LLM request. |
| `goal_to_parameters/second_llm/scenario_generator.py` | Scenario Studio LLM call |
| `goal_to_parameters/second_llm/scenario_merger.py` | Deterministic patch application |
| `goal_to_parameters/second_llm/semantic_compliance.py` | Semantic compliance check |
| `goal_to_parameters/second_llm/simod_input.py` | Handling for raw SIMOD output. |
| `goal_to_parameters/second_llm/simod_runner.py` | Integrated SIMOD runner with Python-native and Docker backends. |
| `goal_to_parameters/second_llm/simod_to_simubridge.py` | Deterministic SIMOD JSON -> SimuBridge baseline scenario builder. |
| `goal_to_parameters/second_llm/validation.py` | Post-schema validation for ScenarioProposal outputs. |

## Stage 3: simulation-based evaluation and refinement

| Module | Purpose (first sentence of its docstring) |
|---|---|
| `goal_to_parameters/second_llm/iterative_evaluator.py` | Iterative evaluation loop — auto-refine scenarios via simulation feedback. |
| `goal_to_parameters/second_llm/kpi_computation.py` | KPI computation from simulated event logs. |
| `goal_to_parameters/second_llm/multi_seed_evaluation.py` | Multi-seed scenario evaluation with 95% confidence intervals. |
| `goal_to_parameters/second_llm/prosimos_runner.py` | Prosimos simulation runner — executes SimuBridge scenarios via Prosimos. |
| `goal_to_parameters/second_llm/scenario_evaluation.py` | Scenario evaluation — deterministic comparison of baseline vs proposed KPIs. |

## Shared / UI

| Module | Purpose (first sentence of its docstring) |
|---|---|
| `goal_to_parameters/__init__.py` | GLASS — Goal-based LLM-Assisted Simulation Studio |
| `goal_to_parameters/app.py` | Streamlit entry point: the Goal to KPI page (Stage 1) and navigation to Scenario Studio and Scenario Evaluation. |
| `goal_to_parameters/examples/__init__.py` | Package marker of the bundled examples. |
| `goal_to_parameters/examples/second_llm_examples.py` | Pre-built example bundles for the Scenario Studio. |
| `goal_to_parameters/llm/__init__.py` | Package exports of the LLM provider adapters. |
| `goal_to_parameters/llm/anthropic_provider.py` | Anthropic API provider. |
| `goal_to_parameters/llm/cost_tracker.py` | Session-level LLM cost tracker — logs token usage and estimated cost to terminal. |
| `goal_to_parameters/llm/huggingface_provider.py` | HuggingFace Inference API provider. |
| `goal_to_parameters/llm/ollama_provider.py` | Ollama local model provider. |
| `goal_to_parameters/llm/openai_provider.py` | OpenAI API provider. |
| `goal_to_parameters/llm/openrouter_provider.py` | OpenRouter provider — OpenAI-compatible API with access to many cheap models. |
| `goal_to_parameters/llm/provider.py` | Abstract base class for LLM providers. |
| `goal_to_parameters/prompts/__init__.py` | Package exports of the Stage 1 and Stage 2 prompt builders. |
| `goal_to_parameters/second_llm/__init__.py` | Scenario Studio — from verified KPIs to simulation parameters. |
| `goal_to_parameters/second_llm/state.py` | Streamlit session-state helpers for the Scenario Studio. |
| `goal_to_parameters/tests/__init__.py` | Package marker of the unit tests. |
| `goal_to_parameters/tests/conftest.py` | pytest configuration for goal_to_parameters tests. |
| `goal_to_parameters/tests/test_patch_flow.py` | Tests for the delta/patch architecture. |
| `goal_to_parameters/tests/test_scenario_evaluation.py` | Tests for the scenario evaluation module — baseline vs proposed KPI comparison. |
| `goal_to_parameters/ui/__init__.py` | UI modules for the GLASS application. |
| `goal_to_parameters/ui/evaluation_panel.py` | Streamlit UI panel for Scenario Evaluation — baseline vs proposed comparison. |
| `goal_to_parameters/ui/kpi_display.py` | Shared KPI comparison rendering components for Streamlit panels. |
| `goal_to_parameters/ui/second_llm_panel.py` | Streamlit UI panel for the Scenario Studio. |

## Evaluation scripts

The evaluation scripts are not tagged: their SHA-256 values are recorded in the run records.

| Script | Stage |
|---|---|
| `examples/loanapp/run_example.py` | 1, and 2 + 3 (the example; calls the batch scripts' per-run functions) |
| `evaluation/loanapp_p2p/scripts/run_stage1_batch.py`, `verify_stage1_results.py`, `reprocess_stage1_normalised.py`, `run_refinement_pilot.py`, `verify_refinement_pilot.py` | 1 |
| `evaluation/real_life_ablation/scripts/run_stage1_ablation.py` | 1 |
| `evaluation/loanapp_p2p/scripts/run_stage2_batch.py` (one run = Stage 2 and Stage 3), `complete_stage2_records.py`, `stage2_reporting.py`, `verify_stage2_results.py`, `compare_stage2_*.py`, `compare_stage2_versions.py`, `make_stage2_*workbook.py`, `find_unit_defect_runs.py`, `report_demand_changed.py`, `report_loop_target_gap.py` | 2 + 3 |
| `evaluation/loanapp_p2p/scripts/compare_loop_seeds.py`, `make_loop_seeds_workbook.py`, `seed_analysis.py` | 2 + 3 (loop replications, number of evaluation seeds) |
| `evaluation/loanapp_p2p/scripts/replay_kb_option.py` | 2 + 3 (consistency of the knowledge-base option with the recorded runs) |
| `evaluation/real_life_ablation/scripts/run_ablation_batch.py`, `complete_ablation_records.py`, `ablation_reporting.py`, `verify_ablation.py`, `make_ablation_workbook.py`, `build_config.py` | 2 + 3 |
| `evaluation/loanapp_p2p/scripts/build_stage2_baselines.py`, `qbp2prosimos.py`, `merge_ends.py`; `evaluation/real_life_ablation/scripts/select_baselines.py`, `validate_baselines.py`, `compute_waiting_times.py` | 3 (baseline models and inputs) |
| `evaluation/loanapp_p2p/scripts/summarize_runtimes.py` | all (run times) |
| BPIC/Sepsis harness: `evaluation/run_stage1_evaluation.py` | 1 |
| BPIC/Sepsis harness: `evaluation/run_stage2_ablation.py`, `run_baseline_comparison.py`, `run_baseline_simulation.py`, `run_simod_baselines.py`, `verify_stage2_prereqs.py`, `test_one_call.py` | 2 + 3 |
| BPIC/Sepsis harness: `evaluation/download_logs.py`, `convert_xes_to_csv.py`, `results_dashboard.ipynb`, `baseline_comparison_dashboard.ipynb` | shared |
| `scripts/eval_retrieval.py` | 2 (knowledge-base retrieval) |
| `scripts/optimize_prompt.py` | 2 (scenario prompt) |

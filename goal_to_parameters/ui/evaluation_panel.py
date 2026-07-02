"""Streamlit UI panel for Scenario Evaluation — baseline vs proposed comparison.

Provides a UI to:
  1. Select/load baseline and proposed simulation results.
  2. Configure simulation settings.
  3. Run the comparison (via Prosimos or from uploaded CSVs).
  4. Display KPI comparison table, summary card, and charts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from second_llm.kpi_computation import KPIComputationResult, compute_kpis
from second_llm.models import FirstLLMInput, RawSimodInput, SimodResult
from second_llm.output_schema import SimuBridgeScenario
from second_llm.prosimos_runner import get_available_backend, is_prosimos_available, load_simulation_log
from second_llm.scenario_evaluation import (
    KPITarget,
    ScenarioEvaluationResult,
    TargetDirection,
    compare_kpis,
    evaluate_from_logs,
    evaluate_scenarios,
)
from ui.kpi_display import render_summary_card, render_comparison_table, render_kpi_chart, render_raw_kpi_details, render_all_kpis_comparison



# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _direction_from_str(s: str) -> TargetDirection:
    """Parse a direction string to enum."""
    s_lower = s.strip().lower()
    if s_lower in ("minimize", "min", "decrease", "reduce"):
        return TargetDirection.MINIMIZE
    if s_lower in ("maximize", "max", "increase"):
        return TargetDirection.MAXIMIZE
    return TargetDirection.MAINTAIN


def _get_workspace_scenarios() -> tuple[SimuBridgeScenario | None, SimuBridgeScenario | None, str]:
    """Extract baseline and proposed scenarios from workspace session state."""
    from second_llm.state import get_workspace

    ws = get_workspace()
    if ws is None:
        return None, None, ""

    bpmn_xml = ""
    baseline: SimuBridgeScenario | None = None
    proposed: SimuBridgeScenario | None = None

    # BPMN XML from SIMOD input
    if ws.raw_simod_input and ws.raw_simod_input.simod_result:
        bpmn_xml = ws.raw_simod_input.simod_result.bpmn_content or ""

    # Baseline: build from SIMOD JSON
    if ws.raw_simod_input and ws.raw_simod_input.simod_result:
        json_content = ws.raw_simod_input.simod_result.json_params_content
        if json_content:
            try:
                from second_llm.simod_to_simubridge import build_baseline_scenario
                result = build_baseline_scenario(
                    json.loads(json_content), bpmn_xml=bpmn_xml
                )
                if result.ok:
                    baseline = result.scenario
            except Exception:
                pass

    # Proposed: from generated scenario or direct upload
    uploaded_proposed = st.session_state.get("_eval_uploaded_proposed_scenario")
    if uploaded_proposed is not None:
        proposed = uploaded_proposed
    else:
        gen_result = st.session_state.get("_second_llm_gen_result")
        if gen_result and gen_result.proposal and gen_result.proposal.scenario:
            proposed = gen_result.proposal.scenario

    return baseline, proposed, bpmn_xml


def _get_kpi_targets_from_workspace() -> list[KPITarget]:
    """Extract KPI targets from the first-LLM output in workspace state."""
    from second_llm.state import get_workspace

    ws = get_workspace()
    if ws is None or ws.first_llm_input is None:
        return []

    targets: list[KPITarget] = []
    try:
        parsed = ws.first_llm_input.parsed
        if parsed is None and ws.first_llm_input.raw_json_text:
            parsed = json.loads(ws.first_llm_input.raw_json_text)

        if parsed is None:
            return []

        kpis = parsed.get("kpis", [])
        for kpi in kpis:
            name = kpi.get("kpi_name") or kpi.get("name", "")
            direction = _direction_from_str(
                kpi.get("target_direction", "minimize")
            )
            category = kpi.get("category", "")
            is_safeguard = direction == TargetDirection.MAINTAIN

            targets.append(KPITarget(
                name=name,
                direction=direction,
                category=category,
                is_safeguard=is_safeguard,
                unit=kpi.get("unit", ""),
                measurable_as=kpi.get("measurable_as") or None,
            ))
    except Exception:
        pass

    return targets


def _build_cost_map(scenario: SimuBridgeScenario | None) -> dict[str, float]:
    """Build a resource → cost/hour map from a scenario's roles."""
    if scenario is None:
        return {}
    cost_map: dict[str, float] = {}
    for role in scenario.resourceParameters.roles:
        for res in role.resources:
            cost_map[res.id] = role.costHour
    return cost_map


# -----------------------------------------------------------------------
# Evaluation examples
# -----------------------------------------------------------------------

_EXAMPLES_DATA_DIR = Path(__file__).resolve().parent.parent / "examples" / "data"

_EVALUATION_EXAMPLES: dict[str, dict[str, str]] = {
    "Purchase-to-Pay (Full KPIs)": {
        "dir": "purchasing",
        "bpmn_file": "model.bpmn",
        "json_params_file": "sim_params.json",
        "first_llm_file": "first_llm.json",
    },
    "Purchase-to-Pay (Time Focus)": {
        "dir": "purchasing_time_focus",
        "bpmn_file": "model.bpmn",
        "json_params_file": "sim_params.json",
        "first_llm_file": "first_llm.json",
    },
}


def _load_evaluation_example(name: str) -> bool:
    """Load an evaluation example into workspace state. Returns True on success."""
    from second_llm.state import get_workspace, set_first_llm_input, set_raw_simod_input

    example = _EVALUATION_EXAMPLES.get(name)
    if example is None:
        return False

    data_dir = _EXAMPLES_DATA_DIR / example["dir"]
    bpmn_path = data_dir / example["bpmn_file"]
    json_params_path = data_dir / example["json_params_file"]
    first_llm_path = data_dir / example["first_llm_file"]

    if not bpmn_path.exists() or not json_params_path.exists() or not first_llm_path.exists():
        return False

    bpmn_content = bpmn_path.read_text(encoding="utf-8")
    json_params_content = json_params_path.read_text(encoding="utf-8")
    first_llm_text = first_llm_path.read_text(encoding="utf-8")

    first_llm_parsed = json.loads(first_llm_text)
    set_first_llm_input(FirstLLMInput(
        raw_json_text=first_llm_text,
        parsed=first_llm_parsed,
    ))

    simod_result = SimodResult(
        bpmn_content=bpmn_content,
        json_params_content=json_params_content,
        process_name="Purchase-to-Pay",
    )
    set_raw_simod_input(RawSimodInput(
        raw_text=json_params_content,
        line_count=json_params_content.count("\n") + 1,
        is_non_empty=True,
        simod_result=simod_result,
    ))

    return True


# -----------------------------------------------------------------------
# Main panel
# -----------------------------------------------------------------------

def render_evaluation_panel() -> None:
    """Render the full Scenario Evaluation panel."""

    st.title("Scenario Evaluation")
    st.markdown(
        "<p class='gtk-hero-caption'>"
        "Compare baseline (as-is) and proposed (LLM-generated) scenarios "
        "by simulating both under identical conditions and measuring KPI differences."
        "</p>",
        unsafe_allow_html=True,
    )

    # --- Status bar ---
    backend = get_available_backend()
    _BACKEND_LABELS = {"python": "Prosimos (Python)", "docker": "Prosimos (Docker)"}
    backend_label = _BACKEND_LABELS.get(backend.value, backend.value.title()) if backend else "Not available"
    baseline, proposed, bpmn_xml = _get_workspace_scenarios()
    targets = _get_kpi_targets_from_workspace()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Simulator", backend_label if backend else "None")
    with col2:
        st.metric("Baseline", "Loaded" if baseline else "Missing")
    with col3:
        st.metric("Proposed", "Loaded" if proposed else "Missing")
    with col4:
        st.metric("Target KPIs", str(len(targets)))

    st.divider()

    # --- Load example ---
    with st.expander("Load Preset Example", expanded=False):
        load_col1, load_col2 = st.columns([3, 1])
        with load_col1:
            selected_example = st.selectbox(
                "Select example",
                options=list(_EVALUATION_EXAMPLES.keys()),
                key="_eval_example_select",
                label_visibility="collapsed",
            )
        with load_col2:
            if st.button("Load", key="_eval_load_btn", width="stretch"):
                if _load_evaluation_example(selected_example):
                    st.toast(f"Loaded: {selected_example}")
                    st.rerun()
                else:
                    st.error(f"Failed to load: {selected_example}")

    st.divider()

    # --- Simulation settings ---
    st.subheader("Simulation Settings")
    sim_col1, sim_col2, sim_col3 = st.columns(3)
    with sim_col1:
        total_cases = st.number_input(
            "Number of cases", min_value=100, max_value=50000,
            value=1000, step=100, key="_eval_total_cases",
        )
    with sim_col2:
        seed = st.number_input(
            "Random seed", min_value=1, value=42, key="_eval_seed",
        )
    with sim_col3:
        start_time = st.text_input(
            "Start time", value="2024-01-01 09:00:00.000000+00:00",
            key="_eval_start_time",
        )

    # Multi-seed confidence interval controls
    ci_col1, ci_col2 = st.columns([1, 3])
    with ci_col1:
        use_multi_seed = st.checkbox(
            "Confidence Intervals",
            value=False,
            key="_eval_multi_seed",
            help="Run multiple replications with different seeds to compute 95% confidence intervals (Law & Kelton, 2000).",
        )
    with ci_col2:
        num_seeds = st.number_input(
            "Number of replications",
            min_value=3, max_value=30, value=5, step=1,
            key="_eval_num_seeds",
            disabled=not use_multi_seed,
            help="More replications = tighter CIs but longer runtime. 5–10 recommended.",
        )

    # --- Data source tabs ---
    tab_sim, tab_upload = st.tabs([
        "Run Simulation (Prosimos)",
        "Upload Simulation Results",
    ])

    with tab_sim:
        if not backend:
            st.warning(
                "Neither Prosimos Python package nor Docker is available.\n\n"
                "**Option 1:** Install Docker Desktop and pull the image: "
                "`docker pull nokal/prosimos`\n\n"
                "**Option 2:** Use a Python 3.9–3.11 environment with "
                "`pip install prosimos`\n\n"
                "**Option 3:** Use the Upload tab to load pre-computed CSV results."
            )

        can_run = baseline is not None and proposed is not None and bpmn_xml and targets
        if not can_run:
            missing: list[str] = []
            if not baseline:
                missing.append("baseline scenario (load SIMOD output in Scenario Studio)")
            if not proposed:
                missing.append("proposed scenario (generate in Scenario Studio)")
            if not bpmn_xml:
                missing.append("BPMN XML")
            if not targets:
                missing.append("target KPIs (load first-LLM JSON in Scenario Studio)")
            st.info(f"Missing: {', '.join(missing)}")

        run_disabled = not can_run or not backend
        btn_label = f"Run Comparison ({int(num_seeds)} seeds)" if use_multi_seed else "Run Comparison"
        if st.button(
            btn_label,
            type="primary",
            disabled=run_disabled,
            width="stretch",
            key="_eval_run_btn",
        ):
            cost_map = _build_cost_map(baseline)

            if use_multi_seed:
                from second_llm.multi_seed_evaluation import evaluate_multi_seed

                progress_bar = st.progress(0, text="Starting multi-seed evaluation…")

                def _on_progress(seed_idx: int, total: int, msg: str) -> None:
                    progress_bar.progress(seed_idx / total, text=msg)

                multi_result = evaluate_multi_seed(
                    baseline_scenario=baseline,
                    proposed_scenario=proposed,
                    bpmn_xml=bpmn_xml,
                    targets=targets,
                    num_seeds=int(num_seeds),
                    base_seed=int(seed),
                    total_cases=int(total_cases),
                    start_time=start_time,
                    cost_per_hour=cost_map,
                    on_progress=_on_progress,
                )
                progress_bar.empty()
                st.session_state["_eval_multi_seed_result"] = multi_result
                st.session_state.pop("_eval_result", None)
            else:
                with st.spinner("Running baseline and proposed simulations..."):
                    eval_result = evaluate_scenarios(
                        baseline_scenario=baseline,
                        proposed_scenario=proposed,
                        bpmn_xml=bpmn_xml,
                        targets=targets,
                        total_cases=int(total_cases),
                        start_time=start_time,
                        seed=int(seed),
                        cost_per_hour=cost_map,
                    )
                st.session_state["_eval_result"] = eval_result
                st.session_state.pop("_eval_multi_seed_result", None)

    with tab_upload:
        st.markdown("Upload pre-computed simulation event log CSVs (baseline and proposed).")
        upload_col1, upload_col2 = st.columns(2)
        with upload_col1:
            baseline_file = st.file_uploader(
                "Baseline simulation log", type=["csv"],
                key="_eval_baseline_upload",
            )
        with upload_col2:
            proposed_file = st.file_uploader(
                "Proposed simulation log", type=["csv"],
                key="_eval_proposed_upload",
            )

        upload_can_compare = (
            baseline_file is not None
            and proposed_file is not None
            and len(targets) > 0
        )

        if not targets:
            st.info("Load first-LLM JSON in Scenario Studio to define target KPIs.")

        if st.button(
            "Compare Uploaded Logs",
            type="primary",
            disabled=not upload_can_compare,
            width="stretch",
            key="_eval_upload_btn",
        ):
            baseline_df = pd.read_csv(baseline_file)
            proposed_df = pd.read_csv(proposed_file)

            cost_map = _build_cost_map(baseline) if baseline else {}

            with st.spinner("Computing KPIs and comparing..."):
                eval_result = evaluate_from_logs(
                    baseline_log=baseline_df,
                    proposed_log=proposed_df,
                    targets=targets,
                    cost_per_hour=cost_map,
                )

            st.session_state["_eval_result"] = eval_result

        # --- Upload ScenarioProposal JSON ---
        st.divider()
        st.markdown("**Or upload a ScenarioProposal JSON** (exported from Scenario Studio)")

        scenario_json_file = st.file_uploader(
            "ScenarioProposal or SimuBridge JSON",
            type=["json"],
            key="_eval_scenario_json_upload",
            help="Upload a ScenarioProposal JSON or a raw SimuBridge scenario JSON.",
        )

        if scenario_json_file is not None:
            try:
                raw_json = scenario_json_file.read().decode("utf-8")
                parsed_json = json.loads(raw_json)

                if "scenario" in parsed_json:
                    from second_llm.output_schema import ScenarioProposal
                    from second_llm.scenario_generator import ScenarioGenerationResult

                    proposal = ScenarioProposal.model_validate(parsed_json)
                    gen_result_uploaded = ScenarioGenerationResult(
                        proposal=proposal,
                        raw_llm_output=raw_json,
                        generation_notes=["Loaded from uploaded ScenarioProposal JSON"],
                    )
                    st.session_state["_second_llm_gen_result"] = gen_result_uploaded
                    st.success(
                        f"Loaded ScenarioProposal: **{proposal.scenario_name}** "
                        f"({len(proposal.modifications)} modifications)"
                    )
                    st.rerun()

                elif "resourceParameters" in parsed_json:
                    from second_llm.output_schema import SimuBridgeScenario
                    from second_llm.scenario_generator import ScenarioGenerationResult

                    scenario_obj = SimuBridgeScenario.model_validate(parsed_json)
                    st.session_state["_eval_uploaded_proposed_scenario"] = scenario_obj
                    st.success(
                        f"Loaded SimuBridge scenario: **{scenario_obj.scenarioName}**"
                    )
                    st.rerun()

                else:
                    st.error(
                        "Invalid JSON format. Expected a ScenarioProposal (has 'scenario' key) "
                        "or a raw SimuBridge scenario (has 'resourceParameters' key)."
                    )
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")
            except Exception as e:
                st.error(f"Failed to parse scenario: {e}")

    # --- Display results ---
    st.divider()

    multi_seed_result = st.session_state.get("_eval_multi_seed_result")
    eval_result: ScenarioEvaluationResult | None = st.session_state.get("_eval_result")

    if multi_seed_result is not None:
        from ui.kpi_display import render_multi_seed_comparison_table, render_multi_seed_chart, render_per_seed_table

        if multi_seed_result.error:
            st.error(f"Multi-seed evaluation failed: {multi_seed_result.error}")
            return

        if multi_seed_result.summary:
            st.subheader("Evaluation Summary")
            render_summary_card(multi_seed_result.summary)
            st.caption(
                f"Based on {multi_seed_result.n_seeds} independent replications "
                f"(seeds {multi_seed_result.seeds_used[0]}–{multi_seed_result.seeds_used[-1]}). "
                f"95% CIs via Student's t-distribution."
            )

        st.subheader("KPI Comparison (95% Confidence Intervals)")
        render_multi_seed_comparison_table(multi_seed_result.kpi_comparisons)

        st.subheader("Visual Comparison")
        render_multi_seed_chart(multi_seed_result.kpi_comparisons)

        with st.expander("Statistical Details", expanded=False):
            for comp in multi_seed_result.kpi_comparisons:
                if comp.status != "computed":
                    continue
                sig_label = (
                    "YES (p < 0.05)" if comp.statistically_significant is True
                    else "NO (p ≥ 0.05)" if comp.statistically_significant is False
                    else "N/A"
                )
                p_str = f", p = {comp.p_value:.3f}" if comp.p_value is not None else ""
                st.markdown(
                    f"**{comp.kpi_name}** — "
                    f"Baseline: {comp.mean_baseline:.2f} ± {comp.baseline_stats.std:.2f}, "
                    f"Proposed: {comp.mean_proposed:.2f} ± {comp.proposed_stats.std:.2f}, "
                    f"Significant: {sig_label}{p_str}"
                )

        with st.expander("Per-Seed Values", expanded=False):
            render_per_seed_table(
                multi_seed_result.kpi_comparisons,
                multi_seed_result.seeds_used,
            )

        if multi_seed_result.averaged_baseline_kpis and multi_seed_result.averaged_proposed_kpis:
            with st.expander("All Computed Statistics — Mean Across Seeds (Baseline vs Proposed)", expanded=False):
                st.caption(f"Mean values averaged across {multi_seed_result.n_seeds} replication seeds.")
                render_all_kpis_comparison(
                    multi_seed_result.averaged_baseline_kpis,
                    multi_seed_result.averaged_proposed_kpis,
                )

        with st.expander("Simulation Settings", expanded=False):
            st.json(multi_seed_result.simulation_settings)

        return

    if eval_result is None:
        st.info("Run a comparison or upload results to see the evaluation.")
        return

    if eval_result.error:
        st.error(f"Evaluation failed: {eval_result.error}")
        return

    # Summary card
    if eval_result.summary:
        st.subheader("Evaluation Summary")
        render_summary_card(eval_result.summary)

    # KPI comparison table (target KPIs only)
    st.subheader("KPI Comparison")
    render_comparison_table(eval_result.kpi_comparisons)

    # Charts
    st.subheader("Visual Comparison")
    render_kpi_chart(eval_result.kpi_comparisons)

    # Full computed statistics across all Prosimos KPIs
    if eval_result.baseline_kpis and eval_result.proposed_kpis:
        with st.expander("All Computed Statistics (Baseline vs Proposed)", expanded=False):
            render_all_kpis_comparison(eval_result.baseline_kpis, eval_result.proposed_kpis)

    with st.expander("Simulation Settings", expanded=False):
        st.json(eval_result.simulation_settings)

"""Streamlit UI panel for the Scenario Studio.

Renders a two-step flow:
  1. **Inputs** - load the first-LLM JSON and run SIMOD or paste its output.
  2. **Chat** - continue in a ChatGPT-style workspace that already carries
     the loaded context from the input step.

The draft payload builder is still available, but it now lives inside the
chat workspace instead of as a separate top-level tab.
"""

from __future__ import annotations

import html
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as _st_components
import yaml

from examples.second_llm_examples import get_example, get_example_names
from llm import (
    AnthropicProvider,
    HuggingFaceProvider,
    LLMProvider,
    OllamaProvider,
    OpenAIProvider,
    OpenRouterProvider,
)
from second_llm.orchestrator import SecondLLMWorkspaceOrchestrator
from second_llm.simod_runner import SimodBackend, is_docker_available, is_python_simod_available
from second_llm.state import get_last_draft, get_workspace

_VIEW_KEY = "second_llm__active_view"
_VIEW_INPUTS = "Inputs"
_VIEW_CHAT = "Chat"

_PROVIDER_LABELS = {
    "ollama": "Ollama", "huggingface": "HuggingFace",
    "openai": "OpenAI", "anthropic": "Anthropic", "openrouter": "OpenRouter",
}


# -----------------------------------------------------------------------
# LLM provider helpers (self-contained — no circular import from app.py)
# -----------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@st.cache_data(show_spinner=False)
def _load_config() -> dict[str, Any]:
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_default_api_key(provider_name: str) -> str:
    env_map = {
        "huggingface": ("HUGGINGFACE_API_TOKEN", "HF_TOKEN"),
        "openai": ("OPENAI_API_KEY",),
        "anthropic": ("ANTHROPIC_API_KEY",),
        "openrouter": ("OPENROUTER_API_KEY",),
    }
    for var in env_map.get(provider_name, ()):
        val = os.getenv(var, "")
        if val:
            return val
    return ""


def _create_provider(provider_name: str, model_name: str, api_key: str = "") -> LLMProvider:
    config = _load_config()
    if provider_name == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL") or config.get("ollama", {}).get("base_url", "http://localhost:11434")
        return OllamaProvider(model=model_name, base_url=base_url)
    if provider_name == "huggingface":
        return HuggingFaceProvider(api_key=api_key, model=model_name)
    if provider_name == "openai":
        return OpenAIProvider(api_key=api_key, model=model_name)
    if provider_name == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model_name)
    if provider_name == "openrouter":
        return OpenRouterProvider(api_key=api_key, model=model_name)
    raise ValueError(f"Unsupported provider: {provider_name}")


def _render_provider_sidebar() -> LLMProvider | None:
    """Render provider configuration in the sidebar and return the provider."""
    config = _load_config()
    provider_options = [name for name in _PROVIDER_LABELS if name in config]
    if not provider_options:
        st.sidebar.warning("No LLM providers configured in config.yaml.")
        return None

    default_provider = config.get("default_provider", provider_options[0])
    default_index = provider_options.index(default_provider) if default_provider in provider_options else 0

    provider_name = st.sidebar.selectbox(
        "LLM Provider",
        options=provider_options,
        index=default_index,
        format_func=lambda item: _PROVIDER_LABELS.get(item, item.title()),
        key="second_llm__provider",
    )

    provider_config = config.get(provider_name, {})
    available_models = provider_config.get("available_models", [])
    if not available_models:
        st.sidebar.error(f"No models configured for '{provider_name}'.")
        return None

    default_model = provider_config.get("default_model", available_models[0])
    default_model_index = available_models.index(default_model) if default_model in available_models else 0
    model_name = st.sidebar.selectbox(
        "Model",
        options=available_models,
        index=default_model_index,
        key="second_llm__model",
    )

    api_key = ""
    if provider_name != "ollama":
        key_widget = f"second_llm__{provider_name}_api_key"
        if key_widget not in st.session_state:
            st.session_state[key_widget] = _get_default_api_key(provider_name)
        api_key = (st.sidebar.text_input(
            "API Key",
            key=key_widget,
            type="password",
            help=f"Leave blank if already set in .env",
        ) or "").strip()

    # Status indicator
    if provider_name == "ollama":
        try:
            provider = _create_provider(provider_name, model_name)
            ok, msg = provider.health_check()
        except Exception as exc:
            ok, msg = False, str(exc)
    elif not api_key:
        ok = False
        msg = f"{_PROVIDER_LABELS[provider_name]} API key is missing."
        provider = None
    else:
        ok = True
        msg = f"{_PROVIDER_LABELS[provider_name]} ready — {model_name}"
        provider = None

    color = "#15803d" if ok else "#b91c1c"
    bg = "#ecfdf5" if ok else "#fef2f2"
    st.sidebar.markdown(
        f"<div style='background:{bg};border:1px solid {color}33;border-radius:8px;"
        f"padding:0.45rem 0.75rem;font-size:0.84rem;font-weight:600;color:{color};"
        f"margin-top:0.35rem;'>"
        f"<span style='margin-right:0.4rem;'>&#9679;</span>{html.escape(msg)}</div>",
        unsafe_allow_html=True,
    )

    if not ok and provider_name != "ollama":
        return None

    try:
        return _create_provider(provider_name, model_name, api_key)
    except Exception as exc:
        st.sidebar.error(f"Provider error: {exc}")
        return None


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _read_uploaded_text(uploaded_file: object) -> str:
    """Decode an uploaded file to UTF-8 text."""
    try:
        return uploaded_file.read().decode("utf-8")  # type: ignore[union-attr]
    except Exception:
        return ""


def _save_uploaded_csv(uploaded_file: object) -> Path | None:
    """Write an uploaded CSV to a temporary file and return its path."""
    try:
        data = uploaded_file.read()  # type: ignore[union-attr]
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", prefix="simod_log_")
        tmp.write(data)
        tmp.close()
        return Path(tmp.name)
    except Exception:
        return None


def _get_current_kpi_session_json() -> str | None:
    """Return the current Goal -> KPI session result as JSON, if available."""
    current_result = st.session_state.get("current_kpis")
    if current_result is None:
        return None

    if hasattr(current_result, "model_dump_json"):
        try:
            return current_result.model_dump_json(indent=2)
        except Exception:
            return None

    if hasattr(current_result, "model_dump"):
        try:
            return json.dumps(current_result.model_dump(mode="json"), indent=2)
        except Exception:
            return None

    if isinstance(current_result, dict):
        try:
            return json.dumps(current_result, indent=2)
        except Exception:
            return None

    return None


def _get_active_view() -> str:
    return st.session_state.get(_VIEW_KEY, _VIEW_INPUTS)


def _set_active_view(view: str) -> None:
    st.session_state[_VIEW_KEY] = view


def _can_open_chat() -> bool:
    ws = get_workspace()
    return bool(ws.first_llm_input.is_valid and ws.raw_simod_input.is_non_empty)


def _open_chat_when_ready() -> None:
    if _can_open_chat():
        _set_active_view(_VIEW_CHAT)


_STATUS_TONES = {
    "ok":   ("#059669", "#ecfdf5", "&#10003;"),
    "warn": ("#b45309", "#fffbeb", "!"),
    "idle": ("#64748b", "#f1f5f9", "&#9675;"),
}


def _status_pill(label: str, value: str, tone: str) -> str:
    color, bg, icon = _STATUS_TONES.get(tone, _STATUS_TONES["idle"])
    return (
        f"<div style='border:1px solid {color}33;background:{bg};"
        f"border-radius:10px;padding:10px 14px;display:flex;"
        f"align-items:center;gap:12px;'>"
        f"<span style='font-size:1.1rem;color:{color};line-height:1;'>{icon}</span>"
        f"<div style='display:flex;flex-direction:column;line-height:1.2;'>"
        f"<span style='font-size:0.72rem;font-weight:600;color:#64748b;"
        f"letter-spacing:0.06em;text-transform:uppercase;'>{label}</span>"
        f"<span style='font-size:0.95rem;font-weight:600;color:{color};'>{value}</span>"
        f"</div></div>"
    )


def _render_section_header(num: int | str, title: str, description: str = "") -> None:
    """Render a styled numbered section header (mirrors the one in app.py)."""
    num_str = html.escape(str(num))
    desc_html = (
        f"<span class='gtk-section-desc'>{html.escape(description)}</span>"
        if description else ""
    )
    st.markdown(
        f"<div class='gtk-section-header'>"
        f"<span class='gtk-section-num'>{num_str}</span>"
        f"<div class='gtk-section-body'>"
        f"<span class='gtk-section-title'>{html.escape(title)}</span>"
        f"{desc_html}"
        f"</div></div>",
        unsafe_allow_html=True,
    )


def _render_workspace_status() -> None:
    ws = get_workspace()
    if ws.first_llm_input.is_valid:
        first_value, first_tone = "Validated", "ok"
    elif ws.first_llm_input.parse_error or ws.first_llm_input.validation_error:
        first_value, first_tone = "Invalid", "warn"
    else:
        first_value, first_tone = "Missing", "idle"

    has_simod = ws.raw_simod_input.is_non_empty
    simod_value, simod_tone = ("Loaded", "ok") if has_simod else ("Missing", "idle")

    has_chat = bool(ws.clarification_session.messages)
    if _can_open_chat():
        chat_value, chat_tone = "Ready", "ok"
    elif has_chat:
        chat_value, chat_tone = "Started", "ok"
    else:
        chat_value, chat_tone = "Waiting", "idle"

    status_col_1, status_col_2, status_col_3 = st.columns(3, gap="medium")
    status_col_1.markdown(_status_pill("First LLM JSON", first_value, first_tone), unsafe_allow_html=True)
    status_col_2.markdown(_status_pill("SIMOD", simod_value, simod_tone), unsafe_allow_html=True)
    status_col_3.markdown(_status_pill("Chat", chat_value, chat_tone), unsafe_allow_html=True)


def _render_chat_context_snapshot(orch: SecondLLMWorkspaceOrchestrator) -> None:
    ws = get_workspace()

    with st.expander("Loaded Context", expanded=True):
        st.markdown(orch.get_chat_context_markdown())

        if ws.first_llm_input.parsed:
            with st.expander("Validated first-LLM JSON", expanded=False):
                st.json(ws.first_llm_input.parsed)

        if ws.raw_simod_input.simod_result:
            sr = ws.raw_simod_input.simod_result
            preview_col_1, preview_col_2 = st.columns(2)
            if sr.bpmn_content:
                with preview_col_1:
                    with st.expander("BPMN diagram", expanded=False):
                        _tab_diagram, _tab_xml = st.tabs(["Diagram", "XML"])
                        with _tab_diagram:
                            _render_bpmn_viewer(sr.bpmn_content)
                        with _tab_xml:
                            st.code(sr.bpmn_content[:5000], language="xml")
                            if len(sr.bpmn_content) > 5000:
                                st.caption("(truncated)")
            if sr.json_params_content:
                with preview_col_2:
                    with st.expander("Simulation parameters preview", expanded=False):
                        try:
                            st.json(json.loads(sr.json_params_content))
                        except json.JSONDecodeError:
                            st.code(sr.json_params_content[:5000], language="json")
                            if len(sr.json_params_content) > 5000:
                                st.caption("(truncated)")
        elif ws.raw_simod_input.is_non_empty:
            with st.expander("Raw SIMOD preview", expanded=False):
                st.code(ws.raw_simod_input.raw_text[:3000], language="text")
                if len(ws.raw_simod_input.raw_text) > 3000:
                    st.caption("(truncated to 3 000 characters)")


# -----------------------------------------------------------------------
# Step 1: Inputs
# -----------------------------------------------------------------------

def _render_inputs_view(orch: SecondLLMWorkspaceOrchestrator) -> None:
    """Load the validated JSON and SIMOD output."""

    ws = get_workspace()

    _render_section_header(
        1, "Load inputs",
        "Provide the validated first-LLM JSON and SIMOD output. Both are required before opening the chat workspace.",
    )

    # -- Load Example --
    with st.expander("Load a pre-built example", expanded=not _can_open_chat()):
        example_names = get_example_names()
        cols = st.columns(len(example_names))
        for idx, name in enumerate(example_names):
            if cols[idx].button(name, key=f"second_llm__load_example_{idx}", width="stretch"):
                example = get_example(name)
                if example:
                    orch.parse_first_llm_json(example["first_llm_json"])
                    orch.accept_simod_raw_input(
                        example["simod_output"],
                        bpmn_xml=example.get("bpmn_xml", ""),
                    )
                    st.toast(f"Loaded example: {name}")
                    _open_chat_when_ready()
                    st.rerun()

    with st.container(border=True):
        st.markdown("##### First LLM JSON (validated)")
        current_kpi_session_json = _get_current_kpi_session_json()
        if current_kpi_session_json:
            if st.button("Load Output From Current KPI Session", width="stretch"):
                result = orch.parse_first_llm_json(current_kpi_session_json)
                if result.is_valid:
                    st.success("Current KPI session output loaded and validated successfully.")
                    _open_chat_when_ready()
                    st.rerun()
                if result.parse_error:
                    st.error(f"JSON parse error: {result.parse_error}")
                elif result.validation_error:
                    st.error(
                        "Schema validation error: the current KPI session output does not match "
                        f"the stage-1 KPI result schema.\n\n{result.validation_error}"
                    )
        else:
            st.caption("No current Goal -> KPI session result is available to import yet.")
        first_llm_file = st.file_uploader(
            "Upload first-LLM JSON file",
            type=["json"],
            key="second_llm__first_llm_file",
        )
        first_llm_text = st.text_area(
            "Or paste the validated first-LLM JSON here",
            value=ws.first_llm_input.raw_json_text,
            height=200,
            key="second_llm__first_llm_text",
            placeholder='{"simulation_goal_structured": "...", "kpis": [...], "reasoning": "..."}',
        )
        effective_first_llm = _read_uploaded_text(first_llm_file) if first_llm_file is not None else first_llm_text
        if effective_first_llm and effective_first_llm != ws.first_llm_input.raw_json_text:
            result = orch.parse_first_llm_json(effective_first_llm)
            if result.parse_error:
                st.error(f"JSON parse error: {result.parse_error}")
            elif result.validation_error:
                st.error(
                    "Schema validation error: the JSON is valid, but it does not match "
                    f"the stage-1 KPI result schema.\n\n{result.validation_error}"
                )
            else:
                st.success("First-LLM JSON loaded and validated successfully.")

        if ws.first_llm_input.parsed:
            with st.expander("Validated first-LLM JSON preview", expanded=False):
                st.json(ws.first_llm_input.parsed)
        elif ws.first_llm_input.parse_error:
            st.warning(f"Parse error: {ws.first_llm_input.parse_error}")
        elif ws.first_llm_input.validation_error:
            st.warning(
                "Schema validation error: the uploaded JSON does not match the "
                "stage-1 KPI result schema."
            )

    with st.container(border=True):
        st.markdown("##### SIMOD Output")

        simod_mode = st.radio(
            "How would you like to provide SIMOD output?",
            options=["Run SIMOD on an event log", "Paste / upload SIMOD output manually"],
            key="second_llm__simod_mode",
            horizontal=True,
        )

        if simod_mode == "Run SIMOD on an event log":
            _render_simod_runner(orch)
        else:
            _render_simod_manual(orch)

        if ws.raw_simod_input.is_non_empty:
            sr = ws.raw_simod_input.simod_result
            if sr and sr.process_name:
                st.success(
                    f"SIMOD results loaded for process **{sr.process_name}** "
                    f"({ws.raw_simod_input.line_count} lines total)."
                )
            else:
                st.success(f"SIMOD output loaded ({ws.raw_simod_input.line_count} lines).")

            # Show SIMOD output preview on the Inputs tab
            if sr:
                preview_col_1, preview_col_2 = st.columns(2)
                if sr.bpmn_content:
                    with preview_col_1:
                        with st.expander("BPMN diagram", expanded=False):
                            _tab_diagram, _tab_xml = st.tabs(["Diagram", "XML"])
                            with _tab_diagram:
                                _render_bpmn_viewer(sr.bpmn_content)
                            with _tab_xml:
                                st.code(sr.bpmn_content[:5000], language="xml")
                                if len(sr.bpmn_content) > 5000:
                                    st.caption("(truncated)")
                if sr.json_params_content:
                    with preview_col_2:
                        with st.expander("Simulation parameters preview", expanded=False):
                            try:
                                st.json(json.loads(sr.json_params_content))
                            except json.JSONDecodeError:
                                st.code(sr.json_params_content[:5000], language="json")
                                if len(sr.json_params_content) > 5000:
                                    st.caption("(truncated)")
            elif ws.raw_simod_input.raw_text:
                with st.expander("Raw SIMOD output preview", expanded=False):
                    st.code(ws.raw_simod_input.raw_text[:3000], language="text")
                    if len(ws.raw_simod_input.raw_text) > 3000:
                        st.caption("(truncated to 3 000 characters)")

    st.write("")
    missing: list[str] = []
    if not ws.first_llm_input.is_valid:
        missing.append("validated first-LLM JSON")
    if not ws.raw_simod_input.is_non_empty:
        missing.append("SIMOD output")

    action_col, helper_col = st.columns([1, 2])
    with action_col:
        if st.button("Open Chat Workspace", type="primary", width="stretch", disabled=bool(missing)):
            _set_active_view(_VIEW_CHAT)
            st.rerun()
    with helper_col:
        if missing:
            st.info(f"Still needed before chat: {', '.join(missing)}.")
        else:
            st.success("The chat workspace is ready and will carry the loaded input context.")


def _render_simod_runner(orch: SecondLLMWorkspaceOrchestrator) -> None:
    """UI for running SIMOD directly on an uploaded event log."""

    st.caption(
        "Upload a CSV event log and run SIMOD to automatically discover "
        "a simulation model (BPMN + JSON parameters)."
    )

    docker_ok = is_docker_available()
    python_ok = is_python_simod_available()

    backend_options: list[str] = []
    if docker_ok:
        backend_options.append("Docker")
    if python_ok:
        backend_options.append("Python")
    if not docker_ok:
        backend_options.append("Docker (not detected)")
    if not python_ok:
        backend_options.append("Python (not installed)")

    selected_backend_label = st.radio(
        "SIMOD backend",
        options=backend_options,
        key="second_llm__simod_backend",
        horizontal=True,
        help=(
            "**Docker** (recommended): only needs Docker Desktop running. "
            "**Python**: needs `pip install simod` (Python 3.9-3.11) + Java 1.8."
        ),
    )

    col_docker, col_python = st.columns(2)
    with col_docker:
        if docker_ok:
            st.caption("Docker: available")
        else:
            st.caption(
                "Docker: not detected - install "
                "[Docker Desktop](https://www.docker.com/products/docker-desktop/) "
                "and make sure the engine is running."
            )
    with col_python:
        if python_ok:
            st.caption("Python simod: installed")
        else:
            st.caption(
                "Python simod: not installed - "
                "`pip install simod` (requires Python 3.9-3.11 + Java 1.8)"
            )

    if selected_backend_label.startswith("Docker"):
        backend = SimodBackend.DOCKER
        backend_ready = docker_ok
    else:
        backend = SimodBackend.PYTHON
        backend_ready = python_ok

    if not backend_ready:
        st.warning(
            f"The selected backend ({selected_backend_label}) is not available. "
            "Please install it or switch to the other backend."
        )

    event_log_file = st.file_uploader(
        "Event log CSV",
        type=["csv"],
        key="second_llm__simod_event_log",
        help=(
            "CSV must contain columns: case_id, activity, resource, "
            "start_time, end_time (names configurable in SIMOD settings)."
        ),
    )

    one_shot = st.checkbox(
        "One-shot mode (no hyperparameter optimisation - faster)",
        value=True,
        key="second_llm__simod_one_shot",
    )

    run_disabled = event_log_file is None or not backend_ready
    if st.button("Run SIMOD", type="primary", disabled=run_disabled, width="stretch"):
        csv_path = _save_uploaded_csv(event_log_file)
        if csv_path is None:
            st.error("Failed to save the uploaded event log.")
            return
        try:
            with st.spinner("Running SIMOD - this may take a few minutes..."):
                orch.run_simod(event_log_path=csv_path, one_shot=one_shot, backend=backend)
            st.success("SIMOD finished successfully.")
            _open_chat_when_ready()
            st.rerun()
        except ImportError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"SIMOD failed: {exc}")


def _render_simod_manual(orch: SecondLLMWorkspaceOrchestrator) -> None:
    """Fallback UI for pasting / uploading raw SIMOD output."""

    ws = get_workspace()

    simod_file = st.file_uploader(
        "Upload SIMOD output file",
        type=["json", "txt", "xml", "csv"],
        key="second_llm__simod_file",
    )
    simod_text = st.text_area(
        "Or paste SIMOD output here",
        value=ws.raw_simod_input.raw_text if not ws.raw_simod_input.simod_result else "",
        height=200,
        key="second_llm__simod_text",
        placeholder="Paste SIMOD output (JSON, XML, text, ...)",
    )
    effective_simod = _read_uploaded_text(simod_file) if simod_file is not None else simod_text
    if effective_simod and effective_simod != ws.raw_simod_input.raw_text:
        simod_result = orch.accept_simod_raw_input(effective_simod)
        st.success(f"SIMOD output loaded ({simod_result.line_count} lines).")
        _open_chat_when_ready()
        if _get_active_view() == _VIEW_CHAT:
            st.rerun()


# -----------------------------------------------------------------------
# Step 2: Chat
# -----------------------------------------------------------------------

def _render_chat_view(orch: SecondLLMWorkspaceOrchestrator) -> None:
    """ChatGPT-style clarification workspace backed by loaded inputs."""

    ws = get_workspace()

    _render_section_header(
        2, "Chat workspace",
        "The LLM will ask about operational context that event logs and SIMOD cannot capture — budgets, staffing policies, overtime rules, constraints. It signals readiness when enough context is collected.",
    )

    if not orch.has_ready_chat_context():
        st.warning(
            "The chat workspace needs both the validated first-LLM JSON and the SIMOD output. "
            "Please load them in the input step first."
        )
        if st.button("Back to Inputs", type="primary"):
            _set_active_view(_VIEW_INPUTS)
            st.rerun()
        return

    orch.greet()
    orch.sync_chat_context()

    _, back_col = st.columns([4, 1])
    with back_col:
        if st.button("\u2190 Back to Inputs", width="stretch"):
            _set_active_view(_VIEW_INPUTS)
            st.rerun()

    _render_chat_context_snapshot(orch)

    # --- Chat messages ---
    for msg in ws.clarification_session.messages:
        if msg.content.startswith("=== SIMULATION FEEDBACK"):
            continue
        if msg.role.value == "assistant":
            with st.chat_message("assistant"):
                st.markdown(msg.content)
        elif msg.role.value == "user":
            with st.chat_message("user"):
                st.markdown(msg.content)

    # --- Action buttons ---
    # Both appear once the chatbot has gathered enough operational context.
    generate_clicked = False
    optimize_clicked = False
    has_gen_result = "_second_llm_gen_result" in st.session_state
    ready = orch.is_ready_to_generate()
    if ready and orch.has_provider and not has_gen_result:
        st.divider()
        st.success(
            "I've gathered enough operational context. "
            "Click below to generate the scenario."
        )
        from second_llm.prosimos_runner import get_available_backend as _get_backend
        _has_prosimos = _get_backend() is not None
        generate_clicked = st.button(
            "Generate Scenario",
            type="primary",
            width="stretch",
            disabled=not _has_prosimos,
            help="Generate, simulate, evaluate, and auto-refine until KPIs improve (requires Prosimos)" if _has_prosimos else "Requires Prosimos (Docker or Python)",
        )

    # --- Chat input — hidden once a scenario has been generated ---
    if not has_gen_result:
        user_input = st.chat_input(
            "Ask questions or add context...",
            key="second_llm__chat_input",
        )
        if user_input:
            orch.append_chat_message(user_input)
            st.rerun()

    if generate_clicked:
        _run_iterative_optimization(
            orch, ws,
            int(st.session_state.get("_eval_total_cases", 1000)),
        )

    # --- Scenario result (below chat) ---
    if has_gen_result:
        st.divider()
        _render_scenario_result()

    # --- Iterative optimization result (below scenario) ---
    if "_iter_optim_result" in st.session_state:
        st.divider()
        _render_iterative_optimization_result()

    # --- Bottom actions ---
    st.divider()
    if st.button("Reset Chat", type="secondary", width="stretch"):
        ws.clarification_session.messages.clear()
        if not hasattr(ws.clarification_session, "last_context_signature"):
            object.__setattr__(ws.clarification_session, "last_context_signature", "")
        ws.clarification_session.last_context_signature = ""
        st.session_state.pop("_second_llm_gen_result", None)
        st.session_state.pop("_iter_optim_result", None)
        orch.greet()
        orch.sync_chat_context()
        st.rerun()


# -----------------------------------------------------------------------
# Iterative optimization (Generate & Optimize)
# -----------------------------------------------------------------------

def _get_evaluation_targets() -> list:
    """Extract KPI targets from the workspace's first-LLM output."""
    import json as _json
    from second_llm.scenario_evaluation import KPITarget, TargetDirection

    ws = get_workspace()
    if ws is None or ws.first_llm_input is None or not ws.first_llm_input.parsed:
        return []

    targets = []
    for kpi in ws.first_llm_input.parsed.get("kpis", []):
        name = kpi.get("kpi_name") or kpi.get("name", "")
        dir_str = kpi.get("target_direction", "minimize").strip().lower()
        if dir_str in ("minimize", "min", "decrease", "reduce"):
            direction = TargetDirection.MINIMIZE
        elif dir_str in ("maximize", "max", "increase"):
            direction = TargetDirection.MAXIMIZE
        else:
            direction = TargetDirection.MAINTAIN
        targets.append(KPITarget(
            name=name,
            direction=direction,
            category=kpi.get("category", ""),
            is_safeguard=(direction == TargetDirection.MAINTAIN),
            unit=kpi.get("unit", ""),
        ))
    return targets


def _run_iterative_optimization(orch, ws, total_cases: int = 1000) -> None:
    """Run the iterative generate→simulate→evaluate→feedback loop."""
    import json as _json
    from second_llm.iterative_evaluator import run_iterative_evaluation
    from second_llm.prosimos_runner import get_available_backend
    from second_llm.scenario_evaluation import KPITarget
    from second_llm.simod_to_simubridge import build_baseline_scenario

    # Get baseline scenario and BPMN
    bpmn_xml = ""
    if ws.raw_simod_input.simod_result and ws.raw_simod_input.simod_result.bpmn_content:
        bpmn_xml = ws.raw_simod_input.simod_result.bpmn_content

    json_content = None
    if ws.raw_simod_input.simod_result and ws.raw_simod_input.simod_result.json_params_content:
        json_content = ws.raw_simod_input.simod_result.json_params_content

    if not json_content or not bpmn_xml:
        st.error("Missing SIMOD output (BPMN or JSON params). Cannot run optimization.")
        return

    baseline_build = build_baseline_scenario(_json.loads(json_content), bpmn_xml=bpmn_xml)
    if not baseline_build.ok:
        st.error(f"Failed to build baseline scenario: {'; '.join(baseline_build.errors)}")
        return

    baseline_scenario = baseline_build.scenario
    targets = _get_evaluation_targets()
    if not targets:
        st.error("No KPI targets found. Load first-LLM JSON first.")
        return

    # Build cost map from baseline
    cost_map: dict[str, float] = {}
    for role in baseline_scenario.resourceParameters.roles:
        for res in role.resources:
            cost_map[res.id] = role.costHour

    # Run with progress
    status_container = st.status("Running iterative optimization...", expanded=True)

    def _on_iteration(iteration: int, msg: str) -> None:
        label = f"Iteration {iteration}" if iteration > 0 else "Setup"
        st.write(f"**{label}:** {msg}")

    with status_container:
        iter_result = run_iterative_evaluation(
            orchestrator=orch,
            baseline_scenario=baseline_scenario,
            bpmn_xml=bpmn_xml,
            targets=targets,
            max_iterations=4,
            total_cases=total_cases,
            start_time="2024-01-01 09:00:00.000000+00:00",
            seed=42,
            cost_per_hour=cost_map,
            on_iteration=_on_iteration,
        )

    st.session_state["_iter_optim_result"] = iter_result

    # Also store the best generation result for the scenario display
    if iter_result.best and iter_result.best.gen_result and iter_result.best.gen_result.success:
        st.session_state["_second_llm_gen_result"] = iter_result.best.gen_result

    if iter_result.error:
        status_container.update(label="Optimization failed", state="error")
    elif iter_result.improved:
        status_container.update(
            label=f"All KPIs improved after {iter_result.total_iterations} iteration(s)!",
            state="complete",
        )
    else:
        status_container.update(
            label=f"Best result found after {iter_result.total_iterations} iteration(s)",
            state="complete",
        )
    st.rerun()


def _render_iterative_optimization_result() -> None:
    """Display the iterative optimization results."""
    import pandas as _pd
    from second_llm.iterative_evaluator import IterativeEvaluationResult
    from second_llm.scenario_evaluation import OverallStatus

    iter_result: IterativeEvaluationResult | None = st.session_state.get("_iter_optim_result")
    if iter_result is None:
        return

    st.subheader("Optimization Results")

    if iter_result.error:
        st.error(f"Error: {iter_result.error}")
        return

    if not iter_result.iterations:
        st.info("No iterations completed.")
        return

    # Summary
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Iterations", str(iter_result.total_iterations))
    with col2:
        best_label = f"#{iter_result.best_iteration_idx + 1}" if iter_result.best_iteration_idx is not None else "-"
        st.metric("Best", best_label)
    with col3:
        st.metric("Time", f"{iter_result.total_time_seconds:.0f}s")

    # Iteration history
    rows = []
    for it in iter_result.iterations:
        if it.status == OverallStatus.IMPROVED:
            status_icon = "✅"
        elif it.status == OverallStatus.WORSENED:
            status_icon = "❌"
        elif it.status == OverallStatus.TRADE_OFF_DETECTED:
            status_icon = "⚠️"
        elif it.error:
            status_icon = "💥"
        else:
            status_icon = "❓"

        improved_count = 0
        worsened_count = 0
        if it.eval_result and it.eval_result.kpi_comparisons:
            improved_count = sum(1 for e in it.eval_result.kpi_comparisons if e.improved is True)
            worsened_count = sum(1 for e in it.eval_result.kpi_comparisons if e.improved is False)

        is_best = (iter_result.best_iteration_idx is not None
                   and it.iteration - 1 == iter_result.best_iteration_idx)

        rows.append({
            "#": f"{it.iteration} ⭐" if is_best else it.iteration,
            "Status": f"{status_icon} {it.status.value if it.status else it.error or 'error'}",
            "Score": f"{it.score:.1f}",
            "Improved": improved_count,
            "Worsened": worsened_count,
        })

    st.dataframe(_pd.DataFrame(rows), width="stretch", hide_index=True)

    # Best result KPI details
    best = iter_result.best
    if best and best.eval_result and best.eval_result.ok:
        with st.expander("Best Iteration KPI Comparison", expanded=True):
            for e in best.eval_result.kpi_comparisons:
                if e.status != "computed":
                    continue
                baseline_str = f"{e.baseline_value:.2f}" if e.baseline_value is not None else "?"
                proposed_str = f"{e.proposed_value:.2f}" if e.proposed_value is not None else "?"
                pct_str = f"({e.percentage_change:+.1f}%)" if e.percentage_change is not None else ""
                if e.improved is True:
                    icon = "✅"
                elif e.violated_safeguard:
                    icon = "❌"
                elif e.improved is False:
                    icon = "⚠️"
                else:
                    icon = "➖"
                st.markdown(f"{icon} **{e.kpi_name}**: {baseline_str} → {proposed_str} {pct_str}")

    # Feedback messages
    feedback_iterations = [it for it in iter_result.iterations if it.feedback_message]
    if feedback_iterations:
        with st.expander("Feedback Sent to LLM", expanded=False):
            for it in feedback_iterations:
                st.markdown(f"**After Iteration {it.iteration}:**")
                st.code(it.feedback_message, language=None)


# -----------------------------------------------------------------------
# Scenario result display
# -----------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _build_bpmn_name_map(bpmn_xml: str) -> dict[str, str]:
    """Map BPMN element/flow IDs to human-readable names for display."""
    from second_llm.simod_to_simubridge import build_flow_name_map
    if not bpmn_xml:
        return {}
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(bpmn_xml)
        name_map: dict[str, str] = {}
        for elem in root.iter():
            eid = elem.attrib.get("id")
            name = (elem.attrib.get("name") or "").strip()
            if eid and name:
                name_map[eid] = name
        # Also include flow → target-activity labels
        name_map.update(build_flow_name_map(bpmn_xml))
        return name_map
    except Exception:
        return {}


def _display_name(element_id: str, name_map: dict[str, str]) -> str:
    """Return human-readable name for a BPMN element ID, or the ID itself."""
    return name_map.get(element_id, element_id)


def _render_bpmn_viewer(bpmn_xml: str, height: int = 700) -> None:
    """Render a BPMN diagram using bpmn-js loaded from CDN.

    Falls back to truncated XML when the CDN is unreachable or the XML
    is empty.  The diagram is interactive: scroll to zoom, drag to pan.
    """
    if not bpmn_xml:
        st.info("No BPMN content available.")
        return

    escaped = json.dumps(bpmn_xml)
    html_src = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<script src="https://unpkg.com/bpmn-js@17/dist/bpmn-viewer.development.js"></script>
<style>
  html, body {{ margin:0; padding:0; background:#f8f9fa; }}
  #canvas {{ width:100%; height:{height}px; }}
  #error  {{ color:red; padding:8px; font-family:monospace; font-size:12px; }}
  #controls {{
    position:absolute; top:8px; right:8px; z-index:100;
    display:flex; gap:4px;
  }}
  #controls button {{
    background:#fff; border:1px solid #ccc; border-radius:4px;
    padding:4px 10px; cursor:pointer; font-size:14px; line-height:1;
  }}
  #controls button:hover {{ background:#e9ecef; }}
</style>
</head>
<body style="position:relative;">
<div id="canvas"></div>
<div id="controls">
  <button onclick="zoom(0.2)">+</button>
  <button onclick="zoom(-0.2)">−</button>
  <button onclick="fit()">⊡ Fit</button>
</div>
<div id="error"></div>
<script>
  var viewer = new BpmnJS({{ container: '#canvas' }});
  var canvas;
  viewer.importXML({escaped})
    .then(function() {{
      canvas = viewer.get('canvas');
      canvas.zoom('fit-viewport');
      // Zoom out a bit so the full diagram is visible with padding
      var currentZoom = canvas.zoom();
      canvas.zoom(currentZoom * 0.85);
    }})
    .catch(function(err) {{
      document.getElementById('error').textContent = 'Render error: ' + err.message;
    }});
  function zoom(delta) {{
    if (canvas) canvas.zoom(canvas.zoom() + delta);
  }}
  function fit() {{
    if (canvas) {{ canvas.zoom('fit-viewport'); canvas.zoom(canvas.zoom() * 0.85); }}
  }}
</script>
</body>
</html>"""
    _st_components.html(html_src, height=height, scrolling=False)


def _resolve_literature_ids(paper_ids: list[int]) -> list[str]:
    """Resolve KB paper IDs to short 'Author (Year)' citations."""
    from knowledge.kb_data import LITERATURE

    index = {lit.paper_id: lit for lit in LITERATURE}
    citations: list[str] = []
    for pid in paper_ids:
        lit = index.get(pid)
        if lit:
            citations.append(f"{lit.authors} ({lit.year})")
        else:
            citations.append(f"Paper #{pid}")
    return citations



def _render_cost_report(cost_report) -> None:
    """Render computational cost & queueing impact estimates."""
    from second_llm.cost_estimation import ScenarioCostReport

    cr: ScenarioCostReport = cost_report

    st.divider()
    st.subheader("Estimated Cost of Proposed Changes")

    # Summary metrics — always shown
    if cr.budget_limit is not None:
        cost_cols = st.columns(3)
        cost_cols[0].metric("Additional Monthly Cost", cr.formatted_total)
        cost_cols[1].metric("Budget Limit", f"{cr.budget_limit:,.0f} {cr.currency}/mo")
        cost_cols[2].metric("Within Budget", "No" if cr.exceeds_budget else "Yes")
    else:
        st.metric("Additional Monthly Cost", cr.formatted_total)

    # Cost breakdown
    if cr.cost_estimates:
        st.markdown("**Cost breakdown**")
        for ce in cr.cost_estimates:
            st.markdown(f"- **{ce.intervention}**: {ce.formatted_cost}")
            if ce.computation:
                st.caption(f"  {ce.computation}")
    else:
        st.caption(
            "No resource additions proposed — no additional labour cost. "
            "Changes are limited to schedules, durations, or routing."
        )

    # Queueing estimates
    if cr.queueing_estimates:
        st.markdown("**Queueing impact estimates (M/M/c)**")
        for qe in cr.queueing_estimates:
            delta = qe.proposed_servers - qe.baseline_servers
            st.markdown(
                f"- **{qe.target_element}**: +{delta} server(s) "
                f"({qe.baseline_servers} -> {qe.proposed_servers})"
            )
            st.caption(
                f"  Utilization: {qe.utilization:.0%} ({qe.utilization_source}) | "
                f"P(wait): {qe.baseline_wait_probability:.1%} -> "
                f"{qe.proposed_wait_probability:.1%} | "
                f"E[Wq] reduction: ~{qe.wait_reduction_pct:.0f}%"
            )

    if cr.exceeds_budget:
        st.error(
            f"Total cost ({cr.formatted_total}) exceeds budget "
            f"({cr.budget_limit:,.0f} {cr.currency}/month)"
        )

    if cr.notes:
        with st.expander("Cost estimation notes", expanded=False):
            for note in cr.notes:
                st.write(f"- {note}")


def _render_comparison_report(report, name_map: dict | None = None) -> None:
    """Render the KPI traceability & scenario comparison report."""
    from second_llm.comparison import ComparisonReport

    report: ComparisonReport = report

    st.divider()
    st.subheader("KPI Traceability & Comparison")
    if not report.kpi_traces:
        st.info("No KPI traceability data - first-LLM JSON may not have been parseable.")
        return

    if not report.kpi_traces:
        st.info("No KPI traceability data — first-LLM JSON may not have been parseable.")
        return

    # --- Coverage summary ---
    cov_col1, cov_col2, cov_col3 = st.columns(3)
    cov_col1.metric("KPI Coverage", f"{report.addressed_kpis}/{report.total_kpis}")
    cov_col2.metric("Modifications", str(report.total_modifications))
    cov_col3.metric(
        "Direction Alignment",
        f"{report.total_kpis - len(report.misaligned_kpis)}/{report.total_kpis}",
    )

    # --- Per-KPI traceability ---
    for trace in report.kpi_traces:
        direction_icon = {
            "minimize": "⬇️",
            "maximize": "⬆️",
            "maintain": "➡️",
        }.get(trace.target_direction, "❔")

        coverage_label = {
            "full": "✅ Addressed",
            "partial": "🟡 Partial",
            "unaddressed": "🚫 Not addressed",
        }.get(trace.coverage, "❔")

        alignment_label = ""
        if trace.direction_aligned is True:
            alignment_label = " · ✅ Aligned"
        elif trace.direction_aligned is False:
            alignment_label = " · ❌ Misaligned"

        header = (
            f"{direction_icon}  {trace.kpi_name} ({trace.category}) "
            f"— {coverage_label}{alignment_label}"
        )

        with st.expander(header, expanded=trace.coverage == "unaddressed" or trace.direction_aligned is False):
            col_target, col_impact = st.columns(2)
            with col_target:
                st.markdown(f"**Target:** {trace.target_direction}")
                st.markdown(f"**Category:** {trace.category}")
                st.markdown(f"**Scope:** {trace.process_scope}")
            with col_impact:
                if trace.expected_direction:
                    impact_icon = {
                        "decrease": "⬇️",
                        "increase": "⬆️",
                        "maintain": "➡️",
                    }.get(trace.expected_direction, "❔")
                    st.markdown(f"**Expected impact:** {impact_icon} {trace.expected_direction}")
                    if trace.estimated_magnitude:
                        st.markdown(f"**Magnitude:** {trace.estimated_magnitude}")
                else:
                    st.markdown("**Expected impact:** not specified")

            if trace.impact_reasoning:
                st.caption(trace.impact_reasoning)

            # Linked modifications
            if trace.modifications:
                st.markdown(f"**Linked modifications** ({len(trace.modifications)})")
                for delta in trace.modifications:
                    change_str = ""
                    if delta.change_pct is not None:
                        change_str = f" ({delta.change_pct:+.1f}%)"
                    st.markdown(
                        f"- **{delta.intervention}**: "
                        f"`{_fmt_value(delta.baseline_value)}` -> `{_fmt_value(delta.proposed_value)}`"
                        f"{change_str} [{delta.direction}]"
                    )
            else:
                st.caption("No modifications target this KPI directly.")

    # --- Parameter delta table ---
    if report.parameter_deltas:
        with st.expander("Parameter Delta Table", expanded=False):
            rows = []
            for d in report.parameter_deltas:
                change_str = f"{d.change_pct:+.1f}%" if d.change_pct is not None else "N/A"
                row = {
                    "#": d.modification_index,
                    "Intervention": d.intervention[:50],
                    "Element": _display_name(d.target_element, name_map or {}),
                    "Baseline": _fmt_value(d.baseline_value),
                    "Proposed": _fmt_value(d.proposed_value),
                    "Change": change_str,
                    "Direction": d.direction,
                    "Monthly Cost": d.monthly_cost_formatted or "—",
                    "KPI": d.kpi_reference,
                }
                rows.append(row)
            st.table(pd.DataFrame(rows).set_index("#"))



def _render_scenario_result() -> None:
    """Display the generated ScenarioProposal, or the draft payload."""
    from second_llm.scenario_generator import ScenarioGenerationResult

    gen_result: ScenarioGenerationResult | None = st.session_state.get("_second_llm_gen_result")

    # Show scenario result if available
    if gen_result is not None:
        st.subheader("Generated Scenario")

        if gen_result.error and not gen_result.proposal:
            st.error(f"Generation failed: {gen_result.error}")
            if gen_result.generation_notes:
                with st.expander("Generation log", expanded=False):
                    for note in gen_result.generation_notes:
                        st.write(f"- {note}")
            if gen_result.raw_llm_output:
                with st.expander("Raw LLM output", expanded=False):
                    st.code(gen_result.raw_llm_output[:5000], language="json")
            return

        proposal = gen_result.proposal
        if proposal is None:
            return

        # Build name map for resolving node IDs to human-readable labels
        ws = get_workspace()
        bpmn_xml = ""
        if ws.raw_simod_input.simod_result and ws.raw_simod_input.simod_result.bpmn_content:
            bpmn_xml = ws.raw_simod_input.simod_result.bpmn_content
        _nm = _build_bpmn_name_map(bpmn_xml)

        warning_count = len(proposal.warnings or [])
        if gen_result.validation:
            warning_count += len(gen_result.validation.warnings) + len(gen_result.validation.errors)

        summary_cols = st.columns(3)
        summary_cols[0].markdown(
            _status_pill("Modifications", str(len(proposal.modifications)), "ok"),
            unsafe_allow_html=True,
        )
        summary_cols[1].markdown(
            _status_pill("KPI impacts", str(len(proposal.expected_kpi_impacts)), "ok"),
            unsafe_allow_html=True,
        )
        summary_cols[2].markdown(
            _status_pill(
                "Warnings",
                str(warning_count),
                "warn" if warning_count else "idle",
            ),
            unsafe_allow_html=True,
        )
        st.write("")

        # --- Reasoning ---
        st.markdown(f"**Strategy:** {proposal.reasoning}")

        # --- Modifications ---
        st.markdown(f"**Proposed modifications** ({len(proposal.modifications)})")
        for i, mod in enumerate(proposal.modifications, 1):
            _elem_label = _display_name(mod.target_element, _nm)
            intervention = getattr(mod, "intervention", "") or f"{_elem_label} — {mod.parameter_type}"
            if not getattr(mod, "intervention", ""):
                intervention = f"{_elem_label} - {mod.parameter_type}"
            changed_parameters = getattr(mod, "changed_parameters", "") or mod.parameter_type.replace("_", " ")
            intervention = intervention.replace("\u2014", "-").replace("\u2013", "-")
            mechanism_rationale = getattr(mod, "mechanism_rationale", "") or mod.rationale
            feasibility_assumptions = getattr(mod, "feasibility_assumptions", "") or mod.context_condition or "Not specified"

            # Build evidence source with resolved literature references
            import re as _re
            evidence_source = getattr(mod, "evidence_source", "") or ""
            evidence_source = _re.sub(r"\s*\(paper_id\s*\d+\)", "", evidence_source).strip()
            literature_citations = _resolve_literature_ids(mod.literature_support) if mod.literature_support else []
            if literature_citations and not evidence_source:
                evidence_source = "Knowledge base: " + "; ".join(literature_citations)
            elif literature_citations:
                evidence_source += " | KB: " + "; ".join(literature_citations)
            if not evidence_source:
                evidence_source = "Not specified"

            dir_val = mod.direction.value if hasattr(mod.direction, "value") else str(mod.direction)
            _DIR_ICON = {
                "increase": "▲", "decrease": "▼", "redistribute": "↔",
                "add_new": "✚", "remove": "✕", "change_distribution": "≈",
                "differentiate": "↕",
            }
            _DIR_COLOR = {
                "increase": "#15803d", "decrease": "#dc2626",
                "redistribute": "#2563eb", "add_new": "#7c3aed",
                "remove": "#b91c1c", "change_distribution": "#0369a1",
                "differentiate": "#b45309",
            }
            dir_icon = _DIR_ICON.get(dir_val, "•")
            dir_color = _DIR_COLOR.get(dir_val, "#64748b")

            with st.expander(
                f"{i}. {dir_icon} {intervention}",
                expanded=i <= 3,
            ):
                st.markdown(
                    f"<span style='display:inline-block;background:{dir_color};color:white;"
                    f"padding:0.18rem 0.65rem;border-radius:999px;font-size:0.72rem;"
                    f"font-weight:700;letter-spacing:0.04em;text-transform:uppercase;"
                    f"margin-bottom:0.55rem;'>"
                    f"{html.escape(dir_icon)}&nbsp;{html.escape(dir_val.replace('_', ' '))}"
                    f"</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"**Intervention:** {intervention}")
                st.markdown(f"**Changed parameter(s):** {changed_parameters}")
                st.markdown(f"**Baseline value:** {_fmt_value(mod.baseline_value)}")
                st.markdown(f"**Proposed value:** {_fmt_value(mod.proposed_value)}")
                st.markdown(f"**Mechanism/rationale:** {mechanism_rationale}")
                st.markdown(f"**Evidence source:** {evidence_source}")
                st.markdown(f"**Feasibility assumptions:** {feasibility_assumptions}")
                st.caption(
                    f"KPI target: {mod.kpi_reference} | Element: {_elem_label} | "
                    f"Direction: {mod.direction.value}"
                )
                if mod.context_condition:
                    st.caption(f"Context condition: {mod.context_condition}")

        # --- Context differentiations ---
        if proposal.context_differentiations:
            st.markdown(f"**Context differentiations** ({len(proposal.context_differentiations)})")
            for cd in proposal.context_differentiations:
                st.markdown(
                    f"- **{cd.context_factor}** ({cd.factor_scope}): "
                    f"segments {cd.segments} — {cd.strategy_applied}"
                )

        # --- Warnings (schema + post-validation) ---
        if proposal.warnings:
            for w in proposal.warnings:
                st.warning(w)

        if gen_result.validation:
            for issue in gen_result.validation.warnings:
                st.warning(f"[{issue.category}] {issue.message}")
            for issue in gen_result.validation.errors:
                st.error(f"[{issue.category}] {issue.message}")

        # --- Computational Cost & Impact Estimates ---
        if gen_result.cost_report is not None:
            _render_cost_report(gen_result.cost_report)

        # --- KPI Comparison: simulation-based if available, else static traceability ---
        iter_result = st.session_state.get("_iter_optim_result")
        if (
            iter_result is not None
            and iter_result.best is not None
            and iter_result.best.eval_result is not None
            and iter_result.best.eval_result.ok
        ):
            from ui.kpi_display import render_summary_card, render_comparison_table, render_kpi_chart

            st.divider()
            st.subheader("Simulation-Based KPI Comparison")
            st.caption("Actual results from Prosimos simulation (baseline vs proposed).")
            best_eval = iter_result.best.eval_result
            if best_eval.summary:
                render_summary_card(best_eval.summary)
            render_comparison_table(best_eval.kpi_comparisons)
            render_kpi_chart(best_eval.kpi_comparisons)
        elif gen_result.comparison:
            _render_comparison_report(gen_result.comparison, _nm)

        st.divider()

        # --- What the LLM actually produced (patch vs full scenario) ---
        mode = getattr(gen_result, "decoding_mode", "")
        raw = gen_result.raw_llm_output
        if raw:
            with st.expander(
                "Generated scenario patch — proposed changes (delta only)",
                expanded=False,
            ):
                st.caption(
                    "This is the compact JSON the LLM returned in patch mode. "
                    "It contains only the proposed changes — no baseline fields. "
                    "Compare its size to what legacy mode would have returned "
                    "(the full merged scenario below)."
                )
                st.code(raw[:8000], language="json")

        # --- Generation log (always visible, not just on error) ---
        if gen_result.generation_notes:
            with st.expander("Generation log", expanded=False):
                for note in gen_result.generation_notes:
                    st.write(f"- {note}")

        # --- Raw JSON preview + download ---
        scenario_json = get_workspace().last_scenario_json
        if scenario_json:
            with st.expander("Full ScenarioProposal JSON", expanded=False):
                try:
                    st.json(json.loads(scenario_json))
                except json.JSONDecodeError:
                    st.code(scenario_json[:5000], language="json")

            dl_col1, dl_col2 = st.columns(2)
            with dl_col1:
                st.download_button(
                    "Download ScenarioProposal",
                    data=scenario_json,
                    file_name="scenario_proposal.json",
                    mime="application/json",
                    width="stretch",
                )
            with dl_col2:
                # Extract just the SimuBridge scenario portion
                try:
                    full = json.loads(scenario_json)
                    simubridge_json = json.dumps(full.get("scenario", {}), indent=2)
                    st.download_button(
                        "Download SimuBridge Scenario",
                        data=simubridge_json,
                        file_name="simubridge_scenario.json",
                        mime="application/json",
                        width="stretch",
                    )
                except (json.JSONDecodeError, TypeError):
                    pass

        # --- Send to Evaluation ---
        st.divider()
        if st.button(
            "Send to Evaluation",
            type="primary",
            width="stretch",
            help="Navigate to the Evaluation page. The generated scenario will be auto-loaded as the proposed scenario.",
        ):
            st.session_state["_app_page_nav"] = "Scenario Evaluation"
            st.rerun()

        return

    # Fallback: show draft payload if available
    draft = get_last_draft()
    if draft is not None:
        st.subheader("Draft Payload")
        if draft.warnings:
            for warning in draft.warnings:
                st.warning(warning)
        if draft.notes:
            with st.expander("Build notes", expanded=False):
                for note in draft.notes:
                    st.write(f"- {note}")
        with st.expander("Payload preview", expanded=False):
            st.json(draft.model_dump(mode="json"))
        draft_json_str = json.dumps(draft.model_dump(mode="json"), indent=2, default=str)
        st.download_button(
            "Download draft JSON",
            data=draft_json_str,
            file_name="second_llm_request_draft.json",
            mime="application/json",
            width="stretch",
        )


# -----------------------------------------------------------------------
# Display helpers
# -----------------------------------------------------------------------

def _fmt_value(value: str | None) -> str:
    """Round a numeric string to at most 2 decimal places for display."""
    if not value:
        return value or ""
    try:
        return f"{float(value):.2f}".rstrip("0").rstrip(".")
    except (ValueError, TypeError):
        return str(value)


# -----------------------------------------------------------------------
# Main panel entry point
# -----------------------------------------------------------------------

def render_second_llm_panel() -> None:
    """Render the full Scenario Studio panel."""

    st.title("Scenario Studio")
    st.markdown(
        "<p class='gtk-hero-caption'>Load the validated KPI JSON and SIMOD baseline,"
        " then continue in a chat workspace to capture operational context"
        " and generate the final simulation scenario.</p>",
        unsafe_allow_html=True,
    )

    provider = _render_provider_sidebar()

    orch = SecondLLMWorkspaceOrchestrator(provider=provider)

    # Consume the "Go to Scenario Studio" handoff from the Goal -> KPI page:
    # auto-load the current KPI session output into this panel's workspace so
    # the user does not have to click "Load Output From Current KPI Session"
    # manually. The flag is set in app.py and consumed exactly once.
    if st.session_state.pop("_scenario_studio_autoload_kpis", False):
        current_kpi_session_json = _get_current_kpi_session_json()
        if current_kpi_session_json:
            result = orch.parse_first_llm_json(current_kpi_session_json)
            if result.is_valid:
                st.success("Loaded KPIs from the current Goal → KPI session.")
                _open_chat_when_ready()
            elif result.parse_error:
                st.error(f"Could not import the current KPI session (JSON parse error): {result.parse_error}")
            elif result.validation_error:
                st.error(
                    "Could not import the current KPI session: the output does not "
                    f"match the stage-1 KPI result schema.\n\n{result.validation_error}"
                )
        else:
            st.warning(
                "No current Goal → KPI session was found. Load a KPI JSON below to continue."
            )

    _render_workspace_status()

    st.markdown("<div class='gtk-tab-bar-anchor'></div>", unsafe_allow_html=True)

    active_view = _get_active_view()
    switch_col_1, switch_col_2, switch_col_3 = st.columns([1, 1, 1], gap="medium")
    with switch_col_1:
        if st.button(
            "Inputs",
            width="stretch",
            type=("primary" if active_view == _VIEW_INPUTS else "secondary"),
        ):
            st.session_state["_second_llm_confirm_reset"] = False
            _set_active_view(_VIEW_INPUTS)
    with switch_col_2:
        if st.button(
            "Chat",
            width="stretch",
            type=("primary" if active_view == _VIEW_CHAT else "secondary"),
            disabled=not _can_open_chat(),
        ):
            st.session_state["_second_llm_confirm_reset"] = False
            _set_active_view(_VIEW_CHAT)
    with switch_col_3:
        if st.button("Reset Workspace", type="secondary", width="stretch"):
            st.session_state["_second_llm_confirm_reset"] = True
    st.divider()

    if st.session_state.get("_second_llm_confirm_reset"):
        st.warning("Reset the entire second workspace? This clears the loaded JSON, SIMOD input, chat, and generated scenario.")
        confirm_col, cancel_col = st.columns(2)
        with confirm_col:
            if st.button("Confirm Reset Workspace", type="primary", width="stretch"):
                st.session_state["_second_llm_confirm_reset"] = False
                orch.reset_second_llm_workspace()
                # Clear UI-ephemeral state that lives outside the
                # workspace object (generated scenario, chat draft,
                # file-uploader widgets) so the chat view comes back
                # empty instead of showing the previous run's output.
                for key in list(st.session_state.keys()):
                    if key == _VIEW_KEY:
                        continue
                    if (
                        key.startswith("_second_llm_")
                        or key.startswith("second_llm__first_llm")
                        or key.startswith("second_llm__simod")
                        or key == "second_llm__chat_input"
                        or key.startswith("_iter_optim_")
                        or key.startswith("_eval_")
                    ):
                        st.session_state.pop(key, None)
                _set_active_view(_VIEW_INPUTS)
                st.rerun()
        with cancel_col:
            if st.button("Cancel Reset", width="stretch"):
                st.session_state["_second_llm_confirm_reset"] = False
                st.rerun()

    if _get_active_view() == _VIEW_CHAT:
        _render_chat_view(orch)
    else:
        _render_inputs_view(orch)

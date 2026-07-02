"""Streamlit session-state helpers for the Scenario Studio.

All workspace data is stored under a single session-state key
(``_second_llm_ws``) as a serialisable :class:`SecondLLMWorkspaceState`
so that independent Streamlit reruns do not lose user progress.
"""

from __future__ import annotations

import streamlit as st

from second_llm.models import (
    ClarificationSession,
    FirstLLMInput,
    RawSimodInput,
    SecondLLMRequestDraft,
    SecondLLMWorkspaceState,
)

_STATE_KEY = "_second_llm_ws"


def _normalize_workspace_state(raw_workspace: object) -> SecondLLMWorkspaceState:
    """Upgrade legacy session-state payloads to the current schema."""
    if isinstance(raw_workspace, SecondLLMWorkspaceState):
        workspace = raw_workspace
    elif hasattr(raw_workspace, "model_dump"):
        workspace = SecondLLMWorkspaceState.model_validate(raw_workspace.model_dump())
    elif isinstance(raw_workspace, dict):
        workspace = SecondLLMWorkspaceState.model_validate(raw_workspace)
    else:
        workspace = SecondLLMWorkspaceState()

    session = workspace.clarification_session
    if not hasattr(session, "last_context_signature") or not hasattr(session, "prompt_version"):
        workspace.clarification_session = ClarificationSession(
            messages=list(getattr(session, "messages", [])),
            last_context_signature=str(getattr(session, "last_context_signature", "") or ""),
            prompt_version=str(getattr(session, "prompt_version", "") or ""),
        )
    return workspace


def _ensure_workspace() -> SecondLLMWorkspaceState:
    """Return the current workspace, creating a default one if needed."""
    if _STATE_KEY not in st.session_state:
        st.session_state[_STATE_KEY] = SecondLLMWorkspaceState()
    st.session_state[_STATE_KEY] = _normalize_workspace_state(st.session_state[_STATE_KEY])
    return st.session_state[_STATE_KEY]


# -- Getters ----------------------------------------------------------------

def get_workspace() -> SecondLLMWorkspaceState:
    """Public accessor for the workspace state."""
    return _ensure_workspace()


def get_first_llm_input() -> FirstLLMInput:
    return _ensure_workspace().first_llm_input


def get_raw_simod_input() -> RawSimodInput:
    return _ensure_workspace().raw_simod_input


def get_clarification_session() -> ClarificationSession:
    return _ensure_workspace().clarification_session


def get_last_draft() -> SecondLLMRequestDraft | None:
    return _ensure_workspace().last_draft


# -- Setters ----------------------------------------------------------------

def set_first_llm_input(first_llm: FirstLLMInput) -> None:
    _ensure_workspace().first_llm_input = first_llm


def set_raw_simod_input(simod: RawSimodInput) -> None:
    _ensure_workspace().raw_simod_input = simod


def set_last_draft(draft: SecondLLMRequestDraft) -> None:
    _ensure_workspace().last_draft = draft


def set_last_scenario(scenario_json: str) -> None:
    """Store the raw JSON of the last generated ScenarioProposal."""
    _ensure_workspace().last_scenario_json = scenario_json


def get_last_scenario_json() -> str:
    """Return the stored scenario JSON, or empty string."""
    return _ensure_workspace().last_scenario_json


# -- Reset ------------------------------------------------------------------

def reset_workspace() -> None:
    """Clear the entire workspace and start fresh."""
    st.session_state[_STATE_KEY] = SecondLLMWorkspaceState()

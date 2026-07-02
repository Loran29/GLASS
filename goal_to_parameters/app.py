from __future__ import annotations

import datetime
import html
import json
import os
import re
from pathlib import Path
from typing import Any

import streamlit as st
import yaml
from dotenv import load_dotenv

from llm import AnthropicProvider, HuggingFaceProvider, LLMProvider, OllamaProvider, OpenAIProvider, OpenRouterProvider
from models import EvidenceBasis, KPIGenerationResult, SMARTKpi
from prompts import build_refinement_prompt, build_smart_kpi_prompt
from ui.second_llm_panel import render_second_llm_panel
from utils.semantic_validation import (
    _conditions_semantically_match,
    _context_relationship_lookup,
    _normalize_context_factor_name,
    _segment_supported_by_relationship,
    _supported_relationship_index,
)
from utils import (
    KPIParsingError,
    analyze_text_log_consistency,
    build_context_evidence_prompt,
    build_log_evidence_prompt,
    extract_json_object,
    parse_kpi_generation_payload,
    profile_event_log,
    strip_code_fences,
    validate_kpi_generation_semantics,
)

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
DOTENV_PATH = BASE_DIR / ".env"

PROVIDER_LABELS = {"ollama": "Ollama", "huggingface": "HuggingFace", "openai": "OpenAI", "anthropic": "Anthropic", "openrouter": "OpenRouter"}
CATEGORY_COLORS = {
    "time": "#0f766e",
    "cost": "#b45309",
    "quality": "#1d4ed8",
    "utilization": "#0369a1",
    "throughput": "#be123c",
    "compliance": "#7c3aed",
    "flexibility": "#4d7c0f",
    "occurrence": "#be123c",
}
DECISION_LABELS = {None: "Pending review", "accepted": "Accepted", "rejected": "Rejected"}
DECISION_OPTIONS = ("Pending", "Accept", "Reject")
DECISION_COLORS = {None: "#64748b", "accepted": "#15803d", "rejected": "#b91c1c"}
EXAMPLES = {
    "BPIC 2017": {
        "process_description": (
            "Our loan origination process begins when a customer submits an application "
            "online or through a branch, after which our staff validate the application, "
            "request any missing documentation, and prepare one or more offers for the "
            "customer to consider. A recurring frustration is that applications stall while "
            "we wait on incomplete customer paperwork, and our handling teams often chase "
            "the same leads repeatedly before an offer is either accepted or withdrawn. "
            "Under Dutch and EU consumer-credit rules we are expected to give applicants a "
            "timely and fair decision, and our compliance team has flagged that affordability "
            "and identity checks must be completed consistently on every file regardless of "
            "how busy we are. We are under commercial pressure to convert more qualified "
            "applicants into accepted offers before they go to a competitor, but our "
            "validation specialists are a limited and expensive pool, and weekend and evening "
            "coverage is thin. Management wants the process to feel faster to customers "
            "without cutting the checks that keep us within regulatory limits."
        ),
        "simulation_goal": (
            "Shorten the time customers wait between applying and receiving a usable offer "
            "while increasing the share of applications that reach an accepted outcome, "
            "without adding more than one additional validation specialist and without "
            "reducing the completeness of mandatory compliance checks on any file."
        ),
        "event_log": str(BASE_DIR.parent / "evaluation" / "logs" / "csv" / "bpic2017.csv"),
    },
    "BPIC 2012": {
        "process_description": (
            "When a customer applies for a loan, the application moves through an initial "
            "submission and pre-acceptance screening, followed by completion of the "
            "application file and several rounds of our staff calling the customer back "
            "about outstanding offers before the application is finally approved, declined, "
            "or cancelled. The biggest pain point our team raises is the volume of "
            "call-backs needed to chase customers about offers, which ties up staff and "
            "drags out cases that frequently end up cancelled anyway. We are required to "
            "apply our acceptance criteria uniformly and to document the basis for every "
            "approval and decline, and supervisors have noted that rushed periods correlate "
            "with inconsistent handling. The contact-centre staff who handle the call-backs "
            "are shared with other products, so we cannot simply assume unlimited capacity, "
            "and overtime is tightly budgeted. Leadership would like cases to reach a clear "
            "outcome sooner and with less wasted effort on applications that are never going "
            "to complete."
        ),
        "simulation_goal": (
            "Reduce the overall time and the number of customer call-back rounds needed to "
            "bring an application to a final decision while keeping staff workload within "
            "current contact-centre capacity, and do so without lowering the consistency of "
            "how acceptance and decline decisions are applied."
        ),
        "event_log": str(BASE_DIR.parent / "evaluation" / "logs" / "csv" / "bpic2012.csv"),
    },
    "Sepsis": {
        "process_description": (
            "Patients arriving at the emergency department with suspected sepsis undergo "
            "triage and registration, followed by a series of diagnostic blood tests "
            "including Leucocytes, CRP, and Lactic Acid measurements. Based on test results, "
            "the clinical team administers intravenous antibiotics and fluids. Patients may "
            "be admitted to a normal-care or intensive-care ward, returned to the emergency "
            "department if their condition changes, or discharged through one of several "
            "release pathways. The process is time-critical: delays at registration, in "
            "laboratory turnaround, or in clinical decision-making can significantly worsen "
            "patient outcomes. Emergency staffing levels are constrained and clinicians are "
            "subject to mandatory rest requirements between shifts."
        ),
        "simulation_goal": (
            "Decrease the time from patient arrival to completed diagnostics and start of "
            "antibiotic treatment, and increase the proportion of suspected-sepsis patients "
            "treated within the recommended clinical window, while staying within current "
            "emergency-staffing levels and respecting mandatory clinician rest requirements."
        ),
        "event_log": str(BASE_DIR.parent / "evaluation" / "logs" / "csv" / "sepsis.csv"),
    },
    "Context-Aware Insurance Claim": {
        "process_description": "The insurance claim process begins when a customer submits a claim. A claims agent checks whether all required documents are included. A claim assessor evaluates the case. A supervisor reviews the recommendation and makes the final decision. The process ends when the customer is notified of the decision.",
        "simulation_goal": "Reduce claim decision cycle time while maintaining responsive service across claim segments and priority levels",
        "event_log": "context_aware_insurance_claim.csv",
    },
    "IT Incident Management": {
        "process_description": "The IT incident management process starts when a user reports an incident through the service desk portal. The service desk classifies the incident by severity and type. An L1 support agent performs initial diagnosis and assigns the ticket to the appropriate team. The agent investigates and attempts a fix. If L1 cannot resolve it, the incident is escalated to L2 engineering, and potentially to L3 specialists for complex issues. High-impact changes require formal change approval before implementation. After applying a fix, the resolution is tested and verified before closing the incident. Failed fixes trigger a rework cycle.",
        "simulation_goal": "Reduce mean time to resolution across severity levels while minimizing unnecessary escalations and maintaining SLA compliance for platinum-tier incidents",
        "event_log": "it_incident_management.csv",
    },
    "Purchasing": {
        "process_description": "The purchasing process begins when a buyer submits a Purchase Requisition. A procurement analyst reviews and analyzes the requisition to verify the need and budget. The buyer then creates a Request for Quotation and sends it to one or more suppliers; the RFQ may be amended if supplier feedback requires clarification. Received quotations are compiled into a Quotation Comparison Map, which the procurement team analyzes to choose the best option and settle conditions with the selected supplier. A Purchase Order is then created, approved, and released. The supplier confirms the order and delivers the goods or services. After delivery, the supplier sends an invoice, which the accounts payable team releases and authorizes for payment, ending the process with invoice settlement. Disputes arising during negotiation or delivery are handled through a separate dispute-resolution sub-flow.",
        "simulation_goal": "Reduce end-to-end procurement cycle time from purchase requisition to invoice payment while improving buyer and analyst utilization and minimizing delays in the quotation analysis and purchase order approval stages",
        "event_log": "PurchasingExample.csv",
    },
}
BPM_PROCESS_HINTS = ("process", "workflow", "activity", "activities", "task", "tasks", "step", "steps", "case", "cases", "request", "order", "application", "approval", "review", "resource", "team", "department", "patient", "invoice", "claim", "delivery", "shipment", "discharge")
BPM_GOAL_HINTS = ("reduce", "improve", "increase", "decrease", "maintain", "minimize", "maximize", "cycle time", "lead time", "waiting time", "throughput", "utilization", "quality", "cost", "compliance", "flexibility", "delay", "processing time", "service level", "error rate")
BPM_FEEDBACK_HINTS = ("kpi", "metric", "measure", "measurement", "formula", "target", "category", "smart", "specific", "measurable", "achievable", "relevant", "time-bound", "process", "activity", "resource", "cycle time", "waiting time", "throughput", "utilization", "quality", "cost", "compliance", "replace", "refine", "bottleneck", "time frame")
OUT_OF_SCOPE_PATTERNS = (
    re.compile(r"\bwho is\b|\bwho's\b", re.IGNORECASE),
    re.compile(r"\bpresident\b|\bprime minister\b|\bdonald trump\b", re.IGNORECASE),
    re.compile(r"\bcapital of\b|\bgeography\b", re.IGNORECASE),
    re.compile(r"\bweather\b|\btemperature outside\b", re.IGNORECASE),
    re.compile(r"\bsports?\b|\bscore\b|\bmatch\b", re.IGNORECASE),
    re.compile(r"\bcelebrity\b|\bmovie\b|\bsong\b|\blyrics\b", re.IGNORECASE),
    re.compile(r"\brecipe\b|\bcook\b|\brestaurant\b", re.IGNORECASE),
)


def load_environment() -> None:
    load_dotenv(DOTENV_PATH)


@st.cache_data(show_spinner=False)
def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        return yaml.safe_load(config_file) or {}


def initialize_session_state() -> None:
    defaults: dict[str, Any] = {
        "current_kpis": None,
        "kpi_decisions": {},
        "iteration_history": [],
        "process_description": "",
        "simulation_goal": "",
        "feedback_text": "",
        "last_raw_output": "",
        "context_evidence": None,
        "log_evidence": None,
        "log_profile": None,
        "log_source_name": "",
        "log_source_kind": "",
        "semantic_validation": None,
        "kpi_grounding_assessments": {},
        "event_log_uploader_nonce": 0,
        "_provider_status": (False, "Not configured"),
        "_provider_status_key": None,
        "_clear_feedback_text": False,
        "_reset_decision_widgets": False,
        "_consistency_review_cache": {},
        "_use_llm_consistency_review": True,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


API_KEY_PATTERNS: dict[str, re.Pattern[str]] = {
    "openai": re.compile(r"^sk-[A-Za-z0-9_-]{20,}$"),
    "anthropic": re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,}$"),
    "openrouter": re.compile(r"^sk-or-[A-Za-z0-9_-]{20,}$"),
    "huggingface": re.compile(r"^hf_[A-Za-z0-9]{10,}$"),
}


def sanitize_api_key(key: str) -> str:
    return key.strip()


def validate_api_key_format(provider_name: str, key: str) -> str | None:
    pattern = API_KEY_PATTERNS.get(provider_name)
    if pattern is None or not key:
        return None
    if not pattern.match(key):
        return f"The {PROVIDER_LABELS.get(provider_name, provider_name)} API key format looks incorrect. Please double-check it."
    return None


def sanitize_user_input(text: str) -> str:
    """Remove triple-quote sequences that could break prompt delimiters."""
    return text.replace('"""', "''\"")


def reset_session() -> None:
    defaults = {
        "current_kpis": None,
        "kpi_decisions": {},
        "iteration_history": [],
        "process_description": "",
        "simulation_goal": "",
        "feedback_text": "",
        "last_raw_output": "",
        "context_evidence": None,
        "log_evidence": None,
        "log_profile": None,
        "log_source_name": "",
        "log_source_kind": "",
        "semantic_validation": None,
        "kpi_grounding_assessments": {},
        "_consistency_review_cache": {},
        "_use_llm_consistency_review": True,
    }
    for key, value in defaults.items():
        st.session_state[key] = value
    st.session_state.event_log_uploader_nonce = st.session_state.get("event_log_uploader_nonce", 0) + 1


def apply_pending_review_state_resets() -> None:
    if st.session_state.get("_clear_feedback_text"):
        st.session_state.feedback_text = ""
        st.session_state._clear_feedback_text = False
    if st.session_state.get("_reset_decision_widgets") and st.session_state.current_kpis is not None:
        for kpi in st.session_state.current_kpis.kpis:
            st.session_state[get_decision_widget_key(kpi.name)] = decision_to_option(st.session_state.kpi_decisions.get(kpi.name))
        st.session_state._reset_decision_widgets = False


def apply_custom_styles() -> None:
    st.markdown(
        """
        <style>
        /* ---------- Layout & typography ---------- */
        .block-container { padding-top: 1.8rem; padding-bottom: 3rem; max-width: 1280px; }

        h1, h2, h3, h4 { letter-spacing: -0.01em; }
        h1 { font-weight: 700 !important; }
        h2 { font-weight: 650 !important; margin-top: 1.2rem !important; }
        h3 { font-weight: 600 !important; }

        /* Subtle accent bar under the main title */
        .block-container > div:first-child h1:first-of-type {
            position: relative; padding-bottom: 0.35rem;
        }
        .block-container > div:first-child h1:first-of-type::after {
            content: ""; position: absolute; left: 0; bottom: 0;
            width: 54px; height: 3px; border-radius: 2px;
            background: linear-gradient(90deg, #2563eb 0%, #7c3aed 100%);
        }

        /* Captions a touch softer */
        .stCaption, [data-testid="stCaptionContainer"] { color: #64748b !important; }

        /* ---------- Existing badges (kept) ---------- */
        .goal-badge, .decision-badge {
            display: inline-block; padding: 0.25rem 0.7rem; border-radius: 999px;
            color: white; font-size: 0.78rem; font-weight: 700;
        }
        .goal-badge { text-transform: uppercase; letter-spacing: 0.04em; }
        .decision-badge { margin-left: 0.35rem; }
        .connection-indicator {
            font-size: 0.9rem; font-weight: 600; margin-top: 0.5rem;
            padding: 0.35rem 0.6rem; border-radius: 6px;
            background: rgba(148, 163, 184, 0.10);
        }

        /* ---------- Metric cards ---------- */
        [data-testid="stMetric"] {
            background: rgba(148, 163, 184, 0.06);
            border: 1px solid rgba(148, 163, 184, 0.18);
            border-radius: 10px;
            padding: 0.75rem 1rem;
        }
        [data-testid="stMetricLabel"] {
            font-size: 0.78rem !important; text-transform: uppercase;
            letter-spacing: 0.05em; color: #64748b !important;
        }
        [data-testid="stMetricValue"] { font-weight: 700 !important; }

        /* ---------- Buttons ---------- */
        .stButton > button, .stDownloadButton > button {
            border-radius: 8px; font-weight: 600;
            transition: transform 0.04s ease, box-shadow 0.15s ease;
        }
        .stButton > button:hover:not(:disabled),
        .stDownloadButton > button:hover:not(:disabled) {
            box-shadow: 0 2px 8px rgba(37, 99, 235, 0.15);
        }
        .stButton > button:active:not(:disabled) { transform: translateY(1px); }

        /* ---------- Inputs ---------- */
        .stTextArea textarea, .stTextInput input {
            border-radius: 8px !important;
        }

        /* ---------- Dividers ---------- */
        hr { border-color: rgba(148, 163, 184, 0.25) !important; }

        /* ---------- Pipeline stepper ---------- */
        .gtk-stepper {
            display: flex; align-items: center; justify-content: space-between;
            gap: 0.25rem; margin: 0.6rem 0 1.25rem 0; padding: 0.85rem 1rem;
            background: rgba(148, 163, 184, 0.06);
            border: 1px solid rgba(148, 163, 184, 0.18);
            border-radius: 12px;
        }
        .gtk-step {
            display: flex; align-items: center; gap: 0.55rem;
            flex: 0 0 auto; min-width: 0;
        }
        .gtk-step-bubble {
            width: 28px; height: 28px; flex: 0 0 28px; border-radius: 50%;
            display: inline-flex; align-items: center; justify-content: center;
            font-weight: 700; font-size: 0.85rem;
            background: rgba(148, 163, 184, 0.18); color: #64748b;
            border: 2px solid transparent;
        }
        .gtk-step-label {
            font-weight: 600; font-size: 0.88rem; color: #64748b;
            white-space: nowrap;
        }
        .gtk-step.is-active .gtk-step-bubble {
            background: #2563eb; color: white;
            border-color: rgba(37, 99, 235, 0.25);
            box-shadow: 0 0 0 4px rgba(37, 99, 235, 0.12);
        }
        .gtk-step.is-active .gtk-step-label { color: #1e293b; }
        .gtk-step.is-done .gtk-step-bubble { background: #15803d; color: white; }
        .gtk-step.is-done .gtk-step-label { color: #15803d; }
        .gtk-step-line {
            flex: 1 1 auto; height: 2px; min-width: 16px;
            background: rgba(148, 163, 184, 0.35); border-radius: 1px;
        }
        .gtk-step-line.is-done { background: #15803d; }

        /* ---------- KPI card badge row (inside expanders) ---------- */
        .gtk-badge-row {
            display: flex; gap: 0.4rem; flex-wrap: wrap;
            margin: 0 0 0.5rem 0;
        }
        .gtk-chip {
            display: inline-flex; align-items: center;
            padding: 0.18rem 0.6rem; border-radius: 999px;
            font-size: 0.72rem; font-weight: 700;
            letter-spacing: 0.03em; text-transform: uppercase;
            color: white;
        }
        .gtk-chip.is-outline {
            background: transparent !important;
            border: 1px solid currentColor;
            color: #64748b;
        }

        /* ---------- Enhanced sidebar ---------- */
        [data-testid="stSidebar"] {
            background: #f8fafc !important;
        }
        [data-testid="stSidebar"] .stSubheader,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3 {
            font-size: 0.78rem !important; text-transform: uppercase;
            letter-spacing: 0.08em; color: #94a3b8 !important;
            font-weight: 700 !important; margin-bottom: 0.35rem !important;
        }

        /* ---------- Enhanced expanders ---------- */
        [data-testid="stExpander"] {
            border: 1px solid rgba(148, 163, 184, 0.22) !important;
            border-radius: 10px !important; overflow: hidden !important;
            margin-bottom: 0.5rem; transition: border-color 0.15s ease;
        }
        [data-testid="stExpander"]:hover {
            border-color: rgba(148, 163, 184, 0.38) !important;
        }
        [data-testid="stExpander"] > details > summary {
            padding: 0.65rem 1rem !important;
            background: rgba(248, 250, 252, 0.85) !important;
            font-weight: 600 !important;
        }
        [data-testid="stExpander"] > details[open] > summary {
            border-bottom: 1px solid rgba(148, 163, 184, 0.18) !important;
        }

        /* ---------- Bordered containers ---------- */
        [data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 12px !important;
            border-color: rgba(148, 163, 184, 0.22) !important;
            transition: border-color 0.15s ease;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:hover {
            border-color: rgba(148, 163, 184, 0.38) !important;
        }

        /* ---------- Alert banners ---------- */
        [data-testid="stAlert"] { border-radius: 10px !important; }

        /* ---------- File uploader ---------- */
        [data-testid="stFileUploaderDropzone"] {
            border-radius: 10px !important;
            border: 1.5px dashed rgba(148, 163, 184, 0.4) !important;
            transition: border-color 0.2s ease, background 0.2s ease !important;
        }
        [data-testid="stFileUploaderDropzone"]:hover {
            border-color: #2563eb !important;
            background: rgba(37, 99, 235, 0.03) !important;
        }

        /* ---------- Form fields ---------- */
        [data-baseweb="select"] > div { border-radius: 8px !important; }

        /* ---------- Chat ---------- */
        [data-testid="stChatMessage"] {
            border-radius: 12px !important;
            padding: 0.65rem 1rem !important;
            margin-bottom: 0.5rem !important;
        }
        [data-testid="stChatInputContainer"] {
            border-radius: 14px !important;
            border: 1.5px solid rgba(148, 163, 184, 0.28) !important;
            transition: border-color 0.15s ease, box-shadow 0.15s ease !important;
        }
        [data-testid="stChatInputContainer"]:focus-within {
            border-color: #2563eb !important;
            box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1) !important;
        }

        /* ---------- Download buttons ---------- */
        .stDownloadButton > button {
            background: rgba(148, 163, 184, 0.07) !important;
            border: 1px solid rgba(148, 163, 184, 0.28) !important;
            font-weight: 600 !important;
        }
        .stDownloadButton > button:hover:not(:disabled) {
            background: rgba(37, 99, 235, 0.07) !important;
            border-color: #2563eb !important;
            color: #2563eb !important;
        }

        /* ---------- Status widget ---------- */
        [data-testid="stStatusWidget"] { border-radius: 10px !important; }

        /* ---------- Section header component ---------- */
        .gtk-section-header {
            display: flex; align-items: flex-start; gap: 0.75rem;
            margin: 1.75rem 0 0.75rem 0; padding-bottom: 0.65rem;
            border-bottom: 1px solid rgba(148, 163, 184, 0.15);
        }
        .gtk-section-num {
            display: inline-flex; align-items: center; justify-content: center;
            width: 30px; height: 30px; min-width: 30px; border-radius: 50%;
            background: linear-gradient(135deg, #2563eb 0%, #7c3aed 100%);
            color: white; font-weight: 700; font-size: 0.88rem;
            line-height: 1; margin-top: 0.05rem; flex-shrink: 0;
        }
        .gtk-section-body { display: flex; flex-direction: column; gap: 0.2rem; }
        .gtk-section-title {
            font-size: 1.05rem; font-weight: 700; color: #1e293b;
            letter-spacing: -0.01em; line-height: 1.3;
        }
        .gtk-section-desc { font-size: 0.83rem; color: #64748b; line-height: 1.45; }

        /* ---------- Hero caption ---------- */
        .gtk-hero-caption {
            font-size: 0.97rem; color: #475569; line-height: 1.55;
            max-width: 720px; margin: -0.25rem 0 1rem 0;
        }

        /* ---------- Workspace status pills row ---------- */
        .gtk-status-row { display: flex; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 0.5rem; }

        /* ---------- Top nav radio → underline tab appearance ---------- */
        div[data-testid="stRadio"] > label {
            display: none !important;
        }
        div[data-testid="stRadio"] > div[role="radiogroup"] {
            display: flex !important;
            flex-direction: row !important;
            gap: 0 !important;
            border-bottom: 2px solid rgba(148, 163, 184, 0.2) !important;
            padding-bottom: 0 !important;
        }
        div[data-testid="stRadio"] > div[role="radiogroup"] > label {
            display: flex !important;
            align-items: center !important;
            padding: 0.55rem 1.25rem !important;
            font-size: 0.93rem !important;
            font-weight: 600 !important;
            cursor: pointer !important;
            color: #94a3b8 !important;
            background: transparent !important;
            border: none !important;
            border-bottom: 2.5px solid transparent !important;
            margin-bottom: -2px !important;
            white-space: nowrap !important;
            transition: color 0.15s ease, border-color 0.15s ease !important;
        }
        div[data-testid="stRadio"] > div[role="radiogroup"] > label:hover {
            color: #1e293b !important;
        }
        div[data-testid="stRadio"] > div[role="radiogroup"] > label:has(input:checked) {
            color: #2563eb !important;
            border-bottom-color: #2563eb !important;
        }
        /* Hide the radio circle dot */
        div[data-testid="stRadio"] > div[role="radiogroup"] > label > div:first-child {
            display: none !important;
        }
        /* ---------- View-switch tab navigation (legacy anchor) ---------- */
        .element-container:has(.gtk-tab-bar-anchor) + [data-testid="stHorizontalBlock"] button[kind="primary"] {
            background: rgba(37, 99, 235, 0.07) !important;
            color: #2563eb !important;
            border-color: #2563eb !important;
            border-bottom-width: 2.5px !important;
            border-radius: 8px 8px 4px 4px !important;
        }
        .element-container:has(.gtk-tab-bar-anchor) + [data-testid="stHorizontalBlock"] button[kind="secondary"]:not(:disabled) {
            background: transparent !important;
            color: #64748b !important;
            border-color: rgba(148, 163, 184, 0.25) !important;
        }
        .element-container:has(.gtk-tab-bar-anchor) + [data-testid="stHorizontalBlock"] button[kind="secondary"]:hover:not(:disabled) {
            background: rgba(37, 99, 235, 0.04) !important;
            color: #2563eb !important;
            border-color: rgba(37, 99, 235, 0.35) !important;
        }

        </style>
        """,
        unsafe_allow_html=True,
    )


def render_pipeline_stepper(current_step: int, step_states: dict[int, str] | None = None) -> None:
    """Render a 4-step pipeline indicator at the top of the page.

    ``current_step`` is 1-based.  ``step_states`` optionally maps step
    numbers to ``"done"`` / ``"active"`` / ``"pending"``; if omitted,
    steps below ``current_step`` are treated as done.
    """
    steps = [
        (1, "Describe"),
        (2, "Generate"),
        (3, "Review"),
        (4, "Export"),
    ]
    parts: list[str] = ["<div class='gtk-stepper'>"]
    for idx, (num, label) in enumerate(steps):
        state = (step_states or {}).get(num)
        if state is None:
            state = "done" if num < current_step else ("active" if num == current_step else "pending")
        cls = f"gtk-step is-{state}" if state != "pending" else "gtk-step"
        bubble = "&#10003;" if state == "done" else str(num)
        parts.append(
            f"<div class='{cls}'><span class='gtk-step-bubble'>{bubble}</span>"
            f"<span class='gtk-step-label'>{html.escape(label)}</span></div>"
        )
        if idx < len(steps) - 1:
            line_cls = "gtk-step-line is-done" if state == "done" else "gtk-step-line"
            parts.append(f"<div class='{line_cls}'></div>")
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def _render_section_header(num: int | str, title: str, description: str = "") -> None:
    """Render a styled numbered workflow section header."""
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


def contains_any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    normalized = text.lower()
    return any(keyword in normalized for keyword in keywords)


def detect_out_of_scope_text(text: str) -> str | None:
    for pattern in OUT_OF_SCOPE_PATTERNS:
        if pattern.search(text):
            return "This app only supports BPM process descriptions, simulation goals, and KPI-focused refinement feedback. Please remove unrelated requests or general-knowledge topics."
    return None


def validate_generation_scope(process_description: str, simulation_goal: str) -> str | None:
    combined_input = f"{process_description}\n{simulation_goal}".strip()
    if not combined_input:
        return "Please provide both a process description and a simulation goal."
    if out_of_scope_reason := detect_out_of_scope_text(combined_input):
        return out_of_scope_reason
    if len(process_description.strip()) < 40:
        return "The process description is too short. Please describe a real business process with activities, roles, resources, or steps."
    if not contains_any_keyword(process_description, BPM_PROCESS_HINTS):
        return "The process description does not look like a BPM process. Please describe a business process with concrete steps, activities, roles, or resources."
    if not contains_any_keyword(simulation_goal, BPM_GOAL_HINTS):
        return "The simulation goal must describe a BPM performance objective such as cycle time, waiting time, throughput, utilization, quality, cost, or flexibility."
    return None


def validate_refinement_scope(process_description: str, simulation_goal: str, human_feedback: str) -> str | None:
    if generation_error := validate_generation_scope(process_description, simulation_goal):
        return generation_error
    if out_of_scope_reason := detect_out_of_scope_text(human_feedback):
        return out_of_scope_reason
    return None


def get_provider_config(config: dict[str, Any], provider_name: str) -> dict[str, Any]:
    return config.get(provider_name, {})


def get_provider_options(config: dict[str, Any]) -> list[str]:
    return [name for name in PROVIDER_LABELS if name in config]


def get_default_api_key(provider_name: str) -> str:
    if provider_name == "huggingface":
        return os.getenv("HUGGINGFACE_API_TOKEN") or os.getenv("HF_TOKEN") or ""
    if provider_name == "openai":
        return os.getenv("OPENAI_API_KEY", "")
    if provider_name == "anthropic":
        return os.getenv("ANTHROPIC_API_KEY", "")
    if provider_name == "openrouter":
        return os.getenv("OPENROUTER_API_KEY", "")
    return ""


def create_provider(provider_name: str, model_name: str, config: dict[str, Any], api_key: str = "") -> LLMProvider:
    if provider_name == "ollama":
        provider_config = get_provider_config(config, "ollama")
        base_url = os.getenv("OLLAMA_BASE_URL") or provider_config.get("base_url", "http://localhost:11434")
        return OllamaProvider(model=model_name, base_url=base_url)
    if provider_name == "huggingface":
        return HuggingFaceProvider(api_key=api_key, model=model_name)
    if provider_name == "openai":
        return OpenAIProvider(api_key=api_key, model=model_name)
    if provider_name == "anthropic":
        base_url = os.getenv("ANTHROPIC_BASE_URL") or None
        return AnthropicProvider(api_key=api_key, model=model_name, base_url=base_url)
    if provider_name == "openrouter":
        return OpenRouterProvider(api_key=api_key, model=model_name)
    raise ValueError(f"Unsupported provider: {provider_name}")


def get_connection_status(provider_name: str, model_name: str, config: dict[str, Any], api_key: str) -> tuple[bool, str]:
    provider_key = {
        "provider": provider_name,
        "model": model_name,
        "api_key": api_key,
        "ollama_base_url": os.getenv("OLLAMA_BASE_URL") or get_provider_config(config, "ollama").get("base_url", ""),
    }
    if st.session_state.get("_provider_status_key") == provider_key:
        return st.session_state.get("_provider_status", (False, "Not configured"))
    if provider_name == "ollama":
        try:
            status = create_provider(provider_name, model_name, config).health_check()
        except Exception as exc:
            status = (False, str(exc))
    elif not api_key:
        status = (False, f"{PROVIDER_LABELS[provider_name]} API key is missing.")
    else:
        status = (True, f"{PROVIDER_LABELS[provider_name]} API key configured.")
    st.session_state["_provider_status_key"] = provider_key
    st.session_state["_provider_status"] = status
    return status


def _has_significant_context_evidence(context_evidence: str | None) -> bool:
    if not context_evidence:
        return False
    try:
        payload = json.loads(context_evidence)
    except json.JSONDecodeError:
        return False
    return bool(payload.get("significant_relationships"))


def _load_context_evidence_payload(
    context_evidence: str | None,
    *,
    log_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if context_evidence:
        try:
            return json.loads(context_evidence)
        except json.JSONDecodeError:
            return {}
    if not log_profile:
        return {}
    return {
        "significant_relationships": (
            log_profile.get("context_profile", {})
            .get("analysis", {})
            .get("significant_relationships", [])
        ),
        "rejected_relationships": (
            log_profile.get("context_profile", {})
            .get("analysis", {})
            .get("rejected_relationships", [])
        ),
        "filtered_out_factors": (
            log_profile.get("context_profile", {})
            .get("analysis", {})
            .get("filtered_out_factors", [])
        ),
        "metric_metadata": log_profile.get("context_profile", {}).get("metric_metadata", {}),
    }


def _accepted_relationship_support(
    *,
    log_profile: dict[str, Any] | None,
    context_evidence: str | None,
) -> tuple[dict[str, str], set[str], list[dict[str, Any]], dict[tuple[str, str], list[dict[str, Any]]]]:
    detected_context_factors, supported_context_factors, supported_relationships = _context_relationship_lookup(
        log_profile=log_profile,
        context_evidence=context_evidence,
    )
    supported_relationship_index = _supported_relationship_index(
        supported_relationships,
        detected_factor_lookup=detected_context_factors,
        supported_factors=supported_context_factors,
    )
    return (
        detected_context_factors,
        supported_context_factors,
        supported_relationships,
        supported_relationship_index,
    )


def _enrich_context_segment(
    *,
    segment: dict[str, Any],
    support_result: dict[str, Any],
    detected_context_factors: dict[str, str],
    supported_context_factors: set[str],
    evidence_basis: str,
) -> dict[str, Any]:
    matched_relationships = support_result.get("matched_relationships", [])
    if not matched_relationships:
        return segment

    enriched = dict(segment)
    matching_relationship: dict[str, Any] | None = None
    matching_segment: dict[str, Any] | None = None
    segment_condition = str(segment.get("condition", "")).strip()

    for candidate_relationship in matched_relationships:
        relationship_segment = next(
            (
                candidate
                for candidate in candidate_relationship.get("segments", [])
                if _conditions_semantically_match(
                    str(candidate.get("condition", "")).strip(),
                    segment_condition,
                    detected_factor_lookup=detected_context_factors,
                    supported_factors=supported_context_factors,
                )
            ),
            None,
        )
        if relationship_segment is not None:
            matching_relationship = candidate_relationship
            matching_segment = relationship_segment
            break

    relationship = matching_relationship or matched_relationships[0]

    def _set_if_missing(key: str, value: Any) -> None:
        if enriched.get(key) in (None, "") and value not in (None, ""):
            enriched[key] = value

    if relationship.get("factor") not in (None, ""):
        enriched["evidence_factor"] = relationship.get("factor")
    elif enriched.get("evidence_factor") in (None, ""):
        enriched.pop("evidence_factor", None)

    if relationship.get("metric") not in (None, ""):
        enriched["evidence_metric"] = relationship.get("metric")
    elif enriched.get("evidence_metric") in (None, ""):
        enriched.pop("evidence_metric", None)

    _set_if_missing("adjusted_p_value", relationship.get("adjusted_p_value"))
    _set_if_missing("effect_size", relationship.get("effect_size"))
    if matching_segment is not None:
        _set_if_missing("sample_size", matching_segment.get("sample_size"))
        _set_if_missing("observed_baseline", matching_segment.get("observed_median"))
    _set_if_missing(
        "target_type",
        "proxy" if evidence_basis == EvidenceBasis.PROXY_FROM_LOG.value else "direct",
    )
    return enriched


def _sanitize_kpi_grounding_claims(
    result: KPIGenerationResult,
    *,
    log_profile: dict[str, Any] | None,
    context_evidence: str | None,
) -> tuple[KPIGenerationResult, list[dict[str, Any]]]:
    """Remove unsupported log/context claims that the model may hallucinate."""

    has_log = log_profile is not None
    (
        detected_context_factors,
        supported_context_factors,
        _supported_relationships,
        supported_relationship_index,
    ) = _accepted_relationship_support(
        log_profile=log_profile,
        context_evidence=context_evidence,
    )
    has_context = has_log and bool(supported_context_factors)
    payload = result.model_dump()
    warnings: list[dict[str, Any]] = []

    for kpi_payload in payload.get("kpis", []):
        kpi_name = kpi_payload.get("name", "Unnamed KPI")

        if not has_log:
            if kpi_payload.get("supported_by_log") is True:
                warnings.append(
                    {
                        "severity": "warning",
                        "code": "removed_unsupported_log_claim",
                        "message": "Unsupported event-log grounding was removed because no active event log was available.",
                        "kpi_names": [kpi_name],
                    }
                )
            if kpi_payload.get("evidence_basis") != EvidenceBasis.PROCESS_DESCRIPTION_ONLY.value:
                warnings.append(
                    {
                        "severity": "warning",
                        "code": "reset_evidence_basis_without_log",
                        "message": "The evidence basis was reset to process_description_only because no active event log was available.",
                        "kpi_names": [kpi_name],
                    }
                )
            kpi_payload["supported_by_log"] = False
            kpi_payload["evidence_basis"] = EvidenceBasis.PROCESS_DESCRIPTION_ONLY.value

        if not has_context and kpi_payload.get("context_segmentation"):
            warnings.append(
                {
                    "severity": "warning",
                    "code": "removed_unsupported_context_segmentation",
                    "message": "Context segmentation was removed because no significant context evidence was available.",
                    "kpi_names": [kpi_name],
                }
            )
            kpi_payload["context_segmentation"] = []
        elif kpi_payload.get("context_segmentation"):
            kept_segments: list[dict[str, Any]] = []
            removed_conditions: list[str] = []
            removed_factors: set[str] = set()
            removed_pairs: list[dict[str, str | None]] = []

            for segment in kpi_payload.get("context_segmentation", []):
                if not isinstance(segment, dict):
                    removed_conditions.append(str(segment))
                    continue

                condition = str(segment.get("condition") or "").strip()
                support_result = _segment_supported_by_relationship(
                    segment=type("SegmentShim", (), segment)(),
                    supported_relationship_index=supported_relationship_index,
                    detected_factor_lookup=detected_context_factors,
                    supported_factors=supported_context_factors,
                )
                if support_result.get("pair_supported"):
                    kept_segments.append(
                        _enrich_context_segment(
                            segment=segment,
                            support_result=support_result,
                            detected_context_factors=detected_context_factors,
                            supported_context_factors=supported_context_factors,
                            evidence_basis=str(kpi_payload.get("evidence_basis") or EvidenceBasis.PROCESS_DESCRIPTION_ONLY.value),
                        )
                    )
                    if not support_result.get("condition_supported"):
                        warnings.append(
                            {
                                "severity": "warning",
                                "code": "kept_context_segment_with_unmatched_condition",
                                "message": "A context segment was retained because its supported factor-metric pair matched an accepted relationship, but its specific condition did not match accepted segment evidence and should be reviewed.",
                                "kpi_names": [kpi_name],
                                "details": {
                                    "condition": condition or "Unspecified condition",
                                    "evidence_factor": segment.get("evidence_factor"),
                                    "evidence_metric": segment.get("evidence_metric"),
                                },
                            }
                        )
                    continue

                removed_conditions.append(condition or "Unspecified condition")
                evidence_factor = segment.get("evidence_factor")
                if evidence_factor:
                    removed_factors.add(_normalize_context_factor_name(str(evidence_factor)))
                removed_pairs.append(
                    {
                        "evidence_factor": str(evidence_factor) if evidence_factor is not None else None,
                        "evidence_metric": str(segment.get("evidence_metric")) if segment.get("evidence_metric") is not None else None,
                        "reason": support_result.get("reason"),
                    }
                )

            if removed_conditions:
                warnings.append(
                    {
                        "severity": "warning",
                        "code": "removed_unsupported_context_segments",
                        "message": "Context segments were removed because their evidence-supported factor-metric pairs were not supported by the accepted context relationships.",
                        "kpi_names": [kpi_name],
                        "details": {
                            "removed_conditions": removed_conditions,
                            "removed_factors": sorted(removed_factors),
                            "supported_factors": sorted(supported_context_factors),
                            "removed_pairs": removed_pairs,
                        },
                    }
                )
            kpi_payload["context_segmentation"] = kept_segments

    return KPIGenerationResult.model_validate(payload), warnings


# Capture everything inside start_time(...) up to the closing paren — handles
# unescaped apostrophes ('Supplier's Name'), escaped ('Supplier\'s Name'), and
# double-quoted ("Supplier's Name") without complex quote-matching logic.
_FORMULA_ACTIVITY_RE = re.compile(
    r"""start_time\s*\(\s*([^)]+?)\s*\)""",
    re.IGNORECASE,
)

# Duration formula pattern: complete_time(X) - start_time(X) = activity processing
# time. These must NOT be auto-filled with "{X} Waiting Time".
_DURATION_FORMULA_RE = re.compile(
    r"""complete_time\s*\(""",
    re.IGNORECASE,
)


def _fill_missing_activity_measurable_as(
    result: KPIGenerationResult,
) -> tuple[KPIGenerationResult, list[dict[str, Any]]]:
    """Auto-fill measurable_as for activity-level time KPIs whose LLM left it null.

    Extracts the activity name from suggested_formula using the pattern
    start_time('Activity Name') and constructs '{Activity Name} Waiting Time'.
    This prevents the fuzzy matcher in scenario_evaluation from silently
    matching the KPI against the wrong global metric (e.g. Average Waiting Time).
    """
    payload = result.model_dump()
    warnings: list[dict[str, Any]] = []

    for kpi_payload in payload.get("kpis", []):
        if kpi_payload.get("measurable_as") is not None:
            continue
        if kpi_payload.get("process_scope") != "activity_level":
            continue
        if kpi_payload.get("category") != "time":
            continue

        formula = kpi_payload.get("suggested_formula") or ""

        # Skip duration-pattern formulas: complete_time(X) - start_time(X).
        # "{X} Waiting Time" is queue wait, not activity processing time.
        formula_lower = formula.lower()
        complete_pos = formula_lower.find("complete_time(")
        start_pos = formula_lower.find("start_time(")
        if complete_pos != -1 and start_pos != -1 and complete_pos < start_pos:
            continue

        match = _FORMULA_ACTIVITY_RE.search(formula)
        if not match:
            continue

        raw = match.group(1).strip()
        # Strip surrounding quote characters (handles ', ", and unescaped apostrophes)
        if len(raw) >= 2 and raw[0] in ("'", '"') and raw[-1] in ("'", '"'):
            raw = raw[1:-1]
        elif raw and raw[0] in ("'", '"'):
            raw = raw[1:]
        # Unescape backslash-escaped chars (e.g. \' → ')
        activity_name = re.sub(r'\\(.)', r'\1', raw).strip()
        inferred = f"{activity_name} Waiting Time"
        kpi_payload["measurable_as"] = inferred
        warnings.append({
            "severity": "info",
            "code": "inferred_measurable_as",
            "message": (
                f"measurable_as was null for activity-level time KPI '{kpi_payload.get('name')}'; "
                f"auto-filled as '{inferred}' from suggested_formula."
            ),
            "kpi_names": [kpi_payload.get("name")],
        })

    return KPIGenerationResult.model_validate(payload), warnings


def _finalize_generated_result(
    result: KPIGenerationResult,
    *,
    simulation_goal: str,
    log_profile: dict[str, Any] | None,
    context_evidence: str | None,
) -> tuple[KPIGenerationResult, dict[str, Any]]:
    """Deterministically sanitize, validate, and return the final KPI set."""

    result, sanitation_warnings = _sanitize_kpi_grounding_claims(
        result,
        log_profile=log_profile,
        context_evidence=context_evidence,
    )
    result, infer_warnings = _fill_missing_activity_measurable_as(result)
    semantic_validation = validate_kpi_generation_semantics(
        result,
        simulation_goal=simulation_goal,
        log_profile=log_profile,
        context_evidence=context_evidence,
    ).to_dict()
    semantic_validation["issues"].extend(sanitation_warnings + infer_warnings)
    semantic_validation["has_warnings"] = bool(
        semantic_validation.get("has_warnings") or sanitation_warnings or infer_warnings
    )
    return result, semantic_validation


def parse_with_retries(
    provider: LLMProvider,
    system_prompt: str,
    user_prompt: str,
    simulation_goal: str,
    temperature: float,
    max_retries: int = 2,
    *,
    log_profile: dict[str, Any] | None = None,
    context_evidence: str | None = None,
    few_shot_messages: list[dict[str, str]] | None = None,
    json_mode: bool = False,
) -> tuple[KPIGenerationResult, str, dict[str, Any]]:
    base_user_prompt = user_prompt
    latest_raw_output = ""
    last_error: KPIParsingError | None = None
    for attempt in range(max_retries + 1):
        latest_raw_output = provider.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            few_shot_messages=few_shot_messages,
            json_mode=json_mode,
        )
        try:
            result = parse_kpi_generation_payload(latest_raw_output)
            result, semantic_validation = _finalize_generated_result(
                result,
                simulation_goal=simulation_goal,
                log_profile=log_profile,
                context_evidence=context_evidence,
            )
            if semantic_validation.get("has_errors"):
                issue_lines = []
                for issue in semantic_validation.get("issues", [])[:5]:
                    if issue.get("severity") == "error":
                        issue_lines.append(f"- {issue.get('message')}")
                last_error = KPIParsingError(
                    "Model output passed JSON validation but failed semantic validation:\n" + "\n".join(issue_lines),
                    latest_raw_output,
                )
                if attempt == max_retries:
                    break
                user_prompt = (
                    f"{base_user_prompt}\n\n"
                    "Your previous output matched the JSON schema but had semantic KPI-quality issues. "
                    "Repair the KPI set and output ONLY the corrected JSON object.\n\n"
                    "Semantic issues to fix:\n"
                    + "\n".join(issue_lines)
                    + "\n\nPrevious invalid output:\n"
                    + latest_raw_output
                )
                continue
            return result, latest_raw_output, semantic_validation
        except KPIParsingError as exc:
            last_error = exc
            if attempt == max_retries:
                break
            user_prompt = (
                f"{base_user_prompt}\n\n"
                "Your previous output did not match the required JSON schema. Please fix the issue and output ONLY the JSON object.\n\n"
                f"Validation issue:\n{exc}\n\n"
                f"Previous invalid output:\n{latest_raw_output}"
            )
    if last_error is not None:
        raise KPIParsingError(str(last_error), latest_raw_output)
    raise KPIParsingError("The model did not return a valid KPI set after 2 retries.", latest_raw_output)


def get_decision_widget_key(kpi_name: str) -> str:
    return f"kpi_decision_widget::{kpi_name}"


def _render_issue(issue: dict[str, Any]) -> None:
    message = issue.get("message", "")
    code = issue.get("code")
    label = f"[{code}] {message}" if code else message
    if issue.get("severity") == "error":
        st.error(label)
    else:
        st.warning(label)


def decision_to_option(decision: str | None) -> str:
    if decision == "accepted":
        return "Accept"
    if decision == "rejected":
        return "Reject"
    return "Pending"


def option_to_decision(option: str) -> str | None:
    if option == "Accept":
        return "accepted"
    if option == "Reject":
        return "rejected"
    return None


def ensure_decision_widget_state(result: KPIGenerationResult) -> None:
    for kpi in result.kpis:
        widget_key = get_decision_widget_key(kpi.name)
        if widget_key not in st.session_state:
            st.session_state[widget_key] = decision_to_option(st.session_state.kpi_decisions.get(kpi.name))


def update_kpi_decision_from_widget(kpi_name: str) -> None:
    widget_key = get_decision_widget_key(kpi_name)
    st.session_state.kpi_decisions[kpi_name] = option_to_decision(st.session_state[widget_key])


def schedule_review_state_reset() -> None:
    st.session_state._clear_feedback_text = True
    st.session_state._reset_decision_widgets = True


def sync_decisions_with_current_kpis() -> None:
    current_result: KPIGenerationResult | None = st.session_state.current_kpis
    if current_result is None:
        st.session_state.kpi_decisions = {}
        return
    current_names = [kpi.name for kpi in current_result.kpis]
    st.session_state.kpi_decisions = {name: st.session_state.kpi_decisions.get(name) for name in current_names}


def reset_review_state(
    result: KPIGenerationResult,
    *,
    semantic_validation: dict[str, Any] | None = None,
    grounding_assessments: dict[str, dict[str, Any]] | None = None,
) -> None:
    st.session_state.current_kpis = result
    st.session_state.kpi_decisions = {kpi.name: None for kpi in result.kpis}
    st.session_state.semantic_validation = semantic_validation
    st.session_state.kpi_grounding_assessments = grounding_assessments or {}


def add_iteration_history(result: KPIGenerationResult, provider: LLMProvider, iteration_type: str, accepted_kpis: list[str] | None = None, rejected_kpis: list[str] | None = None, feedback: str = "") -> None:
    st.session_state.iteration_history.append({
        "iteration": len(st.session_state.iteration_history) + 1,
        "type": iteration_type,
        "provider_model": provider.get_model_name(),
        "accepted": accepted_kpis or [],
        "rejected": rejected_kpis or [],
        "feedback": feedback,
        "result": result.model_dump(),
    })


def escape_markdown_table_text(value: str | None) -> str:
    if not value:
        return "-"
    return html.escape(value).replace("|", "\\|").replace("\n", "<br>")


def render_smart_table(kpi: SMARTKpi) -> None:
    table = "\n".join([
        "| SMART | Details |",
        "|---|---|",
        f"| Specific | {escape_markdown_table_text(kpi.smart_breakdown.specific)} |",
        f"| Measurable | {escape_markdown_table_text(kpi.smart_breakdown.measurable)} |",
        f"| Achievable | {escape_markdown_table_text(kpi.smart_breakdown.achievable)} |",
        f"| Relevant | {escape_markdown_table_text(kpi.smart_breakdown.relevant)} |",
        f"| Time-bound | {escape_markdown_table_text(kpi.smart_breakdown.time_bound)} |",
    ])
    st.markdown(table, unsafe_allow_html=True)


def render_category_badge(category: str) -> str:
    color = CATEGORY_COLORS.get(category, "#334155")
    return f"<span class='goal-badge' style='background:{color};'>{html.escape(category.title())}</span>"


def render_decision_badge(decision: str | None) -> str:
    color = DECISION_COLORS.get(decision, "#64748b")
    return f"<span class='decision-badge' style='background:{color};'>{html.escape(DECISION_LABELS[decision])}</span>"


def extract_log_artifacts(file_obj: Any) -> tuple[dict[str, Any] | None, str | None, str | None]:
    profile = profile_event_log(file_obj)
    if profile is None:
        return None, None, None
    return profile, build_log_evidence_prompt(profile), build_context_evidence_prompt(profile)


def _consistency_review_signature(
    *,
    process_description: str,
    log_profile: dict[str, Any] | None,
    provider_name: str,
    model_name: str,
) -> str:
    top_activities = [
        entry.get("name", "")
        for entry in (log_profile or {}).get("top_activities", [])
        if entry.get("name")
    ]
    payload = {
        "process_description": process_description.strip(),
        "top_activities": top_activities,
        "provider_name": provider_name,
        "model_name": model_name,
        "log_source_name": st.session_state.get("log_source_name", ""),
    }
    return json.dumps(payload, sort_keys=True)


def _load_consistency_review_cache(signature: str) -> dict[str, Any] | None:
    cache = st.session_state.get("_consistency_review_cache", {})
    return cache.get(signature)


def _store_consistency_review_cache(signature: str, result: dict[str, Any]) -> None:
    cache = dict(st.session_state.get("_consistency_review_cache", {}))
    cache[signature] = result
    st.session_state["_consistency_review_cache"] = cache


def _parse_json_response(raw_output: str) -> dict[str, Any] | None:
    cleaned = strip_code_fences(raw_output)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        extracted = extract_json_object(cleaned)
        if not extracted:
            return None
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            return None


def _consistency_review_few_shots() -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "Review suspected mismatches between a process description and event-log activity labels.",
                    "process_description": (
                        "The loan application process begins when a customer submits a loan request online. "
                        "A senior manager approves or rejects the application. If approved, the disbursement team releases the funds."
                    ),
                    "top_log_activities": [
                        "Submit Application",
                        "Approve Application",
                        "Reject Application",
                        "Release Funds",
                    ],
                    "suspected_missing_in_description": [
                        "Approve Application",
                        "Reject Application",
                        "Release Funds",
                    ],
                    "suspected_missing_in_log": [],
                    "required_output_schema": {
                        "dismissed_in_text": ["activity"],
                        "confirmed_missing_in_text": ["activity"],
                        "dismissed_in_log": ["fragment"],
                        "confirmed_missing_in_log": ["fragment"],
                        "notes": ["short explanation"],
                    },
                },
                indent=2,
            ),
        },
        {
            "role": "assistant",
            "content": json.dumps(
                {
                    "dismissed_in_text": [
                        "Approve Application",
                        "Reject Application",
                        "Release Funds",
                    ],
                    "confirmed_missing_in_text": [],
                    "dismissed_in_log": [],
                    "confirmed_missing_in_log": [],
                    "notes": [
                        "All three suspected activities are semantically present through equivalent wording in the description."
                    ],
                },
                indent=2,
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "Review suspected mismatches between a process description and event-log activity labels.",
                    "process_description": (
                        "A customer submits an insurance claim. A claims agent checks the documents. "
                        "A supervisor makes the final decision and the customer is notified."
                    ),
                    "top_log_activities": [
                        "Submit Claim",
                        "Check Documents",
                        "Fraud Review",
                        "Notify Customer",
                    ],
                    "suspected_missing_in_description": ["Fraud Review"],
                    "suspected_missing_in_log": [],
                    "required_output_schema": {
                        "dismissed_in_text": ["activity"],
                        "confirmed_missing_in_text": ["activity"],
                        "dismissed_in_log": ["fragment"],
                        "confirmed_missing_in_log": ["fragment"],
                        "notes": ["short explanation"],
                    },
                },
                indent=2,
            ),
        },
        {
            "role": "assistant",
            "content": json.dumps(
                {
                    "dismissed_in_text": [],
                    "confirmed_missing_in_text": ["Fraud Review"],
                    "dismissed_in_log": [],
                    "confirmed_missing_in_log": [],
                    "notes": [
                        "The process description does not mention fraud handling or an equivalent review step."
                    ],
                },
                indent=2,
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "Review suspected mismatches between a process description and event-log activity labels.",
                    "process_description": (
                        "The discharge coordinator explains aftercare instructions and schedules follow-up appointments for the patient."
                    ),
                    "top_log_activities": [
                        "Explain Aftercare",
                        "Schedule Follow-Up",
                    ],
                    "suspected_missing_in_description": [],
                    "suspected_missing_in_log": [
                        "aftercare instructions",
                        "follow-up appointments",
                    ],
                    "required_output_schema": {
                        "dismissed_in_text": ["activity"],
                        "confirmed_missing_in_text": ["activity"],
                        "dismissed_in_log": ["fragment"],
                        "confirmed_missing_in_log": ["fragment"],
                        "notes": ["short explanation"],
                    },
                },
                indent=2,
            ),
        },
        {
            "role": "assistant",
            "content": json.dumps(
                {
                    "dismissed_in_text": [],
                    "confirmed_missing_in_text": [],
                    "dismissed_in_log": [
                        "aftercare instructions",
                        "follow-up appointments",
                    ],
                    "confirmed_missing_in_log": [],
                    "notes": [
                        "Both description fragments are clearly represented by equivalent log activities."
                    ],
                },
                indent=2,
            ),
        },
    ]


def _semantic_consistency_review(
    *,
    provider: LLMProvider,
    process_description: str,
    heuristic_result: dict[str, Any],
    log_profile: dict[str, Any],
) -> dict[str, Any]:
    missing_in_text = heuristic_result.get("missing_in_text", [])
    missing_in_log = heuristic_result.get("missing_in_log", [])
    if not missing_in_text and not missing_in_log:
        return {
            "status": "aligned",
            "warnings": [],
            "dismissed_in_text": [],
            "confirmed_missing_in_text": [],
            "dismissed_in_log": [],
            "confirmed_missing_in_log": [],
            "review_source": "heuristic_only",
        }

    top_activities = [
        entry.get("name", "")
        for entry in log_profile.get("top_activities", [])
        if entry.get("name")
    ][:10]
    system_prompt = (
        "You are a precise BPM semantic consistency reviewer. "
        "Decide whether suspected mismatches between a process description and event-log activity labels "
        "are real mismatches or only wording/paraphrase differences. "
        "Be conservative: dismiss warnings when the meaning is clearly present in different wording. "
        "Return only JSON."
    )
    user_prompt = json.dumps(
        {
            "task": (
                "Review the suspected mismatches. If an activity or fragment is clearly present with equivalent meaning, "
                "dismiss it. Only confirm it as missing if the meaning is genuinely absent."
            ),
            "process_description": process_description,
            "top_log_activities": top_activities,
            "suspected_missing_in_description": missing_in_text,
            "suspected_missing_in_log": missing_in_log,
            "required_output_schema": {
                "dismissed_in_text": ["activity"],
                "confirmed_missing_in_text": ["activity"],
                "dismissed_in_log": ["fragment"],
                "confirmed_missing_in_log": ["fragment"],
                "notes": ["short explanation"],
            },
        },
        indent=2,
    )

    raw_output = provider.generate(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.0,
        few_shot_messages=_consistency_review_few_shots(),
        json_mode=True,
    )
    payload = _parse_json_response(raw_output)
    if payload is None:
        return {
            "status": "warning",
            "warnings": heuristic_result.get("warnings", []),
            "dismissed_in_text": [],
            "confirmed_missing_in_text": missing_in_text,
            "dismissed_in_log": [],
            "confirmed_missing_in_log": missing_in_log,
            "review_source": "heuristic_fallback",
            "notes": ["The semantic consistency reviewer did not return valid JSON, so the heuristic warnings were kept."],
        }

    confirmed_missing_in_text = [
        item for item in payload.get("confirmed_missing_in_text", [])
        if item in missing_in_text
    ]
    confirmed_missing_in_log = [
        item for item in payload.get("confirmed_missing_in_log", [])
        if item in missing_in_log
    ]
    dismissed_in_text = [
        item for item in missing_in_text
        if item not in confirmed_missing_in_text
    ]
    dismissed_in_log = [
        item for item in missing_in_log
        if item not in confirmed_missing_in_log
    ]

    warnings: list[str] = []
    if confirmed_missing_in_text:
        warnings.append(
            "The active log contains frequent activities that are not clearly reflected in the description: "
            + ", ".join(confirmed_missing_in_text)
            + "."
        )
    if confirmed_missing_in_log:
        warnings.append(
            "The process description mentions activity fragments that are not clearly visible in the active log: "
            + ", ".join(confirmed_missing_in_log)
            + "."
        )

    return {
        "status": "warning" if warnings else "aligned",
        "warnings": warnings,
        "dismissed_in_text": dismissed_in_text,
        "confirmed_missing_in_text": confirmed_missing_in_text,
        "dismissed_in_log": dismissed_in_log,
        "confirmed_missing_in_log": confirmed_missing_in_log,
        "review_source": "hybrid_llm",
        "notes": payload.get("notes", []),
    }


def set_active_log(
    profile: dict[str, Any] | None,
    evidence: str | None,
    context_evidence: str | None,
    *,
    source_name: str,
    source_kind: str,
) -> None:
    st.session_state.log_profile = profile
    st.session_state.log_evidence = evidence
    st.session_state.context_evidence = context_evidence
    st.session_state.log_source_name = source_name
    st.session_state.log_source_kind = source_kind


def clear_active_log() -> None:
    st.session_state.log_profile = None
    st.session_state.log_evidence = None
    st.session_state.context_evidence = None
    st.session_state.log_source_name = ""
    st.session_state.log_source_kind = ""
    st.session_state.kpi_grounding_assessments = {}
    st.session_state.event_log_uploader_nonce = st.session_state.get("event_log_uploader_nonce", 0) + 1


def render_kpi_card(kpi: SMARTKpi) -> None:
    widget_key = get_decision_widget_key(kpi.name)
    decision = st.session_state.kpi_decisions.get(kpi.name)
    strip_color = DECISION_COLORS.get(decision, "#cbd5e1")
    st.markdown(
        f"<div style='height:3px;border-radius:2px 2px 0 0;"
        f"background:{strip_color};margin-bottom:-1px;'></div>",
        unsafe_allow_html=True,
    )
    with st.expander(kpi.name, expanded=False):
        category_color = CATEGORY_COLORS.get(kpi.category.value, "#64748b")
        decision = st.session_state.kpi_decisions.get(kpi.name)
        decision_color = DECISION_COLORS.get(decision, "#64748b")
        decision_label = DECISION_LABELS.get(decision, "Pending review")
        chip_row = (
            "<div class='gtk-badge-row'>"
            f"<span class='gtk-chip' style='background:{category_color};'>"
            f"{html.escape(kpi.category.value.title())}</span>"
            f"<span class='gtk-chip' style='background:{decision_color};'>"
            f"{html.escape(decision_label)}</span>"
            f"<span class='gtk-chip is-outline'>"
            f"{html.escape(kpi.target_direction.title())}</span>"
            "</div>"
        )
        st.markdown(chip_row, unsafe_allow_html=True)
        st.radio(f"Decision for {kpi.name}", options=DECISION_OPTIONS, key=widget_key, horizontal=True, on_change=update_kpi_decision_from_widget, args=(kpi.name,))
        st.caption(kpi.description)
        st.markdown("**SMART Breakdown**")
        render_smart_table(kpi)
        info_col_1, info_col_2 = st.columns(2)
        with info_col_1:
            st.markdown(f"**Target Direction:** {kpi.target_direction.title()}")
            st.markdown(f"**Process Scope:** {kpi.process_scope.value.replace('_', ' ').title()}")
            st.markdown(f"**Evidence Basis:** {kpi.evidence_basis.value.replace('_', ' ').title()}")
            st.markdown(f"**Supported by Log:** {'Yes' if kpi.supported_by_log else 'No'}")
        with info_col_2:
            st.markdown("**Suggested Formula**")
            st.code(kpi.suggested_formula or "Not provided", language="text")
            if kpi.context_segmentation:
                st.markdown("**Context Segmentation**")
                for segment in kpi.context_segmentation:
                    st.markdown(f"- `{segment.condition}` -> {segment.target}")
                    baseline_parts: list[str] = []
                    if segment.observed_baseline is not None:
                        baseline_parts.append(str(segment.observed_baseline))
                    if segment.sample_size is not None:
                        baseline_parts.append(f"n={segment.sample_size}")
                    if baseline_parts:
                        st.caption(f"Observed in log: {' '.join(baseline_parts)}")


def current_review_summary(result: KPIGenerationResult) -> tuple[int, int, int]:
    accepted = rejected = pending = 0
    for kpi in result.kpis:
        decision = st.session_state.kpi_decisions.get(kpi.name)
        if decision == "accepted":
            accepted += 1
        elif decision == "rejected":
            rejected += 1
        else:
            pending += 1
    return accepted, rejected, pending


def export_markdown(
    result: KPIGenerationResult,
    process_description: str = "",
    simulation_goal_original: str = "",
    metadata: dict | None = None,
) -> str:
    sections = ["# GLASS Report", ""]
    if metadata:
        sections += [
            "## Export Metadata", "",
            f"- **Provider / Model:** {metadata.get('provider', '')} / {metadata.get('model', '')}",
            f"- **Temperature:** {metadata.get('temperature', '')}",
            f"- **Refinement iterations:** {metadata.get('refinement_iterations', 0)}",
            f"- **Exported at:** {metadata.get('exported_at', '')}",
            "",
        ]
    if process_description:
        sections += ["## Process Description", process_description, ""]
    if simulation_goal_original:
        sections += ["## Original Simulation Goal", simulation_goal_original, ""]
    sections += ["## Structured Simulation Goal", result.simulation_goal_structured, "", "## KPI Set", ""]
    for index, kpi in enumerate(result.kpis, start=1):
        sections.extend([
            f"### {index}. {kpi.name}",
            f"- Category: {kpi.category.value}",
            f"- Description: {kpi.description}",
            f"- Target direction: {kpi.target_direction.value}",
            f"- Process scope: {kpi.process_scope.value}",
            f"- Evidence basis: {kpi.evidence_basis.value}",
            f"- Supported by log: {'yes' if kpi.supported_by_log else 'no'}",
            f"- Suggested formula: {kpi.suggested_formula or 'Not provided'}",
            "",
            "| SMART | Details |",
            "|---|---|",
            f"| Specific | {escape_markdown_table_text(kpi.smart_breakdown.specific)} |",
            f"| Measurable | {escape_markdown_table_text(kpi.smart_breakdown.measurable)} |",
            f"| Achievable | {escape_markdown_table_text(kpi.smart_breakdown.achievable)} |",
            f"| Relevant | {escape_markdown_table_text(kpi.smart_breakdown.relevant)} |",
            f"| Time-bound | {escape_markdown_table_text(kpi.smart_breakdown.time_bound)} |",
            "",
        ])
        if kpi.context_segmentation:
            sections.extend(["**Context segmentation**"])
            for segment in kpi.context_segmentation:
                rationale = f" ({segment.rationale})" if segment.rationale else ""
                sections.append(f"- {segment.condition}: {segment.target}{rationale}")
            sections.append("")
    sections.extend(["## Reasoning", result.reasoning])
    return "\n".join(sections)


def handle_generation(
    provider_name: str,
    model_name: str,
    api_key: str,
    config: dict[str, Any],
    process_description: str,
    simulation_goal: str,
    num_kpis: int | None,
    temperature: float,
    log_evidence: str | None = None,
    context_evidence: str | None = None,
    log_profile: dict[str, Any] | None = None,
) -> None:
    provider = create_provider(provider_name, model_name, config, api_key)
    system_prompt, few_shot_messages, user_prompt = build_smart_kpi_prompt(
        process_description=sanitize_user_input(process_description),
        simulation_goal=sanitize_user_input(simulation_goal),
        num_kpis=num_kpis,
        log_evidence=log_evidence,
        context_evidence=context_evidence,
    )
    with st.status("Generating SMART KPIs...", expanded=True) as status:
        status.write("Building the prompt package.")
        status.write(f"Sending request to `{provider.get_model_name()}`.")
        result, raw_output, semantic_validation = parse_with_retries(
            provider=provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            simulation_goal=simulation_goal,
            temperature=temperature,
            log_profile=log_profile,
            context_evidence=context_evidence,
            few_shot_messages=few_shot_messages,
            json_mode=True,
        )
        if not result.kpis and "out of scope" in result.simulation_goal_structured.lower():
            status.update(label="Out-of-scope inputs detected", state="error")
            raise ValueError("The inputs were flagged as out of scope for BPM KPI generation. Please describe a real business process and a simulation-focused goal.")
        status.write("Valid JSON received and validated against the KPI schema.")
        if semantic_validation.get("has_warnings"):
            status.write("Additional semantic validation warnings were recorded for reviewer visibility.")
        status.update(label="SMART KPI generation complete", state="complete")
    st.session_state.last_raw_output = raw_output
    st.session_state.iteration_history = []
    reset_review_state(
        result,
        semantic_validation=semantic_validation,
        grounding_assessments=semantic_validation.get("grounding_assessments", {}),
    )
    schedule_review_state_reset()
    add_iteration_history(result=result, provider=provider, iteration_type="initial")


def handle_refinement(
    provider_name: str,
    model_name: str,
    api_key: str,
    config: dict[str, Any],
    process_description: str,
    simulation_goal: str,
    human_feedback: str,
    log_evidence: str | None = None,
    context_evidence: str | None = None,
    log_profile: dict[str, Any] | None = None,
) -> None:
    current_result: KPIGenerationResult = st.session_state.current_kpis
    accepted_kpis = [kpi.name for kpi in current_result.kpis if st.session_state.kpi_decisions.get(kpi.name) == "accepted"]
    rejected_kpis = [kpi.name for kpi in current_result.kpis if st.session_state.kpi_decisions.get(kpi.name) == "rejected"]
    provider = create_provider(provider_name, model_name, config, api_key)
    system_prompt, user_prompt = build_refinement_prompt(
        process_description=sanitize_user_input(process_description),
        simulation_goal=sanitize_user_input(simulation_goal),
        previous_kpis_json=current_result.model_dump_json(indent=2),
        human_feedback=sanitize_user_input(human_feedback),
        accepted_kpi_names=accepted_kpis,
        rejected_kpi_names=rejected_kpis,
        total_kpis=len(current_result.kpis),
        log_evidence=log_evidence,
        context_evidence=context_evidence,
    )
    with st.status("Refining KPI set...", expanded=True) as status:
        status.write("Locking accepted KPIs and regenerating rejected ones.")
        status.write(f"Sending refinement request to `{provider.get_model_name()}`.")
        result, raw_output, semantic_validation = parse_with_retries(
            provider=provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            simulation_goal=simulation_goal,
            temperature=0.2,
            log_profile=log_profile,
            context_evidence=context_evidence,
            json_mode=True,
        )
        status.write("Refined KPI set validated successfully.")
        if semantic_validation.get("has_warnings"):
            status.write("Additional semantic validation warnings were recorded for reviewer visibility.")
        status.update(label="KPI refinement complete", state="complete")
    st.session_state.last_raw_output = raw_output
    reset_review_state(
        result,
        semantic_validation=semantic_validation,
        grounding_assessments=semantic_validation.get("grounding_assessments", {}),
    )
    schedule_review_state_reset()
    add_iteration_history(result=result, provider=provider, iteration_type="refinement", accepted_kpis=accepted_kpis, rejected_kpis=rejected_kpis, feedback=human_feedback)


def render_history() -> None:
    all_iterations = st.session_state.iteration_history
    previous_iterations = all_iterations[:-1]
    with st.expander("Previous Iterations", expanded=False):
        if not previous_iterations:
            st.caption("No previous iterations yet.")
            return
        for idx in reversed(range(len(previous_iterations))):
            entry = all_iterations[idx]
            # Decisions on this iteration's KPIs are stored in the next entry
            next_entry = all_iterations[idx + 1]
            accepted_names = set(next_entry["accepted"])
            rejected_names = set(next_entry["rejected"])
            st.markdown(f"**Iteration {entry['iteration']}** ({entry['type'].title()}) — `{entry['provider_model']}`")
            kpis = entry["result"].get("kpis", [])
            for kpi in kpis:
                name = kpi["name"]
                category = kpi.get("category", "").title()
                if name in accepted_names:
                    st.write(f"- ✅ **{name}** [{category}]")
                elif name in rejected_names:
                    st.write(f"- ❌ **{name}** [{category}]")
                else:
                    st.write(f"- **{name}** [{category}]")
            if next_entry.get("feedback"):
                st.caption(f"Feedback given: {next_entry['feedback']}")
            st.divider()


def _render_goal_to_parameters_page() -> None:
    """Render the original Goal → KPI generation / review page."""
    config = load_config()
    provider_options = get_provider_options(config)
    if not provider_options:
        st.error("No providers were configured in config.yaml.")
        return

    default_provider = config.get("default_provider", provider_options[0])
    default_provider_index = provider_options.index(default_provider) if default_provider in provider_options else 0
    temperature = 0.2

    st.title("Goal to KPI")
    st.markdown(
        "<p class='gtk-hero-caption'>Describe a business process and a simulation goal"
        " &rarr; review the generated SMART KPIs &rarr; accept or reject"
        " &rarr; refine with feedback.</p>",
        unsafe_allow_html=True,
    )

    # Pipeline stepper — reflects where the user is in the flow.
    _current_kpis = st.session_state.get("current_kpis")
    if _current_kpis is None:
        _has_inputs = bool(
            (st.session_state.get("process_description") or "").strip()
            and (st.session_state.get("simulation_goal") or "").strip()
        )
        _step = 2 if _has_inputs else 1
    else:
        _accepted, _rejected, _pending = current_review_summary(_current_kpis)
        if _pending == 0 and _rejected == 0 and _accepted > 0:
            _step = 4
        else:
            _step = 3
    render_pipeline_stepper(_step)

    with st.sidebar:
        st.markdown("<p style='font-size:0.72rem;text-transform:uppercase;letter-spacing:0.08em;color:#94a3b8;font-weight:700;margin-bottom:0.4rem;'>Model Settings</p>", unsafe_allow_html=True)
        provider_name = st.selectbox(
            "LLM Provider",
            options=provider_options,
            index=default_provider_index,
            format_func=lambda item: PROVIDER_LABELS.get(item, item.title()),
            key="goal_to_parameters__provider",
        )
        provider_config = get_provider_config(config, provider_name)
        available_models = provider_config.get("available_models", [])
        if not available_models:
            st.error(f"No models configured for '{provider_name}'.")
            return

        model_state_key = "goal_to_parameters__model"
        default_model = provider_config.get("default_model", available_models[0])
        if st.session_state.get(model_state_key) not in available_models:
            st.session_state[model_state_key] = default_model if default_model in available_models else available_models[0]
        model_name = st.selectbox(
            "Model",
            options=available_models,
            index=available_models.index(st.session_state[model_state_key]),
            key=model_state_key,
        )

        api_key = ""
        if provider_name != "ollama":
            api_key_widget_key = f"goal_to_parameters__{provider_name}_api_key"
            if api_key_widget_key not in st.session_state:
                st.session_state[api_key_widget_key] = get_default_api_key(provider_name)
            api_key = (st.text_input(
                "API Key",
                key=api_key_widget_key,
                type="password",
                help="Leave blank if already set in .env",
            ) or "").strip()
            if api_key_format_error := validate_api_key_format(provider_name, api_key):
                st.warning(api_key_format_error)

        is_connected, connection_message = get_connection_status(provider_name, model_name, config, api_key)
        status_color = "#15803d" if is_connected else "#b91c1c"
        status_bg = "#ecfdf5" if is_connected else "#fef2f2"
        st.markdown(
            f"<div style='background:{status_bg};border:1px solid {status_color}33;"
            f"border-radius:8px;padding:0.45rem 0.75rem;font-size:0.84rem;"
            f"font-weight:600;color:{status_color};margin-top:0.35rem;'>"
            f"<span style='margin-right:0.4rem;'>&#9679;</span>"
            f"{html.escape(connection_message)}</div>",
            unsafe_allow_html=True,
        )
        if provider_name == "ollama" and not is_connected:
            st.caption("Please install and start Ollama: https://ollama.ai")

        st.markdown("<hr style='margin:1rem 0;border-color:rgba(148,163,184,0.25);'>", unsafe_allow_html=True)
        st.markdown("<p style='font-size:0.72rem;text-transform:uppercase;letter-spacing:0.08em;color:#94a3b8;font-weight:700;margin-bottom:0.4rem;'>Generation Options</p>", unsafe_allow_html=True)

        kpi_count_options = ["Auto", 3, 4, 5, 6]
        kpi_count_selection = st.select_slider("Number of KPIs", options=kpi_count_options, value="Auto")
        num_kpis = None if kpi_count_selection == "Auto" else kpi_count_selection
        if num_kpis is None:
            st.caption("The LLM will choose the optimal number (3-6) based on your process and goal.")

        st.checkbox(
            "LLM Consistency Review",
            key="_use_llm_consistency_review",
            help="Run an additional LLM pass to check KPI semantic consistency before presenting the review.",
        )

        if st.session_state.get("current_kpis") is not None or st.session_state.get("iteration_history"):
            st.markdown("<hr style='margin:1rem 0;border-color:rgba(148,163,184,0.25);'>", unsafe_allow_html=True)
            if st.button("Reset / Start Over", width="stretch", type="secondary"):
                reset_session()
                st.rerun()

    _render_section_header(
        1, "Describe your process",
        "Pick a bundled example or write your own, then optionally attach an event log for grounding.",
    )

    example_col, button_col = st.columns([3, 1])
    with example_col:
        selected_example = st.selectbox("Load an example", options=["— select —"] + list(EXAMPLES.keys()), label_visibility="collapsed")
    with button_col:
        if st.button("Load Example", width="stretch", disabled=selected_example == "— select —"):
            example = EXAMPLES[selected_example]
            st.session_state.process_description = example["process_description"]
            st.session_state.simulation_goal = example["simulation_goal"]
            # Pre-compute log evidence from the bundled example CSV.
            log_file = example.get("event_log")
            if log_file:
                p = Path(log_file)
                csv_path = p if p.is_absolute() else (BASE_DIR / "examples" / log_file)
                csv_path = csv_path.resolve()
                try:
                    with csv_path.open("rb") as fh:
                        log_profile, log_evidence, context_evidence = extract_log_artifacts(fh)
                        set_active_log(
                            log_profile,
                            log_evidence,
                            context_evidence,
                            source_name=log_file,
                            source_kind="example",
                        )
                except Exception:
                    clear_active_log()
            else:
                clear_active_log()
            st.rerun()

    input_col_1, input_col_2 = st.columns([1.6, 1])
    with input_col_1:
        process_description = st.text_area("Process Description", key="process_description", height=240, placeholder="Example: The order fulfillment process starts when a customer places an order. The sales team verifies the order, the warehouse team picks and packs items, a quality check is performed, and the logistics team arranges delivery.")
    with input_col_2:
        simulation_goal = st.text_area("Simulation Goal", key="simulation_goal", height=240, placeholder="Example: Reduce fulfillment cycle time while maintaining quality standards")

    # Optional event log upload for KPI grounding.
    uploaded_log = st.file_uploader(
        "Event Log (optional — CSV)",
        type=["csv"],
        help="Upload a CSV event log to build a structured evidence profile for KPI grounding. The raw rows are not sent directly.",
        key=f"event_log_uploader::{st.session_state.event_log_uploader_nonce}",
    )
    if uploaded_log is not None:
        # A user-uploaded file overrides any example-loaded evidence.
        try:
            log_profile, log_evidence, context_evidence = extract_log_artifacts(uploaded_log)
            if log_profile and log_evidence:
                set_active_log(
                    log_profile,
                    log_evidence,
                    context_evidence,
                    source_name=uploaded_log.name,
                    source_kind="upload",
                )
            else:
                st.warning("Could not extract useful evidence from the uploaded file. Make sure it contains an activity column.")
                clear_active_log()
        except Exception:
            st.warning("Failed to read the event log file. It will be ignored.")
            clear_active_log()

    # Show the active log like a loaded file, with an explicit clear action.
    if st.session_state.log_profile and st.session_state.log_evidence:
        loaded_log_col, remove_log_col = st.columns([4, 1])
        source_kind = "Example file" if st.session_state.log_source_kind == "example" else "Uploaded file"
        loaded_log_col.caption(f"{source_kind} loaded: `{st.session_state.log_source_name}`")
        if remove_log_col.button("Remove Log", width="stretch"):
            clear_active_log()
            st.rerun()
        consistency_result = analyze_text_log_consistency(process_description, st.session_state.log_profile)
        consistency_review = consistency_result
        if (
            consistency_result.get("status") == "warning"
            and st.session_state.get("_use_llm_consistency_review")
            and is_connected
            and process_description.strip()
        ):
            signature = _consistency_review_signature(
                process_description=process_description,
                log_profile=st.session_state.log_profile,
                provider_name=provider_name,
                model_name=model_name,
            )
            cached_review = _load_consistency_review_cache(signature)
            if cached_review is None:
                try:
                    consistency_provider = create_provider(provider_name, model_name, config, api_key)
                    cached_review = _semantic_consistency_review(
                        provider=consistency_provider,
                        process_description=process_description,
                        heuristic_result=consistency_result,
                        log_profile=st.session_state.log_profile,
                    )
                except Exception:
                    cached_review = {
                        "status": consistency_result.get("status", "warning"),
                        "warnings": consistency_result.get("warnings", []),
                        "dismissed_in_text": [],
                        "confirmed_missing_in_text": consistency_result.get("missing_in_text", []),
                        "dismissed_in_log": [],
                        "confirmed_missing_in_log": consistency_result.get("missing_in_log", []),
                        "review_source": "heuristic_fallback",
                        "notes": [
                            "The LLM semantic consistency review could not be completed, so the heuristic warnings were kept."
                        ],
                    }
                _store_consistency_review_cache(signature, cached_review)
            consistency_review = cached_review
        if consistency_review.get("status") == "warning":
            for warning in consistency_review.get("warnings", []):
                st.warning(warning)

    _render_section_header(
        2, "Generate SMART KPIs",
        "The model produces a structured goal + 3–6 SMART KPIs grounded in your description (and log, if provided).",
    )
    if st.button("Generate SMART KPIs", type="primary", width="stretch"):
        if validation_error := validate_generation_scope(process_description, simulation_goal):
            st.error(validation_error)
        else:
            try:
                handle_generation(
                    provider_name=provider_name,
                    model_name=model_name,
                    api_key=api_key,
                    config=config,
                    process_description=process_description,
                    simulation_goal=simulation_goal,
                    num_kpis=num_kpis,
                    temperature=temperature,
                    log_evidence=st.session_state.log_evidence,
                    context_evidence=st.session_state.context_evidence,
                    log_profile=st.session_state.log_profile,
                )
                st.toast("SMART KPIs generated successfully!", icon="✅")
                st.rerun()
            except KPIParsingError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(str(exc))

    current_result: KPIGenerationResult | None = st.session_state.current_kpis
    if current_result is None:
        st.markdown(
            "<div style='border:1.5px dashed rgba(148,163,184,0.35);border-radius:12px;"
            "padding:2.75rem 1.5rem;text-align:center;margin-top:1rem;"
            "background:rgba(248,250,252,0.6);'>"
            "<div style='font-size:1.6rem;margin-bottom:0.5rem;color:#94a3b8;'>&#9635;</div>"
            "<div style='font-weight:600;color:#64748b;font-size:0.95rem;'>"
            "Generated KPIs will appear here</div>"
            "<div style='color:#94a3b8;font-size:0.82rem;margin-top:0.35rem;'>"
            "Fill in the process description and simulation goal above,"
            " then click <strong>Generate SMART KPIs</strong>.</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        return

    sync_decisions_with_current_kpis()
    ensure_decision_widget_state(current_result)
    accepted_count, rejected_count, pending_count = current_review_summary(current_result)
    _render_section_header(
        3, "Review & refine",
        "Accept or reject each generated KPI. Rejected KPIs can be refined with your feedback.",
    )
    summary_col_1, summary_col_2, summary_col_3 = st.columns(3)
    summary_col_1.metric("Accepted", accepted_count)
    summary_col_2.metric("Rejected", rejected_count)
    summary_col_3.metric("Pending", pending_count)
    _, accept_all_col = st.columns([4, 1])
    with accept_all_col:
        accept_all_disabled = pending_count == 0 and rejected_count == 0
        if st.button("Accept All", width="stretch", type="secondary", disabled=accept_all_disabled):
            for kpi in current_result.kpis:
                st.session_state.kpi_decisions[kpi.name] = "accepted"
                st.session_state[get_decision_widget_key(kpi.name)] = "Accept"
            st.rerun()

    # --- Quick-review strip: compact one-row-per-KPI overview ---
    strip_rows: list[str] = []
    for kpi in current_result.kpis:
        cat_color = CATEGORY_COLORS.get(kpi.category.value, "#64748b")
        decision = st.session_state.kpi_decisions.get(kpi.name)
        dec_color = DECISION_COLORS.get(decision, "#64748b")
        dec_label = DECISION_LABELS.get(decision, "Pending review")
        strip_rows.append(
            f"<div style='display:flex;align-items:center;gap:0.6rem;"
            f"padding:0.42rem 0.75rem;border-radius:7px;"
            f"background:rgba(148,163,184,0.05);margin-bottom:0.28rem;'>"
            f"<span style='background:{cat_color};color:white;padding:0.15rem 0.55rem;"
            f"border-radius:999px;font-size:0.68rem;font-weight:700;"
            f"letter-spacing:0.04em;text-transform:uppercase;flex-shrink:0;'>"
            f"{html.escape(kpi.category.value.title())}</span>"
            f"<span style='flex:1;font-size:0.87rem;font-weight:600;color:#1e293b;"
            f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>"
            f"{html.escape(kpi.name)}</span>"
            f"<span style='background:{dec_color};color:white;padding:0.15rem 0.55rem;"
            f"border-radius:999px;font-size:0.68rem;font-weight:700;"
            f"letter-spacing:0.04em;text-transform:uppercase;flex-shrink:0;'>"
            f"{html.escape(dec_label)}</span>"
            f"</div>"
        )
    st.markdown(
        "<div style='border:1px solid rgba(148,163,184,0.2);border-radius:10px;"
        "padding:0.5rem;background:rgba(248,250,252,0.8);margin:0.5rem 0 0.9rem 0;'>"
        + "".join(strip_rows)
        + "</div>",
        unsafe_allow_html=True,
    )

    for kpi in current_result.kpis:
        render_kpi_card(kpi)

    all_reviewed = pending_count == 0
    has_rejections = rejected_count > 0
    st.text_area(
        "Feedback for rejected KPIs",
        key="feedback_text",
        height=140,
        disabled=not has_rejections,
        placeholder="Only BPM- and KPI-focused feedback is allowed. Explain what should change in the rejected KPI, which process activity or metric it should focus on, and how the SMART structure should improve.",
    )
    if not all_reviewed:
        st.info("Review each KPI before refining or exporting the set.")
    elif has_rejections and not st.session_state.feedback_text.strip():
        st.info("Add feedback for the rejected KPIs to enable refinement.")

    refine_disabled = not (all_reviewed and has_rejections and st.session_state.feedback_text.strip())
    if st.button("Refine KPIs", width="stretch", disabled=refine_disabled):
        if validation_error := validate_refinement_scope(process_description, simulation_goal, st.session_state.feedback_text.strip()):
            st.error(validation_error)
        else:
            try:
                handle_refinement(
                    provider_name=provider_name,
                    model_name=model_name,
                    api_key=api_key,
                    config=config,
                    process_description=process_description,
                    simulation_goal=simulation_goal,
                    human_feedback=st.session_state.feedback_text.strip(),
                    log_evidence=st.session_state.log_evidence,
                    context_evidence=st.session_state.context_evidence,
                    log_profile=st.session_state.log_profile,
                )
                st.toast("KPI set refined successfully!", icon="✅")
                st.rerun()
            except KPIParsingError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(str(exc))

    _render_section_header(
        4, "Export",
        "Download the accepted KPI set as JSON or Markdown when all reviews are complete.",
    )
    export_ready = all_reviewed and not has_rejections
    export_help = None if export_ready else "Accept all current KPIs before exporting the final set."

    # Quick handoff to Scenario Studio using the current session's KPI set.
    # Signals the Scenario Studio panel to auto-load the current KPIs into its
    # workspace on the next render (the panel consumes this flag).
    if st.button(
        "Go to Scenario Studio",
        disabled=not export_ready,
        help=(
            "Open the Scenario Studio with the accepted KPIs loaded."
            if export_ready else
            "Accept all current KPIs before continuing to the Scenario Studio."
        ),
        width="stretch",
        type="primary",
    ):
        st.session_state["_scenario_studio_autoload_kpis"] = True
        st.session_state["_app_page_nav"] = "Scenario Studio"
        st.rerun()

    export_col_1, export_col_2 = st.columns(2)
    export_metadata = {
        "provider": provider_name,
        "model": model_name,
        "temperature": temperature,
        "refinement_iterations": max(0, len(st.session_state.iteration_history) - 1),
        "exported_at": datetime.datetime.now().isoformat(),
    }
    export_data = {
        "metadata": export_metadata,
        "process_description": process_description,
        "simulation_goal_original": simulation_goal,
        **current_result.model_dump(),
    }
    export_col_1.download_button("Export as JSON", data=json.dumps(export_data, indent=2), file_name="goal_to_parameters_result.json", mime="application/json", disabled=not export_ready, help=export_help, width="stretch")
    export_col_2.download_button(
        "Export as Markdown",
        data=export_markdown(
            current_result,
            process_description=process_description,
            simulation_goal_original=simulation_goal,
            metadata=export_metadata,
        ),
        file_name="goal_to_parameters_report.md",
        mime="text/markdown",
        disabled=not export_ready,
        help=export_help,
        width="stretch",
    )
    render_history()


_PAGE_OPTIONS = ["Goal to KPI", "Scenario Studio", "Scenario Evaluation"]


def main() -> None:
    """Application entry point — shared setup then page routing."""
    load_environment()
    st.set_page_config(
        page_title="GLASS",
        page_icon=":dart:",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            "About": (
                "**GLASS — Goal-based LLM-Assisted Simulation Studio** — "
                "a two-stage LLM pipeline that turns a BPM "
                "simulation goal into SMART KPIs and, in the second stage, "
                "into scenario-ready SimuBridge parameter changes."
            ),
        },
    )
    initialize_session_state()
    apply_pending_review_state_resets()
    apply_custom_styles()

    if "_app_page_nav" in st.session_state:
        st.session_state["_app_page"] = st.session_state.pop("_app_page_nav")

    if st.session_state.get("_app_page") not in _PAGE_OPTIONS:
        st.session_state["_app_page"] = _PAGE_OPTIONS[0]

    selected_page = st.radio(
        "nav",
        options=_PAGE_OPTIONS,
        key="_app_page",
        horizontal=True,
        label_visibility="collapsed",
    )
    st.markdown("<div style='margin-bottom:1.25rem;'></div>", unsafe_allow_html=True)

    if selected_page == _PAGE_OPTIONS[1]:
        render_second_llm_panel()
    elif selected_page == _PAGE_OPTIONS[2]:
        from ui.evaluation_panel import render_evaluation_panel
        render_evaluation_panel()
    else:
        _render_goal_to_parameters_page()


if __name__ == "__main__":
    main()

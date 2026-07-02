"""Scenario Studio — from verified KPIs to simulation parameters."""

from .chatbot import ClarificationChatbot
from .comparison import ComparisonReport, build_comparison_report
from .context_summary import OperationalContextSummary, build_context_summary
from .cost_estimation import ScenarioCostReport, build_cost_report
from .models import (
    ChatMessage,
    ChatRole,
    ClarificationSession,
    FirstLLMInput,
    RawSimodInput,
    SecondLLMRequestDraft,
    SecondLLMWorkspaceState,
    SimodResult,
)
from .orchestrator import SecondLLMWorkspaceOrchestrator
from .output_schema_patch import (
    PatchDiagnostic,
    PatchModification,
    PatchParameterType,
    ScenarioPatch,
)
from .patch_validator import PatchValidationResult, validate_patch
from .payload_builder import DraftPayloadBuilder
from .scenario_generator import (
    ScenarioGenerationResult,
    generate_scenario_patch,
    generate_scenario_proposal,
)
from .scenario_merger import MergeResult, apply_patch
from .simod_input import accept_simod_raw_input
from .simod_runner import SimodBackend, SimodRunner
from .simod_to_simubridge import BaselineBuildResult, build_baseline_scenario
from .validation import ValidationIssue, ValidationResult, validate_proposal

__all__ = [
    # Legacy
    "ChatMessage",
    "ChatRole",
    "ClarificationChatbot",
    "ComparisonReport",
    "ClarificationSession",
    "OperationalContextSummary",
    "ScenarioCostReport",
    "build_context_summary",
    "build_cost_report",
    "DraftPayloadBuilder",
    "FirstLLMInput",
    "RawSimodInput",
    "ScenarioGenerationResult",
    "SecondLLMRequestDraft",
    "SecondLLMWorkspaceOrchestrator",
    "SecondLLMWorkspaceState",
    "SimodBackend",
    "SimodResult",
    "SimodRunner",
    "ValidationIssue",
    "ValidationResult",
    "accept_simod_raw_input",
    "build_comparison_report",
    "generate_scenario_proposal",
    "validate_proposal",
    # Patch / delta architecture
    "BaselineBuildResult",
    "MergeResult",
    "PatchDiagnostic",
    "PatchModification",
    "PatchParameterType",
    "PatchValidationResult",
    "ScenarioPatch",
    "apply_patch",
    "build_baseline_scenario",
    "generate_scenario_patch",
    "validate_patch",
]

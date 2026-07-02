"""Scenario Studio LLM call: generate a goal-oriented ScenarioProposal.

Orchestrates the full pipeline:
  1. Build evidence (RAG retrieval + SIMOD/log/context filtering)
  2. Construct the structured evidence briefing prompt
  3. Call the LLM provider
  4. Extract and parse the JSON response into a ScenarioProposal
  5. Retry with error feedback on parse/validation failure

The ScenarioProposal contains both the human-readable modification
intent (traceable to KPIs and literature) and the machine-readable
SimuBridge scenario configuration.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from knowledge.retrieval import SecondLLMEvidence, build_second_llm_evidence
from llm.provider import LLMProvider
from second_llm.comparison import ComparisonReport, build_comparison_report, enrich_deltas_with_cost
from second_llm.context_summary import OperationalContextSummary, build_context_summary
from second_llm.cost_estimation import (
    ScenarioCostReport,
    build_cost_report,
    repair_budget_overshoot,
)
from second_llm.output_schema import ScenarioProposal, get_constrained_decoding_schema
from second_llm.output_schema_patch import (
    ScenarioPatch,
    get_patch_constrained_decoding_schema,
)
from second_llm.compatibility_adapter import build_legacy_proposal
from second_llm.patch_validator import (
    enforce_headcount_constraints,
    repair_patch_against_baseline,
    validate_patch,
)
from second_llm.scenario_merger import apply_patch
from second_llm.semantic_compliance import check_semantic_compliance
from second_llm.simod_to_simubridge import build_baseline_scenario, build_flow_name_map
from second_llm.validation import (
    ValidationResult,
    extract_baseline_hours_from_simod,
    repair_labor_norms,
    validate_proposal,
)
from utils.parsing import extract_json_object, strip_code_fences

logger = logging.getLogger(__name__)


@dataclass
class ScenarioGenerationResult:
    """Container for the outcome of a scenario generation attempt."""

    proposal: ScenarioProposal | None = None
    raw_llm_output: str = ""
    evidence: SecondLLMEvidence | None = None
    context_summary: OperationalContextSummary | None = None
    attempts: int = 0
    error: str | None = None
    generation_notes: list[str] = field(default_factory=list)
    validation: ValidationResult | None = None
    comparison: ComparisonReport | None = None
    cost_report: ScenarioCostReport | None = None
    decoding_mode: str = "retry"  # "retry" or "constrained"
    merge_stability: float | None = None
    """Proportion of patch modifications that survived deterministic merge
    without being skipped (applied / total). None if no merge attempt was made."""

    @property
    def success(self) -> bool:
        return self.proposal is not None


def _extract_and_parse_json(raw_output: str) -> dict[str, Any]:
    """Extract and parse a JSON object from the LLM output.

    Handles markdown code fences and extra text around the JSON.
    """
    cleaned = strip_code_fences(raw_output)

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try extracting embedded JSON object
    extracted = extract_json_object(cleaned)
    if extracted:
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            pass

    # Last resort: find the largest {...} block
    brace_match = re.search(r"\{", cleaned)
    if brace_match:
        depth = 0
        start = brace_match.start()
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start : i + 1])
                    except json.JSONDecodeError:
                        break

    raise ValueError(
        "Could not extract a valid JSON object from the LLM response. "
        f"Response starts with: {cleaned[:200]}..."
    )


def _parse_simod_json(
    simod_raw_text: str,
    simod_json_content: str | None = None,
) -> dict[str, Any] | None:
    """Try to parse SIMOD output as a JSON dict."""
    for source in (simod_json_content, simod_raw_text):
        if source:
            try:
                return json.loads(source)
            except (json.JSONDecodeError, TypeError):
                continue
    return None


_MAX_ERROR_CHARS = 1500


def _truncate_validation_error(error_msg: str) -> str:
    """Truncate a Pydantic validation error preserving field-level structure.

    Pydantic errors are formatted as multiple ``N validation errors ...``
    blocks separated by newlines.  Naively slicing at a character limit
    can cut mid-field, losing the field name that the LLM needs to fix
    the issue.  This function keeps whole error lines and prioritises
    lines that mention field names (contain ``->`` or start with a
    known Pydantic error prefix).
    """
    if len(error_msg) <= _MAX_ERROR_CHARS:
        return error_msg

    lines = error_msg.splitlines()
    kept: list[str] = []
    budget = _MAX_ERROR_CHARS

    for line in lines:
        # +1 for the newline we'll re-join with
        line_cost = len(line) + 1
        if line_cost > budget:
            break
        kept.append(line)
        budget -= line_cost

    if len(kept) < len(lines):
        remaining = len(lines) - len(kept)
        kept.append(f"... ({remaining} more error lines truncated)")

    return "\n".join(kept)


def generate_scenario_proposal(
    provider: LLMProvider,
    first_llm_json: str,
    first_llm_parsed: dict[str, Any],
    simod_raw_text: str = "",
    simod_json_content: str | None = None,
    chat_history: list[dict[str, str]] | None = None,
    log_profile: dict[str, Any] | None = None,
    context_profile: dict[str, Any] | None = None,
    *,
    max_retries: int = 2,
    temperature: float = 0.3,
    constrained_decoding: bool = False,
) -> ScenarioGenerationResult:
    """Run the full second-LLM scenario generation pipeline.

    Parameters
    ----------
    provider:
        The configured LLM provider to call.
    first_llm_json:
        Raw JSON string of the verified first-LLM output.
    first_llm_parsed:
        Parsed dict of the first-LLM output.
    simod_raw_text:
        Raw SIMOD output text (manual paste fallback).
    simod_json_content:
        Structured SIMOD JSON content (from integrated runner).
    chat_history:
        Clarification chat messages as ``[{"role": ..., "content": ...}]``.
    log_profile:
        Full event-log profile dict (optional).
    context_profile:
        Context evidence profile dict (optional).
    max_retries:
        Number of retry attempts on parse/validation failure.
    temperature:
        LLM temperature for generation.
    constrained_decoding:
        When True and the provider supports it, use structured outputs
        (JSON Schema constrained decoding) to guarantee valid JSON
        structure at generation time.  Falls back to the retry loop
        when the provider does not support it.

    Returns
    -------
    ScenarioGenerationResult
        Contains the parsed proposal (or error), raw output, evidence,
        and generation notes.
    """
    result = ScenarioGenerationResult()
    notes: list[str] = []

    # --- Resolve constrained decoding ---
    use_constrained = (
        constrained_decoding and provider.supports_structured_output()
    )
    schema_dict: dict[str, Any] | None = None
    if use_constrained:
        schema_dict = get_constrained_decoding_schema()
        result.decoding_mode = "constrained"
        notes.append(
            f"Constrained decoding enabled "
            f"(provider: {provider.get_model_name()})."
        )
    else:
        result.decoding_mode = "retry"
        if constrained_decoding:
            notes.append(
                f"Constrained decoding requested but provider "
                f"'{provider.get_model_name()}' does not support it; "
                f"falling back to retry loop."
            )

    # --- Stage 1: Build evidence ---
    goal_structured = first_llm_parsed.get("simulation_goal_structured", "")
    kpis = first_llm_parsed.get("kpis", [])
    simod_dict = _parse_simod_json(simod_raw_text, simod_json_content)

    evidence = build_second_llm_evidence(
        goal_structured=goal_structured,
        kpis=kpis,
        simod_json=simod_dict,
        log_profile=log_profile,
        context_profile=context_profile,
        bpmn_xml=bpmn_xml,
    )
    result.evidence = evidence
    notes.extend(evidence.retrieval_notes)

    # --- Stage 1b: Summarise operational context from chat ---
    ctx_summary: OperationalContextSummary | None = None
    if chat_history:
        non_system = [m for m in chat_history if m.get("role") != "system"]
        if non_system:
            ctx_summary = build_context_summary(non_system, provider)
            result.context_summary = ctx_summary
            if ctx_summary and not ctx_summary.is_empty:
                notes.append(
                    "Operational context summarised from chat "
                    f"({len(non_system)} messages)."
                )
            else:
                notes.append("No operational constraints extracted from chat.")

    # --- Stage 2: Build prompt (lazy import to avoid circular dependency) ---
    from prompts.scenario_proposal_prompt import build_scenario_proposal_prompt

    system_prompt, _, user_prompt = build_scenario_proposal_prompt(
        first_llm_json=first_llm_json,
        evidence=evidence,
        chat_history=chat_history,
        operational_context=ctx_summary,
    )
    notes.append(
        f"Prompt: system={len(system_prompt)} chars, "
        f"user={len(user_prompt)} chars"
    )

    # --- Stage 3+4: Call LLM with retry loop ---
    last_error: str | None = None
    base_user_prompt = user_prompt
    last_valid_proposal: ScenarioProposal | None = None
    last_valid_vr: ValidationResult | None = None

    for attempt in range(max_retries + 1):
        result.attempts = attempt + 1
        notes.append(f"Attempt {attempt + 1}/{max_retries + 1}")

        try:
            raw_output = provider.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                json_mode=True,
                json_schema=schema_dict,
            )
            result.raw_llm_output = raw_output
        except Exception as exc:
            last_error = f"LLM call failed: {exc}"
            notes.append(last_error)
            logger.warning("Second LLM call failed (attempt %d): %s", attempt + 1, exc)
            continue

        # Parse JSON from response
        try:
            parsed_dict = _extract_and_parse_json(raw_output)
        except ValueError as exc:
            last_error = str(exc)
            notes.append(f"JSON extraction failed: {last_error}")
            if attempt < max_retries:
                user_prompt = (
                    f"{base_user_prompt}\n\n"
                    "---\n"
                    "YOUR PREVIOUS OUTPUT WAS NOT VALID JSON. "
                    "Output ONLY a single JSON object matching the schema. "
                    "No markdown, no explanation text.\n\n"
                    f"Error: {last_error}"
                )
            continue

        # Validate with Pydantic schema
        try:
            proposal = ScenarioProposal.model_validate(parsed_dict)
        except Exception as exc:
            last_error = f"Schema validation failed: {exc}"
            notes.append(last_error)
            logger.warning(
                "ScenarioProposal validation failed (attempt %d): %s",
                attempt + 1,
                exc,
            )
            if attempt < max_retries:
                error_msg = _truncate_validation_error(str(exc))

                user_prompt = (
                    f"{base_user_prompt}\n\n"
                    "---\n"
                    "YOUR PREVIOUS OUTPUT HAD VALIDATION ERRORS. "
                    "Fix the issues below and output ONLY the corrected "
                    "JSON object.\n\n"
                    f"Validation errors:\n{error_msg}\n\n"
                    f"Your previous output (first 3000 chars):\n"
                    f"{raw_output[:3000]}"
                )
            continue

        notes.append("ScenarioProposal parsed and validated successfully.")

        if proposal.warnings:
            for w in proposal.warnings:
                notes.append(f"[schema warning] {w}")

        # Keep track of the best schema-valid proposal in case retries are exhausted.
        last_valid_proposal = proposal

        # --- Auto-repair over-cap labor schedules ---
        # Per-role cap = baseline calendar hours * (1 + overtime slack).
        # Over-cap timetables are clamped and headcount is bumped to
        # preserve the intended total role-hours.  Runs before
        # post-schema validation so the user never sees an illegal
        # schedule in the output.
        baseline_hours_by_role = extract_baseline_hours_from_simod(simod_dict)
        repair_notes = repair_labor_norms(
            proposal, baseline_by_role=baseline_hours_by_role,
        )
        for rn in repair_notes:
            notes.append(f"[auto-repair] {rn}")

        budget_repair_notes = repair_budget_overshoot(
            proposal, context_summary=ctx_summary,
        )
        for rn in budget_repair_notes:
            notes.append(f"[budget-repair] {rn}")

        # --- Post-schema validation (constraints + directional + feasibility) ---
        vr = validate_proposal(proposal, context_summary=ctx_summary)
        result.validation = vr
        last_valid_vr = vr  # always keep the latest VR from a schema-valid proposal

        if vr.warnings:
            for w in vr.warnings:
                notes.append(f"[validation warning] {w.message}")

        if vr.has_errors:
            error_summary = vr.error_summary()
            last_error = f"Post-schema validation errors:\n{error_summary}"
            notes.append(last_error)
            logger.warning(
                "Post-schema validation failed (attempt %d): %s",
                attempt + 1,
                error_summary,
            )
            if attempt < max_retries:
                _budget_error = any(
                    "budget_exceeded" in e.category or "budget" in e.message.lower()
                    for e in vr.errors
                )
                if _budget_error:
                    _budget_msgs = [
                        e.message for e in vr.errors
                        if "budget_exceeded" in e.category or "budget" in e.message.lower()
                    ]
                    _retry_header = (
                        "YOUR PREVIOUS OUTPUT EXCEEDS THE STATED BUDGET.\n"
                        + "\n".join(_budget_msgs)
                        + "\n\nYou MUST significantly cut the cost of your "
                        "proposed modifications. Steps:\n"
                        "1. Remove or reduce resource_count increase "
                        "modifications — these are the primary cost driver "
                        "(cost = delta × costHour × weekly_hours × 4.33).\n"
                        "2. Replace headcount increases with "
                        "timetable/calendar changes for existing resources.\n"
                        "3. If a KPI cannot be addressed within the budget, "
                        "move it to unresolved_kpis.\n"
                        "Output ONLY the corrected JSON."
                    )
                else:
                    _retry_header = (
                        "YOUR PREVIOUS OUTPUT PASSED SCHEMA VALIDATION BUT "
                        "HAS CONSTRAINT OR CONSISTENCY ERRORS. Fix the "
                        "issues below and output ONLY the corrected JSON."
                    )
                user_prompt = (
                    f"{base_user_prompt}\n\n"
                    "---\n"
                    f"{_retry_header}\n\n"
                    f"Errors:\n{error_summary}\n\n"
                    f"Your previous output (first 3000 chars):\n"
                    f"{raw_output[:3000]}"
                )
                continue

        # Success — build comparison report and cost estimates
        result.proposal = proposal
        result.comparison = build_comparison_report(first_llm_json, proposal)
        if result.comparison.notes:
            for note in result.comparison.notes:
                notes.append(f"[comparison] {note}")

        # Computational cost and queueing impact estimates
        cost_report = build_cost_report(proposal, context_summary=ctx_summary)
        result.cost_report = cost_report
        if cost_report.has_estimates:
            for note in cost_report.notes:
                notes.append(f"[cost] {note}")
            enrich_deltas_with_cost(result.comparison, cost_report)

        result.generation_notes = notes
        return result

    # All retries exhausted.
    # If the LLM produced a schema-valid proposal on any attempt, return it
    # so the user always sees a result.  Remaining constraint violations are
    # kept on result.validation so the UI surfaces them as on-screen warnings
    # rather than a hard error — the generation found the best feasible
    # solution it could within the given number of attempts.
    if last_valid_proposal is not None:
        result.proposal = last_valid_proposal
        result.validation = last_valid_vr
        result.comparison = build_comparison_report(first_llm_json, last_valid_proposal)
        cost_report = build_cost_report(last_valid_proposal, context_summary=ctx_summary)
        result.cost_report = cost_report
        if cost_report.has_estimates:
            enrich_deltas_with_cost(result.comparison, cost_report)
        notes.append(
            f"Returned best available proposal after {result.attempts} attempt(s); "
            "some operational constraints may not be fully satisfied — "
            "see validation issues above."
        )
        result.generation_notes = notes
        return result

    result.error = last_error
    result.generation_notes = notes
    return result


# ===================================================================
# Patch-based (delta) generation flow
# ===================================================================

def generate_scenario_patch(
    provider: LLMProvider,
    first_llm_json: str,
    first_llm_parsed: dict[str, Any],
    simod_raw_text: str = "",
    simod_json_content: str | None = None,
    bpmn_xml: str = "",
    chat_history: list[dict[str, str]] | None = None,
    log_profile: dict[str, Any] | None = None,
    context_profile: dict[str, Any] | None = None,
    *,
    max_retries: int = 2,
    temperature: float = 0.3,
    constrained_decoding: bool = False,
    strict_merge: bool = False,
) -> ScenarioGenerationResult:
    """Patch-based second-LLM pipeline.

    Pipeline
    --------
    1. Build evidence and operational-context summary (same as legacy flow).
    2. Deterministically build the SIMOD -> SimuBridge baseline.
    3. Ask the LLM for a :class:`ScenarioPatch` using the patch prompt.
    4. Pre-merge validate the patch (grounding, coverage, faithfulness).
    5. Deterministically merge the patch onto the baseline.
    6. Adapt to a legacy :class:`ScenarioProposal` so downstream
       comparison / cost / UI code is unchanged.

    The function returns a :class:`ScenarioGenerationResult` whose
    ``proposal`` field is the legacy-shaped object built by the adapter.
    ``decoding_mode`` is prefixed with ``"patch-"`` so callers can
    distinguish patch-mode runs from legacy-mode runs.
    """
    result = ScenarioGenerationResult()
    notes: list[str] = []

    use_constrained = (
        constrained_decoding and provider.supports_structured_output()
    )
    schema_dict: dict[str, Any] | None = None
    if use_constrained:
        schema_dict = get_patch_constrained_decoding_schema()
        result.decoding_mode = "patch-constrained"
        notes.append(
            f"Patch constrained decoding enabled "
            f"(provider: {provider.get_model_name()})."
        )
    else:
        result.decoding_mode = "patch-retry"

    # --- Evidence ---
    goal_structured = first_llm_parsed.get("simulation_goal_structured", "")
    kpis = first_llm_parsed.get("kpis", [])
    declared_kpis = [k.get("name") for k in kpis if isinstance(k, dict) and k.get("name")]
    simod_dict = _parse_simod_json(simod_raw_text, simod_json_content)

    evidence = build_second_llm_evidence(
        goal_structured=goal_structured,
        kpis=kpis,
        simod_json=simod_dict,
        log_profile=log_profile,
        context_profile=context_profile,
        bpmn_xml=bpmn_xml,
    )
    result.evidence = evidence
    notes.extend(evidence.retrieval_notes)

    ctx_summary: OperationalContextSummary | None = None
    if chat_history:
        non_system = [m for m in chat_history if m.get("role") != "system"]
        if non_system:
            ctx_summary = build_context_summary(non_system, provider)
            result.context_summary = ctx_summary

    # --- Baseline from SIMOD ---
    baseline_build = build_baseline_scenario(simod_dict, bpmn_xml=bpmn_xml)
    flow_name_map = build_flow_name_map(bpmn_xml)
    for bn in baseline_build.notes:
        notes.append(f"[baseline] {bn}")
    if not baseline_build.ok or baseline_build.scenario is None:
        result.error = (
            "Cannot build SimuBridge baseline from SIMOD output: "
            + "; ".join(baseline_build.errors)
        )
        result.generation_notes = notes
        return result
    baseline = baseline_build.scenario

    # --- Patch prompt ---
    from prompts.scenario_patch_prompt import build_scenario_patch_prompt

    system_prompt, _, user_prompt = build_scenario_patch_prompt(
        first_llm_json=first_llm_json,
        evidence=evidence,
        chat_history=chat_history,
        operational_context=ctx_summary,
    )
    notes.append(
        f"Patch prompt: system={len(system_prompt)} chars, "
        f"user={len(user_prompt)} chars"
    )

    base_user_prompt = user_prompt
    last_error: str | None = None
    last_valid_proposal: ScenarioProposal | None = None
    last_valid_vr: ValidationResult | None = None
    last_valid_merge_stability: float | None = None

    for attempt in range(max_retries + 1):
        result.attempts = attempt + 1
        notes.append(f"Patch attempt {attempt + 1}/{max_retries + 1}")

        try:
            raw_output = provider.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                json_mode=True,
                json_schema=schema_dict,
            )
            result.raw_llm_output = raw_output
        except Exception as exc:
            last_error = f"LLM call failed: {exc}"
            notes.append(last_error)
            continue

        try:
            parsed_dict = _extract_and_parse_json(raw_output)
        except ValueError as exc:
            last_error = str(exc)
            if attempt < max_retries:
                user_prompt = (
                    f"{base_user_prompt}\n\n---\n"
                    "YOUR PREVIOUS OUTPUT WAS NOT VALID JSON. Output ONLY a "
                    "single JSON ScenarioPatch object.\n\n"
                    f"Error: {last_error}"
                )
            continue

        try:
            patch = ScenarioPatch.model_validate(parsed_dict)
        except Exception as exc:
            last_error = f"Patch schema validation failed: {exc}"
            notes.append(last_error)
            if attempt < max_retries:
                error_msg = _truncate_validation_error(str(exc))
                user_prompt = (
                    f"{base_user_prompt}\n\n---\n"
                    "YOUR PREVIOUS OUTPUT HAD SCHEMA ERRORS.\n"
                    f"Errors:\n{error_msg}\n\n"
                    f"Previous output (first 3000 chars):\n{raw_output[:3000]}"
                )
            continue

        # Auto-repair element name mistakes before validation.
        patch, repair_notes = repair_patch_against_baseline(patch, baseline)
        for rn in repair_notes:
            notes.append(f"[patch-repair] {rn}")

        # Deterministically enforce user-stated headcount constraints so
        # they are never violated in the final output, regardless of what
        # the LLM proposed.
        if ctx_summary is not None:
            patch, enforce_notes = enforce_headcount_constraints(
                patch, baseline, ctx_summary,
            )
            for en in enforce_notes:
                notes.append(f"[constraint-enforce] {en}")

        # Pre-merge validation
        pv = validate_patch(patch, baseline=baseline, declared_kpis=declared_kpis)
        if pv.warnings:
            for w in pv.warnings:
                notes.append(f"[patch warning] {w.message}")
        if pv.has_errors:
            err_sum = pv.error_summary()
            last_error = f"Patch validation errors:\n{err_sum}"
            notes.append(last_error)
            if attempt < max_retries:
                user_prompt = (
                    f"{base_user_prompt}\n\n---\n"
                    "YOUR PREVIOUS PATCH HAS GROUNDING/COVERAGE ERRORS. "
                    "Fix them and re-emit the ScenarioPatch.\n\n"
                    f"Errors:\n{err_sum}\n\n"
                    f"Previous output (first 3000 chars):\n{raw_output[:3000]}"
                )
            continue

        # Merge
        merge_result = apply_patch(baseline, patch, strict=strict_merge, element_name_map=flow_name_map)
        for w in merge_result.warning_messages:
            notes.append(f"[merge warning] {w}")
        if merge_result.has_errors and strict_merge:
            last_error = (
                "Merge failed in strict mode: "
                + "; ".join(merge_result.error_messages)
            )
            notes.append(last_error)
            if attempt < max_retries:
                user_prompt = (
                    f"{base_user_prompt}\n\n---\n"
                    "YOUR PREVIOUS PATCH COULD NOT BE APPLIED.\n"
                    f"Errors:\n{chr(10).join(merge_result.error_messages)}\n\n"
                    f"Previous output (first 3000 chars):\n{raw_output[:3000]}"
                )
            continue
        if merge_result.scenario is None:
            last_error = "Merge produced no scenario; aborting."
            notes.append(last_error)
            continue

        _total_mods = len(patch.modifications)
        _applied_mods = len(merge_result.applied_modifications)
        _merge_stability = round(
            _applied_mods / _total_mods if _total_mods > 0 else 1.0, 4
        )
        notes.append(
            f"[merge stability] {_applied_mods}/{_total_mods} modifications applied "
            f"({_merge_stability * 100:.1f}%)"
        )

        # Adapt to legacy shape so downstream reports run unchanged
        merge_warn_notes = [
            f"merger: {w}" for w in merge_result.warning_messages
        ]
        proposal = build_legacy_proposal(
            patch, merge_result.scenario, extra_warnings=merge_warn_notes,
        )

        # In patch mode, timetables come from the SIMOD baseline (not from
        # the LLM), so labor-norm clamping must NOT run — it would corrupt
        # SIMOD-discovered working hours and inflate headcounts artificially.

        budget_repair_notes = repair_budget_overshoot(
            proposal, context_summary=ctx_summary,
        )
        for rn in budget_repair_notes:
            notes.append(f"[budget-repair] {rn}")

        vr = validate_proposal(proposal, context_summary=ctx_summary)
        result.validation = vr
        last_valid_proposal = proposal  # track best schema-valid+merged proposal
        last_valid_vr = vr
        last_valid_merge_stability = _merge_stability
        for w in vr.warnings:
            notes.append(f"[validation warning] {w.message}")

        if vr.has_errors:
            error_summary = vr.error_summary()
            last_error = f"Post-schema validation errors:\n{error_summary}"
            notes.append(last_error)
            if attempt < max_retries:
                _budget_error = any(
                    "budget_exceeded" in e.category or "budget" in e.message.lower()
                    for e in vr.errors
                )
                if _budget_error:
                    _budget_msgs = [
                        e.message for e in vr.errors
                        if "budget_exceeded" in e.category or "budget" in e.message.lower()
                    ]
                    _retry_header = (
                        "YOUR PREVIOUS PATCH EXCEEDS THE STATED BUDGET.\n"
                        + "\n".join(_budget_msgs)
                        + "\n\nYou MUST significantly cut the cost of your "
                        "proposed modifications. Steps:\n"
                        "1. Remove or reduce resource_count increase "
                        "modifications — these are the primary cost driver "
                        "(cost = delta × costHour × weekly_hours × 4.33).\n"
                        "2. Replace headcount increases with "
                        "timetable/calendar changes for existing resources.\n"
                        "3. If a KPI cannot be addressed within the budget, "
                        "move it to unresolved_kpis.\n"
                        "Re-emit ONLY the corrected ScenarioPatch."
                    )
                else:
                    _retry_header = (
                        "YOUR PREVIOUS PATCH PASSED MERGE BUT HAS CONSTRAINT OR "
                        "CONSISTENCY ERRORS. Fix the issues below and re-emit the "
                        "ScenarioPatch."
                    )
                user_prompt = (
                    f"{base_user_prompt}\n\n---\n"
                    f"{_retry_header}\n\n"
                    f"Errors:\n{error_summary}\n\n"
                    f"Previous output (first 3000 chars):\n{raw_output[:3000]}"
                )
            continue

        # Semantic compliance check — runs whenever chat history exists.
        # Hard violations trigger a retry when retries remain; on the last
        # attempt they are logged as warnings so the user can still see them.
        if chat_history:
            sc = check_semantic_compliance(chat_history, proposal, provider)
            for sv in sc.violations:
                notes.append(f"[semantic-{sv.severity}] {sv.violation}")
            if sc.has_hard_violations and attempt < max_retries:
                violation_text = sc.violation_summary()
                last_error = f"Semantic compliance violations:\n{violation_text}"
                notes.append(last_error)
                user_prompt = (
                    f"{base_user_prompt}\n\n---\n"
                    "YOUR PREVIOUS PATCH VIOLATES CONSTRAINTS THE USER STATED "
                    "IN THE CHAT. You MUST fix every violation below and "
                    "re-emit the ScenarioPatch.\n\n"
                    f"Violations:\n{violation_text}\n\n"
                    f"Previous output (first 3000 chars):\n{raw_output[:3000]}"
                )
                continue

        result.proposal = proposal
        result.merge_stability = _merge_stability
        result.comparison = build_comparison_report(first_llm_json, proposal)
        cost_report = build_cost_report(proposal, context_summary=ctx_summary)
        result.cost_report = cost_report
        if cost_report.has_estimates:
            enrich_deltas_with_cost(result.comparison, cost_report)

        result.generation_notes = notes
        return result
    if last_valid_proposal is not None:
        result.proposal = last_valid_proposal
        result.merge_stability = last_valid_merge_stability
        result.validation = last_valid_vr
        result.comparison = build_comparison_report(first_llm_json, last_valid_proposal)
        cost_report = build_cost_report(last_valid_proposal, context_summary=ctx_summary)
        result.cost_report = cost_report
        if cost_report.has_estimates:
            enrich_deltas_with_cost(result.comparison, cost_report)
        notes.append(
            f"Returned best available proposal after {result.attempts} attempt(s); "
            "some operational constraints may not be fully satisfied — "
            "see validation issues above."
        )
        result.generation_notes = notes
        return result

    result.error = last_error
    result.generation_notes = notes
    return result

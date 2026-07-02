"""Patch-level validation for :class:`ScenarioPatch` outputs.

This module answers a single question: *before we even try to merge
this patch, does it look grounded, complete, and internally consistent
against the declared KPIs and the SIMOD baseline?*

It deliberately **reuses** the existing numeric/heuristic utilities in
:mod:`second_llm.validation` rather than duplicating them.  The only new
logic lives in this file:

  * KPI coverage (each optimisation-target KPI is addressed by a
    modification OR explicitly listed in ``unresolved_kpis``).
  * Modification-level grounding (each mod cites a paper *or* a
    baseline value).
  * Baseline-faithfulness pre-flight (target element exists and quoted
    baseline agrees with SIMOD within tolerance).

The merger (:mod:`second_llm.scenario_merger`) runs the same
faithfulness checks at apply time — ``validate_patch`` exists so that
pre-merge callers (tests, retry prompts) can surface the issues *before*
constructing a full merged scenario, which is cheaper and produces
clearer error messages for the LLM retry loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from second_llm.output_schema import ModificationDirection, SimuBridgeScenario
from second_llm.output_schema_patch import (
    PatchDiagnostic,
    PatchModification,
    PatchParameterType,
    ScenarioPatch,
)
from second_llm.scenario_merger import (
    _check_baseline_value_match,
    _check_target_exists,
    _detect_conflicts,
    _resolve,
)
from second_llm.validation import _extract_first_number  # reused


@dataclass
class PatchValidationResult:
    """Pre-merge patch validation verdict."""

    diagnostics: list[PatchDiagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[PatchDiagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[PatchDiagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]

    @property
    def has_errors(self) -> bool:
        return any(d.severity == "error" for d in self.diagnostics)

    def error_summary(self, max_chars: int = 1500) -> str:
        """Format errors for the LLM retry prompt."""
        lines = [
            f"- [{d.category}] {d.message}" for d in self.errors
        ]
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... (truncated)"
        return text


# -------------------------------------------------------------------
# Grounding: each modification must cite evidence
# -------------------------------------------------------------------

def _check_grounding(mod: PatchModification, idx: int) -> PatchDiagnostic | None:
    has_paper = bool(mod.literature_support)
    has_evidence_text = bool(mod.evidence_source.strip())
    has_baseline_quote = bool(mod.baseline_value.strip())

    # Evidence is adequate if ANY of: paper IDs, non-empty evidence_source,
    # or a non-empty baseline_value quote (which itself is grounding in
    # the SIMOD baseline).
    if not (has_paper or has_evidence_text or has_baseline_quote):
        return PatchDiagnostic(
            severity="error", category="ungrounded",
            message=(
                f"Modification #{idx} on '{mod.target_element}' has no "
                f"literature_support, no evidence_source, and no "
                f"baseline_value quote. Every change must cite evidence."
            ),
            modification_index=idx, element=mod.target_element,
        )
    return None


# -------------------------------------------------------------------
# Numeric direction check (reuses _extract_first_number from validation.py)
# -------------------------------------------------------------------

_DIRECTIONLESS = {
    ModificationDirection.REDISTRIBUTE,
    ModificationDirection.ADD_NEW,
    ModificationDirection.REMOVE,
    ModificationDirection.CHANGE_DISTRIBUTION,
    ModificationDirection.DIFFERENTIATE,
}

# Parameter types whose baseline/proposed values are NOT scalar numbers —
# extracting the first digit from a node-ID string gives a meaningless result.
_SKIP_NUMERIC_CHECK = {
    PatchParameterType.GATEWAY_PROBABILITIES,
    PatchParameterType.RESOURCE_CALENDAR,
}


def _check_direction_consistency(
    mod: PatchModification, idx: int,
) -> PatchDiagnostic | None:
    if mod.direction in _DIRECTIONLESS:
        return None
    if mod.parameter_type in _SKIP_NUMERIC_CHECK:
        return None
    base = _extract_first_number(mod.baseline_value)
    prop = _extract_first_number(mod.proposed_value)
    if base is None or prop is None:
        return None
    if base == prop:
        return PatchDiagnostic(
            severity="error", category="no_op",
            message=(
                f"Modification #{idx} on '{mod.target_element}': numeric "
                f"baseline equals proposed ({base}) — this is a no-op."
            ),
            modification_index=idx, element=mod.target_element,
        )
    if mod.direction == ModificationDirection.INCREASE and prop < base:
        return PatchDiagnostic(
            severity="warning", category="direction_inconsistent",
            message=(
                f"Modification #{idx} on '{mod.target_element}' claims "
                f"direction='increase' but proposed ({prop}) < baseline ({base})."
            ),
            modification_index=idx, element=mod.target_element,
        )
    if mod.direction == ModificationDirection.DECREASE and prop > base:
        return PatchDiagnostic(
            severity="warning", category="direction_inconsistent",
            message=(
                f"Modification #{idx} on '{mod.target_element}' claims "
                f"direction='decrease' but proposed ({prop}) > baseline ({base})."
            ),
            modification_index=idx, element=mod.target_element,
        )
    return None


# -------------------------------------------------------------------
# KPI coverage — every declared KPI is either targeted or explicitly unresolved
# -------------------------------------------------------------------

def _check_kpi_coverage(
    patch: ScenarioPatch, declared_kpis: list[str],
) -> list[PatchDiagnostic]:
    if not declared_kpis:
        return []
    targeted = {m.kpi_reference for m in patch.modifications}
    unresolved = {u.kpi_name for u in patch.unresolved_kpis}
    impacts = {i.kpi_name for i in patch.expected_kpi_impacts}

    out: list[PatchDiagnostic] = []
    for kpi in declared_kpis:
        if kpi in targeted or kpi in unresolved:
            continue
        # Warn rather than error: the LLM may have named the KPI slightly
        # differently in the modification (e.g. abbreviation mismatch).
        # Generation still proceeds; the gap is visible in the UI.
        out.append(PatchDiagnostic(
            severity="warning", category="kpi_uncovered",
            message=(
                f"KPI '{kpi}' is neither targeted by any modification nor "
                f"explicitly listed in unresolved_kpis. The scenario will be "
                f"generated without a grounded change for this KPI."
            ),
            element=kpi,
        ))

    # Non-fatal: KPIs have no expected impact listed
    for kpi in targeted:
        if kpi not in impacts:
            out.append(PatchDiagnostic(
                severity="warning", category="missing_impact",
                message=(
                    f"KPI '{kpi}' is targeted by modifications but has no "
                    f"entry in expected_kpi_impacts."
                ),
                element=kpi,
            ))
    return out


# -------------------------------------------------------------------
# Baseline faithfulness (reuses merger checks)
# -------------------------------------------------------------------

def _check_baseline_faithfulness(
    patch: ScenarioPatch, baseline: SimuBridgeScenario,
) -> list[PatchDiagnostic]:
    out: list[PatchDiagnostic] = []
    for i, mod in enumerate(patch.modifications, start=1):
        # For role-targeting parameter types we always run the existence
        # check — even for ADD_NEW, because the repair step has already
        # changed ADD_NEW to INCREASE for roles that exist.  A surviving
        # ADD_NEW here means the role is genuinely absent, which is an error.
        check_exists = (
            mod.parameter_type in _ROLE_PARAM_TYPES
            or mod.direction != ModificationDirection.ADD_NEW
        )
        if check_exists:
            exists = _check_target_exists(baseline, mod, i)
            if exists is not None:
                if mod.parameter_type in _ROLE_PARAM_TYPES:
                    # Keep as error and include available role names so the
                    # LLM's retry prompt is actionable.
                    available = [r.id for r in baseline.resourceParameters.roles]
                    available_str = ", ".join(available[:15])
                    if len(available) > 15:
                        available_str += f" … (+{len(available) - 15} more)"
                    out.append(PatchDiagnostic(
                        severity="error",
                        category=exists.category,
                        message=(
                            f"'{mod.target_element}' is not a role in the SIMOD "
                            f"baseline. You MUST use one of the actual role names "
                            f"from the baseline: [{available_str}]. "
                            f"Do NOT invent conceptual role names."
                        ),
                        modification_index=i,
                        element=mod.target_element,
                    ))
                else:
                    # Non-role targets: downgrade to warning — the merger skips
                    # them gracefully in tolerant mode.
                    out.append(PatchDiagnostic(
                        severity="warning",
                        category=exists.category,
                        message=(
                            exists.message
                            + " — this modification will be skipped; other "
                            "changes will still be applied."
                        ),
                        modification_index=exists.modification_index,
                        element=exists.element,
                    ))
                continue  # no point checking value if element missing
        val_issue = _check_baseline_value_match(baseline, mod, i)
        if val_issue is not None:
            out.append(val_issue)
    return out


# -------------------------------------------------------------------
# Pre-validation repair: redirect individual resource names to roles
# -------------------------------------------------------------------

_ROLE_PARAM_TYPES = {
    PatchParameterType.RESOURCE_COUNT,
    PatchParameterType.RESOURCE_COST,
}


def repair_patch_against_baseline(
    patch: ScenarioPatch,
    baseline: SimuBridgeScenario,
) -> tuple[ScenarioPatch, list[str]]:
    """Auto-repair common LLM mistakes before validation.

    Currently handles one case: the LLM sets ``target_element`` to an
    individual resource name (e.g. "Alberto Duport") for a
    ``resource_count`` or ``resource_cost`` modification, when the
    merger expects the *role* name/id that the resource belongs to.

    If the target does not match any role but does match a resource
    inside some role, the target is silently redirected to that role.
    """
    notes: list[str] = []

    # Build index: lowercased resource id/name -> parent role
    resource_to_role: dict[str, Any] = {}
    for role in baseline.resourceParameters.roles:
        for resource in role.resources:
            for attr in ("id", "name"):
                val = getattr(resource, attr, None)
                if val:
                    resource_to_role[str(val).lower()] = role

    roles = baseline.resourceParameters.roles

    for mod in patch.modifications:
        if mod.parameter_type not in _ROLE_PARAM_TYPES:
            continue
        # Already resolves to a known role — check for ADD_NEW direction fix.
        resolved_role = _resolve(roles, mod.target_element)
        if resolved_role is not None:
            # Role exists but direction is ADD_NEW: correct it to INCREASE
            # so the merger won't skip the existence check.
            if (
                mod.parameter_type == PatchParameterType.RESOURCE_COUNT
                and mod.direction == ModificationDirection.ADD_NEW
            ):
                mod.direction = ModificationDirection.INCREASE
                notes.append(
                    f"Auto-repaired: changed direction for resource_count "
                    f"on existing role '{mod.target_element}' from "
                    f"'add_new' to 'increase'."
                )
            continue
        # Check if it matches an individual resource.
        parent_role = resource_to_role.get(mod.target_element.lower())
        if parent_role is not None:
            old_name = mod.target_element
            mod.target_element = parent_role.id
            notes.append(
                f"Auto-repaired: redirected {mod.parameter_type.value} "
                f"target from individual resource '{old_name}' to "
                f"parent role '{parent_role.id}'."
            )

    return patch, notes


def enforce_headcount_constraints(
    patch: "ScenarioPatch",
    baseline: "SimuBridgeScenario",
    context_summary: Any,
) -> tuple["ScenarioPatch", list[str]]:
    """Deterministically cap resource_count modifications to user-stated limits.

    Runs two passes:
    1. Per-role cap — each modification is capped to the effective max
       headcount derived from ``max_headcount`` / ``max_additional`` in the
       context summary.
    2. Global cap — the total additions across ALL modifications are capped
       to ``calendar_constraints.total_max_additional_resources`` if set.

    Modifications reduced to a no-op (proposed == baseline) are removed and
    their KPI is moved to ``unresolved_kpis`` so coverage is not silently lost.
    """
    from second_llm.output_schema import UnresolvedKPI  # local to avoid circular

    notes: list[str] = []
    if context_summary is None or getattr(context_summary, "is_empty", True):
        return patch, notes

    def _parse_int(s: str) -> int | None:
        try:
            return int(round(float(str(s).strip())))
        except (ValueError, TypeError):
            return None

    def _drop_noop_and_mark_unresolved(mod_index: int, reason: str) -> None:
        mod = patch.modifications[mod_index]
        kpi = mod.kpi_reference
        patch.modifications.pop(mod_index)
        # Only move to unresolved if no other modification covers the same KPI
        still_covered = any(m.kpi_reference == kpi for m in patch.modifications)
        if not still_covered and kpi:
            already = any(u.kpi_name == kpi for u in patch.unresolved_kpis)
            if not already:
                patch.unresolved_kpis.append(UnresolvedKPI(
                    kpi_name=kpi,
                    reason="blocked_by_operational_constraint",
                    explanation=reason,
                ))

    # --- Pass 1: per-role cap ---
    i = 0
    while i < len(patch.modifications):
        mod = patch.modifications[i]
        if mod.parameter_type != PatchParameterType.RESOURCE_COUNT:
            i += 1
            continue
        current = _parse_int(mod.baseline_value)
        proposed = _parse_int(mod.proposed_value)
        if current is None or proposed is None or proposed <= current:
            i += 1
            continue
        effective_max = context_summary.get_effective_max_headcount(
            mod.target_element, current_count=current,
        )
        if effective_max is not None and proposed > effective_max:
            if effective_max <= current:
                reason = (
                    f"User constraint: '{mod.target_element}' headcount "
                    f"already at or above the stated limit ({effective_max})."
                )
                notes.append(
                    f"Removed resource_count mod for '{mod.target_element}': "
                    f"effective max {effective_max} <= current {current}."
                )
                _drop_noop_and_mark_unresolved(i, reason)
                # don't increment — list shifted
            else:
                notes.append(
                    f"Capped '{mod.target_element}' from {proposed} to "
                    f"{effective_max} (user constraint)."
                )
                mod.proposed_value = str(effective_max)
                i += 1
        else:
            i += 1

    # --- Pass 2: global total-additional cap ---
    global_max = context_summary.get_global_max_additional()
    if global_max is not None:
        total_added = sum(
            max(0, (_parse_int(m.proposed_value) or 0) - (_parse_int(m.baseline_value) or 0))
            for m in patch.modifications
            if m.parameter_type == PatchParameterType.RESOURCE_COUNT
        )
        if total_added > global_max:
            excess = total_added - global_max
            # Trim from the end of the list (smallest-priority first)
            j = len(patch.modifications) - 1
            while j >= 0 and excess > 0:
                mod = patch.modifications[j]
                if mod.parameter_type != PatchParameterType.RESOURCE_COUNT:
                    j -= 1
                    continue
                cur = _parse_int(mod.baseline_value) or 0
                prop = _parse_int(mod.proposed_value) or 0
                delta = prop - cur
                if delta <= 0:
                    j -= 1
                    continue
                trim = min(delta, excess)
                new_prop = prop - trim
                excess -= trim
                if new_prop <= cur:
                    reason = (
                        f"Global headcount budget exhausted: "
                        f"total additional resources capped at {global_max}."
                    )
                    notes.append(
                        f"Removed resource_count mod for '{mod.target_element}' "
                        f"(global cap of {global_max} total additions reached)."
                    )
                    _drop_noop_and_mark_unresolved(j, reason)
                    # don't decrement — list shifted, j now points to next item
                else:
                    notes.append(
                        f"Trimmed '{mod.target_element}' from {prop} to {new_prop} "
                        f"to stay within global max_additional={global_max}."
                    )
                    mod.proposed_value = str(new_prop)
                    j -= 1

    return patch, notes


# -------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------

def validate_patch(
    patch: ScenarioPatch,
    baseline: SimuBridgeScenario | None = None,
    declared_kpis: list[str] | None = None,
) -> PatchValidationResult:
    """Validate a :class:`ScenarioPatch` before merging.

    Parameters
    ----------
    patch:
        The LLM-produced patch.
    baseline:
        SimuBridge baseline scenario built from SIMOD. When supplied,
        enables target-existence and baseline-value checks. When omitted,
        only structural checks run.
    declared_kpis:
        Names of the KPIs coming from the first LLM (the ``kpis[*].name``
        list). When supplied, KPI coverage is enforced.

    Returns
    -------
    PatchValidationResult
        Structured diagnostics. Errors block merging in strict mode and
        feed the retry loop.
    """
    result = PatchValidationResult()

    # 1. Conflicts (merger's conflict detector works on the patch alone).
    result.diagnostics.extend(_detect_conflicts(patch))

    # 2. Per-modification grounding + direction.
    for i, mod in enumerate(patch.modifications, start=1):
        g = _check_grounding(mod, i)
        if g is not None:
            result.diagnostics.append(g)
        d = _check_direction_consistency(mod, i)
        if d is not None:
            result.diagnostics.append(d)

    # 3. KPI coverage.
    if declared_kpis is not None:
        result.diagnostics.extend(_check_kpi_coverage(patch, declared_kpis))

    # 4. Baseline faithfulness (only if baseline is available).
    if baseline is not None:
        result.diagnostics.extend(
            _check_baseline_faithfulness(patch, baseline),
        )

    return result

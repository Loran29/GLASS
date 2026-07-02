"""Deterministic patch application: baseline + ScenarioPatch -> merged scenario.

This is the heart of the delta-based architecture. The LLM produces a
:class:`~second_llm.output_schema_patch.ScenarioPatch` describing *what
should change*; this module takes the SIMOD-derived
:class:`~second_llm.output_schema.SimuBridgeScenario` baseline and
produces the final merged scenario that the simulator will consume.

Design
------
The merger is intentionally dumb and deterministic:

  1. It deep-copies the baseline so the input is never mutated.
  2. For each modification it resolves the target element in the baseline,
     checks that the element kind matches the parameter type, (optionally)
     verifies that the quoted ``baseline_value`` is consistent with what
     we actually find, and then applies the change.
  3. All findings are collected in a structured diagnostics object.
  4. In strict mode any ``error``-level diagnostic aborts that modification
     (and, for the whole patch, makes the merger return ``None`` for the
     scenario so callers can surface the failure). In tolerant mode the
     offending modification is skipped and recorded.

Unchanged baseline fields are preserved automatically — there is no
step where the LLM gets a chance to rewrite them.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from typing import Any

from second_llm.output_schema import (
    Activity,
    DistributionParameter,
    DistributionType,
    Gateway,
    ModificationDirection,
    Resource,
    Role,
    SimuBridgeScenario,
    StartEvent,
    TimeDistribution,
    TimeUnit,
    Timetable,
    TimetableItem,
    Weekday,
)
from second_llm.output_schema_patch import (
    PatchDiagnostic,
    PatchModification,
    PatchParameterType,
    PatchTargetKind,
    ScenarioPatch,
    expected_target_kind,
)


# -------------------------------------------------------------------
# Result container
# -------------------------------------------------------------------

@dataclass
class MergeResult:
    """Outcome of merging a :class:`ScenarioPatch` onto a baseline."""

    scenario: SimuBridgeScenario | None = None
    diagnostics: list[PatchDiagnostic] = field(default_factory=list)
    applied_modifications: list[int] = field(default_factory=list)
    skipped_modifications: list[int] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(d.severity == "error" for d in self.diagnostics)

    @property
    def error_messages(self) -> list[str]:
        return [d.message for d in self.diagnostics if d.severity == "error"]

    @property
    def warning_messages(self) -> list[str]:
        return [d.message for d in self.diagnostics if d.severity == "warning"]


# -------------------------------------------------------------------
# Utilities
# -------------------------------------------------------------------

_NUMBER_RE = re.compile(r"[-+]?\d*\.?\d+")


def _first_number(text: str) -> float | None:
    if not isinstance(text, str):
        return None
    m = _NUMBER_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _index_by_name(items: list[Any]) -> dict[str, Any]:
    """Index by both ``id`` and ``name`` (lowercased) for forgiving lookup."""
    out: dict[str, Any] = {}
    for it in items:
        for attr in ("id", "name"):
            val = getattr(it, attr, None)
            if val:
                out[str(val).lower()] = it
    return out


def _resolve(items: list[Any], needle: str) -> Any | None:
    if not needle:
        return None
    return _index_by_name(items).get(needle.lower())


# -------------------------------------------------------------------
# Individual appliers
# -------------------------------------------------------------------

_STRUCT_DURATION_KEYS = {"distributionType", "timeUnit", "values"}


def _distribution_from_payload(
    payload: Any,
    fallback_unit: TimeUnit,
    proposed_scalar: str,
) -> TimeDistribution | None:
    """Build a :class:`TimeDistribution` from a structured payload or scalar."""
    if isinstance(payload, dict) and _STRUCT_DURATION_KEYS & payload.keys():
        try:
            return TimeDistribution.model_validate(payload)
        except Exception:
            return None

    # Scalar -> constant distribution in the existing unit.
    num = _first_number(proposed_scalar)
    if num is None:
        return None
    return TimeDistribution(
        distributionType=DistributionType.CONSTANT,
        timeUnit=fallback_unit,
        values=[DistributionParameter(id="constantValue", value=num)],
    )


def _apply_activity_duration(
    scenario: SimuBridgeScenario,
    mod: PatchModification,
    diag: list[PatchDiagnostic],
    idx: int,
) -> bool:
    activities = [a for m in scenario.models for a in m.modelParameter.activities]
    act = _resolve(activities, mod.target_element)
    if act is None:
        diag.append(PatchDiagnostic(
            severity="error", category="missing_element",
            message=(
                f"Activity '{mod.target_element}' not found in baseline — "
                f"cannot apply activity_duration modification."
            ),
            modification_index=idx, element=mod.target_element,
        ))
        return False

    fallback_unit = act.duration.timeUnit
    new_dist = _distribution_from_payload(
        mod.proposed_structured, fallback_unit, mod.proposed_value,
    )
    if new_dist is None:
        diag.append(PatchDiagnostic(
            severity="error", category="invalid_value",
            message=(
                f"Activity '{mod.target_element}': could not parse a valid "
                f"duration from proposed_value='{mod.proposed_value}' or "
                f"proposed_structured."
            ),
            modification_index=idx, element=mod.target_element,
        ))
        return False

    act.duration = new_dist
    return True


def _apply_inter_arrival_time(
    scenario: SimuBridgeScenario,
    mod: PatchModification,
    diag: list[PatchDiagnostic],
    idx: int,
) -> bool:
    events = [e for m in scenario.models for e in m.modelParameter.events]
    if not events:
        diag.append(PatchDiagnostic(
            severity="error", category="missing_element",
            message="No start events in baseline — cannot apply inter_arrival_time modification.",
            modification_index=idx, element=mod.target_element,
        ))
        return False

    event = _resolve(events, mod.target_element) or events[0]
    fallback_unit = event.interArrivalTime.timeUnit
    new_dist = _distribution_from_payload(
        mod.proposed_structured, fallback_unit, mod.proposed_value,
    )
    if new_dist is None:
        diag.append(PatchDiagnostic(
            severity="error", category="invalid_value",
            message=(
                f"Start event '{event.id}': could not parse proposed "
                f"inter-arrival time from '{mod.proposed_value}'."
            ),
            modification_index=idx, element=event.id,
        ))
        return False

    event.interArrivalTime = new_dist
    return True


def _apply_gateway_probabilities(
    scenario: SimuBridgeScenario,
    mod: PatchModification,
    diag: list[PatchDiagnostic],
    idx: int,
    element_name_map: dict[str, str] | None = None,
) -> bool:
    gateways = [g for m in scenario.models for g in m.modelParameter.gateways]
    gw = _resolve(gateways, mod.target_element)
    if gw is None:
        diag.append(PatchDiagnostic(
            severity="error", category="missing_element",
            message=f"Gateway '{mod.target_element}' not found in baseline.",
            modification_index=idx, element=mod.target_element,
        ))
        return False

    new_probs: dict[str, float] | None = None
    if isinstance(mod.proposed_structured, dict):
        raw = mod.proposed_structured.get("probabilities", mod.proposed_structured)
        if isinstance(raw, dict):
            try:
                new_probs = {str(k): float(v) for k, v in raw.items()}
            except (TypeError, ValueError):
                new_probs = None

    # Fallback: scalar proposed_value like "approve=0.8, reject=0.2"
    if new_probs is None:
        kv_matches = re.findall(
            r"([A-Za-z0-9_\-]+)\s*[:=]\s*([-+]?\d*\.?\d+)", mod.proposed_value,
        )
        if kv_matches:
            try:
                new_probs = {k: float(v) for k, v in kv_matches}
            except ValueError:
                new_probs = None

    if not new_probs:
        diag.append(PatchDiagnostic(
            severity="error", category="invalid_value",
            message=(
                f"Gateway '{mod.target_element}': proposed_value "
                f"'{mod.proposed_value}' is not a parseable probability map."
            ),
            modification_index=idx, element=mod.target_element,
        ))
        return False

    # The existing gateway's keys define the legitimate outgoing flows.
    unknown = set(new_probs) - set(gw.probabilities)
    if unknown:
        nm = element_name_map or {}
        readable = sorted(nm.get(fid, fid) for fid in unknown)
        diag.append(PatchDiagnostic(
            severity="warning", category="unknown_flow",
            message=(
                f"Gateway '{mod.target_element}': proposed probabilities "
                f"reference unknown flow(s) {readable} — ignored."
            ),
            modification_index=idx, element=mod.target_element,
        ))
        for u in unknown:
            new_probs.pop(u, None)

    # Fill missing keys with their current baseline value so Pydantic's
    # sum-to-one invariant stands a chance.
    for key, val in gw.probabilities.items():
        new_probs.setdefault(key, val)

    total = sum(new_probs.values())
    if total <= 0:
        diag.append(PatchDiagnostic(
            severity="error", category="invalid_value",
            message=f"Gateway '{mod.target_element}': probabilities sum to {total}.",
            modification_index=idx, element=mod.target_element,
        ))
        return False
    if abs(total - 1.0) > 0.01:
        new_probs = {k: v / total for k, v in new_probs.items()}

    try:
        gw.probabilities = new_probs
        # Re-run the Pydantic model validator to enforce invariants.
        Gateway.model_validate(gw.model_dump())
    except Exception as exc:
        diag.append(PatchDiagnostic(
            severity="error", category="invalid_value",
            message=f"Gateway '{mod.target_element}': invalid probabilities ({exc}).",
            modification_index=idx, element=mod.target_element,
        ))
        return False
    return True


def _apply_resource_count(
    scenario: SimuBridgeScenario,
    mod: PatchModification,
    diag: list[PatchDiagnostic],
    idx: int,
) -> bool:
    role = _resolve(scenario.resourceParameters.roles, mod.target_element)
    if role is None:
        if mod.direction == ModificationDirection.ADD_NEW:
            diag.append(PatchDiagnostic(
                severity="error", category="missing_element",
                message=(
                    f"ADD_NEW resource_count on '{mod.target_element}' requires "
                    f"an existing baseline role — greenfield role creation is "
                    f"not supported by the merger."
                ),
                modification_index=idx, element=mod.target_element,
            ))
        else:
            diag.append(PatchDiagnostic(
                severity="error", category="missing_element",
                message=f"Role '{mod.target_element}' not found in baseline.",
                modification_index=idx, element=mod.target_element,
            ))
        return False

    proposed_n = _first_number(mod.proposed_value)
    if proposed_n is None and isinstance(mod.proposed_structured, dict):
        raw = mod.proposed_structured.get("count")
        proposed_n = float(raw) if raw is not None else None

    if proposed_n is None:
        diag.append(PatchDiagnostic(
            severity="error", category="invalid_value",
            message=(
                f"Role '{mod.target_element}': cannot parse integer count "
                f"from proposed_value='{mod.proposed_value}'."
            ),
            modification_index=idx, element=mod.target_element,
        ))
        return False

    target_count = max(1, int(round(proposed_n)))
    current = list(role.resources)
    current_count = len(current)

    if target_count == current_count:
        diag.append(PatchDiagnostic(
            severity="warning", category="no_op",
            message=(
                f"Role '{mod.target_element}': proposed count equals baseline "
                f"({target_count}) — modification is a no-op."
            ),
            modification_index=idx, element=mod.target_element,
        ))
        return False

    if target_count < current_count:
        role.resources = current[:target_count]
    else:
        additions: list[Resource] = []
        for i in range(current_count, target_count):
            additions.append(Resource(id=f"{role.id}_{i + 1}"))
        role.resources = current + additions
    return True


def _apply_resource_cost(
    scenario: SimuBridgeScenario,
    mod: PatchModification,
    diag: list[PatchDiagnostic],
    idx: int,
) -> bool:
    role = _resolve(scenario.resourceParameters.roles, mod.target_element)
    if role is None:
        diag.append(PatchDiagnostic(
            severity="error", category="missing_element",
            message=f"Role '{mod.target_element}' not found in baseline.",
            modification_index=idx, element=mod.target_element,
        ))
        return False
    new_cost = _first_number(mod.proposed_value)
    if new_cost is None or new_cost < 0:
        diag.append(PatchDiagnostic(
            severity="error", category="invalid_value",
            message=(
                f"Role '{mod.target_element}': proposed cost "
                f"'{mod.proposed_value}' is not a non-negative number."
            ),
            modification_index=idx, element=mod.target_element,
        ))
        return False
    if math.isclose(role.costHour, new_cost, rel_tol=1e-9, abs_tol=1e-9):
        diag.append(PatchDiagnostic(
            severity="warning", category="no_op",
            message=f"Role '{mod.target_element}': cost already at {new_cost}.",
            modification_index=idx, element=mod.target_element,
        ))
        return False
    role.costHour = new_cost
    return True


def _apply_resource_calendar(
    scenario: SimuBridgeScenario,
    mod: PatchModification,
    diag: list[PatchDiagnostic],
    idx: int,
) -> bool:
    tt = _resolve(scenario.resourceParameters.timeTables, mod.target_element)
    if tt is None:
        diag.append(PatchDiagnostic(
            severity="error", category="missing_element",
            message=f"Timetable '{mod.target_element}' not found in baseline.",
            modification_index=idx, element=mod.target_element,
        ))
        return False

    items_payload: list[Any] | None = None
    if isinstance(mod.proposed_structured, dict):
        raw = mod.proposed_structured.get("timeTableItems") or mod.proposed_structured.get("items")
        if isinstance(raw, list):
            items_payload = raw
    if items_payload is None:
        diag.append(PatchDiagnostic(
            severity="error", category="invalid_value",
            message=(
                f"Timetable '{mod.target_element}': resource_calendar "
                f"modifications require proposed_structured.timeTableItems "
                f"as a list of TimetableItem dicts."
            ),
            modification_index=idx, element=mod.target_element,
        ))
        return False
    try:
        new_items = [TimetableItem.model_validate(it) for it in items_payload]
        if not new_items:
            raise ValueError("timeTableItems cannot be empty")
    except Exception as exc:
        diag.append(PatchDiagnostic(
            severity="error", category="invalid_value",
            message=f"Timetable '{mod.target_element}': invalid timeTableItems ({exc}).",
            modification_index=idx, element=mod.target_element,
        ))
        return False
    tt.timeTableItems = new_items
    return True


def _apply_resource_activity_assignment(
    scenario: SimuBridgeScenario,
    mod: PatchModification,
    diag: list[PatchDiagnostic],
    idx: int,
) -> bool:
    activities = [a for m in scenario.models for a in m.modelParameter.activities]
    act = _resolve(activities, mod.target_element)
    if act is None:
        diag.append(PatchDiagnostic(
            severity="error", category="missing_element",
            message=f"Activity '{mod.target_element}' not found in baseline.",
            modification_index=idx, element=mod.target_element,
        ))
        return False

    role_ids = {r.id for r in scenario.resourceParameters.roles}

    new_roles_raw: list[str] | None = None
    if isinstance(mod.proposed_structured, dict):
        raw = mod.proposed_structured.get("roles") or mod.proposed_structured.get("resources")
        if isinstance(raw, list):
            new_roles_raw = [str(x) for x in raw]
    if new_roles_raw is None and mod.proposed_value:
        new_roles_raw = [
            r.strip() for r in re.split(r"[,;]", mod.proposed_value) if r.strip()
        ]

    if not new_roles_raw:
        diag.append(PatchDiagnostic(
            severity="error", category="invalid_value",
            message=(
                f"Activity '{mod.target_element}': cannot parse roles from "
                f"proposed_value='{mod.proposed_value}'."
            ),
            modification_index=idx, element=mod.target_element,
        ))
        return False

    unknown = [r for r in new_roles_raw if r not in role_ids]
    if unknown:
        diag.append(PatchDiagnostic(
            severity="error", category="missing_element",
            message=(
                f"Activity '{mod.target_element}': roles {unknown} do not "
                f"exist in the baseline."
            ),
            modification_index=idx, element=mod.target_element,
        ))
        return False

    act.resources = new_roles_raw
    return True


# Dispatch table --------------------------------------------------------

_APPLIERS = {
    PatchParameterType.ACTIVITY_DURATION: _apply_activity_duration,
    PatchParameterType.INTER_ARRIVAL_TIME: _apply_inter_arrival_time,
    PatchParameterType.GATEWAY_PROBABILITIES: _apply_gateway_probabilities,
    PatchParameterType.RESOURCE_COUNT: _apply_resource_count,
    PatchParameterType.RESOURCE_COST: _apply_resource_cost,
    PatchParameterType.RESOURCE_CALENDAR: _apply_resource_calendar,
    PatchParameterType.RESOURCE_ACTIVITY_ASSIGNMENT: _apply_resource_activity_assignment,
}


# -------------------------------------------------------------------
# Pre-flight: baseline faithfulness checks
# -------------------------------------------------------------------

def _check_target_exists(
    scenario: SimuBridgeScenario,
    mod: PatchModification,
    idx: int,
) -> PatchDiagnostic | None:
    kind = expected_target_kind(mod.parameter_type)
    if mod.direction == ModificationDirection.DIFFERENTIATE:
        # Segment-derived elements are allowed to be new as long as the
        # original baseline element exists under the same prefix (see
        # prompt rule 2f). We only require *some* baseline match.
        baseline_prefix = mod.target_element.split("_", 1)[0]
        return _check_element_kind(scenario, baseline_prefix, kind, idx)
    return _check_element_kind(scenario, mod.target_element, kind, idx)


def _check_element_kind(
    scenario: SimuBridgeScenario,
    name: str,
    kind: PatchTargetKind,
    idx: int,
) -> PatchDiagnostic | None:
    if kind == PatchTargetKind.ACTIVITY:
        pool = [a for m in scenario.models for a in m.modelParameter.activities]
    elif kind == PatchTargetKind.GATEWAY:
        pool = [g for m in scenario.models for g in m.modelParameter.gateways]
    elif kind == PatchTargetKind.START_EVENT:
        pool = [e for m in scenario.models for e in m.modelParameter.events]
    elif kind == PatchTargetKind.ROLE:
        pool = list(scenario.resourceParameters.roles)
    elif kind == PatchTargetKind.TIMETABLE:
        pool = list(scenario.resourceParameters.timeTables)
    else:
        pool = []

    if _resolve(pool, name) is None:
        # Build a hint: list the actual IDs/names the LLM could use instead.
        hint = ""
        if pool and kind == PatchTargetKind.ACTIVITY:
            candidates = [
                f"{a.name} ({a.id})" if a.name and a.name != a.id else a.id
                for a in pool[:6]
            ]
            hint = f" Known activities (first 6): {candidates}."
        return PatchDiagnostic(
            severity="warning", category="missing_element",
            message=(
                f"Target '{name}' of kind '{kind.value}' does not exist in "
                f"the SIMOD baseline — this modification will be skipped; "
                f"other changes will still be applied.{hint}"
            ),
            modification_index=idx, element=name,
        )
    return None


def _check_baseline_value_match(
    scenario: SimuBridgeScenario,
    mod: PatchModification,
    idx: int,
) -> PatchDiagnostic | None:
    """Compare the LLM's quoted baseline_value to the actual baseline.

    Emits a ``warning`` (not an error) on numeric disagreement so the
    merge can proceed in tolerant mode; strict mode escalates.
    """
    numeric_quoted = _first_number(mod.baseline_value)
    if numeric_quoted is None:
        return None

    actual_numeric: float | None = None
    if mod.parameter_type == PatchParameterType.RESOURCE_COUNT:
        role = _resolve(scenario.resourceParameters.roles, mod.target_element)
        if role is not None:
            actual_numeric = float(len(role.resources))
    elif mod.parameter_type == PatchParameterType.RESOURCE_COST:
        role = _resolve(scenario.resourceParameters.roles, mod.target_element)
        if role is not None:
            actual_numeric = float(role.costHour)
    elif mod.parameter_type == PatchParameterType.ACTIVITY_DURATION:
        act = _resolve(
            [a for m in scenario.models for a in m.modelParameter.activities],
            mod.target_element,
        )
        if act is not None:
            actual_numeric = _distribution_mean(act.duration)
    elif mod.parameter_type == PatchParameterType.INTER_ARRIVAL_TIME:
        events = [e for m in scenario.models for e in m.modelParameter.events]
        event = _resolve(events, mod.target_element) or (events[0] if events else None)
        if event is not None:
            actual_numeric = _distribution_mean(event.interArrivalTime)

    if actual_numeric is None:
        return None

    tol = max(1e-6, abs(actual_numeric) * 0.05)
    if abs(actual_numeric - numeric_quoted) > tol:
        return PatchDiagnostic(
            severity="warning", category="baseline_value_mismatch",
            message=(
                f"Modification on '{mod.target_element}': quoted baseline_value "
                f"{numeric_quoted} disagrees with actual baseline "
                f"{actual_numeric:.4g} (>5% tolerance)."
            ),
            modification_index=idx, element=mod.target_element,
        )
    return None


def _distribution_mean(dist: TimeDistribution) -> float | None:
    """Return a single representative value for a distribution (for comparison)."""
    params = {p.id: p.value for p in dist.values}
    if "mean" in params:
        return params["mean"]
    if "constantValue" in params:
        return params["constantValue"]
    if "lower" in params and "upper" in params:
        return (params["lower"] + params["upper"]) / 2.0
    if "peak" in params:
        return params["peak"]
    return None


def _detect_conflicts(
    patch: ScenarioPatch,
) -> list[PatchDiagnostic]:
    """Flag multiple modifications that touch the same element-field pair."""
    out: list[PatchDiagnostic] = []
    seen: dict[tuple[str, str], int] = {}
    for i, mod in enumerate(patch.modifications, start=1):
        key = (
            mod.target_element.lower().strip(),
            mod.parameter_type.value,
        )
        if key in seen:
            out.append(PatchDiagnostic(
                severity="error", category="conflicting_modifications",
                message=(
                    f"Modifications #{seen[key]} and #{i} both target "
                    f"({mod.target_element}, {mod.parameter_type.value}). "
                    f"Consolidate into a single modification."
                ),
                modification_index=i, element=mod.target_element,
            ))
        else:
            seen[key] = i
    return out


# -------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------

def apply_patch(
    baseline: SimuBridgeScenario,
    patch: ScenarioPatch,
    *,
    strict: bool = False,
    element_name_map: dict[str, str] | None = None,
) -> MergeResult:
    """Apply ``patch`` onto a deep copy of ``baseline``.

    Parameters
    ----------
    baseline:
        Deterministically-built SimuBridge scenario derived from SIMOD.
    patch:
        LLM-produced :class:`ScenarioPatch` describing the delta.
    strict:
        When ``True``, any error-level diagnostic aborts the merge and
        the returned :class:`MergeResult` has ``scenario=None``. When
        ``False`` (default), the merger skips the offending modification
        and continues — each skip is recorded in ``skipped_modifications``
        and in ``diagnostics``.

    Returns
    -------
    MergeResult
        The merged scenario (or ``None`` in strict-failure mode) plus
        structured diagnostics for UI and logging.
    """
    result = MergeResult()

    # Deep copy so callers can keep the original baseline untouched.
    scenario = copy.deepcopy(baseline)

    # 1. Pre-flight: detect conflicting modifications.
    conflicts = _detect_conflicts(patch)
    result.diagnostics.extend(conflicts)
    conflict_indices = {
        d.modification_index for d in conflicts if d.severity == "error"
    }
    if conflicts and strict:
        result.scenario = None
        return result

    # 2. Per-modification apply.
    for i, mod in enumerate(patch.modifications, start=1):
        if i in conflict_indices:
            result.skipped_modifications.append(i)
            continue

        # Existence check (skip for ADD_NEW which creates elements).
        if mod.direction not in (ModificationDirection.ADD_NEW,):
            exists_issue = _check_target_exists(scenario, mod, i)
            if exists_issue is not None:
                result.diagnostics.append(exists_issue)
                if strict:
                    result.skipped_modifications.append(i)
                    continue
                # Tolerant mode still cannot apply a change to a missing
                # element, so always skip.
                result.skipped_modifications.append(i)
                continue

        # Baseline value sanity.
        value_issue = _check_baseline_value_match(scenario, mod, i)
        if value_issue is not None:
            if strict:
                value_issue.severity = "error"
                result.diagnostics.append(value_issue)
                result.skipped_modifications.append(i)
                continue
            else:
                result.diagnostics.append(value_issue)

        # Dispatch.
        applier = _APPLIERS.get(mod.parameter_type)
        if applier is None:
            result.diagnostics.append(PatchDiagnostic(
                severity="error", category="unsupported_parameter_type",
                message=(
                    f"parameter_type '{mod.parameter_type.value}' has no "
                    f"registered applier."
                ),
                modification_index=i, element=mod.target_element,
            ))
            result.skipped_modifications.append(i)
            continue

        if mod.parameter_type == PatchParameterType.GATEWAY_PROBABILITIES:
            ok = _apply_gateway_probabilities(
                scenario, mod, result.diagnostics, i, element_name_map or {},
            )
        else:
            ok = applier(scenario, mod, result.diagnostics, i)
        if ok:
            result.applied_modifications.append(i)
        else:
            result.skipped_modifications.append(i)

    # 3. Final: re-validate the merged scenario under Pydantic rules to
    # catch cross-cutting invariants (e.g. gateway prob sums, role<->tt
    # references).
    try:
        SimuBridgeScenario.model_validate(scenario.model_dump())
    except Exception as exc:
        result.diagnostics.append(PatchDiagnostic(
            severity="error", category="post_merge_validation",
            message=f"Merged scenario fails Pydantic validation: {exc}",
        ))
        if strict:
            result.scenario = None
            return result

    result.scenario = scenario
    return result

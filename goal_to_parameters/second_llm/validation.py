"""Post-schema validation for ScenarioProposal outputs.

Runs AFTER Pydantic schema validation succeeds.  Checks two layers:

  1. **Constraint satisfaction** — value ranges, referential integrity
     between scenario elements, distribution parameter sanity.
  2. **Directional consistency** — do the proposed modifications
     actually move in the direction the LLM claims?

Returns a list of ``ValidationIssue`` objects.  Issues with
``severity="error"`` are fed back into the retry loop so the LLM can
fix them.  Issues with ``severity="warning"`` are surfaced to the user
but do not trigger retries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from second_llm.output_schema import (
    DistributionType,
    ModificationDirection,
    Resource,
    Role,
    ScenarioProposal,
    Timetable,
    TimetableItem,
    Weekday,
)


@dataclass
class ValidationIssue:
    """A single post-schema validation finding."""

    category: str  # "constraint" or "directional"
    severity: str  # "error" or "warning"
    message: str
    element: str = ""  # which element (activity, role, etc.)


@dataclass
class ValidationResult:
    """Collected results from post-schema validation."""

    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    def error_summary(self, max_chars: int = 1500) -> str:
        """Format errors for the retry prompt."""
        lines = [f"- [{i.category}] {i.message}" for i in self.errors]
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... (truncated)"
        return text


# ===================================================================
# 1. Constraint checks
# ===================================================================

def _check_distribution_value_ranges(proposal: ScenarioProposal) -> list[ValidationIssue]:
    """Check that distribution parameter values are in valid ranges."""
    issues: list[ValidationIssue] = []

    for model in proposal.scenario.models:
        # Activity durations
        for act in model.modelParameter.activities:
            for param in act.duration.values:
                if param.id == "mean" and param.value <= 0:
                    issues.append(ValidationIssue(
                        category="constraint",
                        severity="error",
                        message=(
                            f"Activity '{act.name or act.id}' has "
                            f"duration mean={param.value}, must be > 0."
                        ),
                        element=act.id,
                    ))
                if param.id == "variance" and param.value < 0:
                    issues.append(ValidationIssue(
                        category="constraint",
                        severity="error",
                        message=(
                            f"Activity '{act.name or act.id}' has "
                            f"duration variance={param.value}, must be >= 0."
                        ),
                        element=act.id,
                    ))
                if param.id == "constantValue" and param.value < 0:
                    issues.append(ValidationIssue(
                        category="constraint",
                        severity="warning",
                        message=(
                            f"Activity '{act.name or act.id}' has "
                            f"constantValue={param.value}, negative durations "
                            f"are unusual."
                        ),
                        element=act.id,
                    ))
                if param.id in ("lower", "upper") and param.value < 0:
                    issues.append(ValidationIssue(
                        category="constraint",
                        severity="warning",
                        message=(
                            f"Activity '{act.name or act.id}' has "
                            f"{param.id}={param.value}, negative bound is unusual."
                        ),
                        element=act.id,
                    ))

        # Inter-arrival times
        for event in model.modelParameter.events:
            for param in event.interArrivalTime.values:
                if param.id == "mean" and param.value <= 0:
                    issues.append(ValidationIssue(
                        category="constraint",
                        severity="error",
                        message=(
                            f"Start event '{event.id}' has "
                            f"inter-arrival mean={param.value}, must be > 0."
                        ),
                        element=event.id,
                    ))

    return issues


def _check_role_activity_references(proposal: ScenarioProposal) -> list[ValidationIssue]:
    """Check that activities reference valid roles and vice versa."""
    issues: list[ValidationIssue] = []
    role_ids = {r.id for r in proposal.scenario.resourceParameters.roles}

    for model in proposal.scenario.models:
        for act in model.modelParameter.activities:
            for res_ref in act.resources:
                if res_ref not in role_ids:
                    issues.append(ValidationIssue(
                        category="constraint",
                        severity="error",
                        message=(
                            f"Activity '{act.name or act.id}' references "
                            f"role '{res_ref}' which does not exist. "
                            f"Available roles: {sorted(role_ids)}"
                        ),
                        element=act.id,
                    ))

    return issues


def _check_uniform_bounds(proposal: ScenarioProposal) -> list[ValidationIssue]:
    """Check that uniform distributions have lower < upper."""
    issues: list[ValidationIssue] = []

    def _check_dist(dist, label: str):
        if dist.distributionType == DistributionType.UNIFORM:
            params = {p.id: p.value for p in dist.values}
            lower = params.get("lower")
            upper = params.get("upper")
            if lower is not None and upper is not None and lower >= upper:
                issues.append(ValidationIssue(
                    category="constraint",
                    severity="error",
                    message=(
                        f"{label}: uniform distribution has "
                        f"lower={lower} >= upper={upper}."
                    ),
                ))

    for model in proposal.scenario.models:
        for act in model.modelParameter.activities:
            _check_dist(act.duration, f"Activity '{act.name or act.id}'")
        for event in model.modelParameter.events:
            _check_dist(event.interArrivalTime, f"Event '{event.id}'")

    return issues


def _check_simulation_instances(proposal: ScenarioProposal) -> list[ValidationIssue]:
    """Warn if numberOfInstances is suspiciously low."""
    issues: list[ValidationIssue] = []
    if proposal.scenario.numberOfInstances < 100:
        issues.append(ValidationIssue(
            category="constraint",
            severity="warning",
            message=(
                f"numberOfInstances={proposal.scenario.numberOfInstances} "
                f"is very low. At least 100 is recommended for "
                f"statistically meaningful results."
            ),
        ))
    return issues


# ===================================================================
# 2. Directional consistency
# ===================================================================

_NUMBER_RE = re.compile(r"[-+]?\d*\.?\d+")


def _extract_first_number(text: str) -> float | None:
    """Try to extract the first numeric value from a string."""
    m = _NUMBER_RE.search(text)
    if m:
        try:
            return float(m.group())
        except ValueError:
            pass
    return None


def _extract_current_count(baseline_value: str) -> int | None:
    """Parse an integer resource count from a baseline_value string."""
    n = _extract_first_number(baseline_value or "")
    if n is not None:
        try:
            return int(round(n))
        except (ValueError, OverflowError):
            pass
    return None


def _check_directional_consistency(proposal: ScenarioProposal) -> list[ValidationIssue]:
    """Check that modification directions match the actual value changes.

    Only checks modifications where both baseline and proposed values
    can be parsed as numbers.  Skips redistributions, new additions,
    removals, and distribution type changes since those aren't simple
    numeric comparisons.
    """
    issues: list[ValidationIssue] = []
    skip_directions = {
        ModificationDirection.REDISTRIBUTE,
        ModificationDirection.ADD_NEW,
        ModificationDirection.REMOVE,
        ModificationDirection.CHANGE_DISTRIBUTION,
        ModificationDirection.DIFFERENTIATE,
    }

    for mod in proposal.modifications:
        if mod.direction in skip_directions:
            continue

        # Calendar values are timetable strings — first-number extraction
        # picks up hour digits (e.g. "12:00" → 12.0) not weekly hours,
        # so numeric comparison is meaningless.
        if mod.parameter_type == "resource_calendar":
            continue

        baseline_num = _extract_first_number(mod.baseline_value)
        proposed_num = _extract_first_number(mod.proposed_value)

        if baseline_num is None or proposed_num is None:
            continue

        if baseline_num == 0 and proposed_num == 0:
            continue

        if mod.direction == ModificationDirection.INCREASE:
            if proposed_num < baseline_num:
                issues.append(ValidationIssue(
                    category="directional",
                    severity="warning",
                    message=(
                        f"Modification on '{mod.target_element}' "
                        f"({mod.parameter_type}) claims direction='increase' "
                        f"but proposed ({proposed_num}) < baseline "
                        f"({baseline_num})."
                    ),
                    element=mod.target_element,
                ))
        elif mod.direction == ModificationDirection.DECREASE:
            if proposed_num > baseline_num:
                issues.append(ValidationIssue(
                    category="directional",
                    severity="warning",
                    message=(
                        f"Modification on '{mod.target_element}' "
                        f"({mod.parameter_type}) claims direction='decrease' "
                        f"but proposed ({proposed_num}) > baseline "
                        f"({baseline_num})."
                    ),
                    element=mod.target_element,
                ))

    return issues


def _check_kpi_impact_directions(proposal: ScenarioProposal) -> list[ValidationIssue]:
    """Cross-check KPI impact directions against modification directions.

    Only meaningful for parameter types that have a DIRECT relationship with
    time/volume KPIs (e.g. decreasing duration → decreasing cycle time).

    resource_count has an INVERSE relationship (more resources → less waiting),
    so it is excluded to avoid false-positive warnings.
    """
    issues: list[ValidationIssue] = []

    # Parameter types with an inverse effect on time KPIs — increasing them
    # reduces cycle/waiting time, so they must NOT be used in the direction check.
    _INVERSE_OR_INDIRECT = {"resource_count", "resource_cost", "resource_calendar"}

    # Build map: kpi_name → set of modification directions (direct params only)
    kpi_mod_dirs: dict[str, set[str]] = {}
    for mod in proposal.modifications:
        if mod.parameter_type in _INVERSE_OR_INDIRECT:
            continue
        kpi_mod_dirs.setdefault(mod.kpi_reference, set()).add(mod.direction.value)

    for impact in proposal.expected_kpi_impacts:
        mod_dirs = kpi_mod_dirs.get(impact.kpi_name, set())
        if not mod_dirs:
            continue

        if mod_dirs == {"decrease"} and impact.direction == "increase":
            issues.append(ValidationIssue(
                category="directional",
                severity="warning",
                message=(
                    f"KPI '{impact.kpi_name}': all modifications decrease "
                    f"parameters, but expected impact is 'increase'. "
                    f"Verify this is intentional (e.g. decreasing duration "
                    f"increases throughput)."
                ),
            ))
        elif mod_dirs == {"increase"} and impact.direction == "decrease":
            issues.append(ValidationIssue(
                category="directional",
                severity="warning",
                message=(
                    f"KPI '{impact.kpi_name}': all modifications increase "
                    f"parameters, but expected impact is 'decrease'. "
                    f"Verify this is intentional."
                ),
            ))

    return issues


# ===================================================================
# 3. Feasibility checks (against operational context)
# ===================================================================


def _check_fixed_staffing(
    proposal: ScenarioProposal,
    context_summary: Any,
) -> list[ValidationIssue]:
    """Flag modifications that add resources to roles the user declared fixed."""
    issues: list[ValidationIssue] = []
    if context_summary is None or context_summary.is_empty:
        return issues

    for mod in proposal.modifications:
        if mod.parameter_type != "resource_count":
            continue
        if mod.direction not in (
            ModificationDirection.INCREASE,
            ModificationDirection.ADD_NEW,
        ):
            continue

        if context_summary.is_role_fixed(mod.target_element):
            issues.append(ValidationIssue(
                category="feasibility",
                severity="warning",
                message=(
                    f"Modification on '{mod.target_element}' adds resources, "
                    f"but the user stated this role has fixed staffing. "
                    f"This change may not be feasible."
                ),
                element=mod.target_element,
            ))

        max_hc = context_summary.get_effective_max_headcount(
            mod.target_element,
            current_count=_extract_current_count(mod.baseline_value),
        )
        if max_hc is not None:
            try:
                proposed_count = int(float(mod.proposed_value))
            except (ValueError, TypeError):
                proposed_count = None
            if proposed_count is not None and proposed_count > max_hc:
                issues.append(ValidationIssue(
                    category="feasibility",
                    severity="error",
                    message=(
                        f"Modification increases '{mod.target_element}' to "
                        f"{proposed_count} resource(s), but the user stated "
                        f"the maximum allowed is {max_hc}. "
                        f"You MUST reduce proposed_value to at most {max_hc}."
                    ),
                    element=mod.target_element,
                ))

    return issues


def _check_immutable_elements(
    proposal: ScenarioProposal,
    context_summary: Any,
) -> list[ValidationIssue]:
    """Flag modifications that change elements the user declared immutable."""
    issues: list[ValidationIssue] = []
    if context_summary is None or context_summary.is_empty:
        return issues

    immutable = context_summary.get_immutable_elements()
    if not immutable:
        return issues

    for mod in proposal.modifications:
        target_lower = mod.target_element.lower()
        if target_lower in immutable:
            issues.append(ValidationIssue(
                category="feasibility",
                severity="error",
                message=(
                    f"Modification on '{mod.target_element}' "
                    f"({mod.parameter_type}) changes an element the user "
                    f"declared as immutable due to regulation or policy. "
                    f"You MUST remove this modification from your patch."
                ),
                element=mod.target_element,
            ))

    return issues


def _check_overtime_constraints(
    proposal: ScenarioProposal,
    context_summary: Any,
) -> list[ValidationIssue]:
    """Flag resource_calendar mods on roles where overtime is not available."""
    issues: list[ValidationIssue] = []
    if context_summary is None or context_summary.is_empty:
        return issues

    rc = getattr(context_summary, "resource_constraints", {}) or {}
    for mod in proposal.modifications:
        if mod.parameter_type != "resource_calendar":
            continue
        for role_name, role_info in rc.items():
            if role_name.lower() != mod.target_element.lower():
                continue
            overtime_available = role_info.get("overtime_available")
            if overtime_available is False:
                issues.append(ValidationIssue(
                    category="feasibility",
                    severity="error",
                    message=(
                        f"resource_calendar modification on '{mod.target_element}' "
                        f"is not permitted: the user stated overtime is NOT available "
                        f"for this role. You MUST remove this calendar modification."
                    ),
                    element=mod.target_element,
                ))
            break

    return issues


def _check_shift_extension(
    proposal: ScenarioProposal,
    context_summary: Any,
) -> list[ValidationIssue]:
    """Flag resource_calendar mods when global shift extension is not possible."""
    issues: list[ValidationIssue] = []
    if context_summary is None or context_summary.is_empty:
        return issues

    calendar_constraints = getattr(context_summary, "calendar_constraints", {}) or {}
    shift_ok = calendar_constraints.get("shift_extension_possible")
    if shift_ok is False:
        for mod in proposal.modifications:
            if mod.parameter_type == "resource_calendar":
                issues.append(ValidationIssue(
                    category="feasibility",
                    severity="error",
                    message=(
                        f"resource_calendar modification on '{mod.target_element}' "
                        f"is not permitted: the user stated shift extension is NOT "
                        f"possible in this organisation. You MUST remove this "
                        f"calendar modification."
                    ),
                    element=mod.target_element,
                ))

    return issues


_WEEKDAY_ORDER: list[Weekday] = [
    Weekday.MONDAY,
    Weekday.TUESDAY,
    Weekday.WEDNESDAY,
    Weekday.THURSDAY,
    Weekday.FRIDAY,
    Weekday.SATURDAY,
    Weekday.SUNDAY,
]

_MACHINE_HINTS: tuple[str, ...] = (
    "machine", "scanner", "robot", "server", "system", "automated",
    "automation", "device", "equipment", " ct", "ct_", "ct ", "mri",
    "x-ray", "xray", "x_ray", "kiosk", "atm", "conveyor", "printer",
)

_LABOR_NORM_STANDARD_HOURS = 40.0   # baseline working week without overtime
_LABOR_NORM_OVERTIME_CAP_HOURS = 48.0  # max with overtime per EU Working Time Directive

# How much overtime the auto-repair allows above the baseline calendar
# per role.  The repair keeps per-person hours <= baseline * (1 + slack).
_DEFAULT_OVERTIME_SLACK = 0.20

# Per-person weekly-hours floor for any human role whose baseline cannot
# be determined.  Ensures very-small-baseline roles are not repaired to
# absurdly tight schedules.
_DEFAULT_HUMAN_BASELINE_HOURS = 40.0
_DEFAULT_MACHINE_BASELINE_HOURS = 168.0


_SIMOD_DAY_MAP: dict[str, Weekday] = {
    "MONDAY": Weekday.MONDAY,
    "TUESDAY": Weekday.TUESDAY,
    "WEDNESDAY": Weekday.WEDNESDAY,
    "THURSDAY": Weekday.THURSDAY,
    "FRIDAY": Weekday.FRIDAY,
    "SATURDAY": Weekday.SATURDAY,
    "SUNDAY": Weekday.SUNDAY,
}


def _parse_simod_time(value: Any) -> float | None:
    """Parse ``"09:00:00.000"`` / ``"17:30"`` / ``9`` / ``9.5`` into hours-of-day."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        parts = value.strip().split(":")
        try:
            h = float(parts[0])
            m = float(parts[1]) if len(parts) > 1 else 0.0
            s = float(parts[2]) if len(parts) > 2 else 0.0
            return h + m / 60.0 + s / 3600.0
        except (ValueError, IndexError):
            return None
    return None


def _simod_period_hours(period: dict[str, Any]) -> float:
    """Weekly hours contributed by one SIMOD time_period block."""
    from_day = str(period.get("from", "")).upper()
    to_day = str(period.get("to", from_day)).upper()
    start_wd = _SIMOD_DAY_MAP.get(from_day)
    end_wd = _SIMOD_DAY_MAP.get(to_day)
    if start_wd is None or end_wd is None:
        return 0.0
    start_idx = _WEEKDAY_ORDER.index(start_wd)
    end_idx = _WEEKDAY_ORDER.index(end_wd)
    if end_idx < start_idx:
        return 0.0
    days = end_idx - start_idx + 1
    begin = _parse_simod_time(period.get("beginTime") or period.get("begin_time"))
    end = _parse_simod_time(period.get("endTime") or period.get("end_time"))
    if begin is None or end is None or end <= begin:
        return 0.0
    return days * (end - begin)


def extract_baseline_hours_from_simod(
    simod_dict: dict[str, Any] | None,
) -> dict[str, float]:
    """Return ``{role_name_or_id: baseline_weekly_hours}`` from SIMOD output.

    Reads ``resource_profiles`` and ``resource_calendars`` and computes
    the weekly availability implied by each role's baseline calendar.
    Empty dict if the SIMOD JSON is missing or in an unknown shape.

    The mapping is keyed by both the role's ``id`` and its ``name`` so
    lookups are robust to which identifier the second-LLM proposal uses.
    """
    if not isinstance(simod_dict, dict):
        return {}

    # Build calendar_id -> weekly_hours
    cal_hours: dict[str, float] = {}
    calendars = simod_dict.get("resource_calendars") or simod_dict.get("calendars") or []
    if isinstance(calendars, dict):
        calendars = [{"id": k, **v} for k, v in calendars.items() if isinstance(v, dict)]
    if isinstance(calendars, list):
        for cal in calendars:
            if not isinstance(cal, dict):
                continue
            cid = cal.get("id") or cal.get("name")
            if not cid:
                continue
            periods = cal.get("time_periods") or cal.get("timePeriods") or []
            total = 0.0
            if isinstance(periods, list):
                for p in periods:
                    if isinstance(p, dict):
                        total += _simod_period_hours(p)
            cal_hours[str(cid)] = total

    # Build role identifier -> baseline_weekly_hours
    out: dict[str, float] = {}
    profiles = simod_dict.get("resource_profiles") or {}
    if isinstance(profiles, dict):
        profiles = [{"id": k, **v} for k, v in profiles.items() if isinstance(v, dict)]
    if isinstance(profiles, list):
        for prof in profiles:
            if not isinstance(prof, dict):
                continue
            role_ids = {prof.get("id"), prof.get("name")}
            role_ids.discard(None)
            hours_candidates: list[float] = []
            res_list = prof.get("resource_list") or []
            if isinstance(res_list, list):
                for r in res_list:
                    if not isinstance(r, dict):
                        continue
                    cid = r.get("calendar") or r.get("calendar_id")
                    if cid and str(cid) in cal_hours:
                        hours_candidates.append(cal_hours[str(cid)])
            # Some SIMOD variants attach calendar directly on the profile.
            top_cid = prof.get("calendar") or prof.get("calendar_id")
            if top_cid and str(top_cid) in cal_hours:
                hours_candidates.append(cal_hours[str(top_cid)])
            if not hours_candidates:
                continue
            # Use the max, since a role pool inherits the broadest shift.
            weekly = max(hours_candidates)
            for rid in role_ids:
                if rid:
                    out[str(rid)] = weekly

    return out


def _per_role_cap_hours(
    role_id: str,
    baseline_by_role: dict[str, float],
    overtime_slack: float,
) -> float:
    """Per-person weekly-hours cap for a role."""
    is_machine = _looks_like_machine(role_id)
    baseline = baseline_by_role.get(role_id)
    if baseline is None or baseline <= 0:
        baseline = (
            _DEFAULT_MACHINE_BASELINE_HOURS if is_machine
            else _DEFAULT_HUMAN_BASELINE_HOURS
        )
    cap = baseline * (1.0 + overtime_slack)
    # Hard physical ceiling: machines can run 168h/week; humans are capped
    # at the EU Working Time Directive overtime limit (48h).
    max_cap = (
        _DEFAULT_MACHINE_BASELINE_HOURS if is_machine
        else _LABOR_NORM_OVERTIME_CAP_HOURS
    )
    return min(cap, max_cap)


_MAX_DAILY_HOURS = 10  # EU-style daily working-time ceiling incl. overtime


def _repair_timetable_to_cap(
    timetable: Timetable,
    cap_hours: float,
) -> tuple[Timetable, float]:
    """Return (repaired_timetable, new_weekly_hours) clamped to ``cap_hours``.

    Strategy: preserve the first item's daily start time, shrink the
    daily window to at most ``_MAX_DAILY_HOURS`` (realistic shift), then
    trim the weekday range so the resulting weekly hours fit the cap.
    Subsequent timetable items are dropped so the repaired schedule is
    unambiguous.
    """
    if not timetable.timeTableItems:
        return timetable, 0.0

    first = timetable.timeTableItems[0]
    start_idx = _WEEKDAY_ORDER.index(first.startWeekday)

    raw_hpd = max(1, first.endTime - first.startTime)
    hours_per_day = min(raw_hpd, _MAX_DAILY_HOURS)
    # Honour the cap even if the daily window alone would exceed it.
    hours_per_day = int(min(hours_per_day, max(1.0, cap_hours)))
    end_time = min(24, first.startTime + hours_per_day)

    max_days = max(1, min(7, int(cap_hours // hours_per_day)))
    end_idx = min(6, start_idx + max_days - 1)

    new_item = TimetableItem(
        startWeekday=first.startWeekday,
        endWeekday=_WEEKDAY_ORDER[end_idx],
        startTime=first.startTime,
        endTime=end_time,
    )
    return Timetable(
        id=timetable.id,
        timeTableItems=[new_item],
    ), float(max_days * hours_per_day)


def repair_labor_norms(
    proposal: ScenarioProposal,
    baseline_by_role: dict[str, float] | None = None,
    overtime_slack: float = _DEFAULT_OVERTIME_SLACK,
) -> list[str]:
    """Rewrite the proposal so every role's timetable respects a per-role cap.

    The cap for each role is ``baseline_weekly_hours * (1 + overtime_slack)``.
    ``baseline_weekly_hours`` is read from the SIMOD baseline calendar
    when available, otherwise defaults to 40h (humans) or 168h (machines).

    When a role's proposed timetable exceeds its cap, the timetable is
    clamped (daily window preserved, weekday range trimmed), and the
    role's headcount is increased proportionally so the intended total
    role-hours-per-week is still delivered.  Any ``resource_count``
    modification that targets the role is updated in place to match.

    Returns a list of human-readable notes describing each repair.
    """
    baseline = baseline_by_role or {}
    notes: list[str] = []
    tt_by_id = {tt.id: tt for tt in proposal.scenario.resourceParameters.timeTables}
    # Which roles share each timetable?  If two roles share, we only
    # repair the timetable once but bump the headcount of every
    # referencing role.
    roles_by_tt: dict[str, list[Role]] = {}
    for role in proposal.scenario.resourceParameters.roles:
        roles_by_tt.setdefault(role.schedule, []).append(role)

    repaired_tts: dict[str, Timetable] = {}
    scale_by_role: dict[str, int] = {}

    for role in proposal.scenario.resourceParameters.roles:
        tt = tt_by_id.get(role.schedule)
        if tt is None:
            continue
        current = _timetable_weekly_hours(tt)
        cap = _per_role_cap_hours(role.id, baseline, overtime_slack)
        if current <= cap:
            continue

        if role.schedule not in repaired_tts:
            new_tt, new_hours = _repair_timetable_to_cap(tt, cap)
            repaired_tts[role.schedule] = new_tt
        else:
            new_tt = repaired_tts[role.schedule]
            new_hours = _timetable_weekly_hours(new_tt)

        if new_hours <= 0:
            continue
        import math
        scale = max(1, math.ceil(current / new_hours))
        scale_by_role[role.id] = scale
        notes.append(
            f"Role '{role.id}': schedule '{role.schedule}' was "
            f"{current:.0f}h/week; clamped to {new_hours:.0f}h/week "
            f"(cap {cap:.0f}h, baseline "
            f"{baseline.get(role.id, _DEFAULT_HUMAN_BASELINE_HOURS):.0f}h, "
            f"+{int(overtime_slack*100)}% overtime). Headcount scaled "
            f"x{scale} to preserve total role-hours."
        )

    if not repaired_tts and not scale_by_role:
        return notes

    # Apply timetable replacements.
    new_tts = []
    for tt in proposal.scenario.resourceParameters.timeTables:
        new_tts.append(repaired_tts.get(tt.id, tt))
    proposal.scenario.resourceParameters.timeTables = new_tts

    # Scale role headcount.
    for role in proposal.scenario.resourceParameters.roles:
        scale = scale_by_role.get(role.id)
        if not scale or scale <= 1:
            continue
        base_resources = list(role.resources)
        if not base_resources:
            base_resources = [Resource(id=f"{role.id}_1")]
        target_count = len(base_resources) * scale
        additions: list[Resource] = []
        for i in range(len(base_resources), target_count):
            additions.append(Resource(id=f"{role.id}_auto_{i + 1}"))
        role.resources = base_resources + additions

    # Update resource_count modifications that reference repaired roles.
    for mod in proposal.modifications:
        if mod.parameter_type != "resource_count":
            continue
        scale = scale_by_role.get(mod.target_element)
        if not scale or scale <= 1:
            continue
        proposed = _extract_first_number(mod.proposed_value)
        if proposed is None:
            continue
        new_proposed = int(round(proposed * scale))
        mod.proposed_value = f"{new_proposed}"

    return notes


def _looks_like_machine(role_id: str) -> bool:
    """Heuristic to skip labor-norm checks on non-human resource pools."""
    name = f" {role_id.lower()} "
    return any(hint in name for hint in _MACHINE_HINTS)


def _timetable_weekly_hours(timetable: Timetable) -> float:
    """Sum the weekly hours of all items in a timetable.

    Each TimetableItem is interpreted as ``(endTime - startTime)`` hours
    on each weekday in the inclusive range ``startWeekday..endWeekday``
    (the SimuBridge convention used in the prompt examples).  Items
    where ``endTime <= startTime`` or where the weekday range is empty
    contribute zero hours.
    """
    total = 0.0
    for item in timetable.timeTableItems:
        try:
            start_idx = _WEEKDAY_ORDER.index(item.startWeekday)
            end_idx = _WEEKDAY_ORDER.index(item.endWeekday)
        except ValueError:
            continue
        if end_idx < start_idx:
            continue
        days = end_idx - start_idx + 1
        hours_per_day = max(0, item.endTime - item.startTime)
        total += days * hours_per_day
    return total


def _check_labor_norms(
    proposal: ScenarioProposal,
    context_summary: Any = None,
) -> list[ValidationIssue]:
    """Surface labor-norm *warnings* only — the auto-repair rewrites
    any over-cap schedule before validation runs, so this check exists
    solely for transparency when a residual norm-adjacent signal remains.
    Never emits ``severity='error'``.
    """
    issues: list[ValidationIssue] = []
    timetables_by_id = {
        tt.id: tt for tt in proposal.scenario.resourceParameters.timeTables
    }

    for role in proposal.scenario.resourceParameters.roles:
        timetable = timetables_by_id.get(role.schedule)
        if timetable is None:
            continue
        weekly_hours = _timetable_weekly_hours(timetable)
        # After auto-repair, everything <= per-role cap (<=48h for
        # default humans).  A residual >40h schedule is surfaced as an
        # informational warning so the user understands the pool is
        # operating in overtime territory.
        if weekly_hours <= _LABOR_NORM_STANDARD_HOURS:
            continue
        if _looks_like_machine(role.id):
            continue
        issues.append(ValidationIssue(
            category="feasibility",
            severity="warning",
            message=(
                f"Role '{role.id}' runs {weekly_hours:.1f}h/week per "
                f"person — above the {_LABOR_NORM_STANDARD_HOURS:.0f}h "
                f"standard week.  Within the EU Working Time Directive "
                f"overtime cap ({_LABOR_NORM_OVERTIME_CAP_HOURS:.0f}h) "
                f"but worth confirming with the user."
            ),
            element=role.id,
        ))

    return issues


def _check_budget_exceeded(
    proposal: ScenarioProposal,
    context_summary: Any,
) -> list[ValidationIssue]:
    """Flag proposals whose estimated cost exceeds the user-stated budget.

    Up to 10 % overshoot is treated as a soft warning (minor rounding /
    LLM imprecision).  Beyond that it becomes an error so the retry loop
    fires and the LLM is asked to propose cheaper modifications.
    """
    from second_llm.cost_estimation import build_cost_report

    issues: list[ValidationIssue] = []
    if context_summary is None or getattr(context_summary, "is_empty", True):
        return issues

    budget = getattr(context_summary, "budget", {}) or {}
    if not budget.get("additional_monthly"):
        return issues

    budget_limit = float(budget["additional_monthly"])
    report = build_cost_report(proposal, context_summary=context_summary)
    if not report.exceeds_budget:
        return issues

    overshoot_pct = (report.total_monthly_cost - budget_limit) / max(budget_limit, 1.0)

    if overshoot_pct <= 0.10:
        issues.append(ValidationIssue(
            category="feasibility",
            severity="warning",
            message=(
                f"Estimated additional cost ({report.formatted_total}) "
                f"slightly exceeds the stated budget of "
                f"{budget_limit:,.0f} {report.currency}/month "
                f"({overshoot_pct:.0%} over). Review if acceptable."
            ),
        ))
    else:
        issues.append(ValidationIssue(
            category="budget_exceeded",
            severity="error",
            message=(
                f"Estimated additional cost ({report.formatted_total}) "
                f"significantly exceeds the stated budget of "
                f"{budget_limit:,.0f} {report.currency}/month "
                f"({overshoot_pct:.0%} over). "
                f"You MUST reduce the cost of your proposed changes. "
                f"Options: fewer additional resources, schedule reallocation "
                f"instead of headcount increases, overtime-cap adjustments, "
                f"or process-redesign modifications that require no new spend. "
                f"If no cost-effective change is feasible for a KPI, declare "
                f"it in unresolved_kpis rather than proposing an infeasible "
                f"modification."
            ),
        ))

    return issues


# ===================================================================
# 4. Public API
# ===================================================================

def validate_proposal(
    proposal: ScenarioProposal,
    context_summary: Any = None,
) -> ValidationResult:
    """Run all post-schema validation checks on a ScenarioProposal.

    Parameters
    ----------
    proposal:
        The parsed scenario proposal to validate.
    context_summary:
        An :class:`OperationalContextSummary` from the context
        summarisation step (optional).  When provided, feasibility
        checks are run against user-stated operational constraints.

    Returns a ValidationResult collecting all issues found.
    """
    result = ValidationResult()

    # Constraint checks
    result.issues.extend(_check_distribution_value_ranges(proposal))
    result.issues.extend(_check_role_activity_references(proposal))
    result.issues.extend(_check_uniform_bounds(proposal))
    result.issues.extend(_check_simulation_instances(proposal))

    # Directional consistency
    result.issues.extend(_check_directional_consistency(proposal))
    result.issues.extend(_check_kpi_impact_directions(proposal))

    # Labor-norm feasibility is handled by repair_labor_norms before
    # validation runs, so no issues are surfaced here.

    # Feasibility checks (only when operational context is available)
    if context_summary is not None:
        result.issues.extend(_check_fixed_staffing(proposal, context_summary))
        result.issues.extend(_check_immutable_elements(proposal, context_summary))
        result.issues.extend(_check_overtime_constraints(proposal, context_summary))
        result.issues.extend(_check_shift_extension(proposal, context_summary))
        result.issues.extend(_check_budget_exceeded(proposal, context_summary))

    return result

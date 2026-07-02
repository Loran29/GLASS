"""Computational cost and impact estimation for scenario proposals.

Replaces LLM-hallucinated magnitude guesses with grounded computations:

1. **Cost estimation** -- monthly cost of resource changes using
   costHour from roles and weekly hours from timetables.

2. **Queueing-theoretic impact bounds** -- for resource-count changes,
   uses M/M/c queueing theory (Erlang-C formula) to estimate expected
   wait-time reduction rather than relying on LLM guesses.

These estimates are computed post-generation and displayed alongside
the LLM's scenario proposal in the comparison report.

References:
  Gross, D. et al. (2008). Fundamentals of Queueing Theory.
  Van der Aalst, W.M.P. (2023). Challenges and opportunities of
    process mining in BPS.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from second_llm.output_schema import (
    UnresolvedKPI,
    ScenarioProposal,
    Timetable,
    TimeUnit,
    Weekday,
)
from second_llm.validation import _extract_first_number


# ===================================================================
# Constants
# ===================================================================

_WEEKS_PER_MONTH = 4.33

_WEEKDAY_ORDER: dict[Weekday, int] = {
    Weekday.MONDAY: 0,
    Weekday.TUESDAY: 1,
    Weekday.WEDNESDAY: 2,
    Weekday.THURSDAY: 3,
    Weekday.FRIDAY: 4,
    Weekday.SATURDAY: 5,
    Weekday.SUNDAY: 6,
}

_TO_HOURS: dict[TimeUnit, float] = {
    TimeUnit.HOURS: 1.0,
    TimeUnit.MINUTES: 1.0 / 60.0,
    TimeUnit.SECONDS: 1.0 / 3600.0,
}

# Default utilization when it cannot be computed from scenario data.
_DEFAULT_UTILIZATION = 0.85


# ===================================================================
# Utility: weekly hours from timetable
# ===================================================================

def compute_weekly_hours(timetable: Timetable) -> float:
    """Compute total weekly working hours from a timetable's items.

    Follows the SimuBridge convention used in the generation prompt and
    the validator: a :class:`TimetableItem` represents the daily window
    ``(endTime - startTime)`` applied to each weekday in the inclusive
    range ``startWeekday..endWeekday``.  Items with an empty weekday
    range or a non-positive daily window contribute zero hours.  Kept
    in sync with ``second_llm.validation._timetable_weekly_hours``.
    """
    total = 0.0
    for item in timetable.timeTableItems:
        start_day = _WEEKDAY_ORDER.get(item.startWeekday)
        end_day = _WEEKDAY_ORDER.get(item.endWeekday)
        if start_day is None or end_day is None or end_day < start_day:
            continue
        days = end_day - start_day + 1
        hours_per_day = max(0.0, item.endTime - item.startTime)
        total += days * hours_per_day
    return total


# ===================================================================
# M/M/c queueing theory (Erlang-C)
# ===================================================================

def _erlang_c(c: int, rho: float) -> float:
    """Compute Erlang-C probability P(wait) for an M/M/c queue.

    Parameters
    ----------
    c : int
        Number of servers (resources).
    rho : float
        Per-server utilization (must be < 1 for stability).

    Returns
    -------
    float
        Probability that an arriving customer must wait in the queue.
    """
    if c < 1 or rho >= 1.0:
        return 1.0
    if rho <= 0.0:
        return 0.0

    a = c * rho  # total offered load in Erlangs

    # B = (a^c / c!) / (1 - rho)  --  computed in log-space
    log_B = c * math.log(a) - math.lgamma(c + 1) - math.log(1.0 - rho)

    # S = sum_{k=0}^{c-1} a^k / k!  --  each term in log-space
    log_terms: list[float] = []
    for k in range(c):
        log_terms.append(
            k * math.log(a) - math.lgamma(k + 1) if k > 0 else 0.0
        )

    # C(c, a) = B / (S + B)  via log-sum-exp for numerical stability
    all_logs = log_terms + [log_B]
    max_log = max(all_logs)
    S_exp = sum(math.exp(lt - max_log) for lt in log_terms)
    B_exp = math.exp(log_B - max_log)

    return min(1.0, max(0.0, B_exp / (S_exp + B_exp)))


def _expected_wait_factor(c: int, rho: float) -> float:
    """Compute the expected-wait factor: C(c, rho) / (c * (1 - rho)).

    This value is proportional to E[W_q] (the actual expected waiting
    time in the queue equals factor / mu).  Used for computing
    wait-time reduction ratios between two server configurations.
    """
    if rho >= 1.0 or c < 1:
        return float("inf")
    if rho <= 0.0:
        return 0.0
    return _erlang_c(c, rho) / (c * (1.0 - rho))


def _estimate_utilization(
    role_id: str,
    baseline_count: int,
    proposal: ScenarioProposal,
) -> tuple[float, str]:
    """Try to estimate per-server utilization for a role from scenario data.

    Uses activity durations assigned to the role and inter-arrival
    times from the scenario to compute rho = lambda * processing / c.

    Returns ``(utilization, source_description)``.
    """
    # Sum mean processing time per case for activities assigned to this role
    total_hours = 0.0
    for model in proposal.scenario.models:
        for act in model.modelParameter.activities:
            if role_id not in act.resources:
                continue
            mean_val = None
            for p in act.duration.values:
                if p.id in ("mean", "constantValue"):
                    mean_val = p.value
                    break
            if mean_val is not None:
                to_hours = _TO_HOURS.get(act.duration.timeUnit, 1.0)
                total_hours += mean_val * to_hours

    if total_hours <= 0:
        return _DEFAULT_UTILIZATION, "assumed"

    # Find arrival rate (cases per hour) from start events
    arrival_rate: float | None = None
    for model in proposal.scenario.models:
        for event in model.modelParameter.events:
            for p in event.interArrivalTime.values:
                if p.id == "mean" and p.value > 0:
                    to_hours = _TO_HOURS.get(
                        event.interArrivalTime.timeUnit, 1.0,
                    )
                    arrival_rate = 1.0 / (p.value * to_hours)
                    break
            if arrival_rate is not None:
                break

    if arrival_rate is None:
        return _DEFAULT_UTILIZATION, "assumed"

    rho = arrival_rate * total_hours / baseline_count
    if rho >= 1.0:
        return 0.95, "computed (capped)"
    if rho < 0.1:
        return _DEFAULT_UTILIZATION, "assumed"
    return round(rho, 3), "computed"


# ===================================================================
# Data structures
# ===================================================================

@dataclass
class CostEstimate:
    """Cost estimate for a single modification."""

    modification_index: int
    intervention: str
    target_element: str
    parameter_type: str
    monthly_cost: float
    currency: str = "EUR"
    computation: str = ""

    @property
    def formatted_cost(self) -> str:
        if self.monthly_cost == 0:
            return "No additional cost"
        sign = "+" if self.monthly_cost > 0 else ""
        return f"{sign}{self.monthly_cost:,.0f} {self.currency}/month"


@dataclass
class QueueingEstimate:
    """M/M/c queueing-theoretic impact estimate for a resource change."""

    modification_index: int
    target_element: str
    baseline_servers: int
    proposed_servers: int
    utilization: float
    utilization_source: str
    baseline_wait_probability: float
    proposed_wait_probability: float
    wait_reduction_pct: float
    computation: str = ""


@dataclass
class ScenarioCostReport:
    """Aggregated cost and impact estimates for a scenario proposal."""

    cost_estimates: list[CostEstimate] = field(default_factory=list)
    queueing_estimates: list[QueueingEstimate] = field(default_factory=list)
    total_monthly_cost: float = 0.0
    currency: str = "EUR"
    budget_limit: float | None = None
    exceeds_budget: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def formatted_total(self) -> str:
        sign = "+" if self.total_monthly_cost > 0 else ""
        return f"{sign}{self.total_monthly_cost:,.0f} {self.currency}/month"

    @property
    def has_estimates(self) -> bool:
        return bool(self.cost_estimates or self.queueing_estimates)

    def to_prompt_section(self) -> str:
        """Format as text suitable for injection into a generation prompt."""
        if not self.has_estimates:
            return ""

        lines: list[str] = []
        if self.cost_estimates:
            lines.append("### Cost estimates")
            for ce in self.cost_estimates:
                lines.append(f"- {ce.intervention}: {ce.formatted_cost}")
                if ce.computation:
                    lines.append(f"  ({ce.computation})")

        if self.queueing_estimates:
            lines.append("### Queueing impact estimates (M/M/c)")
            for qe in self.queueing_estimates:
                delta = qe.proposed_servers - qe.baseline_servers
                lines.append(
                    f"- {qe.target_element}: +{delta} server(s) "
                    f"({qe.baseline_servers} -> {qe.proposed_servers}), "
                    f"rho={qe.utilization:.0%} ({qe.utilization_source}). "
                    f"Wait reduction: ~{qe.wait_reduction_pct:.0f}%"
                )

        if self.budget_limit is not None:
            lines.append(
                f"\nBudget: {self.budget_limit:,.0f} {self.currency}/month | "
                f"Total cost: {self.formatted_total}"
            )
            if self.exceeds_budget:
                lines.append("WARNING: EXCEEDS BUDGET")

        return "\n".join(lines)


# ===================================================================
# Internal estimators
# ===================================================================

def _estimate_resource_cost(
    idx: int,
    mod: Any,
    role_map: dict[str, Any],
    tt_map: dict[str, Timetable],
    report: ScenarioCostReport,
    proposal: ScenarioProposal,
    context_summary: Any = None,
) -> None:
    """Compute cost and queueing impact for a resource_count modification."""
    baseline_num = _extract_first_number(mod.baseline_value)
    proposed_num = _extract_first_number(mod.proposed_value)
    if baseline_num is None or proposed_num is None:
        return

    baseline_count = int(baseline_num)
    proposed_count = int(proposed_num)
    delta = proposed_count - baseline_count
    if delta == 0:
        return

    role = role_map.get(mod.target_element) or role_map.get(
        mod.target_element.lower()
    )
    if role is None:
        report.notes.append(
            f"Role '{mod.target_element}' not found in scenario "
            f"-- cost estimate skipped."
        )
        return

    tt = tt_map.get(role.schedule)
    timetable_hours = compute_weekly_hours(tt) if tt else None

    # For new-hire additions, prefer hours stated explicitly by the LLM in the
    # modification text over the existing pool's (possibly fragmented) timetable.
    # The LLM often writes "40h/week", "20 h/week" etc. in feasibility_assumptions
    # or evidence_source to describe the intended schedule for the new resource.
    llm_stated_hours: float | None = None
    if delta > 0:
        for field_val in (
            getattr(mod, "feasibility_assumptions", "") or "",
            getattr(mod, "evidence_source", "") or "",
            getattr(mod, "mechanism_rationale", "") or "",
            getattr(mod, "intervention", "") or "",
        ):
            llm_stated_hours = _extract_weekly_hours_from_string(field_val)
            if llm_stated_hours is not None:
                break

    if llm_stated_hours is not None:
        weekly_hours = llm_stated_hours
        hours_source = "LLM-stated"
    elif timetable_hours is not None and timetable_hours >= 1.0:
        weekly_hours = timetable_hours
        hours_source = "timetable"
    else:
        weekly_hours = 40.0
        hours_source = "default"

    # If the user stated a working-hours constraint for this role, cap at that.
    hours_capped = False
    if context_summary is not None:
        user_max_h = getattr(context_summary, "get_max_hours_per_week", lambda _: None)(
            mod.target_element
        )
        if user_max_h is not None and user_max_h > 0 and weekly_hours > float(user_max_h):
            weekly_hours = float(user_max_h)
            hours_capped = True
            hours_source = "user cap"

    cost_per_hour = role.costHour
    monthly_cost = delta * cost_per_hour * weekly_hours * _WEEKS_PER_MONTH

    if hours_capped:
        hours_label = f"{weekly_hours:.0f} h/week (user cap)"
    elif hours_source == "LLM-stated":
        hours_label = f"{weekly_hours:.0f} h/week (from modification text)"
    elif hours_source == "default":
        hours_label = f"{weekly_hours:.0f} h/week (default — no timetable found)"
    else:
        hours_label = f"{weekly_hours:.0f} h/week"
    computation = (
        f"{delta:+d} resource(s) x {cost_per_hour:.0f} {report.currency}/h "
        f"x {hours_label} x {_WEEKS_PER_MONTH} wk/month"
    )

    # Surface overtime multiplier as a note when it exists, so the user
    # knows calendar extensions for this role cost more than regular hours.
    if context_summary is not None:
        rc = getattr(context_summary, "resource_constraints", {}) or {}
        for rname, rinfo in rc.items():
            if rname.lower() == mod.target_element.lower():
                multiplier = rinfo.get("overtime_rate_multiplier")
                if multiplier and float(multiplier) > 1.0:
                    report.notes.append(
                        f"'{mod.target_element}' has an overtime rate of "
                        f"{multiplier}× base. This estimate covers regular "
                        f"hours only — any calendar extension for this role "
                        f"would incur additional cost at the overtime rate."
                    )
                break

    report.cost_estimates.append(CostEstimate(
        modification_index=idx + 1,
        intervention=(
            mod.intervention or f"{mod.target_element} -- resource_count"
        ),
        target_element=mod.target_element,
        parameter_type=mod.parameter_type,
        monthly_cost=monthly_cost,
        currency=report.currency,
        computation=computation,
    ))


def _estimate_queue_impact(
    idx: int,
    mod: Any,
    baseline_c: int,
    proposed_c: int,
    report: ScenarioCostReport,
    proposal: ScenarioProposal,
) -> None:
    """Compute M/M/c queueing estimate for a resource addition."""
    rho, rho_source = _estimate_utilization(
        mod.target_element, baseline_c, proposal,
    )

    rho_proposed = rho * baseline_c / proposed_c
    if rho_proposed >= 1.0:
        report.notes.append(
            f"Role '{mod.target_element}': system still saturated with "
            f"{proposed_c} servers (rho ~ {rho_proposed:.0%})."
        )
        return

    p_wait_baseline = _erlang_c(baseline_c, rho)
    p_wait_proposed = _erlang_c(proposed_c, rho_proposed)

    wf_baseline = _expected_wait_factor(baseline_c, rho)
    wf_proposed = _expected_wait_factor(proposed_c, rho_proposed)

    wait_reduction = (
        (1.0 - wf_proposed / wf_baseline) * 100
        if wf_baseline > 0
        else 0.0
    )

    computation = (
        f"M/M/c: {baseline_c}->{proposed_c} servers, "
        f"rho={rho:.0%}->{rho_proposed:.0%}, "
        f"P(wait)={p_wait_baseline:.1%}->{p_wait_proposed:.1%}, "
        f"E[Wq] reduction ~ {wait_reduction:.0f}%"
    )

    report.queueing_estimates.append(QueueingEstimate(
        modification_index=idx + 1,
        target_element=mod.target_element,
        baseline_servers=baseline_c,
        proposed_servers=proposed_c,
        utilization=rho,
        utilization_source=rho_source,
        baseline_wait_probability=p_wait_baseline,
        proposed_wait_probability=p_wait_proposed,
        wait_reduction_pct=wait_reduction,
        computation=computation,
    ))


def _build_combined_queueing_estimates(
    proposal: ScenarioProposal,
    report: ScenarioCostReport,
) -> None:
    """Compute M/M/c queueing estimates grouped by shared activity.

    Roles that serve the same activity share an arrival stream. Adding
    servers across multiple pools must be evaluated as a single combined
    queue (total baseline servers → total proposed servers) rather than
    as independent per-pool queues — otherwise the benefit of each
    subsequent addition is overstated.

    For each activity that has at least one resource_count increase:
      1. Sum baseline server counts for ALL roles assigned to it.
      2. Sum proposed server counts for those same roles.
      3. Run one M/M/c estimate on the aggregate.
    """
    # Index: role_id → set of activity_ids that reference this role
    role_to_activities: dict[str, set[str]] = {}
    for model in proposal.scenario.models:
        for act in model.modelParameter.activities:
            for role_id in act.resources:
                role_to_activities.setdefault(role_id, set()).add(act.id)

    # For each resource_count modification, parse baseline/proposed counts
    rc_mods: list[tuple[Any, int, int]] = []  # (mod, baseline_c, proposed_c)
    for mod in proposal.modifications:
        if mod.parameter_type != "resource_count":
            continue
        b = _extract_first_number(mod.baseline_value)
        p = _extract_first_number(mod.proposed_value)
        if b is None or p is None or int(p) <= int(b):
            continue
        rc_mods.append((mod, int(b), int(p)))

    if not rc_mods:
        return

    # Build the "current" pool sizes: start from baseline, apply all deltas
    # so we know what each role looks like after all proposed changes.
    current_pool: dict[str, int] = {}   # role_id → proposed count
    baseline_pool: dict[str, int] = {}  # role_id → baseline count
    for mod, b, p in rc_mods:
        baseline_pool[mod.target_element] = b
        current_pool[mod.target_element] = p

    # Group modified roles by activity.  An activity group requires at least
    # one role to have a positive delta.
    activity_groups: dict[str, set[str]] = {}  # activity_id → role_ids
    for mod, _, _ in rc_mods:
        for act_id in role_to_activities.get(mod.target_element, set()):
            activity_groups.setdefault(act_id, set()).add(mod.target_element)

    # Resolve activity names
    act_name_map: dict[str, str] = {
        act.id: (act.name if act.name and act.name != act.id else act.id)
        for model in proposal.scenario.models
        for act in model.modelParameter.activities
    }

    seen_groups: set[frozenset[str]] = set()

    for act_id, role_ids in activity_groups.items():
        group_key = frozenset(role_ids)
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)

        # All roles serving this activity (not just the modified ones) —
        # needed for the true baseline server count.
        all_roles_for_act: set[str] = set()
        for model in proposal.scenario.models:
            for act in model.modelParameter.activities:
                if act.id == act_id:
                    all_roles_for_act.update(act.resources)

        # Baseline = sum of current pool sizes for all roles on this activity.
        # For roles NOT being modified, count their existing resource list.
        total_baseline = 0
        total_proposed = 0
        for role in proposal.scenario.resourceParameters.roles:
            if role.id not in all_roles_for_act:
                continue
            role_baseline = baseline_pool.get(role.id, len(role.resources))
            role_proposed = current_pool.get(role.id, len(role.resources))
            total_baseline += role_baseline
            total_proposed += role_proposed

        if total_proposed <= total_baseline or total_baseline < 1:
            continue

        act_name = act_name_map.get(act_id, act_id)
        role_labels = ", ".join(sorted(role_ids))

        rho, rho_source = _estimate_utilization(
            next(iter(role_ids)), total_baseline, proposal,
        )

        rho_proposed = rho * total_baseline / total_proposed
        if rho_proposed >= 1.0:
            report.notes.append(
                f"Activity '{act_name}': system still saturated with "
                f"{total_proposed} combined servers (rho ~ {rho_proposed:.0%})."
            )
            continue

        p_wait_baseline = _erlang_c(total_baseline, rho)
        p_wait_proposed = _erlang_c(total_proposed, rho_proposed)
        wf_baseline = _expected_wait_factor(total_baseline, rho)
        wf_proposed = _expected_wait_factor(total_proposed, rho_proposed)
        wait_reduction = (
            (1.0 - wf_proposed / wf_baseline) * 100 if wf_baseline > 0 else 0.0
        )

        computation = (
            f"M/M/c combined: {total_baseline}→{total_proposed} servers "
            f"({role_labels}), "
            f"rho={rho:.0%}→{rho_proposed:.0%}, "
            f"P(wait)={p_wait_baseline:.1%}→{p_wait_proposed:.1%}, "
            f"E[Wq] reduction ~{wait_reduction:.0f}%"
        )

        report.queueing_estimates.append(QueueingEstimate(
            modification_index=-1,  # spans multiple mods
            target_element=act_name,
            baseline_servers=total_baseline,
            proposed_servers=total_proposed,
            utilization=rho,
            utilization_source=rho_source,
            baseline_wait_probability=p_wait_baseline,
            proposed_wait_probability=p_wait_proposed,
            wait_reduction_pct=wait_reduction,
            computation=computation,
        ))


def _extract_weekly_hours_from_string(value: str) -> float | None:
    """Extract a weekly-hours figure from a human-readable string.

    Recognises patterns like 'approx 4.5h/week', '~9h/week', '40h/week'.
    """
    m = re.search(r"~?(?:approx\s*)?([0-9]+(?:\.[0-9]+)?)\s*h(?:ours)?/week", value, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _estimate_calendar_cost(
    idx: int,
    mod: Any,
    role_map: dict[str, Any],
    tt_map: dict[str, Timetable],
    report: ScenarioCostReport,
    proposal: ScenarioProposal,
) -> None:
    """Compute incremental monthly cost for a resource_calendar modification."""
    proposed_tt = tt_map.get(mod.target_element)
    if proposed_tt is None:
        return

    proposed_hours = compute_weekly_hours(proposed_tt)
    baseline_hours = _extract_weekly_hours_from_string(str(mod.baseline_value or ""))
    if baseline_hours is None:
        report.notes.append(
            f"Calendar '{mod.target_element}': baseline hours not parseable "
            f"from baseline_value — cost estimate skipped."
        )
        return

    delta_hours = proposed_hours - baseline_hours
    if abs(delta_hours) < 0.1:
        return

    # Find the role whose schedule references this calendar
    cost_per_hour: float | None = None
    for role in proposal.scenario.resourceParameters.roles:
        if role.schedule == mod.target_element:
            cost_per_hour = role.costHour
            break

    if cost_per_hour is None:
        report.notes.append(
            f"Calendar '{mod.target_element}': no role with matching schedule found "
            f"— cost estimate skipped."
        )
        return

    monthly_cost = delta_hours * cost_per_hour * _WEEKS_PER_MONTH
    computation = (
        f"({proposed_hours:.1f} − {baseline_hours:.1f}) h/week delta "
        f"× {cost_per_hour:.0f} {report.currency}/h "
        f"× {_WEEKS_PER_MONTH} wk/month"
    )
    report.cost_estimates.append(CostEstimate(
        modification_index=idx + 1,
        intervention=mod.intervention or f"{mod.target_element} — resource_calendar",
        target_element=mod.target_element,
        parameter_type=mod.parameter_type,
        monthly_cost=monthly_cost,
        currency=report.currency,
        computation=computation,
    ))


# ===================================================================
# Public API
# ===================================================================

def build_cost_report(
    proposal: ScenarioProposal,
    context_summary: Any = None,
) -> ScenarioCostReport:
    """Build computational cost and impact estimates for a scenario.

    Parameters
    ----------
    proposal:
        The generated scenario proposal to analyse.
    context_summary:
        An :class:`OperationalContextSummary` with budget and overtime
        info (optional).

    Returns
    -------
    ScenarioCostReport
        Cost estimates, queueing impact estimates, and budget
        compliance assessment.
    """
    report = ScenarioCostReport()

    # --- Extract budget from context summary ---
    if context_summary is not None and not getattr(
        context_summary, "is_empty", True,
    ):
        budget = getattr(context_summary, "budget", {}) or {}
        if budget.get("additional_monthly"):
            report.budget_limit = float(budget["additional_monthly"])
        budget_currency = budget.get("currency")
        if budget_currency:
            report.currency = str(budget_currency)

    # --- Scenario currency fallback ---
    currency_map = {"euro": "EUR", "dollar": "USD", "Money Unit": "MU"}
    scenario_currency = proposal.scenario.currency.value
    if report.budget_limit is None and scenario_currency in currency_map:
        report.currency = currency_map[scenario_currency]

    # --- Index roles and timetables ---
    role_map: dict[str, Any] = {}
    for r in proposal.scenario.resourceParameters.roles:
        role_map[r.id] = r
        role_map[r.id.lower()] = r  # case-insensitive fallback
    tt_map = {
        tt.id: tt
        for tt in proposal.scenario.resourceParameters.timeTables
    }

    # --- Process each modification ---
    for idx, mod in enumerate(proposal.modifications):
        if mod.parameter_type == "resource_count":
            _estimate_resource_cost(
                idx, mod, role_map, tt_map, report, proposal, context_summary,
            )
        elif mod.parameter_type == "resource_calendar":
            _estimate_calendar_cost(
                idx, mod, role_map, tt_map, report, proposal,
            )

    # --- Combined queueing estimates (grouped by shared activity) ---
    _build_combined_queueing_estimates(proposal, report)

    # --- Aggregate ---
    report.total_monthly_cost = sum(
        ce.monthly_cost for ce in report.cost_estimates
    )

    # --- Budget compliance ---
    if (
        report.budget_limit is not None
        and report.total_monthly_cost > report.budget_limit
    ):
        report.exceeds_budget = True
        report.notes.append(
            f"Total additional cost ({report.formatted_total}) exceeds "
            f"the stated budget of {report.budget_limit:,.0f} "
            f"{report.currency}/month."
        )

    return report


def repair_budget_overshoot(
    proposal: ScenarioProposal,
    context_summary: Any = None,
    *,
    overshoot_tolerance_pct: float = 0.10,
) -> list[str]:
    """Deterministically trim positive-cost headcount additions to fit budget.

    Strategy:
      1. Estimate the current monthly additional cost.
      2. If it exceeds ``budget * (1 + overshoot_tolerance_pct)``, reduce
         ``resource_count`` increases one resource at a time.
      3. Choose the next reduction from the lowest-impact change first,
         using queueing impact per added resource when available.
      4. If a KPI loses all of its modifications through trimming, add it
         to ``proposal.unresolved_kpis`` instead of silently dropping it.
    """
    notes: list[str] = []
    if context_summary is None or getattr(context_summary, "is_empty", True):
        return notes

    budget = getattr(context_summary, "budget", {}) or {}
    if not budget.get("additional_monthly"):
        return notes

    budget_limit = float(budget["additional_monthly"])
    allowed_total = budget_limit * (1.0 + overshoot_tolerance_pct)

    def _ensure_role_headcount(role_id: str, new_count: int) -> bool:
        for role in proposal.scenario.resourceParameters.roles:
            if role.id != role_id:
                continue
            current = list(role.resources)
            if new_count >= len(current):
                return True
            role.resources = current[:new_count]
            return True
        return False

    def _has_other_modifications(kpi_name: str, skip_index: int) -> bool:
        for idx, mod in enumerate(proposal.modifications):
            if idx == skip_index:
                continue
            if mod.kpi_reference == kpi_name:
                return True
        return False

    def _ensure_unresolved_kpi(kpi_name: str, explanation: str) -> None:
        if not kpi_name:
            return
        for item in proposal.unresolved_kpis:
            if item.kpi_name == kpi_name:
                return
        proposal.unresolved_kpis.append(UnresolvedKPI(
            kpi_name=kpi_name,
            reason="blocked_by_operational_constraint",
            explanation=explanation,
        ))

    while True:
        report = build_cost_report(proposal, context_summary=context_summary)
        if report.budget_limit is None or report.total_monthly_cost <= allowed_total:
            if notes:
                final_overshoot = max(0.0, report.total_monthly_cost - budget_limit)
                if final_overshoot > 0:
                    notes.append(
                        f"Budget repair finished at {report.formatted_total}, "
                        f"which is within the allowed +{int(overshoot_tolerance_pct * 100)}% tolerance."
                    )
                else:
                    notes.append(
                        f"Budget repair finished at {report.formatted_total}, within budget."
                    )
            return notes

        queue_by_idx = {
            qe.modification_index: qe for qe in report.queueing_estimates
        }
        cost_by_idx = {
            ce.modification_index: ce for ce in report.cost_estimates
            if ce.monthly_cost > 0
        }
        candidates: list[dict[str, float | int | str]] = []
        for idx, mod in enumerate(proposal.modifications):
            if mod.parameter_type != "resource_count":
                continue
            cost_est = cost_by_idx.get(idx + 1)
            if cost_est is None:
                continue
            baseline_num = _extract_first_number(mod.baseline_value)
            proposed_num = _extract_first_number(mod.proposed_value)
            if baseline_num is None or proposed_num is None:
                continue
            baseline_count = int(round(baseline_num))
            proposed_count = int(round(proposed_num))
            delta = proposed_count - baseline_count
            if delta <= 0 or proposed_count <= 1:
                continue
            qe = queue_by_idx.get(idx + 1)
            impact_per_unit = (
                qe.wait_reduction_pct / delta
                if qe is not None and delta > 0
                else 0.0
            )
            cost_per_unit = cost_est.monthly_cost / delta if delta > 0 else 0.0
            candidates.append({
                "index": idx,
                "baseline": baseline_count,
                "proposed": proposed_count,
                "impact_per_unit": impact_per_unit,
                "cost_per_unit": cost_per_unit,
                "target": mod.target_element,
                "kpi": mod.kpi_reference,
            })

        if not candidates:
            notes.append(
                "Budget repair could not reduce cost further because no positive-cost "
                "resource_count increases were available to trim."
            )
            return notes

        candidates.sort(
            key=lambda item: (
                float(item["impact_per_unit"]),
                -float(item["cost_per_unit"]),
                str(item["target"]).lower(),
            )
        )
        chosen = candidates[0]
        mod_index = int(chosen["index"])
        mod = proposal.modifications[mod_index]
        baseline_count = int(chosen["baseline"])
        proposed_count = int(chosen["proposed"])
        new_count = max(baseline_count, proposed_count - 1)
        unit_savings = float(chosen["cost_per_unit"])

        _ensure_role_headcount(mod.target_element, new_count)
        mod.proposed_value = str(new_count)
        notes.append(
            f"Budget repair reduced '{mod.target_element}' from {proposed_count} to {new_count} "
            f"resource(s), saving about {unit_savings:,.0f} {report.currency}/month."
        )

        if new_count <= baseline_count:
            kpi_name = mod.kpi_reference
            del proposal.modifications[mod_index]
            if not _has_other_modifications(kpi_name, skip_index=-1):
                _ensure_unresolved_kpi(
                    kpi_name,
                    "The scenario's staffing increases had to be removed to keep the proposal within the allowed budget tolerance.",
                )

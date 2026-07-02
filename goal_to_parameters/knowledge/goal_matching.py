"""Goal-category and context-rule matching shared across retrieval stages.

The matchers turn free-text goal descriptions, KPI metadata, and a
context profile into:

  * the set of :class:`GoalCategory` values that describe the goal
  * the subset of :class:`ContextAwareRule` objects that should fire
    given the statistically significant factors in the profile
  * the parameter / literature references cited by those mappings and
    rules (used for the keyword fallback path)

These helpers are pure functions over the knowledge base and do not
depend on the hybrid retriever.
"""

from __future__ import annotations

from typing import Any

from knowledge.kb_data import build_knowledge_base
from knowledge.models import (
    ContextAwareRule,
    GoalCategory,
    GoalParameterMapping,
    LiteratureReference,
    SimulationParameter,
)

_GOAL_KEYWORDS: dict[GoalCategory, list[str]] = {
    GoalCategory.WAITING_TIME: [
        "waiting time", "wait time", "queue", "delay", "access time",
        "response time", "lead time", "turnaround",
    ],
    GoalCategory.PROCESSING_TIME: [
        "processing time", "service time", "activity duration",
        "handling time", "execution time",
    ],
    GoalCategory.COST: [
        "cost", "expense", "budget", "operational cost", "minimize cost",
        "reduce cost", "financial",
    ],
    GoalCategory.PROCESSING_CAPACITY: [
        "capacity", "scalability", "volume", "handle more",
        "production capacity", "peak load",
    ],
    GoalCategory.RESOURCE_UTILISATION: [
        "utilisation", "utilization", "resource usage", "workload",
        "idle time", "occupancy", "efficiency",
    ],
    GoalCategory.QUALITY_COMPLIANCE: [
        "quality", "accuracy", "error rate", "rework", "compliance",
        "sla", "defect", "first-pass", "correctness",
    ],
    GoalCategory.THROUGHPUT: [
        "throughput", "cases per", "output rate", "completion rate",
        "flow rate", "productivity",
    ],
}

_KPI_CATEGORY_TO_GOAL: dict[str, GoalCategory] = {
    "time": GoalCategory.WAITING_TIME,
    "cost": GoalCategory.COST,
    "quality": GoalCategory.QUALITY_COMPLIANCE,
    "utilization": GoalCategory.RESOURCE_UTILISATION,
    "throughput": GoalCategory.THROUGHPUT,
    "compliance": GoalCategory.QUALITY_COMPLIANCE,
    "flexibility": GoalCategory.PROCESSING_CAPACITY,
}


def _match_goal_categories(
    goal_structured: str,
    kpis: list[dict[str, Any]] | None = None,
) -> set[GoalCategory]:
    """Identify which goal categories are relevant based on the structured
    goal text and KPI metadata."""

    matched: set[GoalCategory] = set()
    goal_lower = goal_structured.lower()

    for category, keywords in _GOAL_KEYWORDS.items():
        for kw in keywords:
            if kw in goal_lower:
                matched.add(category)
                break

    if "cycle time" in goal_lower:
        matched.add(GoalCategory.WAITING_TIME)
        matched.add(GoalCategory.PROCESSING_TIME)

    if kpis:
        for kpi in kpis:
            cat = kpi.get("category", "")
            if isinstance(cat, str):
                mapped = _KPI_CATEGORY_TO_GOAL.get(cat.lower())
                if mapped:
                    matched.add(mapped)
            if kpi.get("target_direction") == "maintain":
                matched.add(GoalCategory.QUALITY_COMPLIANCE)

    return matched


def _match_context_rules(
    context_profile: dict[str, Any] | None,
    kpis: list[dict[str, Any]] | None = None,
) -> list[ContextAwareRule]:
    """Select context-aware rules triggered by the available evidence."""
    kb = build_knowledge_base()

    if not context_profile:
        return []

    significant = context_profile.get("significant_relationships", [])
    if not significant:
        return []

    detected_factors = context_profile.get("detected_factors", [])
    factor_scopes: dict[str, str] = {}
    for f in detected_factors:
        name = f.get("name", "").lower()
        scope = f.get("scope", "").lower()
        factor_scopes[name] = scope

    for rel in significant:
        factor_name = rel.get("factor", "").lower()
        if factor_name not in factor_scopes:
            temporal_hints = {"day", "week", "hour", "month", "quarter", "weekend", "holiday"}
            if any(hint in factor_name for hint in temporal_hints):
                factor_scopes[factor_name] = "temporal"
            else:
                factor_scopes[factor_name] = "case_level"

    has_segmentation = False
    if kpis:
        for kpi in kpis:
            if kpi.get("context_segmentation"):
                has_segmentation = True
                break

    active_scopes: set[str] = set(factor_scopes.values())

    triggered: list[ContextAwareRule] = []
    for rule in kb.context_rules:
        scope_match = rule.trigger_factor_scope.value in active_scopes
        factor_name_match = any(
            factor_name in [ex.lower() for ex in rule.trigger_factor_examples]
            for factor_name in factor_scopes
        )
        if scope_match or factor_name_match or has_segmentation:
            triggered.append(rule)

    return triggered


def _collect_referenced_parameters(
    goal_mappings: list[GoalParameterMapping],
    context_rules: list[ContextAwareRule],
) -> list[SimulationParameter]:
    """Return parameter definitions referenced by the selected mappings and rules."""
    kb = build_knowledge_base()
    param_index = {p.name: p for p in kb.parameters}

    referenced_names: list[str] = []
    seen: set[str] = set()

    for mapping in goal_mappings:
        for change in mapping.parameter_changes:
            if change.parameter_name not in seen:
                referenced_names.append(change.parameter_name)
                seen.add(change.parameter_name)

    for rule in context_rules:
        for pname in rule.affected_parameters:
            if pname not in seen:
                referenced_names.append(pname)
                seen.add(pname)

    return [param_index[n] for n in referenced_names if n in param_index]


def _collect_referenced_literature(
    goal_mappings: list[GoalParameterMapping],
) -> list[LiteratureReference]:
    """Return literature references cited by the selected goal mappings."""
    kb = build_knowledge_base()
    lit_index = {lit.paper_id: lit for lit in kb.literature}

    paper_ids: list[int] = []
    seen: set[int] = set()

    for mapping in goal_mappings:
        for change in mapping.parameter_changes:
            for pid in change.paper_ids:
                if pid not in seen:
                    paper_ids.append(pid)
                    seen.add(pid)

    return [lit_index[pid] for pid in paper_ids if pid in lit_index]

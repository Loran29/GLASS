"""Goal-conditional filtering of SIMOD, log, and context evidence.

Each filter projects a raw evidence source (SIMOD JSON, log profile,
context profile) down to the sections that matter for the matched
:class:`GoalCategory` set.  The differentiation briefing is built on
top of the context filter's output.

These helpers are the "presentation layer" of retrieval: they do not
touch the knowledge base or the hybrid retriever.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from knowledge.models import ContextAwareRule, ContextFactorScope, GoalCategory

# Which SIMOD sections are relevant to which goal categories.
# NOTE: task_resource_distribution is intentionally excluded — it contains
# per-resource-per-activity duration distributions that can be 100k+ chars
# for large logs, exceeding LLM context limits.  Duration information is
# instead surfaced as a compact summary in _annotations.
_SIMOD_SECTIONS_BY_GOAL: dict[GoalCategory, list[str]] = {
    GoalCategory.WAITING_TIME: [
        "task_durations",
        "arrival_time_distribution", "arrival_time_calendar",
        "gateway_branching_probabilities", "gateway_probabilities",
        "resource_profiles",
    ],
    GoalCategory.PROCESSING_TIME: [
        "task_durations",
        "resource_profiles",
    ],
    GoalCategory.COST: [
        "resource_profiles", "resource_calendars", "calendars",
    ],
    GoalCategory.PROCESSING_CAPACITY: [
        "resource_profiles", "resource_calendars", "calendars",
        "arrival_time_distribution", "arrival_distribution",
    ],
    GoalCategory.RESOURCE_UTILISATION: [
        "resource_profiles", "resource_calendars", "calendars",
        "task_durations",
    ],
    GoalCategory.QUALITY_COMPLIANCE: [
        "gateway_branching_probabilities", "gateway_probabilities",
        "task_durations",
    ],
    GoalCategory.THROUGHPUT: [
        "resource_profiles", "arrival_time_distribution",
        "arrival_distribution", "resource_calendars", "calendars",
    ],
}

# Which log profile sections are relevant to which goal categories.
_LOG_SECTIONS_BY_GOAL: dict[GoalCategory, list[str]] = {
    GoalCategory.WAITING_TIME: [
        "duration_indicators", "top_variants", "top_transitions",
        "top_activities",
    ],
    GoalCategory.PROCESSING_TIME: [
        "duration_indicators", "top_activities", "top_transitions",
    ],
    GoalCategory.COST: [
        "top_resources", "top_activities",
    ],
    GoalCategory.PROCESSING_CAPACITY: [
        "duration_indicators", "top_resources", "top_activities",
    ],
    GoalCategory.RESOURCE_UTILISATION: [
        "top_resources", "top_activities", "duration_indicators",
    ],
    GoalCategory.QUALITY_COMPLIANCE: [
        "rework_activity_case_counts", "top_activities",
        "top_transitions",
    ],
    GoalCategory.THROUGHPUT: [
        "duration_indicators", "top_resources",
    ],
}

# Context evidence metric prefixes relevant to each goal category.
_CONTEXT_METRICS_BY_GOAL: dict[GoalCategory, list[str]] = {
    GoalCategory.WAITING_TIME: ["cycle_time", "wait_time", "turnaround"],
    GoalCategory.PROCESSING_TIME: ["cycle_time", "processing_time", "duration"],
    GoalCategory.COST: ["cost", "expense"],
    GoalCategory.PROCESSING_CAPACITY: ["cycle_time", "throughput"],
    GoalCategory.RESOURCE_UTILISATION: ["utilization", "utilisation", "workload", "idle"],
    GoalCategory.QUALITY_COMPLIANCE: ["rework", "error", "accuracy", "quality", "compliance"],
    GoalCategory.THROUGHPUT: ["throughput", "cycle_time", "completion"],
}


# ===================================================================
# SIMOD baseline filtering
# ===================================================================

def _identify_bottleneck_activities(
    simod_json: dict[str, Any],
) -> list[str]:
    """Return activity names with the longest mean durations (top-3).

    These are high-priority targets for time- and capacity-related goals.
    Works with both common SIMOD output formats.
    """
    durations: dict[str, float] = {}

    # Format A: flat task_durations dict
    for name, spec in simod_json.get("task_durations", {}).items():
        if isinstance(spec, dict):
            durations[name] = spec.get("mean_hours", 0) or spec.get("mean", 0)

    # Format B: task_resource_distribution array (SIMOD native)
    for task in simod_json.get("task_resource_distribution", []):
        task_id = task.get("task_id", "")
        for res in task.get("resources", []):
            params = res.get("distribution_params", [])
            # Typically params[1] is the mean for most distributions
            if len(params) >= 2:
                mean_val = params[1].get("value", 0) if isinstance(params[1], dict) else 0
                if task_id not in durations or mean_val > durations[task_id]:
                    durations[task_id] = mean_val

    sorted_activities = sorted(durations.items(), key=lambda x: x[1], reverse=True)
    return [name for name, _ in sorted_activities[:3]]


def _identify_rework_gateways(
    simod_json: dict[str, Any],
    kpis: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Return gateway names/IDs where one branch has low probability,
    suggesting a rework or exception path — relevant to quality goals."""
    rework_ids: list[str] = []

    # Format A: flat gateway_probabilities dict
    for gw_name, branches in simod_json.get("gateway_probabilities", {}).items():
        if isinstance(branches, dict):
            probs = list(branches.values())
            if any(0.01 < p < 0.20 for p in probs):
                rework_ids.append(gw_name)

    # Format B: gateway_branching_probabilities array (SIMOD native)
    for gw in simod_json.get("gateway_branching_probabilities", []):
        gw_id = gw.get("gateway_id", "")
        probs = [p.get("value", 0) for p in gw.get("probabilities", [])]
        if any(0.01 < p < 0.20 for p in probs):
            rework_ids.append(gw_id)

    return rework_ids


def _build_bpmn_name_map(bpmn_xml: str) -> dict[str, str]:
    """Return node_id → human-readable name for every named BPMN element."""
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
        return name_map
    except Exception:
        return {}


def _enrich_gateway_names(
    simod_json: dict[str, Any],
    name_map: dict[str, str],
) -> dict[str, Any]:
    """Replace raw node IDs with human-readable names in gateway probability data.

    Handles both SIMOD formats:
      - Format A: flat ``gateway_probabilities`` dict
      - Format B: ``gateway_branching_probabilities`` array (Prosimos native)

    IDs that have no name in the map are left unchanged so nothing is lost.
    """
    if not name_map:
        return simod_json

    result = copy.deepcopy(simod_json)

    def resolve(node_id: str) -> str:
        return name_map.get(node_id, node_id)

    # Format A: {"gateway_id": {"path_id": prob, ...}, ...}
    if isinstance(result.get("gateway_probabilities"), dict):
        enriched: dict[str, Any] = {}
        for gw_id, branches in result["gateway_probabilities"].items():
            gw_label = resolve(gw_id)
            if isinstance(branches, dict):
                enriched[gw_label] = {resolve(pid): prob for pid, prob in branches.items()}
            else:
                enriched[gw_label] = branches
        result["gateway_probabilities"] = enriched

    # Format B: [{"gateway_id": ..., "probabilities": [{"path_id": ..., "value": ...}]}]
    for gw in result.get("gateway_branching_probabilities", []):
        if not isinstance(gw, dict):
            continue
        gw_id = gw.get("gateway_id", "")
        if gw_id and gw_id in name_map:
            gw["gateway_id"] = f"{name_map[gw_id]} ({gw_id})"
        for prob_entry in gw.get("probabilities", []):
            if not isinstance(prob_entry, dict):
                continue
            path_id = prob_entry.get("path_id", "")
            if path_id and path_id in name_map:
                prob_entry["path_id"] = f"{name_map[path_id]} ({path_id})"

    return result


def filter_simod_baseline(
    simod_json: dict[str, Any],
    matched_categories: set[GoalCategory],
    kpis: list[dict[str, Any]] | None = None,
    bpmn_xml: str = "",
) -> dict[str, Any]:
    """Filter and annotate the SIMOD baseline for the second LLM prompt.

    Strategy:
      - Include all sections needed by the matched goal categories
      - Annotate bottleneck activities and rework gateways
      - Carry over process_name and any metadata
      - Include everything if the SIMOD output is already compact
    """
    relevant_keys: set[str] = set()
    for cat in matched_categories:
        relevant_keys.update(_SIMOD_SECTIONS_BY_GOAL.get(cat, []))

    relevant_keys.update(["process_name", "name"])

    filtered: dict[str, Any] = {}

    compact_json = json.dumps(simod_json)
    for key in simod_json:
        key_lower = key.lower().replace("-", "_").replace(" ", "_")
        # Include if it matches a relevant key, or if the whole SIMOD
        # output is compact enough that filtering would lose more than
        # it saves (< 20 kB serialised = compact enough)
        if key_lower in relevant_keys or len(compact_json) < 20_000:
            filtered[key] = simod_json[key]

    # Summarise resource_profiles when it is a large per-user list (common
    # when SIMOD uses DIFFERENTIATED_BY_RESOURCE discovery, producing one
    # profile per individual user).  Replace with a compact activity-level
    # summary so the LLM sees resource counts without 100k+ chars of noise.
    if "resource_profiles" in filtered:
        rp = filtered["resource_profiles"]
        if isinstance(rp, list) and len(json.dumps(rp)) > 10_000:
            summary: dict[str, Any] = {}
            for profile in rp:
                if not isinstance(profile, dict):
                    continue
                role = profile.get("name") or profile.get("id", "unknown")
                res_list = profile.get("resource_list", [])
                cost = res_list[0].get("cost_per_hour", 0) if res_list else 0
                tasks = []
                for r in res_list:
                    tasks.extend(r.get("assignedTasks", []))
                tasks = list(set(tasks))
                summary[role] = {
                    "count": len(res_list),
                    "cost_per_hour": cost,
                    "assigned_task_ids": tasks,
                }
            filtered["resource_profiles"] = summary

    bottlenecks = _identify_bottleneck_activities(simod_json)
    if bottlenecks:
        filtered["_annotations"] = filtered.get("_annotations", {})
        filtered["_annotations"]["bottleneck_activities"] = bottlenecks

    if GoalCategory.QUALITY_COMPLIANCE in matched_categories:
        rework_gws = _identify_rework_gateways(simod_json, kpis)
        if rework_gws:
            filtered["_annotations"] = filtered.get("_annotations", {})
            filtered["_annotations"]["probable_rework_gateways"] = rework_gws

    # resource_profiles may be a dict (example format) or a list of
    # profile objects (real Prosimos/pix-framework SIMOD output).
    resource_profiles = simod_json.get("resource_profiles", {})
    if resource_profiles and GoalCategory.RESOURCE_UTILISATION in matched_categories:
        hints: dict[str, Any] = {}
        if isinstance(resource_profiles, dict):
            for role_name, profile in resource_profiles.items():
                if isinstance(profile, dict):
                    hints[role_name] = {
                        "count": profile.get("count", "?"),
                        "cost_per_hour": profile.get("cost_per_hour", "?"),
                        "note": (
                            "Consider whether this pool is over- or "
                            "under-provisioned for the observed workload."
                        ),
                    }
        elif isinstance(resource_profiles, list):
            for profile in resource_profiles:
                if not isinstance(profile, dict):
                    continue
                role_name = profile.get("name") or profile.get("id", "Unknown")
                res_list = profile.get("resource_list", [])
                cost = "?"
                if isinstance(res_list, list) and res_list:
                    cost = res_list[0].get("cost_per_hour", "?")
                hints[role_name] = {
                    "count": len(res_list) if isinstance(res_list, list) else "?",
                    "cost_per_hour": cost,
                    "note": (
                        "Consider whether this pool is over- or "
                        "under-provisioned for the observed workload."
                    ),
                }
        if hints:
            filtered["_annotations"] = filtered.get("_annotations", {})
            filtered["_annotations"]["resource_utilisation_hints"] = hints

    # Resolve raw node IDs to human-readable names in gateway data so the
    # LLM always sees "Standard onboarding (node_xxx)" instead of bare IDs.
    if bpmn_xml:
        name_map = _build_bpmn_name_map(bpmn_xml)
        filtered = _enrich_gateway_names(filtered, name_map)

    return filtered


# ===================================================================
# Log evidence filtering
# ===================================================================

def filter_log_evidence(
    log_profile: dict[str, Any],
    matched_categories: set[GoalCategory],
    kpis: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Select log profile sections relevant to the matched goal categories.

    Always includes the compact summary and measurable signals.
    Goal-specific sections are selected based on what each category needs:
      - time goals → duration_indicators, top_variants, top_transitions
      - quality goals → rework_activity_case_counts
      - resource goals → top_resources
      - etc.

    Also extracts KPI-specific activity names from the verified KPIs to
    highlight which activities the LLM should focus on.
    """
    filtered: dict[str, Any] = {
        "summary": log_profile.get("summary", {}),
        "measurable_signals": log_profile.get("measurable_signals", []),
    }

    sections_needed: set[str] = set()
    for cat in matched_categories:
        sections_needed.update(_LOG_SECTIONS_BY_GOAL.get(cat, []))

    section_limits: dict[str, int] = {
        "top_activities": 10,
        "top_resources": 8,
        "top_variants": 5,
        "top_transitions": 8,
        "rework_activity_case_counts": 5,
    }

    for section in sections_needed:
        data = log_profile.get(section)
        if data is None:
            continue
        if isinstance(data, list):
            limit = section_limits.get(section, 10)
            filtered[section] = data[:limit]
        else:
            filtered[section] = data

    kpi_activities: list[str] = []
    if kpis:
        for kpi in kpis:
            formula = kpi.get("suggested_formula", "")
            name = kpi.get("name", "")

            for activity_entry in log_profile.get("top_activities", []):
                act_name = activity_entry.get("name", "")
                if act_name and (
                    act_name.lower() in formula.lower()
                    or act_name.lower() in name.lower()
                ):
                    if act_name not in kpi_activities:
                        kpi_activities.append(act_name)

    if kpi_activities:
        filtered["_kpi_relevant_activities"] = kpi_activities

    return filtered


# ===================================================================
# Context evidence filtering
# ===================================================================

def filter_context_evidence(
    context_profile: dict[str, Any],
    matched_categories: set[GoalCategory],
    kpis: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Filter context evidence to only include significant relationships
    whose metrics are relevant to the matched goal categories.

    Returns None if no significant relationships survive filtering.
    """
    analysis = context_profile.get("analysis", {})
    significant = analysis.get("significant_relationships", [])

    if not significant:
        significant = context_profile.get("significant_relationships", [])

    if not significant:
        return None

    relevant_prefixes: set[str] = set()
    for cat in matched_categories:
        relevant_prefixes.update(_CONTEXT_METRICS_BY_GOAL.get(cat, []))

    filtered_relationships: list[dict[str, Any]] = []
    for rel in significant:
        metric = rel.get("metric", "").lower()
        if not relevant_prefixes or any(p in metric for p in relevant_prefixes):
            filtered_relationships.append(rel)

    if not filtered_relationships:
        # Fallback: strict filtering removed everything; keep all
        # significant relationships (they passed statistical screening)
        filtered_relationships = significant

    kpi_factors: set[str] = set()
    if kpis:
        for kpi in kpis:
            for seg in kpi.get("context_segmentation", []):
                factor = seg.get("evidence_factor", "")
                if factor:
                    kpi_factors.add(factor.lower())

    if kpi_factors:
        kpi_rels = [
            r for r in filtered_relationships
            if r.get("factor", "").lower() in kpi_factors
        ]
        other_rels = [
            r for r in filtered_relationships
            if r.get("factor", "").lower() not in kpi_factors
        ]
        filtered_relationships = kpi_rels + other_rels

    filtered: dict[str, Any] = {
        "summary": context_profile.get("summary", {}),
        "significant_relationships": filtered_relationships,
        "detected_factors": context_profile.get("detected_factors", []),
    }

    if analysis:
        filtered["screening"] = {
            "significance_threshold": analysis.get("significance_threshold"),
            "effect_thresholds": analysis.get("effect_thresholds", {}),
            "fdr_method": analysis.get("fdr_method"),
        }

    return filtered


# ===================================================================
# Context-differentiation briefing
# ===================================================================

def _build_differentiation_briefing(
    kpis: list[dict[str, Any]] | None,
    context_filtered: dict[str, Any] | None,
    kb_context_rules: list[ContextAwareRule],
) -> str:
    """Synthesise an actionable differentiation briefing.

    Combines three sources into a concrete, LLM-readable instruction
    block that tells the second LLM *exactly* which factors to
    differentiate, what the observed differences are, and how to
    encode them in the SimuBridge scenario.

    Returns an empty string if no differentiation is warranted.
    """
    sig_rels: list[dict[str, Any]] = []
    if context_filtered:
        sig_rels = context_filtered.get("significant_relationships", [])
    if not sig_rels:
        return ""

    kpi_segments: list[dict[str, Any]] = []
    if kpis:
        for kpi in kpis:
            for seg in kpi.get("context_segmentation", []):
                kpi_segments.append({
                    "kpi": kpi.get("name", "?"),
                    "factor": seg.get("evidence_factor", seg.get("condition", "?")),
                    "condition": seg.get("condition", "?"),
                    "target": seg.get("target", "?"),
                    "observed_baseline": seg.get("observed_baseline"),
                    "effect_size": seg.get("effect_size"),
                })

    factor_evidence: dict[str, list[dict[str, Any]]] = {}
    for rel in sig_rels:
        factor = rel.get("factor", "unknown")
        factor_evidence.setdefault(factor, []).append(rel)

    factor_strategies: dict[str, str] = {}
    for rule in kb_context_rules:
        for factor in factor_evidence:
            fl = factor.lower()
            scope_match = any(
                fl in ex.lower() for ex in rule.trigger_factor_examples
            )
            if not scope_match:
                rel = factor_evidence[factor][0]
                temporal_hints = {"day", "week", "hour", "month", "quarter"}
                is_temporal = any(h in fl for h in temporal_hints)
                if is_temporal and rule.trigger_factor_scope == ContextFactorScope.TEMPORAL:
                    scope_match = True
                elif not is_temporal and rule.trigger_factor_scope == ContextFactorScope.CASE_LEVEL:
                    scope_match = True
            if scope_match and factor not in factor_strategies:
                factor_strategies[factor] = rule.differentiation_strategy

    lines: list[str] = []

    for factor, rels in factor_evidence.items():
        lines.append(f"### Factor: {factor}")

        for rel in rels:
            metric = rel.get("metric", "?")
            p_val = rel.get("adjusted_p_value", rel.get("p_value", "?"))
            effect = rel.get("effect_size", "?")
            segments = rel.get("segment_stats") or rel.get("segments", {})

            line = f"- **{metric}**: p={p_val}, effect_size={effect}"
            if isinstance(segments, dict):
                seg_parts = []
                for seg_name, stats in segments.items():
                    if isinstance(stats, dict):
                        mean = stats.get("mean", stats.get("median", "?"))
                        seg_parts.append(f"{seg_name}={mean}")
                    else:
                        seg_parts.append(f"{seg_name}={stats}")
                if seg_parts:
                    line += f" — segments: {', '.join(seg_parts)}"
            elif isinstance(segments, list):
                for s in segments:
                    if isinstance(s, dict):
                        sname = s.get("segment", s.get("name", "?"))
                        smean = s.get("mean", s.get("median", "?"))
                        line += f"; {sname}={smean}"
            lines.append(line)

        relevant_targets = [
            s for s in kpi_segments
            if s["factor"].lower() == factor.lower()
        ]
        if relevant_targets:
            lines.append("- **KPI targets:**")
            for t in relevant_targets:
                lines.append(
                    f"  - {t['kpi']}: {t['condition']} → target {t['target']}"
                )

        strategy = factor_strategies.get(factor)
        if strategy:
            lines.append(f"- **Recommended strategy:** {strategy}")

        lines.append("- **How to encode in SimuBridge:**")
        fl = factor.lower()
        temporal_hints = {"day", "week", "hour", "month", "quarter"}
        is_temporal = any(h in fl for h in temporal_hints)

        if is_temporal:
            lines.append(
                "  Create separate timetable entries for the "
                "different temporal segments (e.g. peak vs off-peak "
                "hours, weekday vs weekend). Adjust resource counts "
                "or working hours per segment."
            )
        else:
            seg_names: list[str] = []
            for rel in rels:
                segments = rel.get("segment_stats") or rel.get("segments", {})
                if isinstance(segments, dict):
                    seg_names.extend(segments.keys())
                elif isinstance(segments, list):
                    for s in segments:
                        if isinstance(s, dict):
                            seg_names.append(
                                s.get("segment", s.get("name", "?"))
                            )
            seg_names = sorted(set(seg_names))
            while len(seg_names) < 2:
                seg_names.append(f"segment_{chr(65 + len(seg_names))}")

            lines.append(
                f"  For each affected role, create segment-specific "
                f"roles: e.g. \"Role_{seg_names[0]}\" and "
                f"\"Role_{seg_names[1]}\", each with its own resource "
                f"count, cost, and schedule. For affected activities, "
                f"set segment-specific duration distributions. Record "
                f"each split in the context_differentiations array."
            )

        lines.append("")

    return "\n".join(lines)

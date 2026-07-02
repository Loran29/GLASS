"""Scenario evaluation — deterministic comparison of baseline vs proposed KPIs.

Computes whether an LLM-generated scenario achieves its intended process
improvements by running both configurations through simulation and comparing
the resulting KPI values.  All comparisons are code-based; no LLM is
involved in determining improvement or violation.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd

from second_llm.kpi_computation import (
    ComputedKPI,
    KPIComputationResult,
    compute_kpis,
)
from second_llm.output_schema import SimuBridgeScenario
from second_llm.prosimos_runner import (
    ProsimosResult,
    get_available_backend,
    is_prosimos_available,
    load_simulation_log,
    run_prosimos_simulation,
)


# -----------------------------------------------------------------------
# Enums & models
# -----------------------------------------------------------------------

class OverallStatus(str, Enum):
    IMPROVED = "improved"
    WORSENED = "worsened"
    TRADE_OFF_DETECTED = "trade_off_detected"
    INCONCLUSIVE = "inconclusive"
    INVALID = "invalid"


class TargetDirection(str, Enum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    MAINTAIN = "maintain"


@dataclass
class KPITarget:
    """A KPI with its optimization direction and optional thresholds."""

    name: str
    direction: TargetDirection
    category: str = ""
    is_safeguard: bool = False
    tolerance: float | None = None  # For maintain-direction: allowed % deviation
    threshold: float | None = None  # Absolute threshold (e.g., max utilization 0.85)
    unit: str = ""
    measurable_as: str | None = None  # Exact computed KPI name from kpi_computation.py


@dataclass
class KPIComparisonEntry:
    """Comparison result for a single KPI."""

    kpi_name: str
    category: str
    target_direction: str
    baseline_value: float | None
    proposed_value: float | None
    unit: str
    absolute_change: float | None
    percentage_change: float | None
    improved: bool | None  # True/False/None
    is_safeguard: bool
    violated_safeguard: bool | None  # True if safeguard was violated
    interpretation: str
    status: str = "computed"  # computed, not_computable, missing_baseline, missing_proposed


@dataclass
class EvaluationSummary:
    """High-level summary of the evaluation result."""

    target_kpis_improved: bool | None
    safeguards_respected: bool | None
    overall_status: OverallStatus
    trade_offs: list[str] = field(default_factory=list)
    recommendation: str = ""


@dataclass
class ScenarioEvaluationResult:
    """Complete evaluation result comparing baseline vs proposed scenario."""

    comparison_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    baseline_kpis: KPIComputationResult | None = None
    proposed_kpis: KPIComputationResult | None = None
    kpi_comparisons: list[KPIComparisonEntry] = field(default_factory=list)
    summary: EvaluationSummary | None = None
    simulation_settings: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.summary is not None


# -----------------------------------------------------------------------
# Comparison logic
# -----------------------------------------------------------------------

def _compute_change(
    baseline: float | None,
    proposed: float | None,
) -> tuple[float | None, float | None]:
    """Compute absolute and percentage change."""
    if baseline is None or proposed is None:
        return None, None
    absolute = proposed - baseline
    if baseline == 0:
        pct = None if proposed == 0 else float("inf") if proposed > 0 else float("-inf")
    else:
        pct = (absolute / abs(baseline)) * 100.0
    return round(absolute, 4), round(pct, 2) if pct is not None and abs(pct) != float("inf") else pct


def _is_improved(
    direction: TargetDirection,
    baseline: float | None,
    proposed: float | None,
    tolerance: float | None = None,
) -> bool | None:
    """Determine if a KPI improved given its target direction."""
    if baseline is None or proposed is None:
        return None

    if direction == TargetDirection.MINIMIZE:
        return proposed < baseline
    elif direction == TargetDirection.MAXIMIZE:
        return proposed > baseline
    elif direction == TargetDirection.MAINTAIN:
        if tolerance is not None and baseline != 0:
            pct_change = abs(proposed - baseline) / abs(baseline) * 100.0
            return pct_change <= tolerance
        return abs(proposed - baseline) < 1e-9
    return None


def _is_safeguard_violated(
    direction: TargetDirection,
    baseline: float | None,
    proposed: float | None,
    tolerance: float | None = None,
    threshold: float | None = None,
) -> bool | None:
    """Check if a safeguard KPI was violated."""
    if baseline is None or proposed is None:
        return None

    if threshold is not None:
        if direction == TargetDirection.MINIMIZE:
            return proposed > threshold
        elif direction == TargetDirection.MAXIMIZE:
            return proposed < threshold
        elif direction == TargetDirection.MAINTAIN:
            if baseline != 0:
                pct_change = abs(proposed - baseline) / abs(baseline) * 100.0
                if tolerance is not None:
                    return pct_change > tolerance
            return proposed > threshold

    if direction == TargetDirection.MAINTAIN:
        if tolerance is not None and baseline != 0:
            pct_change = abs(proposed - baseline) / abs(baseline) * 100.0
            return pct_change > tolerance
        return abs(proposed - baseline) > 1e-9

    if direction == TargetDirection.MINIMIZE:
        return proposed > baseline
    elif direction == TargetDirection.MAXIMIZE:
        return proposed < baseline

    return None


def _generate_interpretation(
    entry: KPIComparisonEntry,
    direction: TargetDirection,
) -> str:
    """Generate a human-readable interpretation of the KPI comparison."""
    if entry.baseline_value is None:
        return f"Baseline value for {entry.kpi_name} could not be computed."
    if entry.proposed_value is None:
        return f"Proposed value for {entry.kpi_name} could not be computed."

    change_word = "increases" if (entry.absolute_change or 0) > 0 else "decreases"
    pct_str = (
        f" ({entry.percentage_change:+.1f}%)"
        if entry.percentage_change is not None
        else ""
    )

    if entry.violated_safeguard:
        return (
            f"The proposed scenario {change_word} {entry.kpi_name}{pct_str}, "
            f"violating the safeguard constraint."
        )
    if entry.improved is True:
        return (
            f"The proposed scenario {change_word} {entry.kpi_name}{pct_str}, "
            f"achieving the intended improvement."
        )
    if entry.improved is False:
        if direction == TargetDirection.MAINTAIN:
            return (
                f"The proposed scenario {change_word} {entry.kpi_name}{pct_str}, "
                f"exceeding the allowed tolerance for a maintain-target."
            )
        return (
            f"The proposed scenario {change_word} {entry.kpi_name}{pct_str}, "
            f"moving in the wrong direction."
        )

    return f"The proposed scenario {change_word} {entry.kpi_name}{pct_str}."


# -----------------------------------------------------------------------
# KPI matching
# -----------------------------------------------------------------------

_TEMPORAL_SUFFIX_RE = re.compile(
    r"\s*(by|per|/)\s*(month|week|day|year|quarter|hour|minute)s?\s*$",
    re.IGNORECASE,
)

_MATCH_STOP_WORDS = frozenset({
    "for", "by", "the", "a", "an", "of", "in", "to", "and", "or",
    "with", "before", "after", "per", "event", "average", "total",
    "overall", "end",
})

_TOKEN_OVERLAP_THRESHOLD = 0.4


def _strip_temporal_suffix(name: str) -> str:
    """Remove trailing temporal qualifiers like 'by Month', 'per Week'."""
    return _TEMPORAL_SUFFIX_RE.sub("", name).strip()


def _token_overlap(a: str, b: str) -> float:
    """Jaccard-style token overlap ignoring stop words."""
    def _tokens(s: str) -> set[str]:
        return set(re.findall(r'\w+', s.lower())) - _MATCH_STOP_WORDS

    a_tok = _tokens(a)
    b_tok = _tokens(b)
    if not a_tok or not b_tok:
        return 0.0
    return len(a_tok & b_tok) / max(len(a_tok), len(b_tok))


def _match_kpi_value(
    target_name: str,
    computed: KPIComputationResult,
    target_category: str = "",
    measurable_as: str | None = None,
) -> ComputedKPI | None:
    """Find the best matching computed KPI for a target KPI name.

    Four passes in order of confidence:
    0. measurable_as exact lookup — LLM-declared mapping, highest trust.
    1. Exact name match (after stripping temporal suffixes).
    2. Substring containment match.
    3. Token-overlap match restricted to same-category KPIs; returns None if
       the target has a category but no same-category KPIs were computed (e.g.
       a 'quality' KPI when compute_kpis produced no quality KPIs).
    4. Category fallback for common synonym mismatches (e.g. "Lead Time").
    """
    if computed is None:
        return None

    # Pass 0 — measurable_as direct lookup (LLM-declared, highest priority)
    if measurable_as:
        lookup = measurable_as.strip().lower()
        for kpi in computed.kpis:
            if kpi.name.lower().strip() == lookup:
                return kpi

    target_lower = _strip_temporal_suffix(target_name).lower().strip()

    # Pass 1 — exact
    for kpi in computed.kpis:
        if kpi.name.lower().strip() == target_lower:
            return kpi

    # Pass 2 — substring
    for kpi in computed.kpis:
        if target_lower in kpi.name.lower() or kpi.name.lower() in target_lower:
            return kpi

    # Pass 3 — token overlap, same-category preferred
    # If a category is known, restrict to same-category candidates first.
    # Only fall through to Pass 4 (not abort) when no same-category KPIs exist,
    # because compute_kpis() never produces quality/compliance/flexibility KPIs.
    if target_category:
        candidates = [k for k in computed.kpis if k.category == target_category]
    else:
        candidates = computed.kpis

    if candidates:
        best_score = 0.0
        best_kpi: ComputedKPI | None = None
        for kpi in candidates:
            score = _token_overlap(target_lower, kpi.name.lower())
            if score > best_score:
                best_score = score
                best_kpi = kpi

        if best_score >= _TOKEN_OVERLAP_THRESHOLD:
            return best_kpi

    # Pass 4 — category fallback: synonyms and paraphrases (e.g. "Lead Time" → "Average Cycle Time")
    # If token overlap found nothing, use the single best computed KPI for the category.
    # Only applies when there is exactly one candidate (unambiguous) or a clear primary exists.
    _CATEGORY_PRIMARY = {
        "time": "average cycle time",
        "cost": "cost per case",
        "utilization": "resource utilization",
        "throughput": "throughput",
    }
    primary_name = _CATEGORY_PRIMARY.get(target_category, "")
    if primary_name:
        for kpi in computed.kpis:
            if kpi.name.lower() == primary_name:
                return kpi

    return None


# -----------------------------------------------------------------------
# Main evaluation logic
# -----------------------------------------------------------------------

def compare_kpis(
    baseline_kpis: KPIComputationResult,
    proposed_kpis: KPIComputationResult,
    targets: list[KPITarget],
) -> ScenarioEvaluationResult:
    """Compare baseline vs proposed KPI values against targets.

    Parameters
    ----------
    baseline_kpis : KPIComputationResult
        KPIs computed from the baseline simulation.
    proposed_kpis : KPIComputationResult
        KPIs computed from the proposed simulation.
    targets : list[KPITarget]
        Target KPIs with direction and safeguard information.

    Returns
    -------
    ScenarioEvaluationResult
        Full structured comparison result.
    """
    result = ScenarioEvaluationResult(
        baseline_kpis=baseline_kpis,
        proposed_kpis=proposed_kpis,
    )

    if not targets:
        result.error = "No target KPIs provided for comparison."
        return result

    target_improved_count = 0
    target_total = 0
    safeguard_violated_count = 0
    safeguard_total = 0

    for target in targets:
        baseline_kpi = _match_kpi_value(target.name, baseline_kpis, target.category, target.measurable_as)
        proposed_kpi = _match_kpi_value(target.name, proposed_kpis, target.category, target.measurable_as)

        baseline_val = baseline_kpi.value if baseline_kpi else None
        proposed_val = proposed_kpi.value if proposed_kpi else None
        unit = target.unit or (baseline_kpi.unit if baseline_kpi else "")

        abs_change, pct_change = _compute_change(baseline_val, proposed_val)

        improved = _is_improved(
            target.direction, baseline_val, proposed_val, target.tolerance
        )
        violated = None
        if target.is_safeguard:
            violated = _is_safeguard_violated(
                target.direction, baseline_val, proposed_val,
                target.tolerance, target.threshold,
            )

        status = "computed"
        if baseline_val is None and proposed_val is None:
            status = "not_computable"
        elif baseline_val is None:
            status = "missing_baseline"
        elif proposed_val is None:
            status = "missing_proposed"

        entry = KPIComparisonEntry(
            kpi_name=_strip_temporal_suffix(target.name),
            category=target.category,
            target_direction=target.direction.value,
            baseline_value=baseline_val,
            proposed_value=proposed_val,
            unit=unit,
            absolute_change=abs_change,
            percentage_change=pct_change,
            improved=improved,
            is_safeguard=target.is_safeguard,
            violated_safeguard=violated,
            interpretation="",
            status=status,
        )
        entry.interpretation = _generate_interpretation(entry, target.direction)
        result.kpi_comparisons.append(entry)

        if target.is_safeguard:
            safeguard_total += 1
            if violated is True:
                safeguard_violated_count += 1
        else:
            target_total += 1
            if improved is True:
                target_improved_count += 1

    # --- Determine overall status ---
    all_targets_improved = target_improved_count == target_total and target_total > 0
    any_target_improved = target_improved_count > 0
    safeguards_ok = safeguard_violated_count == 0
    any_target_worsened = any(
        e.improved is False and not e.is_safeguard
        for e in result.kpi_comparisons
    )

    trade_offs: list[str] = []

    if all_targets_improved and safeguards_ok:
        overall = OverallStatus.IMPROVED
    elif any_target_worsened and not any_target_improved:
        overall = OverallStatus.WORSENED
    elif any_target_improved and not safeguards_ok:
        overall = OverallStatus.TRADE_OFF_DETECTED
        for e in result.kpi_comparisons:
            if e.violated_safeguard:
                improved_names = [
                    x.kpi_name for x in result.kpi_comparisons
                    if x.improved is True and not x.is_safeguard
                ]
                if improved_names:
                    trade_offs.append(
                        f"{', '.join(improved_names)} improved, "
                        f"but {e.kpi_name} safeguard was violated."
                    )
    elif any_target_improved and any_target_worsened:
        overall = OverallStatus.TRADE_OFF_DETECTED
        improved_names = [
            e.kpi_name for e in result.kpi_comparisons
            if e.improved is True and not e.is_safeguard
        ]
        worsened_names = [
            e.kpi_name for e in result.kpi_comparisons
            if e.improved is False and not e.is_safeguard
        ]
        if improved_names and worsened_names:
            trade_offs.append(
                f"{', '.join(improved_names)} improved, "
                f"but {', '.join(worsened_names)} worsened."
            )
    elif any_target_improved and safeguards_ok and not any_target_worsened:
        # Some targets improved, none worsened, safeguards fine — partial improvement
        # counts as improved (unmatched/not_computable KPIs don't block a positive verdict)
        overall = OverallStatus.IMPROVED
    else:
        all_missing = all(
            e.status != "computed" for e in result.kpi_comparisons
        )
        overall = OverallStatus.INCONCLUSIVE if not all_missing else OverallStatus.INVALID

    recommendation = {
        OverallStatus.IMPROVED: "scenario_accepted",
        OverallStatus.WORSENED: "scenario_rejected",
        OverallStatus.TRADE_OFF_DETECTED: "needs_human_review",
        OverallStatus.INCONCLUSIVE: "needs_human_review",
        OverallStatus.INVALID: "invalid_scenario",
    }[overall]

    result.summary = EvaluationSummary(
        target_kpis_improved=all_targets_improved if target_total > 0 else None,
        safeguards_respected=safeguards_ok if safeguard_total > 0 else None,
        overall_status=overall,
        trade_offs=trade_offs,
        recommendation=recommendation,
    )

    return result


# -----------------------------------------------------------------------
# End-to-end evaluation pipeline
# -----------------------------------------------------------------------

def evaluate_scenarios(
    baseline_scenario: SimuBridgeScenario,
    proposed_scenario: SimuBridgeScenario,
    bpmn_xml: str,
    targets: list[KPITarget],
    *,
    total_cases: int = 1000,
    start_time: str = "2024-01-01 09:00:00.000000+00:00",
    seed: int = 42,
    cost_per_hour: dict[str, float] | None = None,
    baseline_log_path: str | None = None,
    proposed_log_path: str | None = None,
) -> ScenarioEvaluationResult:
    """Run the full evaluation pipeline: simulate both scenarios and compare KPIs.

    If Prosimos is not available or simulation fails, falls back to loading
    pre-computed log files if paths are provided.

    Parameters
    ----------
    baseline_scenario : SimuBridgeScenario
        The as-is SIMOD baseline scenario.
    proposed_scenario : SimuBridgeScenario
        The LLM-generated proposed scenario (after patch merge).
    bpmn_xml : str
        BPMN 2.0 XML for the process model.
    targets : list[KPITarget]
        Target KPIs to evaluate (with direction and safeguard flags).
    total_cases : int
        Number of cases to simulate.
    start_time : str
        Simulation start timestamp.
    seed : int
        Random seed for reproducibility.
    cost_per_hour : dict, optional
        Resource → hourly cost mapping for cost KPI computation.
    baseline_log_path : str, optional
        Path to a pre-computed baseline simulation CSV (fallback).
    proposed_log_path : str, optional
        Path to a pre-computed proposed simulation CSV (fallback).
    """
    settings = {
        "total_cases": total_cases,
        "start_time": start_time,
        "seed": seed,
        "prosimos_available": is_prosimos_available(),
        "backend": str(get_available_backend()),
    }

    backend = get_available_backend()

    # --- Simulate baseline ---
    baseline_result: ProsimosResult | None = None
    if backend is not None:
        baseline_result = run_prosimos_simulation(
            baseline_scenario, bpmn_xml,
            total_cases=total_cases, start_time=start_time, seed=seed,
        )
    if (baseline_result is None or not baseline_result.ok) and baseline_log_path:
        baseline_result = load_simulation_log(baseline_log_path)
        settings["baseline_source"] = "file"
    elif baseline_result and baseline_result.ok:
        settings["baseline_source"] = "prosimos"

    if baseline_result is None or not baseline_result.ok:
        return ScenarioEvaluationResult(
            error=f"Baseline simulation failed: {baseline_result.error if baseline_result else 'No simulator available'}",
            simulation_settings=settings,
        )

    # --- Simulate proposed ---
    proposed_result: ProsimosResult | None = None
    if backend is not None:
        proposed_result = run_prosimos_simulation(
            proposed_scenario, bpmn_xml,
            total_cases=total_cases, start_time=start_time, seed=seed,
        )
    if (proposed_result is None or not proposed_result.ok) and proposed_log_path:
        proposed_result = load_simulation_log(proposed_log_path)
        settings["proposed_source"] = "file"
    elif proposed_result and proposed_result.ok:
        settings["proposed_source"] = "prosimos"

    if proposed_result is None or not proposed_result.ok:
        return ScenarioEvaluationResult(
            error=f"Proposed simulation failed: {proposed_result.error if proposed_result else 'No simulator available'}",
            simulation_settings=settings,
        )

    # --- Compute KPIs ---
    baseline_kpis = compute_kpis(
        baseline_result.simulated_log,
        cost_per_hour=cost_per_hour,
    )
    proposed_kpis = compute_kpis(
        proposed_result.simulated_log,
        cost_per_hour=cost_per_hour,
    )

    # --- Compare ---
    eval_result = compare_kpis(baseline_kpis, proposed_kpis, targets)
    eval_result.simulation_settings = settings

    return eval_result


def evaluate_from_logs(
    baseline_log: pd.DataFrame,
    proposed_log: pd.DataFrame,
    targets: list[KPITarget],
    *,
    cost_per_hour: dict[str, float] | None = None,
) -> ScenarioEvaluationResult:
    """Evaluate two pre-loaded simulation logs without running Prosimos.

    Useful when simulation results already exist (e.g., from external tools).
    """
    baseline_kpis = compute_kpis(baseline_log, cost_per_hour=cost_per_hour)
    proposed_kpis = compute_kpis(proposed_log, cost_per_hour=cost_per_hour)

    eval_result = compare_kpis(baseline_kpis, proposed_kpis, targets)
    eval_result.simulation_settings = {"source": "pre_loaded_logs"}
    return eval_result

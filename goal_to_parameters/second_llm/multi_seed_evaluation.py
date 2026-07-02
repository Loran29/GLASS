"""Multi-seed scenario evaluation with 95% confidence intervals.

Runs both baseline and proposed scenarios N times with different random seeds,
then aggregates the KPI values into mean ± std with Student's t confidence
intervals.  A paired t-test is used to test whether the per-seed difference
for each KPI is statistically significant (p < 0.05).

Academic reference: Law & Kelton (2000) — independent replications method
for discrete-event simulation output analysis.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from second_llm.output_schema import SimuBridgeScenario
from second_llm.prosimos_runner import run_prosimos_simulation
from second_llm.kpi_computation import compute_kpis
from second_llm.scenario_evaluation import (
    EvaluationSummary,
    KPITarget,
    OverallStatus,
    TargetDirection,
    _match_kpi_value,
    _strip_temporal_suffix,
    _is_improved,
    _is_safeguard_violated,
)


# -----------------------------------------------------------------------
# Data models
# -----------------------------------------------------------------------

@dataclass
class SeedStats:
    """Descriptive statistics across N seeds for one scenario's KPI."""

    mean: float
    std: float
    ci_lower: float
    ci_upper: float
    n: int
    values: list[float] = field(default_factory=list)


@dataclass
class MultiSeedKPIComparison:
    """Per-KPI comparison result with confidence intervals."""

    kpi_name: str
    category: str
    target_direction: str
    is_safeguard: bool
    unit: str

    baseline_stats: SeedStats | None
    proposed_stats: SeedStats | None

    # Mean values (shorthand for display)
    mean_baseline: float | None
    mean_proposed: float | None

    # % change of the means
    mean_percentage_change: float | None

    # CI on the per-seed delta (paired)
    ci_lower_change: float | None
    ci_upper_change: float | None

    statistically_significant: bool | None  # p < 0.05 paired t-test

    improved: bool | None
    violated_safeguard: bool | None
    p_value: float | None = None
    status: str = "computed"  # computed | not_computable | missing_baseline | missing_proposed


@dataclass
class MultiSeedEvaluationResult:
    """Complete multi-seed evaluation result."""

    kpi_comparisons: list[MultiSeedKPIComparison] = field(default_factory=list)
    summary: EvaluationSummary | None = None
    seeds_used: list[int] = field(default_factory=list)
    n_seeds: int = 0
    total_time_seconds: float = 0.0
    simulation_settings: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # Full KPI computation results from the last seed run (for all-KPI display)
    last_seed_baseline_kpis: Any = None
    last_seed_proposed_kpis: Any = None
    # Averaged KPI results across all seeds (mean per KPI) for all-KPI display
    averaged_baseline_kpis: Any = None
    averaged_proposed_kpis: Any = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.summary is not None


# -----------------------------------------------------------------------
# Statistics helpers
# -----------------------------------------------------------------------

def _compute_seed_stats(values: list[float]) -> SeedStats:
    """Compute mean, std, and 95% CI (Student's t) for a list of seed values."""
    import math
    from scipy import stats  # type: ignore

    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / max(n - 1, 1)
    std = math.sqrt(variance)
    sem = std / math.sqrt(n)

    if n >= 2 and sem > 0:
        ci = stats.t.interval(0.95, df=n - 1, loc=mean, scale=sem)
        ci_lower, ci_upper = float(ci[0]), float(ci[1])
    else:
        ci_lower = ci_upper = mean

    return SeedStats(
        mean=round(mean, 4),
        std=round(std, 4),
        ci_lower=round(ci_lower, 4),
        ci_upper=round(ci_upper, 4),
        n=n,
        values=values,
    )


def _paired_ttest(baseline_vals: list[float], proposed_vals: list[float]) -> tuple[float | None, bool | None]:
    """Return (p_value, is_significant) from a paired t-test."""
    from scipy import stats  # type: ignore

    n = len(baseline_vals)
    if n < 2 or len(proposed_vals) != n:
        return None, None

    try:
        result = stats.ttest_rel(baseline_vals, proposed_vals)
        p_value = float(result.pvalue)
        return p_value, p_value < 0.05
    except Exception:
        return None, None


def _mean_pct_change(mean_baseline: float, mean_proposed: float) -> float | None:
    if mean_baseline == 0:
        return None
    return round((mean_proposed - mean_baseline) / abs(mean_baseline) * 100.0, 2)


def _ci_on_delta(
    baseline_vals: list[float],
    proposed_vals: list[float],
) -> tuple[float | None, float | None]:
    """Compute 95% CI on the per-seed absolute delta (proposed - baseline)."""
    n = min(len(baseline_vals), len(proposed_vals))
    if n < 2:
        return None, None

    deltas = [proposed_vals[i] - baseline_vals[i] for i in range(n)]
    stats_obj = _compute_seed_stats(deltas)
    return stats_obj.ci_lower, stats_obj.ci_upper


# -----------------------------------------------------------------------
# Main function
# -----------------------------------------------------------------------
# Averaging helper
# -----------------------------------------------------------------------

def _build_averaged_kpi_result(
    collections: dict[str, list[float]],
    reference: Any,
) -> Any:
    """Build a KPIComputationResult whose values are means across seeds.

    Units and categories are taken from the reference (last-seed) result.
    Details include n_seeds and std across seed means.
    """
    import math
    from second_llm.kpi_computation import ComputedKPI, KPIComputationResult

    ref_map = {k.name: k for k in reference.kpis}
    result = KPIComputationResult()

    for name, values in sorted(collections.items()):
        if not values:
            continue
        n = len(values)
        mean_val = sum(values) / n
        variance = sum((v - mean_val) ** 2 for v in values) / max(n - 1, 1)
        std_val = math.sqrt(variance)

        ref = ref_map.get(name)
        result.kpis.append(ComputedKPI(
            name=name,
            value=round(mean_val, 4),
            unit=ref.unit if ref else "",
            category=ref.category if ref else "",
            details={"n_seeds": n, "std": round(std_val, 4)},
        ))

    return result


# -----------------------------------------------------------------------
# Main function
# -----------------------------------------------------------------------

def evaluate_multi_seed(
    baseline_scenario: SimuBridgeScenario,
    proposed_scenario: SimuBridgeScenario,
    bpmn_xml: str,
    targets: list[KPITarget],
    *,
    num_seeds: int = 5,
    base_seed: int = 42,
    total_cases: int = 1000,
    start_time: str = "2024-01-01 09:00:00.000000+00:00",
    cost_per_hour: dict[str, float] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> MultiSeedEvaluationResult:
    """Run baseline and proposed scenarios with N different seeds.

    Parameters
    ----------
    baseline_scenario, proposed_scenario : SimuBridgeScenario
        The as-is and LLM-proposed scenarios.
    bpmn_xml : str
        BPMN 2.0 XML shared by both scenarios.
    targets : list[KPITarget]
        KPI targets with direction and safeguard flags.
    num_seeds : int
        Number of independent replications (5–10 recommended).
    base_seed : int
        First seed; subsequent seeds are base_seed + 1, base_seed + 2, …
    total_cases : int
        Number of cases per simulation run.
    start_time : str
        Simulation start timestamp.
    cost_per_hour : dict, optional
        Resource → hourly cost for cost KPI computation.
    on_progress : callable, optional
        Called as on_progress(seed_index, total_seeds, message) after each seed.
    """
    t_start = time.perf_counter()
    seeds = [base_seed + i for i in range(num_seeds)]
    result = MultiSeedEvaluationResult(seeds_used=seeds, n_seeds=num_seeds)

    # kpi_name → list of (baseline_value, proposed_value) per seed — target KPIs only
    baseline_collections: dict[str, list[float]] = {}
    proposed_collections: dict[str, list[float]] = {}

    # kpi_name → list of values per seed — ALL computed KPIs (for averaged display)
    all_baseline_collections: dict[str, list[float]] = {}
    all_proposed_collections: dict[str, list[float]] = {}
    # Reference KPI objects (last seed) for unit/category metadata
    _ref_baseline_kpis: Any = None
    _ref_proposed_kpis: Any = None

    settings: dict[str, Any] = {
        "num_seeds": num_seeds,
        "base_seed": base_seed,
        "total_cases": total_cases,
        "start_time": start_time,
        "seeds": seeds,
    }

    for seed_idx, seed in enumerate(seeds):
        msg = f"Running seed {seed} ({seed_idx + 1}/{num_seeds})…"
        if on_progress:
            on_progress(seed_idx, num_seeds, msg)

        baseline_run = run_prosimos_simulation(
            baseline_scenario, bpmn_xml,
            total_cases=total_cases, start_time=start_time, seed=seed,
        )
        if baseline_run is None or not baseline_run.ok:
            result.error = f"Baseline simulation failed on seed {seed}: {baseline_run.error if baseline_run else 'unknown'}"
            return result

        proposed_run = run_prosimos_simulation(
            proposed_scenario, bpmn_xml,
            total_cases=total_cases, start_time=start_time, seed=seed,
        )
        if proposed_run is None or not proposed_run.ok:
            result.error = f"Proposed simulation failed on seed {seed}: {proposed_run.error if proposed_run else 'unknown'}"
            return result

        baseline_kpis = compute_kpis(baseline_run.simulated_log, cost_per_hour=cost_per_hour)
        proposed_kpis = compute_kpis(proposed_run.simulated_log, cost_per_hour=cost_per_hour)

        # Collect ALL KPI values across seeds for averaging
        for kpi in baseline_kpis.kpis:
            if kpi.value is not None:
                all_baseline_collections.setdefault(kpi.name, []).append(kpi.value)
        for kpi in proposed_kpis.kpis:
            if kpi.value is not None:
                all_proposed_collections.setdefault(kpi.name, []).append(kpi.value)
        _ref_baseline_kpis = baseline_kpis
        _ref_proposed_kpis = proposed_kpis

        for target in targets:
            key = _strip_temporal_suffix(target.name)
            b_kpi = _match_kpi_value(target.name, baseline_kpis, target.category, target.measurable_as)
            p_kpi = _match_kpi_value(target.name, proposed_kpis, target.category, target.measurable_as)

            if b_kpi is not None and b_kpi.value is not None:
                baseline_collections.setdefault(key, []).append(b_kpi.value)
            if p_kpi is not None and p_kpi.value is not None:
                proposed_collections.setdefault(key, []).append(p_kpi.value)

    if on_progress:
        on_progress(num_seeds, num_seeds, "Aggregating results…")

    # --- Build averaged KPIComputationResult for all-KPI display ---
    if _ref_baseline_kpis is not None and _ref_proposed_kpis is not None:
        result.averaged_baseline_kpis = _build_averaged_kpi_result(
            all_baseline_collections, _ref_baseline_kpis
        )
        result.averaged_proposed_kpis = _build_averaged_kpi_result(
            all_proposed_collections, _ref_proposed_kpis
        )

    # --- Build per-KPI comparisons ---
    target_improved_count = 0
    target_total = 0
    safeguard_violated_count = 0
    safeguard_total = 0

    for target in targets:
        key = _strip_temporal_suffix(target.name)
        b_vals = baseline_collections.get(key, [])
        p_vals = proposed_collections.get(key, [])

        # Determine status
        if not b_vals and not p_vals:
            status = "not_computable"
        elif not b_vals:
            status = "missing_baseline"
        elif not p_vals:
            status = "missing_proposed"
        else:
            status = "computed"

        b_stats = _compute_seed_stats(b_vals) if b_vals else None
        p_stats = _compute_seed_stats(p_vals) if p_vals else None

        mean_b = b_stats.mean if b_stats else None
        mean_p = p_stats.mean if p_stats else None

        mean_pct = _mean_pct_change(mean_b, mean_p) if (mean_b is not None and mean_p is not None) else None

        ci_lo, ci_hi = None, None
        p_val, significant = None, None
        if b_vals and p_vals and len(b_vals) == len(p_vals):
            ci_lo, ci_hi = _ci_on_delta(b_vals, p_vals)
            p_val, significant = _paired_ttest(b_vals, p_vals)

        improved = _is_improved(target.direction, mean_b, mean_p, target.tolerance)
        violated = None
        if target.is_safeguard:
            violated = _is_safeguard_violated(
                target.direction, mean_b, mean_p, target.tolerance, target.threshold,
            )

        # Resolve unit
        unit = target.unit
        if not unit and b_vals:
            # Try to get unit from first-seed kpi object (not available here, leave blank)
            unit = ""

        comp = MultiSeedKPIComparison(
            kpi_name=key,
            category=target.category,
            target_direction=target.direction.value,
            is_safeguard=target.is_safeguard,
            unit=unit,
            baseline_stats=b_stats,
            proposed_stats=p_stats,
            mean_baseline=mean_b,
            mean_proposed=mean_p,
            mean_percentage_change=mean_pct,
            ci_lower_change=ci_lo,
            ci_upper_change=ci_hi,
            statistically_significant=significant,
            p_value=p_val,
            improved=improved,
            violated_safeguard=violated,
            status=status,
        )
        result.kpi_comparisons.append(comp)

        if target.is_safeguard:
            safeguard_total += 1
            if violated is True:
                safeguard_violated_count += 1
        else:
            target_total += 1
            if improved is True:
                target_improved_count += 1

    # --- Summary ---
    all_targets_improved = target_improved_count == target_total and target_total > 0
    any_target_improved = target_improved_count > 0
    safeguards_ok = safeguard_violated_count == 0
    any_target_worsened = any(
        c.improved is False and not c.is_safeguard
        for c in result.kpi_comparisons
    )

    trade_offs: list[str] = []
    if all_targets_improved and safeguards_ok:
        overall = OverallStatus.IMPROVED
    elif any_target_worsened and not any_target_improved:
        overall = OverallStatus.WORSENED
    elif any_target_improved and any_target_worsened:
        overall = OverallStatus.TRADE_OFF_DETECTED
        improved_names = [c.kpi_name for c in result.kpi_comparisons if c.improved is True and not c.is_safeguard]
        worsened_names = [c.kpi_name for c in result.kpi_comparisons if c.improved is False and not c.is_safeguard]
        trade_offs.append(f"{', '.join(improved_names)} improved, but {', '.join(worsened_names)} worsened.")
    elif any_target_improved and not safeguards_ok:
        overall = OverallStatus.TRADE_OFF_DETECTED
    elif any_target_improved and safeguards_ok and not any_target_worsened:
        overall = OverallStatus.IMPROVED
    else:
        overall = OverallStatus.INCONCLUSIVE

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

    result.simulation_settings = settings
    result.total_time_seconds = round(time.perf_counter() - t_start, 1)
    return result

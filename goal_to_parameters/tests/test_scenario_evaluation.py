"""Tests for the scenario evaluation module — baseline vs proposed KPI comparison."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from second_llm.kpi_computation import ComputedKPI, KPIComputationResult, compute_kpis
from second_llm.scenario_evaluation import (
    KPIComparisonEntry,
    KPITarget,
    OverallStatus,
    ScenarioEvaluationResult,
    TargetDirection,
    compare_kpis,
    evaluate_from_logs,
)


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

def _make_kpi_result(kpis: list[tuple[str, float, str, str]]) -> KPIComputationResult:
    """Build a KPIComputationResult from (name, value, unit, category) tuples."""
    return KPIComputationResult(
        kpis=[
            ComputedKPI(name=n, value=v, unit=u, category=c)
            for n, v, u, c in kpis
        ]
    )


def _make_event_log(
    n_cases: int = 100,
    avg_duration_h: float = 2.0,
    avg_wait_h: float = 1.0,
    n_activities: int = 5,
) -> pd.DataFrame:
    """Generate a synthetic event log for testing."""
    rng = np.random.default_rng(42)
    rows = []
    start = pd.Timestamp("2024-01-01 09:00:00")

    for case_id in range(1, n_cases + 1):
        t = start + pd.Timedelta(hours=rng.exponential(0.5))
        for act_idx in range(n_activities):
            wait = pd.Timedelta(hours=max(0, rng.normal(avg_wait_h, 0.5)))
            t += wait
            duration = pd.Timedelta(hours=max(0.01, rng.normal(avg_duration_h, 0.5)))
            rows.append({
                "case_id": f"case_{case_id}",
                "activity": f"Activity_{act_idx + 1}",
                "resource": f"Resource_{(act_idx % 3) + 1}",
                "start_time": t,
                "end_time": t + duration,
            })
            t += duration

    return pd.DataFrame(rows)


# -----------------------------------------------------------------------
# Test: KPI comparison with target_direction = minimize
# -----------------------------------------------------------------------

class TestMinimizeDirection:
    def test_improvement_detected(self):
        baseline = _make_kpi_result([("Average Cycle Time", 12.4, "hours", "time")])
        proposed = _make_kpi_result([("Average Cycle Time", 9.8, "hours", "time")])
        targets = [KPITarget(name="Average Cycle Time", direction=TargetDirection.MINIMIZE, category="time")]

        result = compare_kpis(baseline, proposed, targets)

        assert result.ok
        assert len(result.kpi_comparisons) == 1
        entry = result.kpi_comparisons[0]
        assert entry.improved is True
        assert entry.absolute_change < 0
        assert entry.percentage_change < 0
        assert result.summary.overall_status == OverallStatus.IMPROVED

    def test_worsening_detected(self):
        baseline = _make_kpi_result([("Average Cycle Time", 10.0, "hours", "time")])
        proposed = _make_kpi_result([("Average Cycle Time", 14.0, "hours", "time")])
        targets = [KPITarget(name="Average Cycle Time", direction=TargetDirection.MINIMIZE, category="time")]

        result = compare_kpis(baseline, proposed, targets)

        entry = result.kpi_comparisons[0]
        assert entry.improved is False
        assert result.summary.overall_status == OverallStatus.WORSENED


# -----------------------------------------------------------------------
# Test: KPI comparison with target_direction = maximize
# -----------------------------------------------------------------------

class TestMaximizeDirection:
    def test_improvement_detected(self):
        baseline = _make_kpi_result([("Throughput", 5.0, "cases/day", "throughput")])
        proposed = _make_kpi_result([("Throughput", 7.5, "cases/day", "throughput")])
        targets = [KPITarget(name="Throughput", direction=TargetDirection.MAXIMIZE, category="throughput")]

        result = compare_kpis(baseline, proposed, targets)

        entry = result.kpi_comparisons[0]
        assert entry.improved is True
        assert entry.percentage_change == 50.0
        assert result.summary.overall_status == OverallStatus.IMPROVED

    def test_worsening_detected(self):
        baseline = _make_kpi_result([("Throughput", 10.0, "cases/day", "throughput")])
        proposed = _make_kpi_result([("Throughput", 8.0, "cases/day", "throughput")])
        targets = [KPITarget(name="Throughput", direction=TargetDirection.MAXIMIZE, category="throughput")]

        result = compare_kpis(baseline, proposed, targets)

        entry = result.kpi_comparisons[0]
        assert entry.improved is False


# -----------------------------------------------------------------------
# Test: KPI comparison with target_direction = maintain + tolerance
# -----------------------------------------------------------------------

class TestMaintainDirection:
    def test_within_tolerance(self):
        baseline = _make_kpi_result([("Resource Utilization", 0.70, "ratio", "utilization")])
        proposed = _make_kpi_result([("Resource Utilization", 0.73, "ratio", "utilization")])
        targets = [KPITarget(
            name="Resource Utilization",
            direction=TargetDirection.MAINTAIN,
            category="utilization",
            tolerance=10.0,  # 10% tolerance
        )]

        result = compare_kpis(baseline, proposed, targets)

        entry = result.kpi_comparisons[0]
        assert entry.improved is True  # within tolerance = maintained = improved

    def test_exceeds_tolerance(self):
        baseline = _make_kpi_result([("Resource Utilization", 0.70, "ratio", "utilization")])
        proposed = _make_kpi_result([("Resource Utilization", 0.91, "ratio", "utilization")])
        targets = [KPITarget(
            name="Resource Utilization",
            direction=TargetDirection.MAINTAIN,
            category="utilization",
            tolerance=10.0,
        )]

        result = compare_kpis(baseline, proposed, targets)

        entry = result.kpi_comparisons[0]
        assert entry.improved is False


# -----------------------------------------------------------------------
# Test: Safeguard violation detection
# -----------------------------------------------------------------------

class TestSafeguardViolation:
    def test_safeguard_respected(self):
        baseline = _make_kpi_result([
            ("Average Cycle Time", 12.0, "hours", "time"),
            ("Resource Utilization", 0.65, "ratio", "utilization"),
        ])
        proposed = _make_kpi_result([
            ("Average Cycle Time", 9.0, "hours", "time"),
            ("Resource Utilization", 0.70, "ratio", "utilization"),
        ])
        targets = [
            KPITarget(name="Average Cycle Time", direction=TargetDirection.MINIMIZE, category="time"),
            KPITarget(
                name="Resource Utilization", direction=TargetDirection.MAINTAIN,
                category="utilization", is_safeguard=True, threshold=0.85,
            ),
        ]

        result = compare_kpis(baseline, proposed, targets)

        assert result.summary.overall_status == OverallStatus.IMPROVED
        assert result.summary.safeguards_respected is True
        safeguard_entry = result.kpi_comparisons[1]
        assert safeguard_entry.violated_safeguard is False

    def test_safeguard_violated(self):
        baseline = _make_kpi_result([
            ("Average Cycle Time", 12.0, "hours", "time"),
            ("Resource Utilization", 0.65, "ratio", "utilization"),
        ])
        proposed = _make_kpi_result([
            ("Average Cycle Time", 9.0, "hours", "time"),
            ("Resource Utilization", 0.91, "ratio", "utilization"),
        ])
        targets = [
            KPITarget(name="Average Cycle Time", direction=TargetDirection.MINIMIZE, category="time"),
            KPITarget(
                name="Resource Utilization", direction=TargetDirection.MAINTAIN,
                category="utilization", is_safeguard=True, threshold=0.85,
            ),
        ]

        result = compare_kpis(baseline, proposed, targets)

        assert result.summary.overall_status == OverallStatus.TRADE_OFF_DETECTED
        assert result.summary.safeguards_respected is False
        safeguard_entry = result.kpi_comparisons[1]
        assert safeguard_entry.violated_safeguard is True


# -----------------------------------------------------------------------
# Test: Missing KPI value handling
# -----------------------------------------------------------------------

class TestMissingValues:
    def test_missing_baseline(self):
        baseline = _make_kpi_result([])  # no KPIs computed
        proposed = _make_kpi_result([("Average Cycle Time", 9.0, "hours", "time")])
        targets = [KPITarget(name="Average Cycle Time", direction=TargetDirection.MINIMIZE, category="time")]

        result = compare_kpis(baseline, proposed, targets)

        entry = result.kpi_comparisons[0]
        assert entry.status == "missing_baseline"
        assert entry.improved is None
        assert result.summary.overall_status in (OverallStatus.INCONCLUSIVE, OverallStatus.INVALID)

    def test_missing_proposed(self):
        baseline = _make_kpi_result([("Average Cycle Time", 12.0, "hours", "time")])
        proposed = _make_kpi_result([])
        targets = [KPITarget(name="Average Cycle Time", direction=TargetDirection.MINIMIZE, category="time")]

        result = compare_kpis(baseline, proposed, targets)

        entry = result.kpi_comparisons[0]
        assert entry.status == "missing_proposed"
        assert entry.improved is None


# -----------------------------------------------------------------------
# Test: Percentage change when baseline is zero
# -----------------------------------------------------------------------

class TestZeroBaseline:
    def test_baseline_zero(self):
        baseline = _make_kpi_result([("Cost per Case", 0.0, "EUR", "cost")])
        proposed = _make_kpi_result([("Cost per Case", 5.0, "EUR", "cost")])
        targets = [KPITarget(name="Cost per Case", direction=TargetDirection.MINIMIZE, category="cost")]

        result = compare_kpis(baseline, proposed, targets)

        entry = result.kpi_comparisons[0]
        assert entry.absolute_change == 5.0
        # percentage_change should be None or inf when baseline is 0
        assert entry.percentage_change is None or entry.percentage_change == float("inf")

    def test_both_zero(self):
        baseline = _make_kpi_result([("Cost per Case", 0.0, "EUR", "cost")])
        proposed = _make_kpi_result([("Cost per Case", 0.0, "EUR", "cost")])
        targets = [KPITarget(name="Cost per Case", direction=TargetDirection.MINIMIZE, category="cost")]

        result = compare_kpis(baseline, proposed, targets)

        entry = result.kpi_comparisons[0]
        assert entry.absolute_change == 0.0
        assert entry.percentage_change is None


# -----------------------------------------------------------------------
# Test: Overall status determination
# -----------------------------------------------------------------------

class TestOverallStatus:
    def test_improved(self):
        baseline = _make_kpi_result([("Cycle Time", 10.0, "h", "time")])
        proposed = _make_kpi_result([("Cycle Time", 7.0, "h", "time")])
        targets = [KPITarget(name="Cycle Time", direction=TargetDirection.MINIMIZE, category="time")]

        result = compare_kpis(baseline, proposed, targets)
        assert result.summary.overall_status == OverallStatus.IMPROVED

    def test_worsened(self):
        baseline = _make_kpi_result([("Cycle Time", 10.0, "h", "time")])
        proposed = _make_kpi_result([("Cycle Time", 15.0, "h", "time")])
        targets = [KPITarget(name="Cycle Time", direction=TargetDirection.MINIMIZE, category="time")]

        result = compare_kpis(baseline, proposed, targets)
        assert result.summary.overall_status == OverallStatus.WORSENED

    def test_trade_off(self):
        baseline = _make_kpi_result([
            ("Cycle Time", 10.0, "h", "time"),
            ("Throughput", 5.0, "c/d", "throughput"),
        ])
        proposed = _make_kpi_result([
            ("Cycle Time", 8.0, "h", "time"),
            ("Throughput", 3.0, "c/d", "throughput"),
        ])
        targets = [
            KPITarget(name="Cycle Time", direction=TargetDirection.MINIMIZE, category="time"),
            KPITarget(name="Throughput", direction=TargetDirection.MAXIMIZE, category="throughput"),
        ]

        result = compare_kpis(baseline, proposed, targets)
        assert result.summary.overall_status == OverallStatus.TRADE_OFF_DETECTED

    def test_inconclusive(self):
        baseline = _make_kpi_result([])
        proposed = _make_kpi_result([])
        targets = [KPITarget(name="Cycle Time", direction=TargetDirection.MINIMIZE, category="time")]

        result = compare_kpis(baseline, proposed, targets)
        assert result.summary.overall_status in (OverallStatus.INCONCLUSIVE, OverallStatus.INVALID)

    def test_invalid_no_targets(self):
        baseline = _make_kpi_result([("Cycle Time", 10.0, "h", "time")])
        proposed = _make_kpi_result([("Cycle Time", 8.0, "h", "time")])

        result = compare_kpis(baseline, proposed, [])
        assert result.error is not None


# -----------------------------------------------------------------------
# Test: KPI computation from synthetic logs
# -----------------------------------------------------------------------

class TestKPIComputation:
    def test_computes_basic_kpis(self):
        log = _make_event_log(n_cases=50, avg_duration_h=1.0, avg_wait_h=0.5)
        result = compute_kpis(log, include_activity_kpis=False)

        assert result.error is None
        names = {k.name for k in result.kpis}
        assert "Average Cycle Time" in names
        assert "Average Waiting Time" in names
        assert "Throughput" in names
        assert "Resource Utilization" in names

    def test_empty_log(self):
        result = compute_kpis(pd.DataFrame(), include_activity_kpis=False)
        assert result.error is not None


# -----------------------------------------------------------------------
# Test: evaluate_from_logs end-to-end
# -----------------------------------------------------------------------

class TestEvaluateFromLogs:
    def test_end_to_end(self):
        baseline_log = _make_event_log(n_cases=100, avg_duration_h=2.0, avg_wait_h=1.5)
        proposed_log = _make_event_log(n_cases=100, avg_duration_h=1.5, avg_wait_h=0.8)

        targets = [
            KPITarget(name="Average Cycle Time", direction=TargetDirection.MINIMIZE, category="time"),
            KPITarget(name="Average Waiting Time", direction=TargetDirection.MINIMIZE, category="time"),
        ]

        result = evaluate_from_logs(baseline_log, proposed_log, targets)

        assert result.ok
        assert result.summary is not None
        assert len(result.kpi_comparisons) == 2

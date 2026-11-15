"""Tests for computational cost and impact estimation."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from second_llm.cost_estimation import (
    CostEstimate,
    QueueingEstimate,
    ScenarioCostReport,
    _erlang_c,
    _estimate_utilization,
    _expected_wait_factor,
    build_cost_report,
    compute_weekly_hours,
)
from second_llm.context_summary import OperationalContextSummary
from second_llm.output_schema import (
    Activity,
    DistributionParameter,
    ModelParameter,
    ProcessModel,
    Resource,
    ResourceParameters,
    Role,
    ScenarioProposal,
    SimuBridgeScenario,
    StartEvent,
    TimeDistribution,
    Timetable,
    TimetableItem,
    Weekday,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_timetable(
    tid: str = "default",
    items: list[tuple] | None = None,
) -> Timetable:
    """Build a Timetable from (startDay, startHour, endDay, endHour) tuples."""
    if items is None:
        # Standard Mon-Fri 9-17, one entry per day
        items = [
            (Weekday.MONDAY, 9, Weekday.MONDAY, 17),
            (Weekday.TUESDAY, 9, Weekday.TUESDAY, 17),
            (Weekday.WEDNESDAY, 9, Weekday.WEDNESDAY, 17),
            (Weekday.THURSDAY, 9, Weekday.THURSDAY, 17),
            (Weekday.FRIDAY, 9, Weekday.FRIDAY, 17),
        ]
    return Timetable(
        id=tid,
        timeTableItems=[
            TimetableItem(
                startWeekday=sd, startTime=sh, endWeekday=ed, endTime=eh,
            )
            for sd, sh, ed, eh in items
        ],
    )


def _make_proposal(
    *,
    modifications=None,
    roles=None,
    timetables=None,
    activities=None,
    events=None,
):
    """Build a minimal ScenarioProposal for testing."""
    if timetables is None:
        timetables = [_make_timetable()]
    if roles is None:
        roles = [
            Role(
                id="Analyst",
                schedule="default",
                costHour=50,
                resources=[Resource(id=f"A{i}") for i in range(5)],
            ),
        ]
    if activities is None:
        activities = [
            Activity(
                id="act1",
                name="Review",
                resources=["Analyst"],
                duration=TimeDistribution(
                    distributionType="normal",
                    timeUnit="hours",
                    values=[
                        DistributionParameter(id="mean", value=2.0),
                        DistributionParameter(id="variance", value=0.5),
                    ],
                ),
            ),
        ]
    if events is None:
        events = [
            StartEvent(
                id="start1",
                interArrivalTime=TimeDistribution(
                    distributionType="exponential",
                    timeUnit="hours",
                    values=[DistributionParameter(id="mean", value=1.0)],
                ),
            ),
        ]
    if modifications is None:
        modifications = [
            {
                "parameter_type": "resource_count",
                "target_element": "Analyst",
                "direction": "increase",
                "baseline_value": "5",
                "proposed_value": "7",
                "kpi_reference": "Cycle Time",
                "rationale": "Add analysts to reduce queue.",
            },
        ]

    scenario = SimuBridgeScenario(
        scenarioName="test",
        resourceParameters=ResourceParameters(
            roles=roles,
            timeTables=timetables,
        ),
        models=[
            ProcessModel(
                name="test_process",
                modelParameter=ModelParameter(
                    activities=activities,
                    events=events,
                ),
            ),
        ],
    )
    return ScenarioProposal(
        scenario_name="test",
        reasoning="Test scenario",
        modifications=modifications,
        expected_kpi_impacts=[
            {"kpi_name": "Cycle Time", "direction": "decrease"},
        ],
        scenario=scenario,
    )


# ── Weekly hours ─────────────────────────────────────────────────────────

class TestComputeWeeklyHours:
    def test_standard_five_day_schedule(self):
        tt = _make_timetable()  # Mon-Fri 9-17, one entry per day
        assert compute_weekly_hours(tt) == 40.0

    def test_single_day_block(self):
        tt = _make_timetable(items=[
            (Weekday.MONDAY, 9, Weekday.MONDAY, 17),
        ])
        assert compute_weekly_hours(tt) == 8.0

    def test_multiday_range(self):
        """SimuBridge convention: the daily window (endTime-startTime)
        applies to each weekday in [startWeekday..endWeekday].
        Mon 9 -> Wed 17 means 8h on Mon, Tue, Wed = 24h."""
        tt = _make_timetable(items=[
            (Weekday.MONDAY, 9, Weekday.WEDNESDAY, 17),
        ])
        assert compute_weekly_hours(tt) == 24.0

    def test_reversed_weekday_range_is_empty(self):
        """Friday 22 -> Monday 6 is not a valid weekly span under the
        SimuBridge convention; such items contribute zero hours, in sync
        with the validator's interpretation."""
        tt = _make_timetable(items=[
            (Weekday.FRIDAY, 22, Weekday.MONDAY, 6),
        ])
        assert compute_weekly_hours(tt) == 0.0

    def test_empty_block(self):
        """Monday 9 -> Monday 9 = 0 hours."""
        tt = _make_timetable(items=[
            (Weekday.MONDAY, 9, Weekday.MONDAY, 9),
        ])
        assert compute_weekly_hours(tt) == 0.0

    def test_multiple_blocks_sum(self):
        """Two blocks: morning and afternoon."""
        tt = _make_timetable(items=[
            (Weekday.MONDAY, 8, Weekday.MONDAY, 12),
            (Weekday.MONDAY, 13, Weekday.MONDAY, 17),
        ])
        assert compute_weekly_hours(tt) == 8.0


# ── Erlang-C ─────────────────────────────────────────────────────────────

class TestErlangC:
    def test_low_utilization(self):
        """At low utilization, P(wait) should be very small."""
        p = _erlang_c(5, 0.2)
        assert p < 0.05

    def test_high_utilization(self):
        """At high utilization, P(wait) should be substantial."""
        p = _erlang_c(5, 0.9)
        assert p > 0.3

    def test_single_server(self):
        """M/M/1 special case: P(wait) = rho."""
        p = _erlang_c(1, 0.5)
        assert abs(p - 0.5) < 0.01

    def test_saturated_returns_one(self):
        """rho >= 1 means the system is unstable."""
        assert _erlang_c(5, 1.0) == 1.0
        assert _erlang_c(5, 1.5) == 1.0

    def test_zero_utilization(self):
        assert _erlang_c(5, 0.0) == 0.0

    def test_adding_servers_reduces_wait_probability(self):
        """More servers at the same total load -> lower P(wait)."""
        p5 = _erlang_c(5, 0.85)
        p7 = _erlang_c(7, 0.85 * 5 / 7)
        assert p7 < p5


class TestExpectedWaitFactor:
    def test_more_servers_lower_wait(self):
        rho = 0.85
        wf5 = _expected_wait_factor(5, rho)
        wf7 = _expected_wait_factor(7, rho * 5 / 7)
        assert wf7 < wf5

    def test_saturated_returns_inf(self):
        assert _expected_wait_factor(5, 1.0) == float("inf")

    def test_zero_utilization(self):
        assert _expected_wait_factor(5, 0.0) == 0.0


# ── Utilization estimation ───────────────────────────────────────────────

class TestEstimateUtilization:
    def test_computes_from_scenario_data(self):
        proposal = _make_proposal()
        rho, source = _estimate_utilization("Analyst", 5, proposal)
        # lambda = 1/h, processing = 2h/case, c = 5
        # rho = 1 * 2 / 5 = 0.4
        assert source == "computed"
        assert abs(rho - 0.4) < 0.01

    def test_falls_back_when_no_activities(self):
        proposal = _make_proposal(activities=[
            Activity(
                id="act1", name="Review", resources=["Other"],
                duration=TimeDistribution(
                    distributionType="constant", timeUnit="hours",
                    values=[DistributionParameter(id="constantValue", value=1.0)],
                ),
            ),
        ])
        rho, source = _estimate_utilization("Analyst", 5, proposal)
        assert source == "assumed"
        assert rho == 0.85

    def test_falls_back_when_no_events(self):
        proposal = _make_proposal(events=[])
        rho, source = _estimate_utilization("Analyst", 5, proposal)
        assert source == "assumed"


# ── Cost estimation ──────────────────────────────────────────────────────

class TestBuildCostReport:
    def test_resource_count_increase_cost(self):
        proposal = _make_proposal()
        report = build_cost_report(proposal)

        assert report.has_estimates
        assert len(report.cost_estimates) == 1

        ce = report.cost_estimates[0]
        # +2 analysts * 50 EUR/h * 40 h/week * 4.33 wk/month = 17,320
        expected = 2 * 50 * 40 * 4.33
        assert abs(ce.monthly_cost - expected) < 1.0
        assert ce.target_element == "Analyst"
        assert report.total_monthly_cost == ce.monthly_cost

    def test_no_estimates_for_duration_change(self):
        proposal = _make_proposal(modifications=[
            {
                "parameter_type": "activity_duration",
                "target_element": "Review",
                "direction": "decrease",
                "baseline_value": "2.0 hours",
                "proposed_value": "1.5 hours",
                "kpi_reference": "Cycle Time",
                "rationale": "Reduce review time.",
            },
        ])
        report = build_cost_report(proposal)
        assert not report.has_estimates

    def test_queueing_estimate_generated(self):
        proposal = _make_proposal()
        report = build_cost_report(proposal)

        assert len(report.queueing_estimates) == 1
        qe = report.queueing_estimates[0]
        assert qe.baseline_servers == 5
        assert qe.proposed_servers == 7
        assert qe.wait_reduction_pct > 0

    def test_budget_exceeded_detection(self):
        proposal = _make_proposal()
        ctx = OperationalContextSummary({
            "budget": {"additional_monthly": 1000, "currency": "EUR"},
        })
        report = build_cost_report(proposal, context_summary=ctx)

        # Cost is ~17,320 which exceeds 1,000
        assert report.exceeds_budget
        assert report.budget_limit == 1000.0
        assert len(report.notes) >= 1

    def test_within_budget(self):
        proposal = _make_proposal()
        ctx = OperationalContextSummary({
            "budget": {"additional_monthly": 50000, "currency": "EUR"},
        })
        report = build_cost_report(proposal, context_summary=ctx)
        assert not report.exceeds_budget

    def test_no_context_no_budget_check(self):
        proposal = _make_proposal()
        report = build_cost_report(proposal)
        assert report.budget_limit is None
        assert not report.exceeds_budget

    def test_missing_role_skipped_with_note(self):
        proposal = _make_proposal(modifications=[
            {
                "parameter_type": "resource_count",
                "target_element": "NonExistentRole",
                "direction": "increase",
                "baseline_value": "3",
                "proposed_value": "5",
                "kpi_reference": "Cycle Time",
                "rationale": "Add resources.",
            },
        ])
        report = build_cost_report(proposal)
        assert not report.cost_estimates
        assert any("NonExistentRole" in n for n in report.notes)

    def test_resource_decrease_negative_cost(self):
        proposal = _make_proposal(modifications=[
            {
                "parameter_type": "resource_count",
                "target_element": "Analyst",
                "direction": "decrease",
                "baseline_value": "5",
                "proposed_value": "3",
                "kpi_reference": "Cycle Time",
                "rationale": "Reduce analysts.",
            },
        ])
        report = build_cost_report(proposal)

        assert len(report.cost_estimates) == 1
        assert report.cost_estimates[0].monthly_cost < 0
        # No queueing estimate for decreases
        assert len(report.queueing_estimates) == 0

    def test_to_prompt_section_nonempty(self):
        proposal = _make_proposal()
        report = build_cost_report(proposal)
        section = report.to_prompt_section()
        assert "Cost estimates" in section
        assert "M/M/c" in section

    def test_to_prompt_section_empty_when_no_estimates(self):
        proposal = _make_proposal(modifications=[
            {
                "parameter_type": "activity_duration",
                "target_element": "Review",
                "direction": "decrease",
                "baseline_value": "2.0 hours",
                "proposed_value": "1.5 hours",
                "kpi_reference": "Cycle Time",
                "rationale": "Reduce time.",
            },
        ])
        report = build_cost_report(proposal)
        assert report.to_prompt_section() == ""


# ── Validation integration ───────────────────────────────────────────────

class TestBudgetValidation:
    def test_budget_exceeded_in_validation(self):
        from second_llm.validation import validate_proposal

        proposal = _make_proposal()
        ctx = OperationalContextSummary({
            "budget": {"additional_monthly": 1000, "currency": "EUR"},
        })
        vr = validate_proposal(proposal, context_summary=ctx)
        # Budget issues are categorised as "feasibility" (soft warning ≤10% over)
        # or "budget_exceeded" (error >10% over).  The default proposal costs
        # ~17,320 EUR/month against a 1,000 budget, so the error branch fires.
        budget_issues = [
            i for i in vr.issues
            if "budget" in i.category or "budget" in i.message.lower()
        ]
        assert len(budget_issues) == 1

    def test_no_budget_issue_without_context(self):
        from second_llm.validation import validate_proposal

        proposal = _make_proposal()
        vr = validate_proposal(proposal)
        budget_issues = [
            i for i in vr.issues
            if "budget" in i.category or "budget" in i.message.lower()
        ]
        assert len(budget_issues) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""Tests for the operational context summary and feasibility validation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from second_llm.context_summary import OperationalContextSummary, build_context_summary  # noqa: E402


class OperationalContextSummaryTests(unittest.TestCase):
    """Unit tests for OperationalContextSummary."""

    def test_empty_summary(self) -> None:
        s = OperationalContextSummary()
        self.assertTrue(s.is_empty)
        self.assertEqual(s.resource_constraints, {})
        self.assertEqual(s.immutable_parameters, [])
        self.assertFalse(s.is_role_fixed("Analyst"))
        self.assertEqual(s.get_immutable_elements(), set())

    def test_role_fixed_detection(self) -> None:
        s = OperationalContextSummary({
            "resource_constraints": {
                "Analyst": {"staffing_flexible": False, "reason": "union contract"},
                "Clerk": {"staffing_flexible": True},
            },
        })
        self.assertFalse(s.is_empty)
        self.assertTrue(s.is_role_fixed("Analyst"))
        self.assertTrue(s.is_role_fixed("analyst"))  # case-insensitive
        self.assertFalse(s.is_role_fixed("Clerk"))
        self.assertFalse(s.is_role_fixed("Manager"))

    def test_immutable_elements(self) -> None:
        s = OperationalContextSummary({
            "immutable_parameters": [
                {"element": "Final Approval", "parameter": "duration", "reason": "regulation"},
                {"element": "Intake", "parameter": "assignment", "reason": "policy"},
            ],
        })
        immutable = s.get_immutable_elements()
        self.assertEqual(immutable, {"final approval", "intake"})

    def test_to_json_roundtrip(self) -> None:
        import json
        data = {
            "budget": {"additional_monthly": 5000, "currency": "EUR"},
            "sla_constraints": [{"metric": "cycle_time", "threshold": "48h"}],
        }
        s = OperationalContextSummary(data)
        parsed = json.loads(s.to_json())
        self.assertEqual(parsed["budget"]["additional_monthly"], 5000)
        self.assertEqual(len(parsed["sla_constraints"]), 1)

    def test_build_without_provider_returns_empty(self) -> None:
        messages = [{"role": "user", "content": "Budget is 5000 EUR/month."}]
        result = build_context_summary(messages, provider=None)
        self.assertTrue(result.is_empty)

    def test_build_with_empty_messages_returns_empty(self) -> None:
        result = build_context_summary([], provider=None)
        self.assertTrue(result.is_empty)


class FeasibilityValidationTests(unittest.TestCase):
    """Tests for feasibility-aware validation using context summary."""

    def _make_minimal_proposal(self, modifications):
        """Build a minimal ScenarioProposal for testing."""
        from second_llm.output_schema import (
            ScenarioProposal,
            SimuBridgeScenario,
            ResourceParameters,
            Role,
            Resource,
            Timetable,
            TimetableItem,
            ProcessModel,
            ModelParameter,
            Activity,
            TimeDistribution,
            DistributionParameter,
            Weekday,
        )

        tt = Timetable(
            id="default",
            timeTableItems=[TimetableItem(
                startWeekday=Weekday.MONDAY, startTime=9,
                endWeekday=Weekday.FRIDAY, endTime=17,
            )],
        )
        role = Role(
            id="Analyst", schedule="default", costHour=50,
            resources=[Resource(id="A1")],
        )
        act = Activity(
            id="act1", name="Review", resources=["Analyst"],
            duration=TimeDistribution(
                distributionType="normal", timeUnit="hours",
                values=[
                    DistributionParameter(id="mean", value=2.0),
                    DistributionParameter(id="variance", value=0.5),
                ],
            ),
        )
        scenario = SimuBridgeScenario(
            scenarioName="test",
            resourceParameters=ResourceParameters(
                roles=[role], timeTables=[tt],
            ),
            models=[ProcessModel(
                name="test_process",
                modelParameter=ModelParameter(activities=[act]),
            )],
        )
        return ScenarioProposal(
            scenario_name="test",
            reasoning="Test scenario",
            modifications=modifications,
            expected_kpi_impacts=[{
                "kpi_name": "Cycle Time",
                "direction": "decrease",
            }],
            scenario=scenario,
        )

    def test_fixed_staffing_flagged(self) -> None:
        from second_llm.output_schema import ParameterModification
        from second_llm.validation import validate_proposal

        mod = ParameterModification(
            parameter_type="resource_count",
            target_element="Analyst",
            direction="increase",
            baseline_value="5",
            proposed_value="7",
            kpi_reference="Cycle Time",
            rationale="Add analysts to reduce queue.",
        )
        proposal = self._make_minimal_proposal([mod])
        ctx = OperationalContextSummary({
            "resource_constraints": {
                "Analyst": {"staffing_flexible": False, "reason": "union"},
            },
        })
        vr = validate_proposal(proposal, context_summary=ctx)
        feasibility_issues = [
            i for i in vr.issues if i.category == "feasibility"
        ]
        self.assertEqual(len(feasibility_issues), 1)
        self.assertIn("fixed staffing", feasibility_issues[0].message)

    def test_immutable_element_flagged(self) -> None:
        from second_llm.output_schema import ParameterModification
        from second_llm.validation import validate_proposal

        mod = ParameterModification(
            parameter_type="activity_duration",
            target_element="Final Approval",
            direction="decrease",
            baseline_value="3.0 hours",
            proposed_value="2.0 hours",
            kpi_reference="Cycle Time",
            rationale="Reduce approval time.",
        )
        proposal = self._make_minimal_proposal([mod])
        ctx = OperationalContextSummary({
            "immutable_parameters": [
                {"element": "Final Approval", "parameter": "duration", "reason": "regulation"},
            ],
        })
        vr = validate_proposal(proposal, context_summary=ctx)
        feasibility_issues = [
            i for i in vr.issues if i.category == "feasibility"
        ]
        self.assertEqual(len(feasibility_issues), 1)
        self.assertIn("immutable", feasibility_issues[0].message)

    def test_no_feasibility_issues_without_context(self) -> None:
        from second_llm.output_schema import ParameterModification
        from second_llm.validation import validate_proposal

        mod = ParameterModification(
            parameter_type="resource_count",
            target_element="Analyst",
            direction="increase",
            baseline_value="5",
            proposed_value="7",
            kpi_reference="Cycle Time",
            rationale="Add analysts.",
        )
        proposal = self._make_minimal_proposal([mod])
        vr = validate_proposal(proposal)
        feasibility_issues = [
            i for i in vr.issues if i.category == "feasibility"
        ]
        self.assertEqual(len(feasibility_issues), 0)


if __name__ == "__main__":
    unittest.main()

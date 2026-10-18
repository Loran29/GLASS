"""Relative budget limits from the clarification context are enforced.

The context summary records a relative staff-cost limit ("we can accept up to 35 % higher staff cost") as
budget.max_increase_pct. GLASS showed it to the LLM but only enforced absolute monthly budgets. Now
validate_proposal (given the baseline) compares the merged scenario's total staff cost with the
baseline's: each resource's hourly cost times its weekly working hours, so added resources and extended
calendars both count. Above the limit the proposal gets an error with category budget_exceeded, whose
message states the limit, the baseline cost and the proposed cost.

All data is synthetic: three generic roles at 39, 30 and 31 per hour (baseline 100 per hour, 40 h/week).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from second_llm.compatibility_adapter import build_legacy_proposal  # noqa: E402
from second_llm.context_summary import OperationalContextSummary  # noqa: E402
from second_llm.output_schema_patch import ScenarioPatch  # noqa: E402
from second_llm.scenario_merger import apply_patch  # noqa: E402
from second_llm.simod_to_simubridge import build_baseline_scenario  # noqa: E402
from second_llm.validation import staff_cost_per_week, validate_proposal  # noqa: E402

BPMN = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="defs">'
    '<process id="proc" isExecutable="false">'
    '<startEvent id="Start_1" /><task id="Task_A" name="Task A" /><endEvent id="End_1" />'
    '<sequenceFlow id="f1" sourceRef="Start_1" targetRef="Task_A" />'
    '<sequenceFlow id="f2" sourceRef="Task_A" targetRef="End_1" />'
    "</process></definitions>"
)
DAY = [{"from": "MONDAY", "to": "FRIDAY", "beginTime": "09:00:00", "endTime": "17:00:00"}]
RATES = {"Role A": 39, "Role B": 30, "Role C": 31}
LONGER = [{"startWeekday": "Monday", "endWeekday": "Friday", "startTime": 9, "endTime": 19}]


def simod() -> dict:
    profiles = [{"id": f"P{i}", "name": role, "resource_list": [
        {"id": f"P{i}_1", "name": f"{role}-1", "cost_per_hour": rate, "amount": 1, "calendar": f"Cal{i}"}]}
        for i, (role, rate) in enumerate(RATES.items())]
    return {
        "resource_profiles": profiles,
        "resource_calendars": [{"id": f"Cal{i}", "name": f"Cal{i}", "time_periods": DAY} for i in range(3)],
        "task_resource_distribution": [{"task_id": "Task_A", "resources": [
            {"resource_id": f"P{i}_1", "distribution_name": "fix", "distribution_params": [{"value": 600}]}
            for i in range(3)]}],
        "arrival_time_distribution": {"distribution_name": "fix", "distribution_params": [{"value": 1800}]},
        "gateway_branching_probabilities": [],
    }


def baseline():
    built = build_baseline_scenario(simod(), bpmn_xml=BPMN)
    assert built.ok, built.errors
    return built.scenario


def proposal(base, mods: list[dict]):
    patch = ScenarioPatch.model_validate({
        "scenario_id": "S", "baseline_reference": "SIMOD", "reasoning": "test",
        "modifications": [{"kpi_reference": "Cycle Time", "rationale": "Add capacity.",
                           "evidence_source": "SIMOD", "literature_support": [1], **m} for m in mods],
        "expected_kpi_impacts": [{"kpi_name": "Cycle Time", "direction": "decrease", "estimated_magnitude": "",
                                  "confidence": "medium", "reasoning": ""}]})
    merged = apply_patch(base, patch)
    assert not merged.skipped_modifications, merged.diagnostics
    return build_legacy_proposal(patch, merged.scenario)


def add_one(role: str) -> dict:
    return {"parameter_type": "resource_count", "target_element": role, "direction": "increase",
            "baseline_value": "1", "proposed_value": "2"}


RELATIVE = OperationalContextSummary({"budget": {"max_increase_pct": 35}})


def budget_errors(vr):
    return [i for i in vr.issues if i.category == "budget_exceeded" and i.severity == "error"]


class RelativeBudget(unittest.TestCase):
    def test_cost_counts_headcount_and_hours(self):
        base = baseline()
        self.assertEqual(staff_cost_per_week(base), 100 * 40)
        longer = proposal(base, [{"parameter_type": "resource_calendar", "target_element": "Role C",
                                  "direction": "redistribute", "baseline_value": "09:00-17:00",
                                  "proposed_value": "09:00-19:00", "proposed_structured": {"timeTableItems": LONGER}}])
        self.assertEqual(staff_cost_per_week(longer.scenario), (39 + 30) * 40 + 31 * 50)

    def test_39_percent_rejected_with_feedback(self):
        base = baseline()
        vr = validate_proposal(proposal(base, [add_one("Role A")]), context_summary=RELATIVE, baseline=base)
        errors = budget_errors(vr)
        self.assertEqual(len(errors), 1)
        msg = errors[0].message
        for part in ("39.0 %", "+35 %", "baseline 4,000 per week", "proposed 5,560 per week"):
            self.assertIn(part, msg)

    def test_30_percent_accepted(self):
        base = baseline()
        vr = validate_proposal(proposal(base, [add_one("Role B")]), context_summary=RELATIVE, baseline=base)
        self.assertEqual(budget_errors(vr), [])

    def test_absolute_budgets_unchanged(self):
        base = baseline()
        prop = proposal(base, [add_one("Role A")])
        no_limit = OperationalContextSummary({"budget": {"additional_monthly": 1_000_000}})
        self.assertEqual(budget_errors(validate_proposal(prop, context_summary=no_limit, baseline=base)), [])
        # Without the baseline (as before), the relative limit is not checked.
        self.assertEqual(budget_errors(validate_proposal(prop, context_summary=RELATIVE)), [])
        tight = OperationalContextSummary({"budget": {"additional_monthly": 10}})
        absolute = budget_errors(validate_proposal(prop, context_summary=tight, baseline=base))
        self.assertEqual(len(absolute), 1)
        self.assertIn("/month", absolute[0].message)


if __name__ == "__main__":
    unittest.main()

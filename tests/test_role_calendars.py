"""Role calendars: import keeps each resource's own calendar; a calendar patch applies to the whole role.

Previously the import bound a role to its last resource's calendar and exported that calendar for every
resource, while the other resources' calendars stayed in the scenario unused; a resource_calendar
modification on one of those changed nothing in the simulation. Now each resource keeps its own calendar
(when it differs from the role's schedule) and a resource_calendar modification that names a role, or any
timetable a role uses, applies to every resource of that role.

All process data is synthetic: one generic task ("Task A") and one role with two resources.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from second_llm.output_schema import KPIImpact, ModificationDirection  # noqa: E402
from second_llm.output_schema_patch import PatchModification, PatchParameterType, ScenarioPatch  # noqa: E402
from second_llm.prosimos_runner import scenario_to_prosimos_json  # noqa: E402
from second_llm.scenario_merger import apply_patch  # noqa: E402
from second_llm.simod_to_simubridge import build_baseline_scenario  # noqa: E402

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
LONGER = [{"startWeekday": "Monday", "endWeekday": "Friday", "startTime": 9, "endTime": 19}]
LONGER_EXPORTED = [{"from": "MONDAY", "to": "FRIDAY", "beginTime": "09:00:00", "endTime": "19:00:00"}]


def simod() -> dict:
    return {
        "resource_profiles": [{"id": "Role_X", "name": "Role X", "resource_list": [
            {"id": "Res_1", "name": "Res 1", "cost_per_hour": 20, "amount": 1, "calendar": "Cal_1"},
            {"id": "Res_2", "name": "Res 2", "cost_per_hour": 20, "amount": 1, "calendar": "Cal_2"}]}],
        "resource_calendars": [{"id": "Cal_1", "name": "Cal_1", "time_periods": DAY},
                               {"id": "Cal_2", "name": "Cal_2", "time_periods": DAY}],
        "task_resource_distribution": [{"task_id": "Task_A", "resources": [
            {"resource_id": "Res_1", "distribution_name": "fix", "distribution_params": [{"value": 600}]},
            {"resource_id": "Res_2", "distribution_name": "fix", "distribution_params": [{"value": 600}]}]}],
        "arrival_time_distribution": {"distribution_name": "fix", "distribution_params": [{"value": 1800}]},
        "gateway_branching_probabilities": [],
    }


def exported_calendars(scenario) -> dict[str, list[dict]]:
    """Resource id -> time periods of the calendar it gets in the Prosimos export."""
    out = scenario_to_prosimos_json(scenario, BPMN)
    periods = {c["id"]: c["time_periods"] for c in out["resource_calendars"]}
    return {r["id"]: periods[r["calendar"]] for p in out["resource_profiles"] for r in p["resource_list"]}


def calendar_patch(target: str) -> ScenarioPatch:
    mod = PatchModification(
        parameter_type=PatchParameterType.RESOURCE_CALENDAR, target_element=target,
        direction=ModificationDirection.INCREASE, baseline_value="09:00-17:00", proposed_value="09:00-19:00",
        proposed_structured={"timeTableItems": LONGER}, kpi_reference="Average Cycle Time",
        rationale="Longer working day.", evidence_source="baseline")
    return ScenarioPatch(scenario_id="test", reasoning="test", modifications=[mod],
                         expected_kpi_impacts=[KPIImpact(kpi_name="Average Cycle Time", direction="decrease",
                                                         confidence="medium", reasoning="More capacity.")])


class RoleCalendarTests(unittest.TestCase):
    def setUp(self):
        self.baseline = build_baseline_scenario(simod(), bpmn_xml=BPMN).scenario

    def test_import_keeps_each_resource_calendar(self):
        role = self.baseline.resourceParameters.roles[0]
        self.assertEqual({r.id: r.calendar or role.schedule for r in role.resources},
                         {"Res_1": "Cal_1", "Res_2": "Cal_2"})
        out = scenario_to_prosimos_json(self.baseline, BPMN)
        self.assertEqual({r["id"]: r["calendar"] for r in out["resource_profiles"][0]["resource_list"]},
                         {"Res_1": "Cal_1", "Res_2": "Cal_2"})

    def test_role_calendar_patch_changes_every_resource_calendar(self):
        for target in ("Cal_1", "Cal_2", "Role X"):     # one resource's calendar, the role schedule, the role
            with self.subTest(target=target):
                merged = apply_patch(self.baseline, calendar_patch(target))
                self.assertFalse(merged.has_errors, merged.error_messages)
                self.assertEqual(exported_calendars(merged.scenario),
                                 {"Res_1": LONGER_EXPORTED, "Res_2": LONGER_EXPORTED})
        self.assertEqual(exported_calendars(self.baseline), {"Res_1": DAY, "Res_2": DAY})   # baseline untouched

    def test_shared_calendar_is_unchanged_in_the_model(self):
        data = simod()
        for res in data["resource_profiles"][0]["resource_list"]:
            res["calendar"] = "Cal_1"
        role = build_baseline_scenario(data, bpmn_xml=BPMN).scenario.resourceParameters.roles[0]
        self.assertEqual([r.calendar for r in role.resources], [None, None])
        self.assertNotIn("calendar", role.resources[0].model_dump())


if __name__ == "__main__":
    unittest.main()

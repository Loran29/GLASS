"""Arrival and resource calendars in the Prosimos export.

The export used to replace the baseline's arrival calendar with 24/7 and fill every
weekday missing from a resource calendar with that calendar's hours (so Mon–Fri
calendars became Mon–Sun). Now the arrival calendar is imported into the scenario and
exported, and resource calendars are exported as they are. Baselines whose calendars
are already 24/7 must export byte-identically to before (fingerprints from commit
cb9cb17).

All process data is synthetic: one generic task ("Task A") and one role.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

H24 = [{"from": "MONDAY", "to": "SUNDAY", "beginTime": "00:00:00", "endTime": "23:59:59"}]
# Time format as written by Prosimos/SIMOD converters.
MON_FRI = [{"from": "MONDAY", "to": "FRIDAY", "beginTime": "09:00:00.000", "endTime": "17:00:00.000"}]
MON_FRI_EXPORTED = [{"from": "MONDAY", "to": "FRIDAY", "beginTime": "09:00:00", "endTime": "17:00:00"}]

BPMN = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="defs">'
    '<process id="proc" isExecutable="false">'
    '<startEvent id="Start_1" name="Start" />'
    '<task id="Task_A" name="Task A" />'
    '<endEvent id="End_1" name="End" />'
    '<sequenceFlow id="f1" sourceRef="Start_1" targetRef="Task_A" />'
    '<sequenceFlow id="f2" sourceRef="Task_A" targetRef="End_1" />'
    "</process></definitions>"
)


def simod_json(resource_calendar: list[dict], arrival_calendar: list[dict] | None = None) -> dict:
    data = {
        "resource_profiles": [
            {"id": "Role_X", "name": "Role X", "resource_list": [
                {"id": "Res_1", "name": "Res 1", "cost_per_hour": 20, "amount": 1, "calendar": "Cal_1",
                 "assignedTasks": ["Task_A"]},
            ]},
        ],
        "resource_calendars": [{"id": "Cal_1", "name": "Cal_1", "time_periods": resource_calendar}],
        "task_resource_distribution": [
            {"task_id": "Task_A", "resources": [
                {"resource_id": "Res_1", "distribution_name": "fix", "distribution_params": [{"value": 600}]},
            ]},
        ],
        "arrival_time_distribution": {"distribution_name": "expon",
                                      "distribution_params": [{"value": 1800}, {"value": 0}, {"value": 18000}]},
        "gateway_branching_probabilities": [],
    }
    if arrival_calendar is not None:
        data["arrival_time_calendar"] = arrival_calendar
    return data


def baseline(data: dict):
    from second_llm.simod_to_simubridge import build_baseline_scenario
    return build_baseline_scenario(data, bpmn_xml=BPMN).scenario


def export(scenario) -> dict:
    from second_llm.prosimos_runner import scenario_to_prosimos_json
    return scenario_to_prosimos_json(scenario, BPMN)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def h24_fingerprints() -> dict[str, str]:
    """SHA-256 of every output that must stay byte-identical for baselines with 24/7 calendars."""
    no_arrival = baseline(simod_json(H24))
    return {
        "export_no_arrival_calendar": sha(json.dumps(export(no_arrival))),
        "export_explicit_24_7_arrival_calendar": sha(json.dumps(export(baseline(simod_json(H24, H24))))),
        "baseline_serialisation_no_arrival_calendar": sha(no_arrival.model_dump_json()),
    }


# Computed with the code before this change (commit cb9cb17), for the inputs above.
PRE_CHANGE_FINGERPRINTS = {
    "export_no_arrival_calendar": "f6b039447ffd05ea0f6b610acdc6b5cbdbddbf739f578469c2a0da049cb12bca",
    "export_explicit_24_7_arrival_calendar": "f6b039447ffd05ea0f6b610acdc6b5cbdbddbf739f578469c2a0da049cb12bca",
    "baseline_serialisation_no_arrival_calendar": "ad5f973fb8eb83b1c46f5395a08301861c06df2d06dbe918003f188cff415fec",
}


import unittest  # noqa: E402

from second_llm.output_schema import KPIImpact, ModificationDirection, SimuBridgeScenario  # noqa: E402
from second_llm.output_schema_patch import PatchModification, PatchParameterType, ScenarioPatch  # noqa: E402
from second_llm.scenario_merger import apply_patch  # noqa: E402


class CalendarTests(unittest.TestCase):
    def test_arrival_calendar_round_trips(self):
        scenario = baseline(simod_json(H24, MON_FRI))
        self.assertEqual(export(scenario)["arrival_time_calendar"], MON_FRI_EXPORTED)
        # ... and survives serialising and re-loading the scenario.
        reloaded = SimuBridgeScenario.model_validate_json(scenario.model_dump_json())
        self.assertEqual(export(reloaded)["arrival_time_calendar"], MON_FRI_EXPORTED)

    def test_resource_calendar_round_trips(self):
        calendars = export(baseline(simod_json(MON_FRI)))["resource_calendars"]
        self.assertTrue(calendars)
        for cal in calendars:
            self.assertEqual(cal["time_periods"], MON_FRI_EXPORTED)

    def test_missing_days_are_not_filled(self):
        mon_wed = [{"from": "MONDAY", "to": "WEDNESDAY", "beginTime": "08:00:00", "endTime": "12:00:00"}]
        for cal in export(baseline(simod_json(mon_wed)))["resource_calendars"]:
            self.assertEqual(cal["time_periods"],
                             [{"from": "MONDAY", "to": "WEDNESDAY", "beginTime": "08:00:00", "endTime": "12:00:00"}])
            days = {p["from"] for p in cal["time_periods"]} | {p["to"] for p in cal["time_periods"]}
            self.assertFalse(days & {"THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"})

    def test_24_7_baselines_are_byte_identical_to_before(self):
        self.assertEqual(h24_fingerprints(), PRE_CHANGE_FINGERPRINTS)

    def test_inter_arrival_patch_keeps_arrival_calendar(self):
        mod = PatchModification(
            parameter_type=PatchParameterType.INTER_ARRIVAL_TIME, target_element="Start_1",
            direction=ModificationDirection.INCREASE, baseline_value="1800", proposed_value="3600",
            kpi_reference="Average Cycle Time", rationale="Fewer arrivals.", evidence_source="baseline",
        )
        patch = ScenarioPatch(scenario_id="test", reasoning="test", modifications=[mod],
                              expected_kpi_impacts=[KPIImpact(kpi_name="Average Cycle Time", direction="decrease",
                                                              confidence="medium", reasoning="Less load.")])
        merged = apply_patch(baseline(simod_json(H24, MON_FRI)), patch)
        self.assertIsNotNone(merged.scenario, merged.diagnostics)
        self.assertEqual(export(merged.scenario)["arrival_time_calendar"], MON_FRI_EXPORTED)


if __name__ == "__main__":
    unittest.main()

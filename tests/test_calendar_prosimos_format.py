"""Calendar modifications given in Prosimos period format are accepted.

The LLM sometimes gives a resource_calendar change as structured items in Prosimos's period format
({"from": "MONDAY", "to": "FRIDAY", "beginTime": "09:00:00", "endTime": "19:00:00"}) instead of GLASS's
({"startWeekday": "Monday", "endWeekday": "Friday", "startTime": 9, "endTime": 19}). The merger skipped
those items as invalid. Now scenario_merger.normalise_calendar_items maps them; only items that are
genuinely ambiguous (not whole hours, weekdays that are not day names, unknown keys) are rejected, with
category calendar_unparsed and a message giving both accepted forms.

All process data is synthetic: one generic task and one role whose two resources have their own calendars.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from second_llm.output_schema_patch import ScenarioPatch  # noqa: E402
from second_llm.patch_validator import validate_patch  # noqa: E402
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
LONGER_PROSIMOS = [{"from": "MONDAY", "to": "FRIDAY", "beginTime": "09:00:00", "endTime": "19:00:00.000"}]


def simod() -> dict:
    res = lambda rid: {"id": rid, "name": rid, "cost_per_hour": 20, "amount": 1, "calendar": f"{rid}_cal"}
    return {
        "resource_profiles": [{"id": "Role_X", "name": "Role X", "resource_list": [res("Res_1"), res("Res_2")]}],
        "resource_calendars": [{"id": f"{r}_cal", "name": f"{r}_cal", "time_periods": DAY} for r in ("Res_1", "Res_2")],
        "task_resource_distribution": [{"task_id": "Task_A", "resources": [
            {"resource_id": r, "distribution_name": "fix", "distribution_params": [{"value": 600}]}
            for r in ("Res_1", "Res_2")]}],
        "arrival_time_distribution": {"distribution_name": "fix", "distribution_params": [{"value": 1800}]},
        "gateway_branching_probabilities": [],
    }


def baseline():
    built = build_baseline_scenario(simod(), bpmn_xml=BPMN)
    assert built.ok, built.errors
    return built.scenario


def patch(items: list[dict]) -> ScenarioPatch:
    return ScenarioPatch.model_validate({
        "scenario_id": "S", "baseline_reference": "SIMOD", "reasoning": "test",
        "modifications": [{"parameter_type": "resource_calendar", "target_element": "Role X",
                           "direction": "redistribute", "baseline_value": "09:00-17:00 Mon-Fri",
                           "proposed_value": "09:00-19:00 Mon-Fri", "proposed_structured": {"timeTableItems": items},
                           "kpi_reference": "Cycle Time", "rationale": "Longer hours.", "evidence_source": "SIMOD",
                           "literature_support": [1]}],
        "expected_kpi_impacts": [{"kpi_name": "Cycle Time", "direction": "decrease", "estimated_magnitude": "",
                                  "confidence": "medium", "reasoning": ""}]})


def exported_periods(scenario) -> set:
    out = scenario_to_prosimos_json(scenario, BPMN)
    periods = {c["id"]: c["time_periods"] for c in out["resource_calendars"]}
    return {tuple((p["from"], p["to"], p["beginTime"], p["endTime"]) for p in periods[r["calendar"]])
            for prof in out["resource_profiles"] for r in prof["resource_list"]}


class ProsimosFormat(unittest.TestCase):
    def test_prosimos_item_is_applied_and_exported(self):
        base = baseline()
        p = patch(LONGER_PROSIMOS)
        self.assertFalse(validate_patch(p, baseline=base).has_errors)
        merged = apply_patch(base, p)
        self.assertEqual(merged.applied_modifications, [1])
        self.assertEqual(exported_periods(merged.scenario), {(("MONDAY", "FRIDAY", "09:00:00", "19:00:00"),)})

    def test_glass_form_unchanged(self):
        merged = apply_patch(baseline(), patch([{"startWeekday": "Monday", "endWeekday": "Friday",
                                                 "startTime": 9, "endTime": 19}]))
        self.assertEqual(merged.applied_modifications, [1])

    def test_ambiguous_item_rejected_with_feedback(self):
        base = baseline()
        p = patch([{"from": "MONDAY", "to": "FRIDAY", "beginTime": "09:30:00", "endTime": "18:00:00"}])
        errors = validate_patch(p, baseline=base).errors
        self.assertEqual([e.category for e in errors], ["calendar_unparsed"])
        for part in ("was not applied", "not whole hours", "{startWeekday, endWeekday, startTime, endTime}",
                     "{from, to, beginTime, endTime}"):
            self.assertIn(part, errors[0].message)
        merged = apply_patch(base, p)
        self.assertEqual(merged.skipped_modifications, [1])
        self.assertEqual([d.category for d in merged.diagnostics if d.severity == "error"], ["calendar_unparsed"])
        self.assertEqual(exported_periods(merged.scenario), {(("MONDAY", "FRIDAY", "09:00:00", "17:00:00"),)})


if __name__ == "__main__":
    unittest.main()

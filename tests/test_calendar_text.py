"""Calendar modifications given as text are parsed or reported, never dropped silently.

A resource_calendar modification needs proposed_structured.timeTableItems. The LLM often gave only text
("09:00-17:00 -> 09:00-19:00"); the merger skipped it in tolerant mode without telling anyone, so the same
change was proposed again in every iteration. Now an unambiguous text (one working-time window with a
clock marker, optionally with a weekday range; or an extension by whole hours) is parsed into timetable
items; any other text is an error (category calendar_unparsed) that states the expected format. The
validator reports it to the retry prompt, the generator drops only that modification on its last attempt,
and the refinement feedback lists every modification that was not applied.

All process data is synthetic: one generic task and one role whose two resources have their own calendars.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

import second_llm.iterative_evaluator as ie  # noqa: E402
from llm.provider import LLMProvider  # noqa: E402
from second_llm.kpi_computation import ComputedKPI, KPIComputationResult  # noqa: E402
from second_llm.output_schema import TimetableItem, Weekday  # noqa: E402
from second_llm.output_schema_patch import ScenarioPatch  # noqa: E402
from second_llm.patch_validator import validate_patch  # noqa: E402
from second_llm.scenario_evaluation import KPITarget, TargetDirection, compare_kpis  # noqa: E402
from second_llm.scenario_generator import generate_scenario_patch  # noqa: E402
from second_llm.scenario_merger import apply_patch, parse_calendar_text  # noqa: E402
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
NINE_TO_FIVE = [TimetableItem(startWeekday=Weekday.MONDAY, endWeekday=Weekday.FRIDAY, startTime=9, endTime=17)]


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


def calendar_mod(value: str) -> dict:
    return {"parameter_type": "resource_calendar", "target_element": "Role X", "direction": "redistribute",
            "baseline_value": "09:00-17:00 Mon-Fri", "proposed_value": value, "kpi_reference": "Cycle Time",
            "rationale": "Longer working hours.", "evidence_source": "SIMOD", "literature_support": [1]}


def patch(*mods: dict) -> ScenarioPatch:
    return ScenarioPatch.model_validate({
        "scenario_id": "S", "baseline_reference": "SIMOD", "reasoning": "test", "modifications": list(mods),
        "expected_kpi_impacts": [{"kpi_name": "Cycle Time", "direction": "decrease", "estimated_magnitude": "",
                                  "confidence": "medium", "reasoning": ""}]})


def spans(items) -> list[tuple]:
    return [(i.startWeekday.value, i.endWeekday.value, i.startTime, i.endTime) for i in items]


def calendars(scenario) -> set:
    return {tuple(spans(t.timeTableItems)) for t in scenario.resourceParameters.timeTables}


class Parse(unittest.TestCase):
    def test_window_with_weekdays(self):
        self.assertEqual(spans(parse_calendar_text("09:00–19:00 Mon–Fri", NINE_TO_FIVE)),
                         [("Monday", "Friday", 9, 19)])

    def test_extension_by_hours(self):
        self.assertEqual(spans(parse_calendar_text("extend by 2 hours", NINE_TO_FIVE)),
                         [("Monday", "Friday", 9, 19)])

    def test_ambiguous_texts_are_not_parsed(self):
        for text in ("more flexible hours", "8 to 10", "10", "09:30-18:00", "09:00-12:00 and 13:00-18:00",
                     "extend by 1.5 hours"):
            self.assertIsNone(parse_calendar_text(text, NINE_TO_FIVE), text)


class MergeAndValidate(unittest.TestCase):
    def test_parsed_text_is_applied_to_the_role(self):
        base = baseline()
        p = patch(calendar_mod("09:00–19:00 Mon–Fri"))
        self.assertFalse(validate_patch(p, baseline=base).has_errors)
        merged = apply_patch(base, p)
        self.assertEqual(merged.applied_modifications, [1])
        self.assertEqual(calendars(merged.scenario), {(("Monday", "Friday", 9, 19),)})
        self.assertTrue(any(d.category == "calendar_parsed" for d in merged.diagnostics))

    def test_unparsable_text_is_rejected_with_feedback(self):
        base = baseline()
        p = patch(calendar_mod("more flexible hours"))
        errors = validate_patch(p, baseline=base).errors
        self.assertEqual([e.category for e in errors], ["calendar_unparsed"])
        msg = errors[0].message
        self.assertIn("was not applied", msg)
        self.assertIn("'09:00-19:00 Mon-Fri'", msg)
        self.assertIn("'extend by 2 hours'", msg)
        merged = apply_patch(base, p)
        self.assertEqual(merged.skipped_modifications, [1])
        self.assertEqual(calendars(merged.scenario), {(("Monday", "Friday", 9, 17),)})
        self.assertIn("calendar_unparsed", [d.category for d in merged.diagnostics])


class GeneratorAndFeedback(unittest.TestCase):
    def test_last_attempt_drops_only_the_unparsable_change_and_feedback_says_so(self):
        output = patch(calendar_mod("more flexible hours"),
                       {"parameter_type": "resource_count", "target_element": "Role X", "direction": "increase",
                        "baseline_value": "2", "proposed_value": "3", "kpi_reference": "Cycle Time",
                        "rationale": "Add capacity.", "evidence_source": "SIMOD", "literature_support": [1]}
                       ).model_dump_json()

        class Stub(LLMProvider):
            def __init__(self):
                self.prompts = []

            def generate(self, system_prompt, user_prompt, temperature=0.3, **kwargs):
                self.prompts.append(user_prompt)
                return output

            def get_model_name(self):
                return "stub"

        first = {"simulation_goal_structured": "Reduce the cycle time.",
                 "kpis": [{"name": "Cycle Time", "category": "time", "target_direction": "minimize",
                           "description": "Mean case duration.", "suggested_formula": "mean(end - start)"}]}
        stub = Stub()
        res = generate_scenario_patch(stub, json.dumps(first), first, simod_json_content=json.dumps(simod()),
                                      bpmn_xml=BPMN, max_retries=2)
        self.assertIn("calendar_unparsed", stub.prompts[1])                        # retry prompt
        role = res.proposal.scenario.resourceParameters.roles[0]
        self.assertEqual(len(role.resources), 3)                                   # the other change applied
        self.assertTrue(any(n.startswith("[patch-reject]") for n in res.generation_notes))

        t = [KPITarget(name="Cycle Time", direction=TargetDirection.MINIMIZE, category="time",
                       measurable_as="Average Cycle Time")]
        ev = compare_kpis(KPIComputationResult(kpis=[ComputedKPI("Average Cycle Time", 10.0, "h", "time")]),
                          KPIComputationResult(kpis=[ComputedKPI("Average Cycle Time", 11.0, "h", "time")]), t)
        saved = ie._retrieve_alternative_interventions
        ie._retrieve_alternative_interventions = lambda *a, **k: ""
        try:
            text = ie._build_evaluation_feedback(ev, res, 1, 4)
        finally:
            ie._retrieve_alternative_interventions = saved
        self.assertIn("MODIFICATIONS NOT APPLIED", text)
        self.assertIn("'more flexible hours' cannot be read as a working-time change", text)


if __name__ == "__main__":
    unittest.main()

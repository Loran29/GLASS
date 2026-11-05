"""Tests for timer / intermediate-event delays in Stage 2 (event_duration parameter type).

Covers: import of event_distribution into the baseline, export to Prosimos, the
event_duration patch type (merger, validator, cost estimation), the LLM payload, and
byte-identical behaviour for baselines without timers.

All process data is synthetic: two generic tasks ("Task A", "Task B") and one timer.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

TIMER_ID = "Event_timer_1"
TIMER_SECONDS = 7200.0

_BPMN_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="defs">'
    '<process id="proc" isExecutable="false">'
    '<startEvent id="Start_1" name="Start" />'
    '<task id="Task_A" name="Task A" />'
    '<task id="Task_B" name="Task B" />'
    '<endEvent id="End_1" name="End" />'
)
_BPMN_TAIL = "</process></definitions>"


def bpmn_without_timer() -> str:
    return (
        _BPMN_HEAD
        + '<sequenceFlow id="f1" sourceRef="Start_1" targetRef="Task_A" />'
        + '<sequenceFlow id="f2" sourceRef="Task_A" targetRef="Task_B" />'
        + '<sequenceFlow id="f3" sourceRef="Task_B" targetRef="End_1" />'
        + _BPMN_TAIL
    )


def bpmn_with_timer() -> str:
    return (
        _BPMN_HEAD
        + f'<intermediateCatchEvent id="{TIMER_ID}"><timerEventDefinition /></intermediateCatchEvent>'
        + '<sequenceFlow id="f1" sourceRef="Start_1" targetRef="Task_A" />'
        + f'<sequenceFlow id="f2" sourceRef="Task_A" targetRef="{TIMER_ID}" />'
        + f'<sequenceFlow id="f2b" sourceRef="{TIMER_ID}" targetRef="Task_B" />'
        + '<sequenceFlow id="f3" sourceRef="Task_B" targetRef="End_1" />'
        + _BPMN_TAIL
    )


def simod_json(with_timer: bool) -> dict:
    data = {
        "resource_profiles": [
            {"id": "Role_X", "name": "Role X", "resource_list": [
                {"id": "Res_1", "name": "Res 1", "cost_per_hour": 20, "amount": 1, "calendar": "Cal_1",
                 "assignedTasks": ["Task_A", "Task_B"]},
                {"id": "Res_2", "name": "Res 2", "cost_per_hour": 20, "amount": 1, "calendar": "Cal_1",
                 "assignedTasks": ["Task_A", "Task_B"]},
            ]},
        ],
        "resource_calendars": [
            {"id": "Cal_1", "name": "Cal_1", "time_periods": [
                # 24/7, so the export is the same with and without calendar weekend-filling.
                {"from": "MONDAY", "to": "SUNDAY", "beginTime": "00:00:00", "endTime": "23:59:59"},
            ]},
        ],
        "task_resource_distribution": [
            {"task_id": "Task_A", "resources": [
                {"resource_id": "Res_1", "distribution_name": "fix", "distribution_params": [{"value": 600}]},
            ]},
            {"task_id": "Task_B", "resources": [
                {"resource_id": "Res_2", "distribution_name": "expon",
                 "distribution_params": [{"value": 900}, {"value": 0}, {"value": 9000}]},
            ]},
        ],
        "arrival_time_distribution": {"distribution_name": "expon",
                                      "distribution_params": [{"value": 1800}, {"value": 0}, {"value": 18000}]},
        "gateway_branching_probabilities": [],
    }
    if with_timer:
        data["event_distribution"] = [
            {"event_id": TIMER_ID, "distribution_name": "fix", "distribution_params": [{"value": TIMER_SECONDS}]},
        ]
    return data


FIRST_LLM_JSON = json.dumps({
    "simulation_goal_structured": "Reduce the waiting time before Task B.",
    "kpis": [{"name": "Task B Waiting Time", "category": "time", "target_direction": "minimize"}],
})


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def timerless_fingerprints() -> dict[str, str]:
    """SHA-256 of every output that must stay byte-identical for baselines without timers."""
    from prompts.scenario_patch_prompt import build_scenario_patch_prompt
    from second_llm.output_schema import get_constrained_decoding_schema
    from second_llm.output_schema_patch import get_patch_constrained_decoding_schema
    from second_llm.prosimos_runner import scenario_to_prosimos_json
    from second_llm.simod_to_simubridge import build_baseline_scenario

    out = {}
    plain = build_baseline_scenario(simod_json(with_timer=False), bpmn_xml=bpmn_without_timer()).scenario
    out["export_plain_bpmn"] = sha(json.dumps(scenario_to_prosimos_json(plain, bpmn_without_timer())))
    # BPMN has a timer, but the SIMOD JSON carries no event_distribution: the timer keeps the
    # zero default on export, exactly as before.
    with_evt = build_baseline_scenario(simod_json(with_timer=False), bpmn_xml=bpmn_with_timer()).scenario
    out["export_timer_bpmn_no_distribution"] = sha(json.dumps(scenario_to_prosimos_json(with_evt, bpmn_with_timer())))
    out["baseline_serialisation"] = sha(plain.model_dump_json())
    system_prompt, _, user_prompt = build_scenario_patch_prompt(first_llm_json=FIRST_LLM_JSON)
    out["patch_prompt"] = sha(system_prompt + "\n\x00\n" + user_prompt)
    out["patch_constrained_schema"] = sha(json.dumps(get_patch_constrained_decoding_schema(), sort_keys=True))
    out["legacy_constrained_schema"] = sha(json.dumps(get_constrained_decoding_schema(), sort_keys=True))
    return out


# Computed with the code before this change (commit bddc958), for the inputs above.
PRE_CHANGE_FINGERPRINTS = {
    "export_plain_bpmn": "3764bbd72094670f67a4c639037878492ef805f0fe92446392eb401a34de3ec8",
    "export_timer_bpmn_no_distribution": "54c47709259550bb64e0090f8080f689f053556df6cd91d48f1ac6cf99be4bc2",
    "baseline_serialisation": "fc3967b244d35b5d6750cea807ab3490cef974c3378eec280ac0d2cfcfdc7c48",
    "patch_prompt": "a4e10d99d7093631e297bde97d8143e79e0ad5fe074f15333dee6db5df907806",
    "patch_constrained_schema": "34cf355e2bd0ff36b4cfff08521b2f2d89fcc856fd3dde4a09500da4ab4de7bb",
    "legacy_constrained_schema": "101b9fce25357cc69329a492fa2fe9af718a0c173b6c4a441e4c48ec858fbf25",
}


import unittest  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest import mock  # noqa: E402

from second_llm.compatibility_adapter import build_legacy_proposal  # noqa: E402
from second_llm.cost_estimation import build_cost_report  # noqa: E402
from second_llm.output_schema import DistributionType, KPIImpact, ModificationDirection  # noqa: E402
from second_llm.output_schema_patch import (  # noqa: E402
    PatchModification,
    PatchParameterType,
    ScenarioPatch,
    get_patch_constrained_decoding_schema,
)
from second_llm.patch_validator import validate_patch  # noqa: E402
from second_llm.payload_builder import summarize_intermediate_events  # noqa: E402
from second_llm.prosimos_runner import scenario_to_prosimos_json  # noqa: E402
from second_llm.scenario_merger import apply_patch  # noqa: E402
from second_llm.simod_to_simubridge import build_baseline_scenario  # noqa: E402


def timer_baseline(event_entry: dict | None = None):
    data = simod_json(with_timer=True)
    if event_entry is not None:
        data["event_distribution"] = [event_entry]
    return build_baseline_scenario(data, bpmn_xml=bpmn_with_timer())


def event_mod(target: str = "Timer before Task B", proposed: str = "3600", **kw) -> PatchModification:
    fields = dict(
        parameter_type=PatchParameterType.EVENT_DURATION, target_element=target,
        direction=ModificationDirection.DECREASE, baseline_value=str(TIMER_SECONDS),
        proposed_value=proposed, kpi_reference="Task B Waiting Time",
        rationale="Shorter timer delay before Task B.", evidence_source="baseline timer",
    )
    fields.update(kw)
    return PatchModification(**fields)


def patch_of(*mods: PatchModification) -> ScenarioPatch:
    impact = KPIImpact(kpi_name="Task B Waiting Time", direction="decrease", confidence="medium",
                       reasoning="Shorter timer.")
    return ScenarioPatch(scenario_id="test", reasoning="test", modifications=list(mods),
                         expected_kpi_impacts=[impact])


def exported_timer(scenario) -> dict:
    events = scenario_to_prosimos_json(scenario, bpmn_with_timer())["event_distribution"]
    return next(e for e in events if e["event_id"] == TIMER_ID)


class ImportExportTests(unittest.TestCase):

    def test_import_creates_named_event_with_neighbours(self):
        result = timer_baseline()
        events = result.scenario.models[0].modelParameter.intermediateEvents
        self.assertEqual(len(events), 1)
        evt = events[0]
        self.assertEqual((evt.id, evt.name, evt.predecessor, evt.successor),
                         (TIMER_ID, "Timer before Task B", "Task A", "Task B"))
        self.assertEqual(evt.duration.distributionType, DistributionType.CONSTANT)
        self.assertTrue(any("intermediate (timer) event" in n for n in result.notes))

    def test_round_trip_preserves_fixed_timer(self):
        original = simod_json(with_timer=True)["event_distribution"][0]
        exported = exported_timer(timer_baseline().scenario)
        self.assertEqual(exported, {**original, "distribution_params": [{"value": float(TIMER_SECONDS)}]})

    def test_round_trip_preserves_exponential_timer_mean(self):
        entry = {"event_id": TIMER_ID, "distribution_name": "expon",
                 "distribution_params": [{"value": 5400.0}, {"value": 0.0}, {"value": 54000.0}]}
        exported = exported_timer(timer_baseline(entry).scenario)
        self.assertEqual(exported["distribution_name"], "expon")
        self.assertEqual(exported["distribution_params"][0]["value"], 5400.0)

    def test_display_name_falls_back_to_id_without_successor(self):
        data = simod_json(with_timer=True)
        scenario = build_baseline_scenario(data, bpmn_xml=bpmn_without_timer()).scenario   # timer not in BPMN
        evt = scenario.models[0].modelParameter.intermediateEvents[0]
        self.assertEqual((evt.name, evt.successor), (TIMER_ID, ""))

    def test_event_in_bpmn_but_not_in_scenario_keeps_zero_default(self):
        scenario = build_baseline_scenario(simod_json(with_timer=False), bpmn_xml=bpmn_with_timer()).scenario
        self.assertEqual(exported_timer(scenario), {
            "event_id": TIMER_ID, "distribution_name": "expon",
            "distribution_params": [{"value": 0.0}, {"value": 0.0}, {"value": 1.0}],
        })

    def test_baselines_without_timers_are_byte_identical_to_before(self):
        self.assertEqual(timerless_fingerprints(), PRE_CHANGE_FINGERPRINTS)


class PatchTests(unittest.TestCase):

    def test_merger_shortens_timer(self):
        merged = apply_patch(timer_baseline().scenario, patch_of(event_mod(proposed="3600")))
        self.assertEqual(merged.applied_modifications, [1])
        self.assertEqual(exported_timer(merged.scenario)["distribution_params"], [{"value": 3600.0}])

    def test_merger_sets_timer_to_zero_by_id(self):
        merged = apply_patch(timer_baseline().scenario, patch_of(event_mod(target=TIMER_ID, proposed="0")))
        self.assertEqual(merged.applied_modifications, [1])
        self.assertEqual(exported_timer(merged.scenario), {
            "event_id": TIMER_ID, "distribution_name": "fix", "distribution_params": [{"value": 0.0}],
        })

    def test_validator_accepts_known_event(self):
        result = validate_patch(patch_of(event_mod()), baseline=timer_baseline().scenario)
        self.assertFalse(result.has_errors, result.error_summary())

    def test_validator_rejects_unknown_event(self):
        result = validate_patch(patch_of(event_mod(target="Timer before Task Z")), baseline=timer_baseline().scenario)
        self.assertTrue(any(d.category == "missing_element" for d in result.errors))

    def test_validator_rejects_negative_duration(self):
        result = validate_patch(patch_of(event_mod(proposed="-60")), baseline=timer_baseline().scenario)
        self.assertTrue(any(d.category == "invalid_value" for d in result.errors))

    def test_cost_estimation_unchanged_by_event_modification(self):
        baseline = timer_baseline().scenario
        merged = apply_patch(baseline, patch_of(event_mod())).scenario
        report = build_cost_report(build_legacy_proposal(patch_of(event_mod()), merged))
        self.assertEqual(report.total_monthly_cost, 0.0)
        self.assertEqual(report.cost_estimates, [])


class LLMVisibilityTests(unittest.TestCase):

    def test_payload_lists_events(self):
        summary = summarize_intermediate_events(timer_baseline().scenario)
        self.assertIn(f"Timer before Task B (id {TIMER_ID}): Task A -> Task B", summary)
        self.assertIn("current mean duration 7200 secs", summary)

    def test_prompt_and_schema_offer_event_duration_only_with_timers(self):
        from prompts.scenario_patch_prompt import build_scenario_patch_prompt
        summary = summarize_intermediate_events(timer_baseline().scenario)
        _, _, user_prompt = build_scenario_patch_prompt(first_llm_json=FIRST_LLM_JSON,
                                                        intermediate_events_summary=summary)
        self.assertIn("event_duration (a timer-caused waiting time before an activity", user_prompt)
        self.assertIn(summary, user_prompt)
        enum = get_patch_constrained_decoding_schema(include_event_duration=True)["$defs"]["PatchParameterType"]["enum"]
        self.assertIn("event_duration", enum)

    def test_feedback_attributes_waiting_time_change_to_event(self):
        from second_llm import iterative_evaluator
        proposal = build_legacy_proposal(patch_of(event_mod()), timer_baseline().scenario)
        comparison = SimpleNamespace(kpi_name="Task B Waiting Time", status="computed", baseline_value=2.0,
                                     proposed_value=3.0, percentage_change=50.0, unit="h",
                                     violated_safeguard=False, improved=False, target_direction="minimize")
        eval_result = SimpleNamespace(summary=SimpleNamespace(overall_status=SimpleNamespace(value="worse")),
                                      kpi_comparisons=[comparison])
        with mock.patch.object(iterative_evaluator, "_retrieve_alternative_interventions", return_value=""):
            text = iterative_evaluator._build_evaluation_feedback(
                eval_result, SimpleNamespace(proposal=proposal), iteration=1, max_iterations=3)
        line = next(l for l in text.splitlines() if "Timer before Task B" in l)
        self.assertIn("LIKELY CAUSED DEGRADATION", line)


if __name__ == "__main__":
    unittest.main()

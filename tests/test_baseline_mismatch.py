"""Modifications whose quoted baseline value does not match the model are rejected.

Previously the merger only warned when a modification's ``baseline_value`` differed from the model's
actual value and applied the proposed value anyway, so a modification aimed at the wrong element (or
quoted in another unit) changed the model in a way the LLM never intended. Now a numeric quote more than
25 % from the actual value, or a resource_activity_assignment quote naming other roles, is an error
(category ``baseline_mismatch``): the merger skips the modification, the validator reports it, and the
retry prompt states the actual value. On the generator's last attempt only the rejected modifications
are dropped; the rest of the patch is applied.

All process data is synthetic: one generic task ("Task A", 600 s) and two roles.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from llm.provider import LLMProvider  # noqa: E402
from second_llm.output_schema_patch import PatchModification, PatchParameterType, ScenarioPatch  # noqa: E402
from second_llm.patch_validator import validate_patch  # noqa: E402
from second_llm.scenario_generator import generate_scenario_patch  # noqa: E402
from second_llm.scenario_merger import _distribution_mean, apply_patch  # noqa: E402
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


def simod() -> dict:
    res = lambda rid: {"id": rid, "name": rid, "cost_per_hour": 20, "amount": 1, "calendar": "Cal_1"}
    return {
        "resource_profiles": [{"id": "Role_X", "name": "Role X", "resource_list": [res("Res_1"), res("Res_2")]},
                              {"id": "Role_Y", "name": "Role Y", "resource_list": [res("Res_3")]}],
        "resource_calendars": [{"id": "Cal_1", "name": "Cal_1", "time_periods": DAY}],
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


def mod(**kw) -> dict:
    out = {"parameter_type": "activity_duration", "target_element": "Task A", "direction": "decrease",
           "baseline_value": "600", "proposed_value": "450", "kpi_reference": "Cycle Time",
           "rationale": "Shorten the task.", "evidence_source": "SIMOD: Task A mean 600 s",
           "literature_support": [1]}
    out.update(kw)
    return out


def patch(*mods: dict) -> ScenarioPatch:
    return ScenarioPatch.model_validate({
        "scenario_id": "S", "baseline_reference": "SIMOD", "reasoning": "test",
        "modifications": list(mods),
        "expected_kpi_impacts": [{"kpi_name": "Cycle Time", "direction": "decrease", "estimated_magnitude": "",
                                  "confidence": "medium", "reasoning": ""}]})


def task_a_mean(scenario) -> float:
    return _distribution_mean(scenario.models[0].modelParameter.activities[0].duration)


class BaselineMismatch(unittest.TestCase):
    def test_mismatch_rejected(self):
        base = baseline()
        p = patch(mod(baseline_value="7200", proposed_value="5400"))
        merged = apply_patch(base, p)
        self.assertEqual(merged.skipped_modifications, [1])
        self.assertEqual(task_a_mean(merged.scenario), 600)          # unchanged
        errors = validate_patch(p, baseline=base).errors
        self.assertEqual([e.category for e in errors], ["baseline_mismatch"])

    def test_close_match_accepted(self):
        base = baseline()
        p = patch(mod(baseline_value="700", proposed_value="450"))   # 16.7 % off: within 25 %
        merged = apply_patch(base, p)
        self.assertEqual(merged.applied_modifications, [1])
        self.assertEqual(task_a_mean(merged.scenario), 450)
        self.assertFalse(validate_patch(p, baseline=base).has_errors)

    def test_other_unit_rejected(self):
        """A quote in hours for a duration stored in seconds would otherwise set 0.5 s."""
        merged = apply_patch(baseline(), patch(mod(baseline_value="0.1667 hours", proposed_value="0.125 hours")))
        self.assertEqual(merged.skipped_modifications, [1])
        self.assertEqual(task_a_mean(merged.scenario), 600)

    def test_feedback_states_actual_value(self):
        summary = validate_patch(patch(mod(baseline_value="7200", proposed_value="5400")),
                                 baseline=baseline()).error_summary()
        self.assertIn("[baseline_mismatch]", summary)
        self.assertIn("actual value 600 secs", summary)

    def test_assignment_quote_must_name_the_activity_roles(self):
        base = baseline()
        wrong = mod(parameter_type="resource_activity_assignment", direction="redistribute",
                    baseline_value="Role Y", proposed_value="Role X, Role Y")
        p = patch(wrong)
        errors = validate_patch(p, baseline=base).errors
        self.assertEqual([e.category for e in errors], ["baseline_mismatch"])
        self.assertIn("Role X", errors[0].message)
        right = dict(wrong, baseline_value="Role X")
        self.assertFalse(validate_patch(patch(right), baseline=base).has_errors)
        by_resource = dict(wrong, baseline_value="Res_1, Res_2")      # resources of Role X
        self.assertFalse(validate_patch(patch(by_resource), baseline=base).has_errors)


class StubProvider(LLMProvider):
    """Returns the same patch on every call and records the prompts."""

    def __init__(self, output: str):
        self.output, self.prompts = output, []

    def generate(self, system_prompt, user_prompt, temperature=0.3, **kwargs) -> str:
        self.prompts.append(user_prompt)
        return self.output

    def get_model_name(self) -> str:
        return "stub"


class GeneratorRetry(unittest.TestCase):
    def test_retry_feedback_and_last_attempt_drops_only_rejected(self):
        output = json.dumps(json.loads(patch(
            mod(baseline_value="7200", proposed_value="5400"),
            mod(parameter_type="resource_count", target_element="Role X", direction="increase",
                baseline_value="2", proposed_value="3", evidence_source="SIMOD: Role X count 2"),
        ).model_dump_json()))
        first = {"simulation_goal_structured": "Reduce the cycle time.",
                 "kpis": [{"name": "Cycle Time", "category": "time", "target_direction": "minimize",
                           "description": "Mean case duration.", "suggested_formula": "mean(end - start)"}]}
        provider = StubProvider(output)
        res = generate_scenario_patch(provider, json.dumps(first), first, simod_json_content=json.dumps(simod()),
                                      bpmn_xml=BPMN, max_retries=2)
        self.assertEqual(len(provider.prompts), 3)
        self.assertIn("actual value 600 secs", provider.prompts[1])
        self.assertIsNotNone(res.proposal)
        scn = res.proposal.scenario
        self.assertEqual(task_a_mean(scn), 600)                      # rejected modification not applied
        role_x = next(r for r in scn.resourceParameters.roles if r.id == "Role X")
        self.assertEqual(len(role_x.resources), 3)                   # the other one is


if __name__ == "__main__":
    unittest.main()

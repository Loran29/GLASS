"""Resource-id stems and resource ids in patch targets resolve to their role.

SIMOD gives a resource profile an id (e.g. ``POOL``) and a name (the role, "Role X"), and numbers the
profile's resources ``POOL_1`` … ``POOL_n``. GLASS's scenario keys the role by the profile name. The
prompt showed the SIMOD ids, so the LLM sometimes targeted ``POOL``, which was rejected as an unknown
role; after the retries the run had no scenario. Now the repair step maps the id stem, a resource id or a
resource name with an index to the role, and the prompt shows resources by name where both exist.

All process data is synthetic: one generic task ("Task A") and two roles.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from llm.provider import LLMProvider  # noqa: E402
from second_llm.output_schema_patch import ScenarioPatch  # noqa: E402
from second_llm.patch_validator import repair_patch_against_baseline, validate_patch  # noqa: E402
from second_llm.payload_builder import simod_for_llm  # noqa: E402
from second_llm.scenario_generator import generate_scenario_patch  # noqa: E402
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


def simod() -> dict:
    res = lambda rid, name: {"id": rid, "name": name, "cost_per_hour": 20, "amount": 1, "calendar": f"{rid}_cal"}
    return {
        "resource_profiles": [
            {"id": "POOL", "name": "Role X", "resource_list": [res("POOL_1", "Role X-000001"),
                                                               res("POOL_2", "Role X-000002")]},
            {"id": "other_7f3a", "name": "Role Y", "resource_list": [res("other_7f3a_1", "Role Y-000001")]}],
        "resource_calendars": [{"id": f"{r}_cal", "name": f"{r}_cal", "time_periods": DAY}
                               for r in ("POOL_1", "POOL_2", "other_7f3a_1")],
        "task_resource_distribution": [{"task_id": "Task_A", "resources": [
            {"resource_id": r, "distribution_name": "fix", "distribution_params": [{"value": 600}]}
            for r in ("POOL_1", "POOL_2")]}],
        "arrival_time_distribution": {"distribution_name": "fix", "distribution_params": [{"value": 1800}]},
        "gateway_branching_probabilities": [],
    }


def baseline():
    built = build_baseline_scenario(simod(), bpmn_xml=BPMN)
    assert built.ok, built.errors
    return built.scenario


def count_patch(target: str, **kw) -> ScenarioPatch:
    mod = {"parameter_type": "resource_count", "target_element": target, "direction": "increase",
           "baseline_value": "2", "proposed_value": "3", "kpi_reference": "Cycle Time",
           "rationale": "Add capacity.", "evidence_source": "SIMOD: 2 resources", "literature_support": [1]}
    mod.update(kw)
    return ScenarioPatch.model_validate({
        "scenario_id": "S", "baseline_reference": "SIMOD", "reasoning": "test", "modifications": [mod],
        "expected_kpi_impacts": [{"kpi_name": "Cycle Time", "direction": "decrease", "estimated_magnitude": "",
                                  "confidence": "medium", "reasoning": ""}]})


def repaired(target: str, **kw):
    base = baseline()
    patch, notes = repair_patch_against_baseline(count_patch(target, **kw), base)
    return base, patch, notes


def role_size(scenario, role_id: str) -> int:
    return len(next(r for r in scenario.resourceParameters.roles if r.id == role_id).resources)


class RepairRoleTargets(unittest.TestCase):
    def test_baseline_roles_are_keyed_by_profile_name(self):
        self.assertEqual([r.id for r in baseline().resourceParameters.roles], ["Role X", "Role Y"])

    def test_id_stem_resolves_to_role(self):
        base, patch, notes = repaired("POOL")
        self.assertEqual(patch.modifications[0].target_element, "Role X")
        self.assertTrue(notes)
        self.assertFalse(validate_patch(patch, baseline=base).has_errors)
        self.assertEqual(role_size(apply_patch(base, patch).scenario, "Role X"), 3)

    def test_resource_id_resolves_to_role(self):
        for target in ("POOL_2", "other_7f3a_1"):
            _, patch, _ = repaired(target, baseline_value="2" if target.startswith("POOL") else "1")
            self.assertEqual(patch.modifications[0].target_element,
                             "Role X" if target.startswith("POOL") else "Role Y")

    def test_resource_name_with_index_resolves_to_role(self):
        _, patch, _ = repaired("Role X-000001")
        self.assertEqual(patch.modifications[0].target_element, "Role X")

    def test_add_new_on_stem_becomes_increase(self):
        _, patch, _ = repaired("POOL", direction="add_new")
        self.assertEqual((patch.modifications[0].target_element, patch.modifications[0].direction.value),
                         ("Role X", "increase"))

    def test_calendar_target_stem_resolves_to_role(self):
        base, patch, _ = repaired("POOL", parameter_type="resource_calendar", direction="redistribute",
                                  baseline_value="Mon-Fri 09-17", proposed_value="Mon-Fri 09-19",
                                  proposed_structured={"timeTableItems": LONGER})
        self.assertEqual(patch.modifications[0].target_element, "Role X")
        self.assertFalse(validate_patch(patch, baseline=base).has_errors)

    def test_unknown_name_still_rejected(self):
        base, patch, notes = repaired("Ghost Pool")
        self.assertEqual(patch.modifications[0].target_element, "Ghost Pool")
        self.assertEqual(notes, [])
        errors = validate_patch(patch, baseline=base).errors
        self.assertEqual([e.category for e in errors], ["missing_element"])


class PromptShowsNames(unittest.TestCase):
    def test_ids_replaced_by_names(self):
        src = simod()
        shown = simod_for_llm(src)
        self.assertEqual([p["id"] for p in shown["resource_profiles"]], ["Role X", "Role Y"])
        self.assertEqual([r["id"] for r in shown["resource_profiles"][0]["resource_list"]],
                         ["Role X-000001", "Role X-000002"])
        self.assertEqual([r["resource_id"] for r in shown["task_resource_distribution"][0]["resources"]],
                         ["Role X-000001", "Role X-000002"])
        self.assertEqual(shown["resource_calendars"], src["resource_calendars"])      # calendar ids kept
        self.assertEqual(src, simod())                                                 # input unchanged

    def test_patch_prompt_shows_names(self):
        class Stub(LLMProvider):
            prompts: list[str] = []

            def generate(self, system_prompt, user_prompt, temperature=0.3, **kwargs):
                self.prompts.append(user_prompt)
                return count_patch("Role X").model_dump_json()

            def get_model_name(self):
                return "stub"

        first = {"simulation_goal_structured": "Reduce the cycle time.",
                 "kpis": [{"name": "Cycle Time", "category": "time", "target_direction": "minimize",
                           "description": "Mean case duration.", "suggested_formula": "mean(end - start)"}]}
        stub = Stub()
        res = generate_scenario_patch(stub, json.dumps(first), first, simod_json_content=json.dumps(simod()),
                                      bpmn_xml=BPMN)
        self.assertIsNotNone(res.proposal)
        self.assertIn("Role X-000001", stub.prompts[0])
        self.assertNotIn('"POOL"', stub.prompts[0])
        self.assertNotIn("POOL_1\"", stub.prompts[0])


if __name__ == "__main__":
    unittest.main()

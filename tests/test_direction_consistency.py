"""A modification whose stated direction contradicts its values is rejected.

Previously ``direction_inconsistent`` was a warning: a modification stating direction "decrease" with a
proposed value far above the baseline (e.g. a task of 825.5 s set to 180000 s after the LLM corrected only its
baseline quote) was merged and simulated as a large increase. Now it is an error: the retry feedback states
the stated direction, the baseline and the proposed value, and on the generator's last attempt only the
rejected modification is dropped; the rest of the patch is applied.

All process data is synthetic (tests/test_baseline_mismatch.py: one task "Task A", 600 s, two roles).
"""
from __future__ import annotations

import json
import unittest

from tests.test_baseline_mismatch import (BPMN, StubProvider, baseline, mod, patch, simod,  # noqa: F401
                                          task_a_mean)

from second_llm.patch_validator import validate_patch  # noqa: E402
from second_llm.scenario_generator import generate_scenario_patch  # noqa: E402

FIRST = {"simulation_goal_structured": "Reduce the cycle time.",
         "kpis": [{"name": "Cycle Time", "category": "time", "target_direction": "minimize",
                   "description": "Mean case duration.", "suggested_formula": "mean(end - start)"}]}


class DirectionConsistency(unittest.TestCase):
    def test_decrease_with_larger_value_rejected(self):
        res = validate_patch(patch(mod(baseline_value="600 secs", proposed_value="180000 secs")), baseline=baseline())
        self.assertTrue(res.has_errors)
        self.assertEqual([e.category for e in res.errors], ["direction_inconsistent"])

    def test_increase_with_smaller_value_rejected(self):
        res = validate_patch(patch(mod(direction="increase", baseline_value="600", proposed_value="300")),
                             baseline=baseline())
        self.assertEqual([e.category for e in res.errors], ["direction_inconsistent"])

    def test_consistent_change_accepted(self):
        for direction, proposed in (("decrease", "450"), ("increase", "900")):
            res = validate_patch(patch(mod(direction=direction, proposed_value=proposed)), baseline=baseline())
            self.assertFalse(res.has_errors, (direction, res.error_summary()))
            self.assertFalse([d for d in res.diagnostics if d.category == "direction_inconsistent"])

    def test_feedback_states_direction_and_both_values(self):
        summary = validate_patch(patch(mod(baseline_value="600 secs", proposed_value="180000 secs")),
                                 baseline=baseline()).error_summary()
        self.assertIn("[direction_inconsistent]", summary)
        self.assertIn("direction='decrease'", summary)
        self.assertIn("180000", summary)
        self.assertIn("600", summary)
        self.assertIn("below 600", summary)

    def test_last_attempt_drops_only_the_rejected_modification(self):
        output = json.dumps(json.loads(patch(
            mod(baseline_value="600", proposed_value="180000"),
            mod(parameter_type="resource_count", target_element="Role X", direction="increase",
                baseline_value="2", proposed_value="3", evidence_source="SIMOD: Role X count 2"),
        ).model_dump_json()))
        provider = StubProvider(output)
        res = generate_scenario_patch(provider, json.dumps(FIRST), FIRST, simod_json_content=json.dumps(simod()),
                                      bpmn_xml=BPMN, max_retries=2)
        self.assertEqual(len(provider.prompts), 3)
        self.assertIn("direction='decrease'", provider.prompts[1])
        self.assertIsNotNone(res.proposal)
        self.assertEqual(task_a_mean(res.proposal.scenario), 600)        # not applied
        role_x = next(r for r in res.proposal.scenario.resourceParameters.roles if r.id == "Role X")
        self.assertEqual(len(role_x.resources), 3)                       # the consistent one is
        self.assertTrue(any(n.startswith("[patch-reject]") and "direction='decrease'" in n
                            for n in res.generation_notes))


if __name__ == "__main__":
    unittest.main()

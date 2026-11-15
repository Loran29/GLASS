"""The refinement loop's KPI targets carry measurable_as, like the multi-seed evaluation's.

``ui.second_llm_panel._get_evaluation_targets`` builds the targets the iterative loop evaluates against. Without
``measurable_as`` the loop matched a KPI by its name: "Average Waiting Time Before Review" resolved to the
process-wide "Average Waiting Time" instead of "Review Waiting Time", so the loop's stop rule and feedback used a
different metric than the multi-seed evaluation (``ui.evaluation_panel._get_kpi_targets_from_workspace``).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from second_llm.kpi_computation import ComputedKPI, KPIComputationResult  # noqa: E402
from second_llm.models import FirstLLMInput  # noqa: E402
from second_llm.scenario_evaluation import _find_kpi  # noqa: E402
from second_llm.state import reset_workspace, set_first_llm_input  # noqa: E402
from ui.evaluation_panel import _get_kpi_targets_from_workspace  # noqa: E402
from ui.second_llm_panel import _get_evaluation_targets  # noqa: E402

KPIS = [{"name": "Average Waiting Time Before Review", "category": "time", "target_direction": "minimize",
         "measurable_as": "Review Waiting Time", "description": "Wait before the review step."},
        {"name": "Average Case Cycle Time", "category": "time", "target_direction": "minimize",
         "measurable_as": "Average Cycle Time"},
        {"name": "Approval Rate", "category": "quality", "target_direction": "maximize", "measurable_as": None}]
COMPUTED = KPIComputationResult(kpis=[ComputedKPI("Average Cycle Time", 150.0, "hours", "time"),
                                      ComputedKPI("Average Waiting Time", 140.0, "hours", "time"),
                                      ComputedKPI("Review Waiting Time", 60.0, "hours", "time")])


class LoopTargets(unittest.TestCase):
    def setUp(self):
        reset_workspace()
        set_first_llm_input(FirstLLMInput(raw_json_text="{}", parsed={"kpis": KPIS}))

    def test_loop_targets_carry_measurable_as(self):
        loop, evaluation = _get_evaluation_targets(), _get_kpi_targets_from_workspace()
        self.assertEqual([t.measurable_as for t in loop], ["Review Waiting Time", "Average Cycle Time", None])
        self.assertEqual([t.measurable_as for t in loop], [t.measurable_as for t in evaluation])

    def test_loop_resolves_the_same_kpi_as_the_evaluation(self):
        for loop, evaluation in zip(_get_evaluation_targets()[:2], _get_kpi_targets_from_workspace()[:2]):
            got = _find_kpi(loop.name, COMPUTED, loop.category, loop.measurable_as)
            want = _find_kpi(evaluation.name, COMPUTED, evaluation.category, evaluation.measurable_as)
            self.assertEqual(got.name, want.name)
        wait = _get_evaluation_targets()[0]
        self.assertEqual(_find_kpi(wait.name, COMPUTED, wait.category, wait.measurable_as).value, 60.0)
        self.assertEqual(_find_kpi(wait.name, COMPUTED, wait.category, None).name, "Average Waiting Time")  # by name


if __name__ == "__main__":
    unittest.main()

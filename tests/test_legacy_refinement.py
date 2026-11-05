"""``legacy_refinement`` restores the earlier refinement rules for ablation studies.

With the flag the loop stops at the first iteration whose overall status is ``improved`` (which includes a
partial improvement: some KPIs improved, none worsened) and selects the best iteration by score alone.
Without it (default) the current rules apply: stop when the targets are met; best by achieved targets, then
improved KPIs, then score. Synthetic KPI series as in test_target_achieved.
"""
from __future__ import annotations

import unittest

from tests.test_target_achieved import kpis, run_loop, targets  # also puts goal_to_parameters on sys.path

from second_llm.scenario_evaluation import OverallStatus  # noqa: E402


def run(series, tg, legacy: bool):
    import second_llm.iterative_evaluator as ie
    original = ie.run_iterative_evaluation
    ie.run_iterative_evaluation = lambda *a, **k: original(*a, **k, legacy_refinement=legacy)
    try:
        return run_loop(series, tg)[0]
    finally:
        ie.run_iterative_evaluation = original


class LegacyRefinement(unittest.TestCase):
    def test_legacy_stops_when_improved_although_target_not_achieved(self):
        series = [kpis(0.92, 80.0), kpis(0.80, 85.0), kpis(0.70, 60.0)]   # 1: both improved, 0.92 > 0.85
        legacy, current = run(series, targets(), True), run(series, targets(), False)
        self.assertEqual(legacy.iterations[0].status, OverallStatus.IMPROVED)
        self.assertEqual(len(legacy.iterations), 1)
        self.assertEqual(len(current.iterations), 2)

    def test_legacy_selects_by_score_alone(self):
        series = [kpis(0.98, 150.0),                         # utilization improved, CT worsened 50 %
                  kpis(1.00, 101.0), kpis(1.00, 101.0), kpis(1.00, 101.0)]   # both worsened slightly
        legacy, current = run(series, targets(None), True), run(series, targets(None), False)
        self.assertEqual(len(legacy.iterations), 4)
        self.assertGreater(legacy.iterations[1].score, legacy.iterations[0].score)
        # No iteration has a positive score, so both keep the baseline; the selection shows in the
        # candidate attached for inspection.
        self.assertTrue(legacy.no_improving_scenario and current.no_improving_scenario)
        self.assertEqual(legacy.best_candidate_idx, 1)      # highest score, no KPI improved
        self.assertEqual(current.best_candidate_idx, 0)     # one KPI improved

    def test_default_is_current_rule(self):
        series = [kpis(0.92, 80.0), kpis(0.80, 85.0), kpis(0.70, 60.0)]
        self.assertEqual(len(run(series, targets(), False).iterations), 2)
        self.assertEqual(len(run_loop(series, targets())[0].iterations), 2)


if __name__ == "__main__":
    unittest.main()

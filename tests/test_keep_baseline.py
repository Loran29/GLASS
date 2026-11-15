"""The refinement loop keeps the baseline when no iteration improves on it.

When no iteration reaches a positive score (no KPI improved without a worse one, by the existing
goal-programming score), the loop returns the outcome ``no_improving_scenario``: ``best_iteration_idx`` is
None (no final scenario) and the best iteration is attached as ``best_candidate_idx`` for inspection. Before,
the best of only-worse iterations was returned as the final scenario. Synthetic KPI series as in
test_target_achieved.
"""
from __future__ import annotations

import unittest

from tests.test_target_achieved import kpis, run_loop, targets  # also puts goal_to_parameters on sys.path


class KeepBaseline(unittest.TestCase):
    def test_only_worse_iterations_keep_the_baseline(self):
        series = [kpis(1.05, 120.0), kpis(1.10, 101.0), kpis(1.02, 140.0), kpis(0.99, 100.5)]   # base 0.99 / 100
        res, _ = run_loop(series, targets(None))
        self.assertEqual(len(res.iterations), 4)
        self.assertTrue(all(it.score <= 0 for it in res.iterations))
        self.assertTrue(res.no_improving_scenario)
        self.assertEqual(res.outcome, "no_improving_scenario")
        self.assertIsNone(res.best_iteration_idx)
        self.assertIsNone(res.best)
        self.assertIsNone(res.final_status)
        self.assertFalse(res.targets_met)
        self.assertIsNotNone(res.best_candidate)                 # attached for inspection
        self.assertEqual(res.best_candidate_idx, 3)               # least worse by score

    def test_an_improving_iteration_is_returned(self):
        series = [kpis(1.05, 120.0), kpis(0.97, 95.0)]
        res, _ = run_loop(series, targets(None))
        self.assertFalse(res.no_improving_scenario)
        self.assertEqual(res.outcome, "scenario")
        self.assertEqual(res.best_iteration_idx, 1)
        self.assertIsNone(res.best_candidate_idx)


if __name__ == "__main__":
    unittest.main()

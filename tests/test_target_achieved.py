"""Refinement distinguishes "KPI improved" from "target achieved".

For a KPI target with a numeric threshold, ``achieved`` says whether the value meets it in the KPI's
direction (minimize: <= threshold, maximize: >= threshold). The refinement loop stops only when every KPI
with a threshold has achieved it and every KPI without one has improved; the best iteration is the one
with the most achieved targets, then the most improved KPIs, then the existing score; and the feedback
says per KPI "improved but target not yet reached (current X, target Y)" or "target achieved".

The KPI values are synthetic series; simulation and KPI computation are replaced by the series, and the
LLM by a stub orchestrator that returns an empty proposal.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

import second_llm.iterative_evaluator as ie  # noqa: E402
import second_llm.multi_seed_evaluation as mse  # noqa: E402
from second_llm.kpi_computation import ComputedKPI, KPIComputationResult  # noqa: E402
from second_llm.scenario_evaluation import KPITarget, TargetDirection, compare_kpis, targets_met  # noqa: E402

UTIL, CT = "Pool Utilization", "Average Cycle Time"


def kpis(util: float, ct: float) -> KPIComputationResult:
    return KPIComputationResult(kpis=[ComputedKPI(UTIL, util, "ratio", "utilization"),
                                      ComputedKPI(CT, ct, "hours", "time")])


def targets(threshold: float | None = 0.85) -> list[KPITarget]:
    return [KPITarget(name=UTIL, direction=TargetDirection.MINIMIZE, category="utilization",
                      measurable_as=UTIL, threshold=threshold),
            KPITarget(name=CT, direction=TargetDirection.MINIMIZE, category="time", measurable_as=CT)]


BASE = kpis(0.99, 100.0)


class Orchestrator:
    """Stands in for the LLM: every iteration yields a (dummy) proposal; feedback is recorded."""

    def __init__(self):
        self.feedback: list[str] = []

    def generate_scenario(self, **kwargs):
        return SimpleNamespace(success=True, error=None,
                               proposal=SimpleNamespace(scenario=object(), modifications=[]))

    def inject_feedback_message(self, text: str) -> None:
        self.feedback.append(text)


def run_loop(series: list[KPIComputationResult], tg: list[KPITarget], max_iterations: int = 4):
    """Run GLASS's loop with the baseline BASE and one proposed KPI result per iteration."""
    values = iter([BASE] + series)
    saved = ie.run_prosimos_simulation, ie.compute_kpis, ie.scenario_to_prosimos_json
    ie.run_prosimos_simulation = lambda *a, **k: SimpleNamespace(ok=True, simulated_log=None, error=None)
    ie.compute_kpis = lambda *a, **k: next(values)
    ie.scenario_to_prosimos_json = lambda *a, **k: {}
    try:
        orch = Orchestrator()
        res = ie.run_iterative_evaluation(orch, None, "", tg, max_iterations=max_iterations)
    finally:
        ie.run_prosimos_simulation, ie.compute_kpis, ie.scenario_to_prosimos_json = saved
    return res, orch


class Achieved(unittest.TestCase):
    def test_compare_reports_achieved_next_to_improved(self):
        res = compare_kpis(BASE, kpis(0.92, 80.0), targets())
        util, ct = res.kpi_comparisons
        self.assertEqual((util.improved, util.achieved, util.threshold), (True, False, 0.85))
        self.assertEqual((ct.improved, ct.achieved, ct.threshold), (True, None, None))
        self.assertFalse(res.summary.targets_achieved)
        self.assertFalse(targets_met(res.kpi_comparisons))
        self.assertTrue(compare_kpis(BASE, kpis(0.85, 80.0), targets()).summary.targets_achieved)  # <= 0.85
        self.assertIsNone(compare_kpis(BASE, kpis(0.92, 80.0), targets(None)).summary.targets_achieved)

    def test_maximize_threshold(self):
        t = [KPITarget(name=UTIL, direction=TargetDirection.MAXIMIZE, measurable_as=UTIL, threshold=0.5)]
        self.assertTrue(compare_kpis(kpis(0.2, 1), kpis(0.5, 1), t).kpi_comparisons[0].achieved)
        self.assertFalse(compare_kpis(kpis(0.2, 1), kpis(0.4, 1), t).kpi_comparisons[0].achieved)


class Loop(unittest.TestCase):
    def test_does_not_stop_when_improved_but_not_achieved(self):
        series = [kpis(0.92, 80.0), kpis(0.80, 85.0), kpis(0.70, 60.0)]
        res, _ = run_loop(series, targets())
        self.assertEqual(len(res.iterations), 2)          # iteration 1 improved both, but 0.92 > 0.85
        self.assertEqual(res.best_iteration_idx, 1)
        self.assertTrue(res.targets_met)

    def test_without_threshold_stops_at_first_improvement(self):
        res, _ = run_loop([kpis(0.92, 80.0), kpis(0.80, 85.0)], targets(None))
        self.assertEqual(len(res.iterations), 1)          # unchanged rule

    def test_best_iteration_prefers_achieved_targets(self):
        series = [kpis(0.95, 50.0),                        # improved 2, achieved 0, high score
                  kpis(0.80, 120.0),                       # improved 1, achieved 1
                  kpis(1.20, 40.0),                        # improved 1, achieved 0
                  kpis(0.97, 99.0)]                        # improved 2, achieved 0, low score
        res, _ = run_loop(series, targets())
        self.assertEqual(len(res.iterations), 4)
        self.assertEqual(res.best_iteration_idx, 1)
        self.assertGreater(res.iterations[0].score, res.iterations[1].score)   # score alone would pick 1st

    def test_ties_on_achieved_go_to_improved_count_then_score(self):
        series = [kpis(0.97, 99.0), kpis(0.95, 50.0), kpis(1.10, 40.0), kpis(0.96, 70.0)]
        res, _ = run_loop(series, targets())
        self.assertEqual(res.best_iteration_idx, 1)       # 2 improved, highest score among them

    def test_feedback_distinguishes_improved_from_achieved(self):
        series = [kpis(0.92, 80.0), kpis(0.80, 120.0), kpis(0.80, 70.0)]
        _, orch = run_loop(series, targets())
        first, second = orch.feedback[0], orch.feedback[1]
        self.assertIn("improved but target not yet reached (current 0.92, target 0.85)", first)
        self.assertIn("Targets reached: 0 of 1", first)
        self.assertIn("Pool Utilization: target achieved", second)
        self.assertIn("Targets reached: 1 of 1", second)


class MultiSeed(unittest.TestCase):
    def test_achieved_on_the_seed_mean(self):
        seq = iter([BASE, kpis(0.84, 80.0), BASE, kpis(0.88, 80.0)])   # seed 1, seed 2: mean 0.86
        saved = mse.run_prosimos_simulation, mse.compute_kpis, mse.scenario_to_prosimos_json
        mse.run_prosimos_simulation = lambda *a, **k: SimpleNamespace(ok=True, simulated_log=None, error=None)
        mse.compute_kpis = lambda *a, **k: next(seq)
        mse.scenario_to_prosimos_json = lambda *a, **k: {}
        try:
            res = mse.evaluate_multi_seed(None, None, "", targets(), num_seeds=2)
        finally:
            mse.run_prosimos_simulation, mse.compute_kpis, mse.scenario_to_prosimos_json = saved
        util, ct = res.kpi_comparisons
        self.assertEqual((util.improved, util.achieved, util.threshold), (True, False, 0.85))
        self.assertIsNone(ct.achieved)
        self.assertFalse(res.summary.targets_achieved)


if __name__ == "__main__":
    unittest.main()

"""Paired simulation runs per refinement iteration (``loop_seeds``).

With ``loop_seeds`` = k > 1, the refinement loop simulates the baseline and every proposal on seeds
42, 43, … (k runs each) and compares the mean KPIs; improved / worsened / unchanged, the score, the stop
rule and the feedback use the means, with the same thresholds. The default, 1, must reproduce the
single-run loop exactly: the same simulator and KPI calls (one run on seed 42 per scenario), the KPI
results passed on unchanged, the same comparisons, scores and feedback.

Simulation and KPI computation are replaced by a table of synthetic KPI results per (scenario, seed), and
the LLM by a stub orchestrator that proposes scenarios P1, P2, … in turn.
"""
from __future__ import annotations

import dataclasses
import unittest
from types import SimpleNamespace

from tests.test_target_achieved import CT, UTIL, ie, kpis, targets  # also puts goal_to_parameters on sys.path

from second_llm.kpi_computation import ComputedKPI, KPIComputationResult  # noqa: E402
from second_llm.scenario_evaluation import compare_kpis  # noqa: E402


def same(a, b) -> bool:
    """Equal evaluation results, apart from the random comparison_id."""
    return dataclasses.replace(a, comparison_id="") == dataclasses.replace(b, comparison_id="")


class Orchestrator:
    """Proposes scenario "P<n>" in iteration n; records the feedback."""

    def __init__(self):
        self.n, self.feedback, self.generated = 0, [], []

    def generate_scenario(self, **kwargs):
        self.n += 1
        gen = SimpleNamespace(success=True, error=None,
                              proposal=SimpleNamespace(scenario=f"P{self.n}", modifications=[]))
        self.generated.append(gen)
        return gen

    def inject_feedback_message(self, text: str) -> None:
        self.feedback.append(text)


def run(table: dict, tg, max_iterations: int = 3, fail: tuple | None = None, **kw):
    """Run the loop on the table {(scenario, seed): KPIComputationResult}; return result, orchestrator, trace."""
    trace = []

    def sim(scenario, bpmn, *, total_cases, start_time, seed):
        trace.append(("sim", scenario, seed))
        if fail == (scenario, seed):
            return SimpleNamespace(ok=False, simulated_log=None, error="boom")
        return SimpleNamespace(ok=True, simulated_log=(scenario, seed), error=None)

    def compute(log, **k):
        trace.append(("kpis",) + log)
        return table[log]

    saved = ie.run_prosimos_simulation, ie.compute_kpis, ie.scenario_to_prosimos_json
    ie.run_prosimos_simulation, ie.compute_kpis, ie.scenario_to_prosimos_json = sim, compute, (lambda *a, **k: {})
    try:
        orch = Orchestrator()
        res = ie.run_iterative_evaluation(orch, "B", "", tg, max_iterations=max_iterations, **kw)
    finally:
        ie.run_prosimos_simulation, ie.compute_kpis, ie.scenario_to_prosimos_json = saved
    return res, orch, trace


SINGLE = {("B", 42): kpis(0.99, 100.0), ("P1", 42): kpis(0.95, 90.0),
          ("P2", 42): kpis(0.90, 105.0), ("P3", 42): kpis(0.80, 95.0)}


class DefaultIsSingleRun(unittest.TestCase):
    def check(self, **kw):
        tg = targets()                                             # threshold 0.85: P1 and P2 do not stop the loop
        res, orch, trace = run(SINGLE, tg, **kw)
        self.assertEqual(trace, [("sim", "B", 42), ("kpis", "B", 42), ("sim", "P1", 42), ("kpis", "P1", 42),
                                 ("sim", "P2", 42), ("kpis", "P2", 42), ("sim", "P3", 42), ("kpis", "P3", 42)])
        self.assertIs(res.baseline_kpis, SINGLE[("B", 42)])        # the run's KPIs, not a copy or an average
        self.assertEqual(res.loop_seeds, 1)
        for it, gen in zip(res.iterations, orch.generated):
            expected = compare_kpis(SINGLE[("B", 42)], SINGLE[(f"P{it.iteration}", 42)], tg)
            self.assertIs(it.eval_result.proposed_kpis, SINGLE[(f"P{it.iteration}", 42)])
            self.assertTrue(same(it.eval_result, expected))
            self.assertEqual(it.score, ie.IterationResult(it.iteration, eval_result=expected).score)
        for i, text in enumerate(orch.feedback, 1):                # the feedback of the single-run loop
            self.assertEqual(text, ie._build_evaluation_feedback(res.iterations[i - 1].eval_result,
                                                                 orch.generated[i - 1], i, 3))
            self.assertNotIn("paired simulation runs", text)
        self.assertEqual(len(orch.feedback), 2)
        self.assertEqual(res.best_iteration_idx, 2)                # P3 achieves 0.80 <= 0.85 and lowers the cycle time
        return res

    def test_default(self):
        self.check()

    def test_explicit_one(self):
        self.check(loop_seeds=1)

    def test_failure_message_unchanged(self):
        res, _, _ = run(SINGLE, targets(), fail=("B", 42))
        self.assertEqual(res.error, "Baseline simulation failed: boom")
        res, _, _ = run(SINGLE, targets(), fail=("P1", 42))
        self.assertEqual(res.iterations[0].error, "Simulation failed: boom")


class SeveralSeeds(unittest.TestCase):
    # seed 43 alone would call P1's utilization worse (1.00 > 0.97); the mean over 42-44 is better
    TABLE = {("B", 42): kpis(0.99, 100.0), ("B", 43): kpis(0.97, 110.0), ("B", 44): kpis(0.98, 90.0),
             ("P1", 42): kpis(0.80, 95.0), ("P1", 43): kpis(1.00, 105.0), ("P1", 44): kpis(0.90, 88.0),
             ("P2", 42): kpis(0.70, 90.0), ("P2", 43): kpis(0.80, 100.0), ("P2", 44): kpis(0.75, 80.0)}

    def test_paired_seeds_and_means(self):
        res, orch, trace = run(self.TABLE, targets(), max_iterations=2, loop_seeds=3)
        self.assertEqual([t[1:] for t in trace if t[0] == "sim"],
                         [("B", 42), ("B", 43), ("B", 44), ("P1", 42), ("P1", 43), ("P1", 44),
                          ("P2", 42), ("P2", 43), ("P2", 44)])
        self.assertEqual(res.loop_seeds, 3)
        util, ct = res.iterations[0].eval_result.kpi_comparisons
        self.assertAlmostEqual(util.baseline_value, 0.98)
        self.assertAlmostEqual(util.proposed_value, 0.90)
        self.assertTrue(util.improved)                             # from the mean, although seed 43 is worse
        self.assertFalse(util.achieved)                            # 0.90 > 0.85: the loop continues
        self.assertAlmostEqual(ct.proposed_value, 96.0)
        self.assertTrue(ct.improved)
        self.assertTrue(same(res.iterations[0].eval_result,
                             compare_kpis(ie.average_kpi_results([self.TABLE[("B", s)] for s in (42, 43, 44)]),
                                          ie.average_kpi_results([self.TABLE[("P1", s)] for s in (42, 43, 44)]),
                                          targets())))
        self.assertIn("means over 3 paired simulation runs (seeds 42-44)", orch.feedback[0])
        self.assertEqual(res.best_iteration_idx, 1)                # P2: 0.75 <= 0.85

    def test_failed_seed_named(self):
        res, _, _ = run(self.TABLE, targets(), loop_seeds=3, fail=("P1", 43))
        self.assertEqual(res.iterations[0].error, "Simulation failed (seed 43): boom")

    def test_invalid(self):
        with self.assertRaises(ValueError):
            run(self.TABLE, targets(), loop_seeds=0)


class Averaging(unittest.TestCase):
    def test_values_details_and_missing_kpis(self):
        def res(util, roles, extra=None):
            k = [ComputedKPI("Resource Utilization", util, "ratio", "utilization",
                             {"per_role": roles, "busy_clipped_to_calendar": True, "period_end": "x"})]
            if extra is not None:
                k.append(ComputedKPI("Review Waiting Time", extra, "hours", "time"))
            return KPIComputationResult(kpis=k)

        avg = ie.average_kpi_results([res(0.6, {"Clerk": 0.5, "Officer": 0.9}, 2.0),
                                      res(0.4, {"Clerk": 0.3, "Officer": 0.7}),
                                      res(0.5, {"Clerk": 0.4, "Officer": 0.8}, 4.0)])
        ru, wt = avg.kpis
        self.assertAlmostEqual(ru.value, 0.5)
        self.assertAlmostEqual(ru.details["per_role"]["Officer"], 0.8)
        self.assertIs(ru.details["busy_clipped_to_calendar"], True)   # non-numeric entries are kept
        self.assertEqual(ru.details["period_end"], "x")
        self.assertEqual(ru.details["n_seeds"], 3)
        self.assertAlmostEqual(wt.value, 3.0)                       # over the two seeds where it was computed
        self.assertEqual(wt.details["n_seeds"], 2)


if __name__ == "__main__":
    unittest.main()

"""Direction-aware retrieval takes a KPI's metric from its category, not from its name.

The first direction-aware retrieval decided a problem KPI's metric by looking for "utiliz" in the KPI's
name. A utilization KPI called e.g. "Purchasing Agent Workload Distribution" was read as a capacity KPI, so
strategies raising utilization were not filtered out. Now knowledge.direction.kpi_metric uses the
category (and measurable_as); the name is only a fallback when both are missing.

The retrieval tests run over the bundled knowledge base.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

import second_llm.iterative_evaluator as ie  # noqa: E402
from knowledge.direction import kpi_metric  # noqa: E402
from second_llm.kpi_computation import ComputedKPI, KPIComputationResult  # noqa: E402
from second_llm.scenario_evaluation import KPITarget, TargetDirection, compare_kpis  # noqa: E402

RAISES_UTIL = re.compile(
    r"utili[sz]ation (rose|improved|improves|increased|rate of)|"
    r"(raise[sd]?|raising|increase[sd]?|increasing|improve[sd]?|improving|drives|boost\w*|maximi[sz]\w*)"
    r"( \w+){0,3} utili[sz]ation|utili[sz]ation upward|keeping utili[sz]ation high", re.I)
NAME = "Purchasing Agent Workload Distribution"


class KpiMetric(unittest.TestCase):
    def test_category_decides(self):
        self.assertEqual(kpi_metric("utilization", "Resource Utilization", NAME), "resource_utilisation")
        self.assertEqual(kpi_metric("utilization", None, "Cases by Time of Day"), "resource_utilisation")
        self.assertEqual(kpi_metric("cost", None, "Average Waiting Time"), "cost")
        self.assertIsNone(kpi_metric("quality", None, "Average Waiting Time"))

    def test_time_category_uses_measurable_as_then_name(self):
        self.assertEqual(kpi_metric("time", "Average Waiting Time", "Lead time"), "waiting_time")
        self.assertEqual(kpi_metric("time", "Average Cycle Time", "Wait before review"), "processing_time")
        self.assertEqual(kpi_metric("time", None, "Average Wait Time Before Review"), "waiting_time")
        self.assertEqual(kpi_metric("time", None, "Turnaround"), "processing_time")

    def test_name_only_as_fallback(self):
        self.assertEqual(kpi_metric(None, "Resource Utilization", "Cases by Time of Day"), "resource_utilisation")
        self.assertEqual(kpi_metric("", None, NAME), "resource_utilisation")


class Retrieval(unittest.TestCase):
    def test_unusual_utilization_name_gets_no_raising_strategy(self):
        text = ie._retrieve_alternative_interventions(
            [(NAME, "+1.0%", "minimize")], [], kpi_info={NAME: ("utilization", "Resource Utilization")})
        self.assertTrue(text.strip())
        m = RAISES_UTIL.search(text)
        self.assertIsNone(m, m and m.group(0))

    def test_feedback_passes_the_category(self):
        """End to end: a worsened utilization KPI with that name, through the refinement feedback."""
        t = [KPITarget(name=NAME, direction=TargetDirection.MINIMIZE, category="utilization",
                       measurable_as="Resource Utilization", threshold=0.85)]
        base = KPIComputationResult(kpis=[ComputedKPI("Resource Utilization", 0.83, "ratio", "utilization")])
        prop = KPIComputationResult(kpis=[ComputedKPI("Resource Utilization", 0.86, "ratio", "utilization")])
        res = compare_kpis(base, prop, t)
        self.assertIs(res.kpi_comparisons[0].improved, False)
        text = ie._build_evaluation_feedback(res, None, 1, 4)
        self.assertIn("Literature-Backed Alternative Interventions", text)
        section = text.split("## Literature-Backed Alternative Interventions:")[1].split("## What You Must Do")[0]
        m = RAISES_UTIL.search(section)
        self.assertIsNone(m, m and m.group(0))


if __name__ == "__main__":
    unittest.main()

"""Direction-aware knowledge-base retrieval during refinement.

The refinement feedback lists literature-backed alternative interventions for problem KPIs. Previously the
entries were chosen by text relevance only, so a KPI to *minimize* utilization received strategies whose
stated effect is raising utilization ("Improve operator utilization rates ...", "raised overall average
utilization by 8.5 %"). Now each entry's stated effect on the KPI's metric is derived from its own text
(knowledge/direction.py): entries stating the opposite or a mixed effect are dropped, entries stating no
effect on the metric are kept and ranked last.

The last tests run the real retrieval over the bundled knowledge base.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

import second_llm.iterative_evaluator as ie  # noqa: E402
from knowledge.direction import direction_rank, stated_effect  # noqa: E402

RAISES_UTIL = re.compile(
    r"utili[sz]ation (rose|improved|improves|increased|rate of)|"
    r"(raise[sd]?|raising|increase[sd]?|increasing|improve[sd]?|improving|drives|boost\w*|maximi[sz]\w*)"
    r"( \w+){0,3} utili[sz]ation|utili[sz]ation upward|keeping utili[sz]ation high", re.I)


class StatedEffect(unittest.TestCase):
    def test_utilization_phrases(self):
        cases = {
            "Increase the utilization rate of scanners to reduce idle time": "increase",
            "Reassigning the operator raised overall average utilization by 8.5%": "increase",
            "Utilization improves as a side-effect of shorter activity times": "increase",
            "Reduce waiting time while keeping utilization high": "increase",
            "Adding staff lowered the utilization of the overloaded pool from 97% to 80%": "decrease",
            "Rebalance work without overloading bottleneck roles": "decrease",
            "Improve staff utilization without overloading bottleneck roles": "mixed",
            "Reduce waiting time by adding one server": None,
        }
        for text, expected in cases.items():
            self.assertEqual(stated_effect(text, "resource_utilisation"), expected, text)

    def test_time_metrics_read_improve_as_decrease(self):
        self.assertEqual(stated_effect("Improve the waiting time of patients", "waiting_time"), "decrease")
        self.assertEqual(stated_effect("Reduce patient waiting time", "waiting_time"), "decrease")

    def test_rank(self):
        self.assertEqual(direction_rank("Lower the utilization of the pool", "resource_utilisation", "minimize"), 0)
        self.assertEqual(direction_rank("Reduce waiting time", "resource_utilisation", "minimize"), 1)
        self.assertIsNone(direction_rank("Increase utilization", "resource_utilisation", "minimize"))
        self.assertEqual(direction_rank("Increase utilization", "resource_utilisation", "maximize"), 0)


class RefinementRetrieval(unittest.TestCase):
    def test_minimize_utilization_gets_no_raising_strategy(self):
        text = ie._retrieve_alternative_interventions([("Pool Workload Utilization", "-2.9%", "minimize")], [])
        self.assertTrue(text.strip())                                   # strategies are still retrieved
        self.assertIsNone(RAISES_UTIL.search(text), RAISES_UTIL.search(text) and RAISES_UTIL.search(text).group(0))

    def test_maximize_utilization_keeps_raising_strategies(self):
        text = ie._retrieve_alternative_interventions([("Pool Workload Utilization", "-2.9%", "maximize")], [])
        self.assertIsNotNone(RAISES_UTIL.search(text))


if __name__ == "__main__":
    unittest.main()

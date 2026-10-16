"""Refinement feedback states the remaining gap and the allowed range for a KPI target not yet reached.

For a KPI with a numeric threshold that improved but has not reached it, the feedback states the current
value, the target, the remaining gap (absolute and relative), that a larger change is needed, and the
allowed range of the levers targeting the KPI, taken from the operational-constraint summary that is also
in the prompt; for a capacity lever also how many of the allowed additions remain. Achieved KPIs and KPIs
without a threshold get nothing extra.

All data is synthetic: one utilization KPI of a generic pool "Role X" and one cycle-time KPI.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

import second_llm.iterative_evaluator as ie  # noqa: E402
from second_llm.context_summary import OperationalContextSummary  # noqa: E402
from second_llm.kpi_computation import ComputedKPI, KPIComputationResult  # noqa: E402
from second_llm.scenario_evaluation import KPITarget, TargetDirection, compare_kpis  # noqa: E402

UTIL, CT = "Pool Utilization", "Average Cycle Time"
SUMMARY = OperationalContextSummary({
    "resource_constraints": {"role x": {"max_additional": 3, "max_overtime_hours_per_day": 2},
                             "role y": {"max_additional": 3}},
    "budget": {"max_increase_pct": 35},
})


def kpis(util: float, ct: float) -> KPIComputationResult:
    return KPIComputationResult(kpis=[ComputedKPI(UTIL, util, "ratio", "utilization"),
                                      ComputedKPI(CT, ct, "hours", "time")])


TARGETS = [KPITarget(name=UTIL, direction=TargetDirection.MINIMIZE, category="utilization", measurable_as=UTIL,
                     threshold=0.85),
           KPITarget(name=CT, direction=TargetDirection.MINIMIZE, category="time", measurable_as=CT)]


def mod(ptype: str, target: str, base: str, prop: str, kpi: str = UTIL):
    return SimpleNamespace(parameter_type=ptype, target_element=target, baseline_value=base, proposed_value=prop,
                           kpi_reference=kpi, intervention="", direction="increase")


def feedback(util: float, mods: list, summary=SUMMARY) -> str:
    gen = SimpleNamespace(proposal=SimpleNamespace(modifications=mods), context_summary=summary)
    saved = ie._retrieve_alternative_interventions
    ie._retrieve_alternative_interventions = lambda *a, **k: ""      # no knowledge-base lookup in unit tests
    try:
        return ie._build_evaluation_feedback(compare_kpis(kpis(1.0, 100.0), kpis(util, 80.0), TARGETS), gen, 1, 4)
    finally:
        ie._retrieve_alternative_interventions = saved


class GapAndRange(unittest.TestCase):
    def test_gap_sentence(self):
        text = feedback(0.97, [mod("resource_count", "Role X", "1", "2")])
        self.assertIn("improved but target not yet reached (current 0.97, target 0.85)", text)
        self.assertIn("Remaining gap: 0.12 (12.4% of the current value); a larger change than in this proposal is "
                      "needed to reach the target.", text)

    def test_allowed_range_and_remaining_additions(self):
        text = feedback(0.97, [mod("resource_count", "Role X", "1", "2"), mod("resource_count", "Role Y", "2", "3", CT)])
        self.assertIn("Allowed range for 'Role X' (operational constraints): staffing may be increased by up to +3 "
                      "(to 4); the current proposal adds +1, so 2 more addition(s) remain for this role.", text)
        self.assertIn("Total staff cost may increase by at most 35 %.", text)
        self.assertNotIn("'Role Y' (operational", text)               # its lever targets the other KPI

    def test_calendar_lever_range(self):
        text = feedback(0.97, [mod("resource_calendar", "Role X", "09-17", "09-18")])
        self.assertIn("working time may be extended by up to 2 h per day", text)

    def test_no_lever_lists_the_limits(self):
        text = feedback(0.97, [mod("activity_duration", "Task A", "600", "500", CT)])
        self.assertIn("No modification in the current proposal targets this KPI.", text)
        self.assertIn("Staffing limits from the operational constraints: 'role x' +3, 'role y' +3.", text)

    def test_global_cap(self):
        summary = OperationalContextSummary({"resource_constraints": {"role x": {"max_additional": 3}},
                                             "calendar_constraints": {"total_max_additional_resources": 4}})
        text = feedback(0.97, [mod("resource_count", "Role X", "1", "2")], summary)
        self.assertIn("Across all roles at most +4 additional resources are allowed; the current proposal adds +1, "
                      "so 3 more remain.", text)

    def test_nothing_added_for_achieved_or_without_threshold(self):
        text = feedback(0.80, [mod("resource_count", "Role X", "1", "3")])
        self.assertIn("Pool Utilization: target achieved", text)
        for phrase in ("Remaining gap", "Allowed range", "more addition"):
            self.assertNotIn(phrase, text)


if __name__ == "__main__":
    unittest.main()

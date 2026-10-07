"""Tests for the deterministic measurable_as post-processing in app.py:
normalise_measurable_as (content-based mapping) and the activity-extraction rule shared with
_fill_missing_activity_measurable_as (the start_time('X') term that is not subtracted).

Activity names are neutral placeholders, unrelated to the evaluation processes.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from app import (  # noqa: E402
    _fill_missing_activity_measurable_as,
    _normalise_result_measurable_as,
    normalise_measurable_as,
)
from models import KPIGenerationResult  # noqa: E402

LOG_ACTIVITIES = {name.lower(): name for name in ("Task A", "Task B", "Task C", "Owner's Review", "Re-check Order")}


def _kpi(*, name: str, formula: str, category: str = "time", scope: str = "activity_level",
         measurable_as: str | None = None) -> dict:
    return {
        "name": name,
        "description": "A test KPI.",
        "category": category,
        "smart_breakdown": {
            "specific": "Measures something.",
            "measurable": "AVG(...)",
            "achievable": "Realistic.",
            "relevant": "Aligned.",
            "time_bound": "Evaluated across all simulated cases in the run",
        },
        "target_direction": "minimize",
        "suggested_formula": formula,
        "supported_by_log": True,
        "evidence_basis": "both",
        "process_scope": scope,
        "context_segmentation": [],
        "measurable_as": measurable_as,
    }


def _result(kpis: list[dict]) -> KPIGenerationResult:
    return KPIGenerationResult(simulation_goal_structured="Goal.", kpis=kpis, reasoning="Test.")


class NormaliseMeasurableAsTests(unittest.TestCase):

    def _normalise(self, kpi: dict) -> tuple[dict, dict | None]:
        warning = normalise_measurable_as(kpi, LOG_ACTIVITIES)
        return kpi, warning

    # -- Average Cycle Time ---------------------------------------------------------------

    def test_whole_case_span_overrides_internal_label(self):
        kpi, warning = self._normalise(_kpi(name="Average Case Duration", scope="end_to_end",
                                            formula="AVG(End_Time - Start_Time) across completed cases",
                                            measurable_as="case_cycle_time_hours"))
        self.assertEqual(kpi["measurable_as"], "Average Cycle Time")
        self.assertEqual(kpi["measurable_as_raw"], "case_cycle_time_hours")
        self.assertEqual(warning["code"], "measurable_as_normalised")
        self.assertTrue(warning["details"]["replaced_internal_label"])

    def test_case_cycle_time_metric_fills_null(self):
        kpi, warning = self._normalise(_kpi(name="Average Case Duration", scope="end_to_end",
                                            formula="AVG(case_cycle_time_hours) across completed cases"))
        self.assertEqual(kpi["measurable_as"], "Average Cycle Time")
        self.assertIsNone(kpi["measurable_as_raw"])
        self.assertIsNotNone(warning)

    def test_span_between_two_activities_is_not_whole_case(self):
        kpi, warning = self._normalise(_kpi(name="Task B to Task C Time", scope="end_to_end",
                                            formula="AVG(end_time('Task C') - start_time('Task B'))",
                                            measurable_as="case_cycle_time_hours"))
        self.assertEqual(kpi["measurable_as"], "case_cycle_time_hours")
        self.assertIsNone(warning)

    def test_whole_case_span_needs_end_to_end_scope(self):
        kpi, _ = self._normalise(_kpi(name="Some Span", scope="subprocess",
                                      formula="AVG(case_cycle_time_hours)"))
        self.assertIsNone(kpi["measurable_as"])

    # -- {X} Waiting Time ------------------------------------------------------------------

    def test_waiting_time_from_start_minus_predecessor_end(self):
        kpi, warning = self._normalise(_kpi(name="Wait Before Task B",
                                            formula="AVG(start_time('Task B') - complete_time('Task A'))",
                                            measurable_as="activity_wait_time_hours"))
        self.assertEqual(kpi["measurable_as"], "Task B Waiting Time")
        self.assertEqual(warning["details"]["rule"], "start of activity minus end of predecessor")

    def test_waiting_time_with_max_over_predecessors(self):
        kpi, _ = self._normalise(_kpi(name="Delay Before Task C",
                                      formula="median(Start_Time('Task C') - MAX(End_Time('Task A'), End_Time('Task B')))"))
        self.assertEqual(kpi["measurable_as"], "Task C Waiting Time")

    def test_waiting_time_with_unnamed_predecessor_end(self):
        kpi, _ = self._normalise(_kpi(name="Wait Before Task C",
                                      formula="AVG(start_time('Task C') - MAX(end_time_of_prior_activities))"))
        self.assertEqual(kpi["measurable_as"], "Task C Waiting Time")

    def test_waiting_time_uses_log_spelling_and_apostrophe(self):
        kpi, _ = self._normalise(_kpi(name="Wait Before Review",
                                      formula="AVG(start_time('owner's review') - end_time('Task A'))"))
        self.assertEqual(kpi["measurable_as"], "Owner's Review Waiting Time")

    def test_waiting_time_corrects_attribution_to_predecessor(self):
        kpi, _ = self._normalise(_kpi(name="Wait Before Task B",
                                      formula="AVG(start_time('Task B') - end_time('Task A'))",
                                      measurable_as="Task A Waiting Time"))
        self.assertEqual(kpi["measurable_as"], "Task B Waiting Time")
        self.assertEqual(kpi["measurable_as_raw"], "Task A Waiting Time")

    def test_waiting_time_never_taken_from_subtracted_start(self):
        kpi, warning = self._normalise(_kpi(name="Wait Before Task B",
                                            formula="AVG(end_time('Task B') - start_time('Task A'))"))
        self.assertIsNone(kpi["measurable_as"])
        self.assertIsNone(warning)

    def test_waiting_time_requires_activity_in_log(self):
        kpi, _ = self._normalise(_kpi(name="Wait Before Task Z",
                                      formula="AVG(start_time('Task Z') - end_time('Task A'))",
                                      measurable_as="activity_wait_time_hours"))
        self.assertEqual(kpi["measurable_as"], "activity_wait_time_hours")

    def test_waiting_time_requires_wait_or_delay_in_name(self):
        kpi, _ = self._normalise(_kpi(name="Lead Time of Task B",
                                      formula="AVG(start_time('Task B') - end_time('Task A'))"))
        self.assertIsNone(kpi["measurable_as"])

    def test_waiting_time_requires_predecessor_end_after_start(self):
        kpi, _ = self._normalise(_kpi(name="Wait Before Task B",
                                      formula="AVG(start_time('Task B') - start_time('Task A'))",
                                      measurable_as="activity_wait_time_hours"))
        self.assertEqual(kpi["measurable_as"], "activity_wait_time_hours")

    # -- Average Waiting Time ----------------------------------------------------------------

    def test_end_to_end_sum_of_waits(self):
        kpi, _ = self._normalise(_kpi(name="Total Wait per Case", scope="end_to_end",
                                      formula="AVG(SUM(activity_wait_time_hours) per case)",
                                      measurable_as="case_wait_time_hours"))
        self.assertEqual(kpi["measurable_as"], "Average Waiting Time")

    def test_case_wait_time_metric(self):
        kpi, _ = self._normalise(_kpi(name="Case Wait", scope="end_to_end",
                                      formula="AVG(case_wait_time_hours) across completed cases"))
        self.assertEqual(kpi["measurable_as"], "Average Waiting Time")

    def test_sum_of_waits_needs_end_to_end_scope(self):
        kpi, _ = self._normalise(_kpi(name="Task Wait", scope="subprocess",
                                      formula="SUM(activity_wait_time_hours)"))
        self.assertIsNone(kpi["measurable_as"])

    # -- Average Processing Time ---------------------------------------------------------------

    def test_duration_function(self):
        kpi, _ = self._normalise(_kpi(name="Task A Duration", formula="AVG(duration('Task A'))",
                                      measurable_as="activity_duration_hours"))
        self.assertEqual(kpi["measurable_as"], "Average Processing Time")

    def test_end_minus_start_of_same_activity(self):
        kpi, _ = self._normalise(_kpi(name="Task A Handling",
                                      formula="AVG(complete_time('Task A') - start_time('Task A'))",
                                      measurable_as="Task A Waiting Time"))
        self.assertEqual(kpi["measurable_as"], "Average Processing Time")

    def test_bare_span_at_activity_level(self):
        kpi, _ = self._normalise(_kpi(name="Time to Do Task A",
                                      formula="AVG(End_Time - Start_Time) for 'Task A'"))
        self.assertEqual(kpi["measurable_as"], "Average Processing Time")

    def test_span_between_different_activities_is_not_duration(self):
        kpi, _ = self._normalise(_kpi(name="Task A to Task B",
                                      formula="AVG(end_time('Task B') - start_time('Task A'))"))
        self.assertIsNone(kpi["measurable_as"])

    def test_waiting_named_kpi_is_not_duration(self):
        kpi, _ = self._normalise(_kpi(name="Wait in Task A", formula="AVG(duration('Task A'))"))
        self.assertIsNone(kpi["measurable_as"])

    # -- Resource Utilization ------------------------------------------------------------------

    def test_utilization_category(self):
        kpi, warning = self._normalise(_kpi(name="Team Workload", category="utilization", scope="end_to_end",
                                            formula="SUM(busy_time) / SUM(available_time)",
                                            measurable_as="resource_workload"))
        self.assertEqual(kpi["measurable_as"], "Resource Utilization")
        self.assertTrue(warning["details"]["replaced_internal_label"])

    # -- anything else ---------------------------------------------------------------------------

    def test_other_categories_unchanged(self):
        for category in ("quality", "cost", "throughput"):
            kpi, warning = self._normalise(_kpi(name="Other KPI", category=category,
                                                formula="AVG(duration('Task A'))", measurable_as=None))
            self.assertIsNone(kpi["measurable_as"])
            self.assertIsNone(warning)

    def test_unmatched_time_kpi_keeps_value_and_leaves_raw_unset(self):
        kpi, warning = self._normalise(_kpi(name="Something", formula="AVG(Task A)",
                                            measurable_as="activity_duration_hours"))
        self.assertEqual(kpi["measurable_as"], "activity_duration_hours")
        self.assertIsNone(kpi.get("measurable_as_raw"))
        self.assertIsNone(warning)

    def test_no_warning_when_value_already_correct(self):
        kpi, warning = self._normalise(_kpi(name="Cycle", scope="end_to_end", formula="AVG(case_cycle_time_hours)",
                                            measurable_as="Average Cycle Time"))
        self.assertIsNone(kpi.get("measurable_as_raw"))
        self.assertIsNone(warning)

    def test_existing_raw_is_not_overwritten(self):
        kpi = _kpi(name="Average Case Duration", scope="end_to_end",
                   formula="AVG(End_Time - Start_Time) across completed cases", measurable_as="Average Waiting Time")
        kpi["measurable_as_raw"] = "case_cycle_time_hours"   # recorded in an earlier round
        kpi, warning = self._normalise(kpi)
        self.assertEqual(kpi["measurable_as"], "Average Cycle Time")
        self.assertEqual(kpi["measurable_as_raw"], "case_cycle_time_hours")
        self.assertIsNotNone(warning)

    def test_carried_over_kpi_is_left_byte_identical(self):
        kpi = _kpi(name="Average Case Duration", scope="end_to_end",
                   formula="AVG(End_Time - Start_Time) across completed cases", measurable_as="Average Cycle Time")
        kpi["measurable_as_raw"] = None   # the model gave no measurable_as in the first round
        before = dict(kpi)
        kpi, warning = self._normalise(kpi)
        self.assertEqual(kpi, before)
        self.assertIsNone(warning)

    # -- result-level wrapper ------------------------------------------------------------------

    def test_result_wrapper_reads_activities_from_log_profile(self):
        log_profile = {"_lookup": {"activities": ["task a", "task b"]},
                       "top_activities": [{"name": "Task B", "event_count": 3}]}
        result = _result([_kpi(name="Wait Before Task B", formula="AVG(start_time('task b') - end_time('task a'))")])
        normalised, warnings = _normalise_result_measurable_as(result, log_profile=log_profile)
        self.assertEqual(normalised.kpis[0].measurable_as, "Task B Waiting Time")
        self.assertIsNone(normalised.kpis[0].measurable_as_raw)
        self.assertEqual(len(warnings), 1)

    def test_result_wrapper_without_log_skips_waiting_rule(self):
        result = _result([_kpi(name="Wait Before Task B", formula="AVG(start_time('Task B') - end_time('Task A'))")])
        normalised, warnings = _normalise_result_measurable_as(result, log_profile=None)
        self.assertIsNone(normalised.kpis[0].measurable_as)
        self.assertEqual(warnings, [])


class FillActivityExtractionTests(unittest.TestCase):

    def _fill(self, formula: str) -> str | None:
        filled, _ = _fill_missing_activity_measurable_as(_result([_kpi(name="Some Time", formula=formula)]))
        return filled.kpis[0].measurable_as

    def test_fill_uses_unsubtracted_start_term(self):
        self.assertEqual(self._fill("AVG(start_time('Task B') - end_time('Task A'))"), "Task B Waiting Time")

    def test_fill_never_uses_subtracted_start_term(self):
        self.assertIsNone(self._fill("AVG(end_time('Task B') - start_time('Task A'))"))

    def test_fill_skips_end_minus_start_of_same_activity(self):
        self.assertIsNone(self._fill("AVG(end_time('Task A') - start_time('Task A'))"))

    def test_fill_skips_subtracted_start_inside_max(self):
        self.assertIsNone(self._fill("AVG(end_time('Task C') - MAX(start_time('Task A'), start_time('Task B')))"))

    def test_hyphen_inside_activity_name_is_not_subtraction(self):
        self.assertEqual(self._fill("AVG(start_time('Re-check Order') - end_time('Task A'))"),
                         "Re-check Order Waiting Time")

    def test_hyphen_operator_without_spaces(self):
        self.assertIsNone(self._fill("AVG(end_time('Task B')-start_time('Task A'))"))


if __name__ == "__main__":
    unittest.main()

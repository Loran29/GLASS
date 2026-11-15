"""Tests for two context_analysis hardening fixes:
  1. Non-simulatable temporal factors (event_month, case_start_month, arrival_quarter, …) are rejected early.
  2. Sparse segments (< min_segment_report_n cases) are filtered out and cause rejection
     when fewer than 2 reportable segments remain.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from utils.context_analysis import (  # noqa: E402
    AssociationAnalysisConfig,
    _NON_SIMULATABLE_TEMPORAL_FACTORS,
    _NON_SIMULATABLE_TEMPORAL_TOKENS,
    _is_non_simulatable_temporal,
    _analyze_categorical_relationship,
)


def _obs(factor_value: str, metric_value: float) -> dict:
    return {"factor_value": factor_value, "metric_value": metric_value}


def _make_obs(groups: dict[str, list[float]]) -> list[dict]:
    rows = []
    for fv, values in groups.items():
        for mv in values:
            rows.append(_obs(fv, mv))
    return rows


# ---------------------------------------------------------------------------
# Helpers for calling the function under test
# ---------------------------------------------------------------------------

def _run_categorical(
    factor_name: str,
    groups: dict[str, list[float]],
    *,
    config: AssociationAnalysisConfig | None = None,
) -> dict:
    cfg = config or AssociationAnalysisConfig()
    return _analyze_categorical_relationship(
        factor_name=factor_name,
        factor_scope="categorical",
        factor_type="categorical",
        metric_name="cycle_time",
        metric_scope="end_to_end",
        activity_name=None,
        observations=_make_obs(groups),
        provenance={},
        config=cfg,
    )


# ---------------------------------------------------------------------------
# Fix 1 — non-simulatable temporal factors
# ---------------------------------------------------------------------------

class TestNonSimulatableTemporalRejection(unittest.TestCase):

    def test_frozenset_contains_expected_entries(self):
        for name in ("month", "months", "event_month", "quarter", "event_quarter",
                     "season", "event_season", "year", "event_year",
                     "year_month", "event_year_month"):
            self.assertIn(name, _NON_SIMULATABLE_TEMPORAL_FACTORS, f"{name!r} missing")

    def test_frozenset_does_not_contain_simulatable(self):
        for name in ("hour_of_day", "day_of_week", "weekday", "hour"):
            self.assertNotIn(name, _NON_SIMULATABLE_TEMPORAL_FACTORS, f"{name!r} should not be in set")

    def _assert_rejected_non_simulatable(self, factor_name: str):
        groups = {str(i): [float(i * 10 + j) for j in range(20)] for i in range(1, 4)}
        result = _run_categorical(factor_name, groups)
        self.assertEqual(
            result["rejection_reason"],
            "non_simulatable_temporal_factor",
            f"Expected non_simulatable_temporal_factor for factor '{factor_name}', got: {result['rejection_reason']!r}",
        )
        self.assertFalse(result["accepted"])

    def test_event_month_rejected(self):
        self._assert_rejected_non_simulatable("event_month")

    def test_month_rejected(self):
        self._assert_rejected_non_simulatable("month")

    def test_event_quarter_rejected(self):
        self._assert_rejected_non_simulatable("event_quarter")

    def test_quarter_rejected(self):
        self._assert_rejected_non_simulatable("quarter")

    def test_event_year_rejected(self):
        self._assert_rejected_non_simulatable("event_year")

    def test_year_month_rejected(self):
        self._assert_rejected_non_simulatable("year_month")

    def test_event_season_rejected(self):
        self._assert_rejected_non_simulatable("event_season")

    # -- token-based (derived column name) variants --

    def test_case_start_month_rejected(self):
        self._assert_rejected_non_simulatable("case_start_month")

    def test_arrival_quarter_rejected(self):
        self._assert_rejected_non_simulatable("arrival_quarter")

    def test_submission_year_rejected(self):
        self._assert_rejected_non_simulatable("submission_year")

    def test_creation_month_rejected(self):
        self._assert_rejected_non_simulatable("creation_month")

    def test_is_non_simulatable_temporal_helper(self):
        positives = [
            "month", "event_month", "case_start_month", "arrival_month",
            "quarter", "event_quarter", "arrival_quarter",
            "year", "event_year", "submission_year",
            "season", "event_season",
        ]
        for name in positives:
            self.assertTrue(_is_non_simulatable_temporal(name), f"Expected True for {name!r}")

    def test_is_non_simulatable_temporal_helper_negatives(self):
        negatives = ["day_of_week", "hour_of_day", "priority", "claim_type", "weekday", "hour"]
        for name in negatives:
            self.assertFalse(_is_non_simulatable_temporal(name), f"Expected False for {name!r}")

    def test_case_insensitive(self):
        groups = {"Jan": [float(i) for i in range(20)], "Feb": [float(i + 20) for i in range(20)]}
        result = _run_categorical("Event_Month", groups)
        self.assertEqual(result["rejection_reason"], "non_simulatable_temporal_factor")

    def test_day_of_week_not_rejected(self):
        """day_of_week IS simulatable — should NOT be rejected as non_simulatable_temporal_factor."""
        groups = {
            "Mon": [float(i) for i in range(30)],
            "Tue": [float(i + 5) for i in range(30)],
        }
        # Use low thresholds so the test isn't blocked by sparse-segment rejection.
        cfg = AssociationAnalysisConfig(min_group_size=5, min_test_sample_size=10, min_segment_report_n=10)
        result = _run_categorical("day_of_week", groups, config=cfg)
        self.assertNotEqual(result["rejection_reason"], "non_simulatable_temporal_factor")

    def test_hour_of_day_not_rejected(self):
        groups = {
            "08": [float(i) for i in range(30)],
            "14": [float(i + 8) for i in range(30)],
        }
        cfg = AssociationAnalysisConfig(min_group_size=5, min_test_sample_size=10, min_segment_report_n=10)
        result = _run_categorical("hour_of_day", groups, config=cfg)
        self.assertNotEqual(result["rejection_reason"], "non_simulatable_temporal_factor")


# ---------------------------------------------------------------------------
# Fix 2 — sparse segment reporting threshold
# ---------------------------------------------------------------------------

class TestSparseSegmentFiltering(unittest.TestCase):

    def _config(self, min_segment_report_n: int = 30, min_group_size: int = 15, min_test_sample_size: int = 30) -> AssociationAnalysisConfig:
        return AssociationAnalysisConfig(
            min_group_size=min_group_size,
            min_test_sample_size=min_test_sample_size,
            min_segment_report_n=min_segment_report_n,
        )

    def test_default_config_raised_min_group_size(self):
        cfg = AssociationAnalysisConfig()
        self.assertEqual(cfg.min_group_size, 15)

    def test_default_config_raised_min_test_sample_size(self):
        cfg = AssociationAnalysisConfig()
        self.assertEqual(cfg.min_test_sample_size, 30)

    def test_default_config_has_min_segment_report_n(self):
        cfg = AssociationAnalysisConfig()
        self.assertEqual(cfg.min_segment_report_n, 30)

    def test_sparse_segments_cause_rejection(self):
        """Both groups have 20 obs — enough to pass min_group_size=15 and Kruskal test,
        but fewer than min_segment_report_n=30, so should be rejected."""
        groups = {
            "A": [float(i) for i in range(20)],
            "B": [float(i + 50) for i in range(20)],  # large gap → significant KW
        }
        cfg = self._config(min_group_size=15, min_test_sample_size=30, min_segment_report_n=30)
        result = _run_categorical("claim_type", groups, config=cfg)
        self.assertEqual(
            result["rejection_reason"],
            "insufficient_segment_support_for_reporting",
            f"Expected insufficient_segment_support_for_reporting, got: {result['rejection_reason']!r}",
        )

    def test_one_large_one_small_segment_causes_rejection(self):
        """One group with 40 obs, one with 10 obs: only 1 reportable segment → rejected."""
        groups = {
            "A": [float(i) for i in range(40)],
            "B": [float(i + 100) for i in range(10)],
        }
        # min_group_size=5 so both pass eligibility; min_segment_report_n=30 filters B out
        cfg = self._config(min_group_size=5, min_test_sample_size=15, min_segment_report_n=30)
        result = _run_categorical("priority", groups, config=cfg)
        self.assertEqual(
            result["rejection_reason"],
            "insufficient_segment_support_for_reporting",
        )

    def test_two_large_segments_accepted(self):
        """Both groups have 40 obs → 2 reportable segments → relationship should pass through."""
        groups = {
            "A": [float(i) for i in range(40)],
            "B": [float(i + 80) for i in range(40)],  # large gap → significant
        }
        cfg = self._config(min_group_size=15, min_test_sample_size=30, min_segment_report_n=30)
        result = _run_categorical("priority", groups, config=cfg)
        # Should NOT be rejected for sparse segments (may or may not be accepted by p-value)
        self.assertNotEqual(result["rejection_reason"], "insufficient_segment_support_for_reporting")
        # Segments in result should all have >= 30 obs
        for seg in result.get("segments", []):
            self.assertGreaterEqual(seg["sample_size"], 30)

    def test_segments_in_result_all_above_threshold(self):
        """When accepted, all returned segments must have >= min_segment_report_n obs."""
        groups = {
            "A": [float(i) for i in range(50)],
            "B": [float(i + 100) for i in range(50)],
            "C": [float(i + 5) for i in range(5)],  # small group — filtered by min_group_size
        }
        cfg = self._config(min_group_size=15, min_test_sample_size=30, min_segment_report_n=30)
        result = _run_categorical("region", groups, config=cfg)
        for seg in result.get("segments", []):
            self.assertGreaterEqual(
                seg["sample_size"],
                30,
                f"Segment {seg} has sample_size below min_segment_report_n",
            )

    def test_insufficient_group_support_still_fires_when_below_min_group_size(self):
        """When groups don't meet min_group_size, the earlier check fires, not sparse-segment."""
        groups = {
            "A": [float(i) for i in range(10)],
            "B": [float(i + 50) for i in range(10)],
        }
        cfg = self._config(min_group_size=15, min_test_sample_size=30, min_segment_report_n=30)
        result = _run_categorical("claim_type", groups, config=cfg)
        self.assertEqual(result["rejection_reason"], "insufficient_group_support")


if __name__ == "__main__":
    unittest.main()

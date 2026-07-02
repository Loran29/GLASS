"""Tests that the pipeline resists context-segmentation overfitting for general case attributes.

Three layers of defence are verified in isolation and combined:
  Layer 1 — Statistical filter  (_analyze_categorical_relationship + _finalize_relationship_decisions)
  Layer 2 — Pydantic validation (SMARTKpi model)
  Layer 3 — Sanitizer           (_sanitize_kpi_grounding_claims)

The temporal-factor rejection is covered in test_context_analysis_fixes.py.
This file focuses on the analogous risks for non-temporal case attributes
(e.g. claim_type, priority, supplier) where the overfitting would be caused by
marginal statistics or LLM hallucination rather than non-simulatability.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from app import _sanitize_kpi_grounding_claims, _fill_missing_activity_measurable_as  # noqa: E402
from models import KPIGenerationResult  # noqa: E402
from utils.context_analysis import (  # noqa: E402
    AssociationAnalysisConfig,
    _analyze_categorical_relationship,
    _finalize_relationship_decisions,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _obs(factor_value: str, metric_value: float) -> dict:
    return {"factor_value": factor_value, "metric_value": metric_value}


def _make_obs(groups: dict[str, list[float]]) -> list[dict]:
    rows = []
    for fv, values in groups.items():
        for mv in values:
            rows.append(_obs(fv, mv))
    return rows


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


def _kpi_result(kpis: list[dict]) -> KPIGenerationResult:
    return KPIGenerationResult(
        simulation_goal_structured="Reduce cycle time.",
        kpis=kpis,
        reasoning="Test.",
    )


def _kpi_payload(
    *,
    name: str = "Test KPI",
    category: str = "time",
    supported_by_log: bool = True,
    evidence_basis: str = "both",
    context_segmentation: list[dict] | None = None,
) -> dict:
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
        "suggested_formula": "AVG(end_time - start_time) across completed cases",
        "supported_by_log": supported_by_log,
        "evidence_basis": evidence_basis,
        "process_scope": "end_to_end",
        "context_segmentation": context_segmentation or [],
        "measurable_as": None,
    }


def _context_evidence_json(
    *,
    factor: str,
    metric: str,
    sample_size_a: int = 100,
    sample_size_b: int = 100,
) -> str:
    """Minimal accepted-relationship JSON that the sanitizer reads."""
    return json.dumps({
        "significant_relationships": [
            {
                "factor": factor,
                "factor_name": factor,
                "factor_scope": "categorical",
                "metric": metric,
                "metric_name": metric,
                "accepted": True,
                "is_significant": True,
                "adjusted_p_value": 0.01,
                "effect_size": 0.15,
                "effect_size_type": "epsilon_squared",
                "sample_size": sample_size_a + sample_size_b,
                "segments": [
                    {"condition": f"{factor} = 'high'", "observed_median": 10.0, "sample_size": sample_size_a},
                    {"condition": f"{factor} = 'low'", "observed_median": 5.0, "sample_size": sample_size_b},
                ],
                "summary": "Test summary.",
                "rejection_reason": None,
                "inclusion_reason": "Passed all filters.",
            }
        ],
        "rejected_relationships": [],
        "filtering_config": {},
    })


# ---------------------------------------------------------------------------
# Layer 1 — Statistical filter
# ---------------------------------------------------------------------------

class TestStatisticalFilterNonTemporalAttributes(unittest.TestCase):

    def test_weak_effect_size_rejected(self):
        """A relationship that passes the KW p-value test but has epsilon-squared below the
        min_kruskal_epsilon_squared threshold must be rejected by _finalize_relationship_decisions."""
        # Manufacture a relationship with a significant raw p-value but small effect size
        cfg = AssociationAnalysisConfig(min_group_size=15, min_test_sample_size=30, min_segment_report_n=30)
        synthetic = {
            "factor": "claim_type",
            "metric": "cycle_time",
            "test": "kruskal_wallis",
            "raw_p_value": 0.02,          # significant before correction
            "adjusted_p_value": None,     # will be set by BH in _finalize
            "effect_size": 0.04,          # below min_kruskal_epsilon_squared=0.08
            "effect_size_type": "epsilon_squared",
            "sample_size": 100,
            "segments": [],
            "is_significant": False,
            "accepted": False,
        }
        accepted, rejected = _finalize_relationship_decisions([synthetic], config=cfg)
        self.assertEqual(len(accepted), 0, "Small epsilon-squared should not produce an accepted relationship")
        self.assertTrue(
            any(r.get("rejection_reason") == "practical_effect_too_small" for r in rejected),
            f"Expected practical_effect_too_small rejection, got: {[r.get('rejection_reason') for r in rejected]}",
        )

    def test_insufficient_group_size_rejected(self):
        """Groups with < min_group_size observations should be rejected before the test."""
        groups = {
            "supplier_a": [float(i * 2) for i in range(10)],  # 10 < 15
            "supplier_b": [float(i * 3) for i in range(10)],
        }
        cfg = AssociationAnalysisConfig(min_group_size=15, min_test_sample_size=30, min_segment_report_n=30)
        result = _run_categorical("supplier", groups, config=cfg)
        self.assertEqual(result["rejection_reason"], "insufficient_group_support")

    def test_sparse_segments_rejected_even_when_test_passes(self):
        """Groups meet min_group_size but not min_segment_report_n — rejected after KW test."""
        groups = {
            "A": [float(i) for i in range(20)],  # 20 > 15 but < 30
            "B": [float(i + 60) for i in range(20)],  # large gap → significant KW
        }
        cfg = AssociationAnalysisConfig(min_group_size=15, min_test_sample_size=30, min_segment_report_n=30)
        result = _run_categorical("priority", groups, config=cfg)
        self.assertEqual(result["rejection_reason"], "insufficient_segment_support_for_reporting")

    def test_strong_real_association_passes_all_filters(self):
        """Two groups with 50 obs and a large, clear difference should pass all filters."""
        groups = {
            "urgent": [float(i) for i in range(50)],           # median ≈ 24.5
            "normal": [float(i + 200) for i in range(50)],     # median ≈ 224.5
        }
        cfg = AssociationAnalysisConfig(min_group_size=15, min_test_sample_size=30, min_segment_report_n=30)
        result = _run_categorical("priority", groups, config=cfg)
        accepted, _ = _finalize_relationship_decisions([result], config=cfg)
        self.assertEqual(len(accepted), 1, "Strong real association should be accepted")
        self.assertTrue(accepted[0]["accepted"])
        self.assertTrue(accepted[0]["is_significant"])
        for seg in accepted[0]["segments"]:
            self.assertGreaterEqual(seg["sample_size"], 30)


# ---------------------------------------------------------------------------
# Layer 2 — Pydantic validation
# ---------------------------------------------------------------------------

class TestPydanticValidationNonTemporalAttributes(unittest.TestCase):

    def test_context_segmentation_without_log_support_rejected(self):
        """Pydantic rejects context_segmentation when supported_by_log=False."""
        payload = _kpi_payload(
            name="Priority KPI",
            supported_by_log=False,
            evidence_basis="process_description_only",
            context_segmentation=[{
                "condition": "priority = 'high'",
                "target": "below current baseline",
                "evidence_factor": "priority",
                "evidence_metric": "cycle_time",
            }],
        )
        with self.assertRaises((ValidationError, ValueError)):
            KPIGenerationResult(
                simulation_goal_structured="Reduce cycle time.",
                kpis=[payload],
                reasoning="Test.",
            )

    def test_context_segmentation_with_description_only_basis_rejected(self):
        """Pydantic rejects context_segmentation with evidence_basis=process_description_only."""
        payload = _kpi_payload(
            name="Priority KPI",
            supported_by_log=True,
            evidence_basis="process_description_only",
            context_segmentation=[{
                "condition": "priority = 'high'",
                "target": "below current baseline",
                "evidence_factor": "priority",
                "evidence_metric": "cycle_time",
            }],
        )
        with self.assertRaises((ValidationError, ValueError)):
            KPIGenerationResult(
                simulation_goal_structured="Reduce cycle time.",
                kpis=[payload],
                reasoning="Test.",
            )

    def test_context_segmentation_missing_evidence_factor_rejected(self):
        """Pydantic rejects segments with empty evidence_factor."""
        payload = _kpi_payload(
            name="Priority KPI",
            supported_by_log=True,
            evidence_basis="both",
            context_segmentation=[{
                "condition": "priority = 'high'",
                "target": "below current baseline",
                "evidence_factor": "",   # empty — invalid
                "evidence_metric": "cycle_time",
            }],
        )
        with self.assertRaises((ValidationError, ValueError)):
            KPIGenerationResult(
                simulation_goal_structured="Reduce cycle time.",
                kpis=[payload],
                reasoning="Test.",
            )

    def test_valid_log_grounded_segmentation_passes(self):
        """A well-formed segment with log grounding should pass pydantic validation."""
        payload = _kpi_payload(
            name="Priority Cycle Time",
            supported_by_log=True,
            evidence_basis="both",
            context_segmentation=[{
                "condition": "priority = 'urgent'",
                "target": "below the observed median for urgent cases",
                "evidence_factor": "priority",
                "evidence_metric": "cycle_time",
                "sample_size": 120,
            }],
        )
        result = KPIGenerationResult(
            simulation_goal_structured="Reduce cycle time.",
            kpis=[payload],
            reasoning="Test.",
        )
        self.assertEqual(len(result.kpis[0].context_segmentation), 1)


# ---------------------------------------------------------------------------
# Layer 3 — Sanitizer
# ---------------------------------------------------------------------------

class TestSanitizerNonTemporalAttributes(unittest.TestCase):

    def test_context_segmentation_removed_when_no_context_evidence(self):
        """Even with a log present, all context_segmentation must be stripped when no
        significant relationships exist in the context evidence."""
        kpi = _kpi_payload(
            name="Cycle Time by Claim Type",
            supported_by_log=True,
            evidence_basis="both",
            context_segmentation=[{
                "condition": "claim_type = 'complex'",
                "target": "below current baseline",
                "evidence_factor": "claim_type",
                "evidence_metric": "cycle_time",
            }],
        )
        result = _kpi_result([kpi])
        sanitized, warnings = _sanitize_kpi_grounding_claims(
            result,
            log_profile={"some": "profile"},   # log is present
            context_evidence=None,              # but no context evidence
        )
        self.assertEqual(sanitized.kpis[0].context_segmentation, [])
        codes = {w["code"] for w in warnings}
        self.assertIn("removed_unsupported_context_segmentation", codes)

    def test_hallucinated_factor_segment_removed(self):
        """A segment for a factor NOT in the accepted relationships is stripped."""
        # Context evidence only has 'supplier' as an accepted factor
        context_evidence = _context_evidence_json(factor="supplier", metric="cycle_time")
        kpi = _kpi_payload(
            name="Priority Cycle Time",
            supported_by_log=True,
            evidence_basis="both",
            context_segmentation=[{
                "condition": "priority = 'urgent'",   # 'priority' is NOT in evidence
                "target": "below current baseline",
                "evidence_factor": "priority",
                "evidence_metric": "cycle_time",
            }],
        )
        result = _kpi_result([kpi])
        sanitized, warnings = _sanitize_kpi_grounding_claims(
            result,
            log_profile={"some": "profile"},
            context_evidence=context_evidence,
        )
        self.assertEqual(sanitized.kpis[0].context_segmentation, [])
        codes = {w["code"] for w in warnings}
        self.assertIn("removed_unsupported_context_segments", codes)

    def test_valid_factor_segment_kept(self):
        """A segment whose factor-metric pair IS in the accepted relationships is kept."""
        context_evidence = _context_evidence_json(factor="claim_type", metric="cycle_time")
        kpi = _kpi_payload(
            name="Claim Type Cycle Time",
            supported_by_log=True,
            evidence_basis="both",
            context_segmentation=[{
                "condition": "claim_type = 'high'",
                "target": "below the observed median for this segment",
                "evidence_factor": "claim_type",
                "evidence_metric": "cycle_time",
                "sample_size": 100,
            }],
        )
        result = _kpi_result([kpi])
        sanitized, _ = _sanitize_kpi_grounding_claims(
            result,
            log_profile={"some": "profile"},
            context_evidence=context_evidence,
        )
        self.assertEqual(len(sanitized.kpis[0].context_segmentation), 1)

    def test_mixed_segments_partially_sanitized(self):
        """A KPI with one valid and one hallucinated segment retains only the valid one."""
        context_evidence = _context_evidence_json(factor="claim_type", metric="cycle_time")
        kpi = _kpi_payload(
            name="Mixed KPI",
            supported_by_log=True,
            evidence_basis="both",
            context_segmentation=[
                {
                    "condition": "claim_type = 'high'",   # valid
                    "target": "below the observed median for this segment",
                    "evidence_factor": "claim_type",
                    "evidence_metric": "cycle_time",
                    "sample_size": 100,
                },
                {
                    "condition": "priority = 'urgent'",   # hallucinated
                    "target": "below current baseline",
                    "evidence_factor": "priority",
                    "evidence_metric": "cycle_time",
                },
            ],
        )
        result = _kpi_result([kpi])
        sanitized, warnings = _sanitize_kpi_grounding_claims(
            result,
            log_profile={"some": "profile"},
            context_evidence=context_evidence,
        )
        kept = sanitized.kpis[0].context_segmentation
        self.assertEqual(len(kept), 1)
        self.assertIn("claim_type", kept[0].condition)

    def test_no_log_removes_all_context_segmentation(self):
        """Without a log, all log grounding claims including context_segmentation must be stripped."""
        kpi = _kpi_payload(
            name="No-Log KPI",
            supported_by_log=True,   # LLM incorrectly claims log support
            evidence_basis="both",
            context_segmentation=[{
                "condition": "claim_type = 'complex'",
                "target": "below current baseline",
                "evidence_factor": "claim_type",
                "evidence_metric": "cycle_time",
            }],
        )
        result = _kpi_result([kpi])
        sanitized, warnings = _sanitize_kpi_grounding_claims(
            result,
            log_profile=None,     # no log at all
            context_evidence=None,
        )
        self.assertFalse(sanitized.kpis[0].supported_by_log)
        self.assertEqual(sanitized.kpis[0].context_segmentation, [])


# ---------------------------------------------------------------------------
# Auto-fill for missing activity-level measurable_as
# ---------------------------------------------------------------------------

class TestFillMissingActivityMeasurableAs(unittest.TestCase):

    def _activity_kpi(self, *, name: str, formula: str, measurable_as=None, process_scope="activity_level", category="time") -> dict:
        base = _kpi_payload(name=name, category=category)
        base["suggested_formula"] = formula
        base["process_scope"] = process_scope
        base["measurable_as"] = measurable_as
        return base

    def test_activity_level_time_kpi_with_null_gets_filled(self):
        """Apostrophe in activity name — LLM uses double-quoted formula."""
        kpi = self._activity_kpi(
            name="Waiting Time Before Invoice Release",
            formula='AVG(start_time("Authorize Supplier\'s Invoice payment") - complete_time(preceding)) across completed cases',
            measurable_as=None,
        )
        result = _kpi_result([kpi])
        filled, warnings = _fill_missing_activity_measurable_as(result)
        self.assertEqual(
            filled.kpis[0].measurable_as,
            "Authorize Supplier's Invoice payment Waiting Time",
        )
        codes = {w["code"] for w in warnings}
        self.assertIn("inferred_measurable_as", codes)

    def test_escaped_apostrophe_in_single_quoted_formula(self):
        """LLM uses single-quoted formula with backslash-escaped apostrophe: start_time('X\\'s Y')."""
        kpi = self._activity_kpi(
            name="Waiting Time Before Invoice Release",
            formula="AVG(start_time('Authorize Supplier\\'s Invoice payment') - complete_time('Send Invoice')) across completed cases",
            measurable_as=None,
        )
        result = _kpi_result([kpi])
        filled, warnings = _fill_missing_activity_measurable_as(result)
        self.assertEqual(
            filled.kpis[0].measurable_as,
            "Authorize Supplier's Invoice payment Waiting Time",
        )
        codes = {w["code"] for w in warnings}
        self.assertIn("inferred_measurable_as", codes)

    def test_unescaped_apostrophe_in_single_quoted_formula(self):
        """LLM uses single-quoted formula with bare apostrophe: start_time('Supplier's Name')."""
        kpi = self._activity_kpi(
            name="Waiting Time Before Invoice Release",
            formula="AVG(start_time('Authorize Supplier's Invoice payment') - complete_time('Send Invoice')) across completed cases",
            measurable_as=None,
        )
        result = _kpi_result([kpi])
        filled, warnings = _fill_missing_activity_measurable_as(result)
        self.assertEqual(
            filled.kpis[0].measurable_as,
            "Authorize Supplier's Invoice payment Waiting Time",
        )
        codes = {w["code"] for w in warnings}
        self.assertIn("inferred_measurable_as", codes)

    def test_duration_formula_not_filled(self):
        """complete_time(X) - start_time(X) is activity duration, not waiting time — must not be filled."""
        kpi = self._activity_kpi(
            name="Average Time to Analyze RFQ",
            formula="AVG(complete_time('Analyze Request for Quotation') - start_time('Analyze Request for Quotation')) across completed cases",
            measurable_as=None,
        )
        result = _kpi_result([kpi])
        filled, warnings = _fill_missing_activity_measurable_as(result)
        self.assertIsNone(filled.kpis[0].measurable_as)
        self.assertEqual(warnings, [])

    def test_already_set_measurable_as_not_overwritten(self):
        """If measurable_as is already set, do not overwrite it."""
        kpi = self._activity_kpi(
            name="Waiting Time Before PO Approval",
            formula="AVG(start_time('Approve Purchase Order for payment') - ...) across completed cases",
            measurable_as="Approve Purchase Order for payment Waiting Time",
        )
        result = _kpi_result([kpi])
        filled, warnings = _fill_missing_activity_measurable_as(result)
        self.assertEqual(filled.kpis[0].measurable_as, "Approve Purchase Order for payment Waiting Time")
        self.assertEqual(warnings, [])

    def test_end_to_end_scope_not_filled(self):
        """end_to_end scope KPI with null measurable_as must NOT be auto-filled."""
        kpi = self._activity_kpi(
            name="Procurement Cycle Time",
            formula="AVG(start_time('Pay Invoice') - start_time('Create Purchase Requisition')) across completed cases",
            measurable_as=None,
            process_scope="end_to_end",
        )
        result = _kpi_result([kpi])
        filled, warnings = _fill_missing_activity_measurable_as(result)
        self.assertIsNone(filled.kpis[0].measurable_as)
        self.assertEqual(warnings, [])

    def test_quality_category_not_filled(self):
        """Quality KPIs with null measurable_as must NOT be auto-filled (null is correct)."""
        kpi = self._activity_kpi(
            name="RFQ Amendment Rate",
            formula="COUNT(start_time('Amend Request for Quotation')) / COUNT(...) across the simulation run",
            measurable_as=None,
            process_scope="activity_level",
            category="quality",
        )
        result = _kpi_result([kpi])
        filled, warnings = _fill_missing_activity_measurable_as(result)
        self.assertIsNone(filled.kpis[0].measurable_as)
        self.assertEqual(warnings, [])

    def test_formula_without_start_time_pattern_not_filled(self):
        """If the formula doesn't contain start_time('...'), no auto-fill occurs."""
        kpi = self._activity_kpi(
            name="Obscure KPI",
            formula="AVG(some_metric) across completed cases",
            measurable_as=None,
        )
        result = _kpi_result([kpi])
        filled, warnings = _fill_missing_activity_measurable_as(result)
        self.assertIsNone(filled.kpis[0].measurable_as)
        self.assertEqual(warnings, [])

    def test_simple_activity_name_filled(self):
        """Standard activity name without special characters also works."""
        kpi = self._activity_kpi(
            name="Triage Waiting Time KPI",
            formula="AVG(start_time('Triage') - complete_time(previous)) across completed cases",
            measurable_as=None,
        )
        result = _kpi_result([kpi])
        filled, warnings = _fill_missing_activity_measurable_as(result)
        self.assertEqual(filled.kpis[0].measurable_as, "Triage Waiting Time")


if __name__ == "__main__":
    unittest.main()

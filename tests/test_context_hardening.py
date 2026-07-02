from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from app import (  # noqa: E402
    _finalize_generated_result,
    _sanitize_kpi_grounding_claims,
    parse_with_retries,
)
from models import KPIGenerationResult  # noqa: E402
from utils.context_analysis import (  # noqa: E402
    AssociationAnalysisConfig,
    _apply_bh_fdr_correction,
    _finalize_relationship_decisions,
    analyze_contextual_impact,
)
from utils.log_processing import profile_event_log  # noqa: E402
from utils.semantic_validation import validate_kpi_generation_semantics  # noqa: E402


def _csv_bytes(text: str) -> io.BytesIO:
    return io.BytesIO(text.encode("utf-8"))


def _kpi_payload(
    *,
    name: str,
    description: str = "Measures process performance.",
    category: str = "time",
    target_direction: str = "minimize",
    supported_by_log: bool = False,
    evidence_basis: str = "process_description_only",
    process_scope: str = "end_to_end",
    context_segmentation: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "description": description,
        "category": category,
        "smart_breakdown": {
            "specific": description,
            "measurable": "Measured from process events.",
            "achievable": "Realistic under current operations.",
            "relevant": "Aligned with the simulation goal.",
            "time_bound": "Measured weekly.",
        },
        "target_direction": target_direction,
        "suggested_formula": "AVG(end_time - start_time)",
        "supported_by_log": supported_by_log,
        "evidence_basis": evidence_basis,
        "process_scope": process_scope,
        "context_segmentation": context_segmentation or [],
    }


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def generate(self, **_: object) -> str:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


class ContextHardeningTests(unittest.TestCase):
    def test_factor_screening_records_exclusion_reasons(self) -> None:
        csv_text = """case_id,activity,timestamp,resource,region,technical_id,comments,sparse_note
1,Submit,2025-01-01 09:00,A,North,REQ-10001,"Very long explanatory note about the special handling circumstances",x
1,Review,2025-01-01 10:00,B,North,REQ-10002,"Very long explanatory note about the special handling circumstances",
2,Submit,2025-01-02 09:00,A,North,REQ-10003,"Very long explanatory note about the special handling circumstances",
2,Review,2025-01-02 11:00,B,North,REQ-10004,"Very long explanatory note about the special handling circumstances",
3,Submit,2025-01-03 09:00,A,South,REQ-10005,"Very long explanatory note about the special handling circumstances",
3,Review,2025-01-03 10:30,B,South,REQ-10006,"Very long explanatory note about the special handling circumstances",
4,Submit,2025-01-04 09:15,A,South,REQ-10007,"Very long explanatory note about the special handling circumstances",
4,Review,2025-01-04 10:45,B,South,REQ-10008,"Very long explanatory note about the special handling circumstances",
"""
        profile = profile_event_log(_csv_bytes(csv_text))
        self.assertIsNotNone(profile)
        context_profile = profile["context_profile"]

        included_names = {factor["name"] for factor in context_profile["included_factors"]}
        excluded = {factor["name"]: factor for factor in context_profile["excluded_factors"]}

        self.assertIn("region", included_names)
        self.assertIn("technical_id", excluded)
        self.assertIn("comments", excluded)
        self.assertIn("sparse_note", excluded)
        self.assertIn("identifier", excluded["technical_id"]["exclusion_reason"].lower())
        self.assertIn("free text", excluded["comments"]["exclusion_reason"].lower())
        self.assertIn("missingness", excluded["sparse_note"]["exclusion_reason"].lower())

    def test_metric_provenance_is_populated(self) -> None:
        csv_text = """case_id,activity,timestamp,end_time,resource,priority
1,Submit,2025-01-01 09:00,2025-01-01 09:10,A,high
1,Review,2025-01-01 10:00,2025-01-01 10:30,B,high
2,Submit,2025-01-02 09:00,2025-01-02 09:05,A,low
2,Review,2025-01-02 12:00,2025-01-02 12:20,B,low
"""
        profile = profile_event_log(_csv_bytes(csv_text))
        self.assertIsNotNone(profile)
        metric_metadata = profile["context_profile"]["metric_metadata"]

        self.assertEqual(metric_metadata["case_cycle_time_hours"]["status"], "derived_from_log")
        self.assertEqual(metric_metadata["activity_wait_time_hours"]["status"], "derived_from_log")
        self.assertEqual(metric_metadata["activity_duration_hours"]["status"], "approximated")
        self.assertIn("end_time", metric_metadata["activity_duration_hours"]["derivation_notes"])

    def test_bh_multiple_testing_correction_is_applied(self) -> None:
        relationships = [
            {"raw_p_value": 0.01},
            {"raw_p_value": 0.03},
            {"raw_p_value": 0.04},
        ]
        _apply_bh_fdr_correction(relationships)
        adjusted = [relationship["adjusted_p_value"] for relationship in relationships]
        self.assertEqual(adjusted, [0.03, 0.04, 0.04])
        self.assertEqual([relationship["p_value"] for relationship in relationships], adjusted)

    def test_effect_size_filter_rejects_trivial_significant_result(self) -> None:
        accepted, rejected = _finalize_relationship_decisions(
            [
                {
                    "factor": "priority",
                    "raw_p_value": 0.01,
                    "adjusted_p_value": 0.01,
                    "effect_size": 0.12,
                    "effect_size_type": "spearman_rho",
                    "notes": [],
                },
                {
                    "factor": "claim_type",
                    "raw_p_value": 0.01,
                    "adjusted_p_value": 0.01,
                    "effect_size": 0.11,
                    "effect_size_type": "epsilon_squared",
                    "notes": [],
                },
            ],
            config=AssociationAnalysisConfig(
                min_abs_spearman_rho=0.3,
                min_kruskal_epsilon_squared=0.08,
            ),
        )

        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["factor"], "claim_type")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["factor"], "priority")
        self.assertEqual(rejected[0]["rejection_reason"], "practical_effect_too_small")

    def test_support_thresholds_block_fragile_associations(self) -> None:
        result = analyze_contextual_impact(
            factor_definitions=[
                {"name": "region", "scope": "case_level", "value_type": "categorical"},
            ],
            case_observations=[
                {"factors": {"region": "north"}, "metrics": {"case_cycle_time_hours": 1.0}},
                {"factors": {"region": "north"}, "metrics": {"case_cycle_time_hours": 1.1}},
                {"factors": {"region": "south"}, "metrics": {"case_cycle_time_hours": 3.0}},
                {"factors": {"region": "south"}, "metrics": {"case_cycle_time_hours": 3.2}},
            ],
            activity_observations=[],
            metric_metadata={"case_cycle_time_hours": {"status": "derived_from_log", "derivation_notes": "Derived."}},
            config=AssociationAnalysisConfig(min_group_size=3, min_test_sample_size=6),
        )

        self.assertEqual(result["significant_relationships"], [])
        self.assertTrue(result["rejected_relationships"])
        self.assertEqual(
            result["rejected_relationships"][0]["rejection_reason"],
            "insufficient_group_support",
        )

    def test_graceful_when_no_significant_associations_remain(self) -> None:
        result = analyze_contextual_impact(
            factor_definitions=[
                {"name": "priority", "scope": "case_level", "value_type": "categorical"},
            ],
            case_observations=[
                {"factors": {"priority": "high"}, "metrics": {"case_cycle_time_hours": 2.0}},
                {"factors": {"priority": "high"}, "metrics": {"case_cycle_time_hours": 2.0}},
                {"factors": {"priority": "high"}, "metrics": {"case_cycle_time_hours": 2.0}},
                {"factors": {"priority": "low"}, "metrics": {"case_cycle_time_hours": 2.0}},
                {"factors": {"priority": "low"}, "metrics": {"case_cycle_time_hours": 2.0}},
                {"factors": {"priority": "low"}, "metrics": {"case_cycle_time_hours": 2.0}},
            ],
            activity_observations=[],
            metric_metadata={"case_cycle_time_hours": {"status": "derived_from_log", "derivation_notes": "Derived."}},
        )

        self.assertEqual(result["significant_relationships"], [])
        self.assertGreaterEqual(len(result["rejected_relationships"]), 1)
        self.assertIn("benjamini_hochberg", result["fdr_method"])

    def test_supported_context_segments_are_enriched_with_traceability(self) -> None:
        result = KPIGenerationResult.model_validate(
            {
                "simulation_goal_structured": "Use supported context evidence.",
                "reasoning": "Testing traceability enrichment.",
                "kpis": [
                    _kpi_payload(
                        name="Cycle Time by Claim Type",
                        supported_by_log=True,
                        evidence_basis="both",
                        context_segmentation=[
                            {
                                "condition": "claim_type = simple",
                                "target": "<= 24 hours",
                                "rationale": "Segment-specific target.",
                                "evidence_factor": "claim_type",
                                "evidence_metric": "case_cycle_time_hours",
                            }
                        ],
                    )
                ],
            }
        )
        context_evidence = json.dumps(
            {
                "significant_relationships": [
                    {
                        "factor": "claim_type",
                        "metric": "case_cycle_time_hours",
                        "adjusted_p_value": 0.012,
                        "effect_size": 0.19,
                        "sample_size": 84,
                        "segments": [
                            {
                                "condition": "claim_type = simple",
                                "observed_median": 30.0,
                                "sample_size": 52,
                            }
                        ],
                    }
                ]
            }
        )

        sanitized, warnings = _sanitize_kpi_grounding_claims(
            result,
            log_profile={"context_profile": {}},
            context_evidence=context_evidence,
        )

        self.assertEqual(warnings, [])
        segment = sanitized.kpis[0].context_segmentation[0]
        self.assertEqual(segment.evidence_factor, "claim_type")
        self.assertEqual(segment.evidence_metric, "case_cycle_time_hours")
        self.assertEqual(segment.adjusted_p_value, 0.012)
        self.assertEqual(segment.effect_size, 0.19)
        self.assertEqual(segment.sample_size, 52)
        self.assertEqual(segment.observed_baseline, 30.0)
        self.assertEqual(segment.target_type, "direct")

    def test_sanitation_keeps_supported_segment_with_condition_wording_difference(self) -> None:
        result = KPIGenerationResult.model_validate(
            {
                "simulation_goal_structured": "Use supported context evidence.",
                "reasoning": "Testing tolerant sanitation.",
                "kpis": [
                    _kpi_payload(
                        name="Triage Waiting Time by Time of Day",
                        supported_by_log=True,
                        evidence_basis="both",
                        context_segmentation=[
                            {
                                "condition": "Time of Day <= 16.0",
                                "target": "<= 25 minutes",
                                "rationale": "Segment-specific target.",
                                "evidence_factor": "event_hour_of_day",
                                "evidence_metric": "activity_wait_time_hours",
                            }
                        ],
                    )
                ],
            }
        )
        context_evidence = json.dumps(
            {
                "significant_relationships": [
                    {
                        "factor": "event_hour_of_day",
                        "metric": "activity_wait_time_hours",
                        "sample_size": 1058,
                        "segments": [
                            {"condition": "event_hour_of_day <= 16", "observed_median": 0.35, "sample_size": 769},
                            {"condition": "event_hour_of_day > 16", "observed_median": 0.58, "sample_size": 289},
                        ],
                    }
                ]
            }
        )

        sanitized, warnings = _sanitize_kpi_grounding_claims(
            result,
            log_profile={"context_profile": {}},
            context_evidence=context_evidence,
        )

        self.assertEqual(warnings, [])
        self.assertEqual(len(sanitized.kpis[0].context_segmentation), 1)
        segment = sanitized.kpis[0].context_segmentation[0]
        self.assertEqual(segment.sample_size, 769)
        self.assertEqual(segment.observed_baseline, 0.35)

    def test_sanitation_removes_segment_with_unsupported_factor_metric_pair(self) -> None:
        result = KPIGenerationResult.model_validate(
            {
                "simulation_goal_structured": "Use supported context evidence.",
                "reasoning": "Testing unsupported pair removal.",
                "kpis": [
                    _kpi_payload(
                        name="Priority Waiting Time",
                        supported_by_log=True,
                        evidence_basis="both",
                        context_segmentation=[
                            {
                                "condition": "priority = high",
                                "target": "<= 4 hours",
                                "rationale": "Unsupported pair.",
                                "evidence_factor": "priority",
                                "evidence_metric": "activity_wait_time_hours",
                            }
                        ],
                    )
                ],
            }
        )
        context_evidence = json.dumps(
            {
                "significant_relationships": [
                    {
                        "factor": "claim_type",
                        "metric": "case_cycle_time_hours",
                        "segments": [{"condition": "claim_type = simple", "observed_median": 30.0, "sample_size": 52}],
                    }
                ]
            }
        )

        sanitized, warnings = _sanitize_kpi_grounding_claims(
            result,
            log_profile={"context_profile": {}},
            context_evidence=context_evidence,
        )

        self.assertEqual(sanitized.kpis[0].context_segmentation, [])
        self.assertEqual(warnings[0]["code"], "removed_unsupported_context_segments")

    def test_sanitation_temporal_factor_normalization_avoids_false_removal(self) -> None:
        result = KPIGenerationResult.model_validate(
            {
                "simulation_goal_structured": "Use supported context evidence.",
                "reasoning": "Testing temporal normalization in sanitation.",
                "kpis": [
                    _kpi_payload(
                        name="Cycle Time by Day of Week",
                        supported_by_log=True,
                        evidence_basis="both",
                        context_segmentation=[
                            {
                                "condition": "day_of_week = Monday",
                                "target": "<= 8 hours",
                                "rationale": "Temporal segment.",
                                "evidence_factor": "day_of_week",
                                "evidence_metric": "case_cycle_time_hours",
                            }
                        ],
                    )
                ],
            }
        )
        context_evidence = json.dumps(
            {
                "significant_relationships": [
                    {
                        "factor": "case_start_day_of_week",
                        "metric": "case_cycle_time_hours",
                        "segments": [
                            {"condition": "case_start_day_of_week = Monday", "observed_median": 7.5, "sample_size": 24},
                            {"condition": "case_start_day_of_week = Tuesday", "observed_median": 6.8, "sample_size": 20},
                        ],
                    }
                ]
            }
        )

        sanitized, warnings = _sanitize_kpi_grounding_claims(
            result,
            log_profile={"context_profile": {}},
            context_evidence=context_evidence,
        )

        self.assertEqual(warnings, [])
        self.assertEqual(len(sanitized.kpis[0].context_segmentation), 1)
        segment = sanitized.kpis[0].context_segmentation[0]
        self.assertEqual(segment.evidence_factor, "case_start_day_of_week")
        self.assertEqual(segment.evidence_metric, "case_cycle_time_hours")
        self.assertEqual(segment.sample_size, 24)
        self.assertEqual(segment.observed_baseline, 7.5)

    def test_sanitation_warns_when_pair_supported_but_condition_unmatched(self) -> None:
        result = KPIGenerationResult.model_validate(
            {
                "simulation_goal_structured": "Use supported context evidence.",
                "reasoning": "Testing unmatched condition warning.",
                "kpis": [
                    _kpi_payload(
                        name="Cycle Time by Claim Type",
                        supported_by_log=True,
                        evidence_basis="both",
                        context_segmentation=[
                            {
                                "condition": "claim_type = vip",
                                "target": "<= 24 hours",
                                "rationale": "Retained on pair support only.",
                                "evidence_factor": "claim_type",
                                "evidence_metric": "case_cycle_time_hours",
                            }
                        ],
                    )
                ],
            }
        )
        context_evidence = json.dumps(
            {
                "significant_relationships": [
                    {
                        "factor": "claim_type",
                        "metric": "case_cycle_time_hours",
                        "segments": [
                            {"condition": "claim_type = simple", "observed_median": 30.0, "sample_size": 52},
                            {"condition": "claim_type = complex", "observed_median": 42.0, "sample_size": 31},
                        ],
                    }
                ]
            }
        )

        sanitized, warnings = _sanitize_kpi_grounding_claims(
            result,
            log_profile={"context_profile": {}},
            context_evidence=context_evidence,
        )

        self.assertEqual(len(sanitized.kpis[0].context_segmentation), 1)
        warning_codes = {warning["code"] for warning in warnings}
        self.assertIn("kept_context_segment_with_unmatched_condition", warning_codes)
        segment = sanitized.kpis[0].context_segmentation[0]
        self.assertIsNone(segment.sample_size)
        self.assertIsNone(segment.observed_baseline)

    def test_sanitation_prefers_relationship_with_matching_segment_for_baseline(self) -> None:
        result = KPIGenerationResult.model_validate(
            {
                "simulation_goal_structured": "Use supported context evidence.",
                "reasoning": "Testing relationship selection for segment baselines.",
                "kpis": [
                    _kpi_payload(
                        name="Cycle Time by Claim Type",
                        supported_by_log=True,
                        evidence_basis="both",
                        context_segmentation=[
                            {
                                "condition": "claim_type = simple",
                                "target": "<= 24 hours",
                                "rationale": "Use the matching simple-claim segment.",
                                "evidence_factor": "claim_type",
                                "evidence_metric": "case_cycle_time_hours",
                            }
                        ],
                    )
                ],
            }
        )
        context_evidence = json.dumps(
            {
                "significant_relationships": [
                    {
                        "factor": "claim_type",
                        "metric": "case_cycle_time_hours",
                        "sample_size": 6,
                        "segments": [
                            {"condition": "claim_type = complex", "observed_median": 42.0, "sample_size": 6},
                        ],
                    },
                    {
                        "factor": "claim_type",
                        "metric": "case_cycle_time_hours",
                        "sample_size": 52,
                        "segments": [
                            {"condition": "claim_type = simple", "observed_median": 30.0, "sample_size": 52},
                        ],
                    },
                ]
            }
        )

        sanitized, warnings = _sanitize_kpi_grounding_claims(
            result,
            log_profile={"context_profile": {}},
            context_evidence=context_evidence,
        )

        self.assertEqual(warnings, [])
        segment = sanitized.kpis[0].context_segmentation[0]
        self.assertEqual(segment.sample_size, 52)
        self.assertEqual(segment.observed_baseline, 30.0)

    def test_unsupported_segmentation_is_removed_when_no_log_is_available(self) -> None:
        result = KPIGenerationResult.model_validate(
            {
                "simulation_goal_structured": "Reduce delays.",
                "reasoning": "Testing no-log fallback.",
                "kpis": [
                    _kpi_payload(
                        name="Priority-Sensitive Review Waiting Time",
                        supported_by_log=True,
                        evidence_basis="both",
                        context_segmentation=[
                            {
                                "condition": "priority = high",
                                "target": "<= 4 hours",
                                "rationale": "Unsupported without a log.",
                                "evidence_factor": "priority",
                                "evidence_metric": "activity_wait_time_hours",
                            }
                        ],
                    )
                ],
            }
        )

        finalized, semantic_validation = _finalize_generated_result(
            result,
            simulation_goal="Reduce review waiting time.",
            log_profile=None,
            context_evidence=None,
        )

        kpi = finalized.kpis[0]
        self.assertFalse(kpi.supported_by_log)
        self.assertEqual(kpi.evidence_basis.value, "process_description_only")
        self.assertEqual(kpi.context_segmentation, [])
        warning_codes = {issue["code"] for issue in semantic_validation["issues"]}
        self.assertIn("removed_unsupported_log_claim", warning_codes)
        self.assertIn("removed_unsupported_context_segmentation", warning_codes)

    def test_parse_with_retries_repairs_semantic_errors(self) -> None:
        invalid_first = json.dumps(
            {
                "simulation_goal_structured": "Reduce cycle time while maintaining quality.",
                "reasoning": "Initial draft.",
                "kpis": [
                    _kpi_payload(
                        name="Average Cycle Time",
                        description="Measures overall cycle time.",
                        supported_by_log=False,
                        evidence_basis="process_description_only",
                    ),
                    _kpi_payload(
                        name="Average Cycle Time",
                        description="Measures quality safeguards.",
                        category="quality",
                        target_direction="maintain",
                        supported_by_log=False,
                        evidence_basis="process_description_only",
                    ),
                ],
            }
        )
        repaired = json.dumps(
            {
                "simulation_goal_structured": "Reduce cycle time while maintaining quality.",
                "reasoning": "Repaired draft.",
                "kpis": [
                    _kpi_payload(
                        name="Average Cycle Time",
                        description="Measures overall cycle time.",
                        supported_by_log=False,
                        evidence_basis="process_description_only",
                    ),
                    _kpi_payload(
                        name="Quality Check Pass Rate",
                        description="Measures whether quality remains stable.",
                        category="quality",
                        target_direction="maintain",
                        supported_by_log=False,
                        evidence_basis="process_description_only",
                    ),
                ],
            }
        )

        provider = FakeProvider([invalid_first, repaired])
        result, _, semantic_validation = parse_with_retries(
            provider=provider,
            system_prompt="system",
            user_prompt="user",
            simulation_goal="Reduce cycle time while maintaining quality.",
            temperature=0.0,
            max_retries=1,
            log_profile=None,
            context_evidence=None,
        )

        self.assertEqual(provider.calls, 2)
        self.assertEqual([kpi.name for kpi in result.kpis], ["Average Cycle Time", "Quality Check Pass Rate"])
        self.assertFalse(semantic_validation["has_errors"])


class SchemaHardeningTests(unittest.TestCase):
    """Tests for Improvements 1 and 3: schema-level validation hardening."""

    # -- Case A: empty segmentation + process_description_only is fine --
    def test_empty_segmentation_with_description_only_validates(self) -> None:
        result = KPIGenerationResult.model_validate(
            {
                "simulation_goal_structured": "Reduce cycle time.",
                "reasoning": "Testing empty segmentation.",
                "kpis": [
                    _kpi_payload(
                        name="Cycle Time",
                        supported_by_log=False,
                        evidence_basis="process_description_only",
                    )
                ],
            }
        )
        self.assertEqual(result.kpis[0].context_segmentation, [])

    # -- Case B: segmentation + supported_by_log=False must fail --
    def test_segmentation_without_log_support_fails(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            KPIGenerationResult.model_validate(
                {
                    "simulation_goal_structured": "Test.",
                    "reasoning": "Test.",
                    "kpis": [
                        _kpi_payload(
                            name="Segmented KPI",
                            supported_by_log=False,
                            evidence_basis="both",
                            context_segmentation=[
                                {
                                    "condition": "region = north",
                                    "target": "<= 5 hours",
                                    "evidence_factor": "region",
                                    "evidence_metric": "case_cycle_time_hours",
                                }
                            ],
                        )
                    ],
                }
            )
        self.assertIn("supported_by_log", str(ctx.exception))

    # -- Case C: segmentation + process_description_only must fail --
    def test_segmentation_with_description_only_fails(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            KPIGenerationResult.model_validate(
                {
                    "simulation_goal_structured": "Test.",
                    "reasoning": "Test.",
                    "kpis": [
                        _kpi_payload(
                            name="Segmented KPI",
                            supported_by_log=True,
                            evidence_basis="process_description_only",
                            context_segmentation=[
                                {
                                    "condition": "region = north",
                                    "target": "<= 5 hours",
                                    "evidence_factor": "region",
                                    "evidence_metric": "case_cycle_time_hours",
                                }
                            ],
                        )
                    ],
                }
            )
        self.assertIn("process_description_only", str(ctx.exception))

    # -- Case D: segmentation missing evidence_factor must fail --
    def test_segmentation_missing_evidence_factor_fails(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            KPIGenerationResult.model_validate(
                {
                    "simulation_goal_structured": "Test.",
                    "reasoning": "Test.",
                    "kpis": [
                        _kpi_payload(
                            name="Segmented KPI",
                            supported_by_log=True,
                            evidence_basis="both",
                            context_segmentation=[
                                {
                                    "condition": "region = north",
                                    "target": "<= 5 hours",
                                    "evidence_metric": "case_cycle_time_hours",
                                }
                            ],
                        )
                    ],
                }
            )
        self.assertIn("evidence_factor", str(ctx.exception))

    # -- Case E: segmentation missing evidence_metric must fail --
    def test_segmentation_missing_evidence_metric_fails(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            KPIGenerationResult.model_validate(
                {
                    "simulation_goal_structured": "Test.",
                    "reasoning": "Test.",
                    "kpis": [
                        _kpi_payload(
                            name="Segmented KPI",
                            supported_by_log=True,
                            evidence_basis="both",
                            context_segmentation=[
                                {
                                    "condition": "region = north",
                                    "target": "<= 5 hours",
                                    "evidence_factor": "region",
                                }
                            ],
                        )
                    ],
                }
            )
        self.assertIn("evidence_metric", str(ctx.exception))

    # -- Case F: valid segmentation with log grounding validates --
    def test_valid_segmentation_with_log_grounding_validates(self) -> None:
        result = KPIGenerationResult.model_validate(
            {
                "simulation_goal_structured": "Test.",
                "reasoning": "Test.",
                "kpis": [
                    _kpi_payload(
                        name="Segmented KPI",
                        supported_by_log=True,
                        evidence_basis="both",
                        context_segmentation=[
                            {
                                "condition": "region = north",
                                "target": "<= 5 hours",
                                "evidence_factor": "region",
                                "evidence_metric": "case_cycle_time_hours",
                            }
                        ],
                    )
                ],
            }
        )
        segment = result.kpis[0].context_segmentation[0]
        self.assertEqual(segment.evidence_factor, "region")
        self.assertEqual(segment.evidence_metric, "case_cycle_time_hours")

    # -- Case G: missing process_scope must fail instead of auto-defaulting --
    def test_missing_process_scope_fails_validation(self) -> None:
        payload = _kpi_payload(name="No Scope KPI")
        del payload["process_scope"]
        with self.assertRaises(ValidationError) as ctx:
            KPIGenerationResult.model_validate(
                {
                    "simulation_goal_structured": "Test.",
                    "reasoning": "Test.",
                    "kpis": [payload],
                }
            )
        self.assertIn("process_scope", str(ctx.exception))

    # -- Case H: prompt example 6 demonstrates traceability fields --
    def test_prompt_example_6_contains_traceability_fields(self) -> None:
        from prompts.smart_kpi_prompt import _EXAMPLE_6_OUTPUT  # noqa: E402

        for kpi in _EXAMPLE_6_OUTPUT["kpis"]:
            for idx, segment in enumerate(kpi.get("context_segmentation", [])):
                self.assertTrue(
                    segment.get("evidence_factor"),
                    f"Example 6 KPI '{kpi['name']}' segment {idx} missing evidence_factor",
                )
                self.assertTrue(
                    segment.get("evidence_metric"),
                    f"Example 6 KPI '{kpi['name']}' segment {idx} missing evidence_metric",
                )

    def test_prompt_example_6_does_not_claim_adjusted_p_value_without_evidence(self) -> None:
        from prompts.smart_kpi_prompt import _EXAMPLE_6_CONTEXT_EVIDENCE, _EXAMPLE_6_OUTPUT  # noqa: E402

        evidence_has_adjusted = any(
            "adjusted_p_value" in relationship
            for relationship in _EXAMPLE_6_CONTEXT_EVIDENCE.get("significant_relationships", [])
        )
        output_has_adjusted = any(
            "adjusted_p_value" in segment
            for kpi in _EXAMPLE_6_OUTPUT["kpis"]
            for segment in kpi.get("context_segmentation", [])
        )

        if not evidence_has_adjusted:
            self.assertFalse(output_has_adjusted)

    def test_prompt_text_requires_explicit_adjusted_p_values(self) -> None:
        from prompts.smart_kpi_prompt import REQUIRED_SCHEMA, build_smart_kpi_prompt  # noqa: E402

        self.assertIn('Do not relabel a raw "p_value" as an "adjusted_p_value"', REQUIRED_SCHEMA)
        _, _, user_prompt = build_smart_kpi_prompt(
            process_description="A process starts and ends.",
            simulation_goal="Reduce cycle time.",
            num_kpis=3,
            context_evidence='{"significant_relationships": []}',
        )
        self.assertIn("Do not relabel a raw p_value as adjusted_p_value", user_prompt)

    def test_prompt_text_requires_omitting_unavailable_traceability_fields(self) -> None:
        from prompts.smart_kpi_prompt import REQUIRED_SCHEMA, build_smart_kpi_prompt  # noqa: E402

        self.assertIn(
            "omit it rather than inferring or inventing it",
            REQUIRED_SCHEMA,
        )
        _, _, user_prompt = build_smart_kpi_prompt(
            process_description="A process starts and ends.",
            simulation_goal="Reduce cycle time.",
            num_kpis=3,
            context_evidence='{"significant_relationships": []}',
        )
        self.assertIn(
            "omit it rather than inferring or inventing it",
            user_prompt,
        )

    # -- Segmentation with event_log_only is valid --
    def test_segmentation_with_event_log_only_validates(self) -> None:
        result = KPIGenerationResult.model_validate(
            {
                "simulation_goal_structured": "Test.",
                "reasoning": "Test.",
                "kpis": [
                    _kpi_payload(
                        name="Log Only Segmented KPI",
                        supported_by_log=True,
                        evidence_basis="event_log_only",
                        context_segmentation=[
                            {
                                "condition": "priority = high",
                                "target": "<= 2 hours",
                                "evidence_factor": "priority",
                                "evidence_metric": "activity_wait_time_hours",
                            }
                        ],
                    )
                ],
            }
        )
        self.assertTrue(result.kpis[0].context_segmentation)

    # -- Segmentation with proxy_from_log is valid --
    def test_segmentation_with_proxy_from_log_validates(self) -> None:
        result = KPIGenerationResult.model_validate(
            {
                "simulation_goal_structured": "Test.",
                "reasoning": "Test.",
                "kpis": [
                    _kpi_payload(
                        name="Proxy Segmented KPI",
                        supported_by_log=True,
                        evidence_basis="proxy_from_log",
                        context_segmentation=[
                            {
                                "condition": "day_of_week = Monday",
                                "target": "<= 3 hours",
                                "evidence_factor": "day_of_week",
                                "evidence_metric": "case_cycle_time_hours",
                            }
                        ],
                    )
                ],
            }
        )
        self.assertTrue(result.kpis[0].context_segmentation)


class SemanticValidationHardeningTests(unittest.TestCase):
    def test_unsupported_context_condition_becomes_error(self) -> None:
        result = KPIGenerationResult.model_validate(
            {
                "simulation_goal_structured": "Reduce cycle time.",
                "reasoning": "Testing unsupported segmented conditions.",
                "kpis": [
                    _kpi_payload(
                        name="Cycle Time by Claim Type",
                        supported_by_log=True,
                        evidence_basis="both",
                        context_segmentation=[
                            {
                                "condition": "claim_type = vip",
                                "target": "<= 24 hours",
                                "evidence_factor": "claim_type",
                                "evidence_metric": "case_cycle_time_hours",
                            }
                        ],
                    )
                ],
            }
        )
        context_evidence = json.dumps(
            {
                "significant_relationships": [
                    {
                        "factor": "claim_type",
                        "metric": "case_cycle_time_hours",
                        "segments": [
                            {"condition": "claim_type = simple"},
                            {"condition": "claim_type = complex"},
                        ],
                    }
                ]
            }
        )

        validation = validate_kpi_generation_semantics(
            result,
            simulation_goal="Reduce cycle time.",
            context_evidence=context_evidence,
        )

        matching_issue = next(
            issue for issue in validation.issues if issue.code == "unsupported_context_condition"
        )
        self.assertEqual(matching_issue.severity, "error")

    def test_mismatched_evidence_factor_metric_pair_fails_validation(self) -> None:
        result = KPIGenerationResult.model_validate(
            {
                "simulation_goal_structured": "Reduce waiting time.",
                "reasoning": "Testing unsupported evidence pair.",
                "kpis": [
                    _kpi_payload(
                        name="Priority Waiting Time",
                        supported_by_log=True,
                        evidence_basis="both",
                        context_segmentation=[
                            {
                                "condition": "priority = high",
                                "target": "<= 4 hours",
                                "evidence_factor": "priority",
                                "evidence_metric": "activity_wait_time_hours",
                            }
                        ],
                    )
                ],
            }
        )
        context_evidence = json.dumps(
            {
                "significant_relationships": [
                    {
                        "factor": "claim_type",
                        "metric": "case_cycle_time_hours",
                        "segments": [{"condition": "claim_type = simple"}],
                    }
                ]
            }
        )

        validation = validate_kpi_generation_semantics(
            result,
            simulation_goal="Reduce waiting time.",
            context_evidence=context_evidence,
        )

        matching_issue = next(
            issue for issue in validation.issues if issue.code == "unsupported_context_evidence_pair"
        )
        self.assertEqual(matching_issue.severity, "error")

    def test_valid_evidence_pair_passes_with_condition_format_difference(self) -> None:
        result = KPIGenerationResult.model_validate(
            {
                "simulation_goal_structured": "Reduce waiting time.",
                "reasoning": "Testing tolerant condition matching.",
                "kpis": [
                    _kpi_payload(
                        name="Triage Waiting Time by Time of Day",
                        supported_by_log=True,
                        evidence_basis="both",
                        context_segmentation=[
                            {
                                "condition": "Time of Day <= 16.0",
                                "target": "<= 25 minutes",
                                "evidence_factor": "event_hour_of_day",
                                "evidence_metric": "activity_wait_time_hours",
                            }
                        ],
                    )
                ],
            }
        )
        context_evidence = json.dumps(
            {
                "significant_relationships": [
                    {
                        "factor": "event_hour_of_day",
                        "metric": "activity_wait_time_hours",
                        "segments": [
                            {"condition": "event_hour_of_day <= 16"},
                            {"condition": "event_hour_of_day > 16"},
                        ],
                    }
                ]
            }
        )

        validation = validate_kpi_generation_semantics(
            result,
            simulation_goal="Reduce waiting time.",
            context_evidence=context_evidence,
        )

        issue_codes = {issue.code for issue in validation.issues}
        self.assertNotIn("unsupported_context_condition", issue_codes)
        self.assertNotIn("unsupported_context_evidence_pair", issue_codes)

    def test_temporal_factor_normalization_avoids_false_mismatch(self) -> None:
        result = KPIGenerationResult.model_validate(
            {
                "simulation_goal_structured": "Reduce cycle time.",
                "reasoning": "Testing temporal normalization.",
                "kpis": [
                    _kpi_payload(
                        name="Cycle Time by Day of Week",
                        supported_by_log=True,
                        evidence_basis="both",
                        context_segmentation=[
                            {
                                "condition": "day_of_week = Monday",
                                "target": "<= 8 hours",
                                "evidence_factor": "day_of_week",
                                "evidence_metric": "case_cycle_time_hours",
                            }
                        ],
                    )
                ],
            }
        )
        context_evidence = json.dumps(
            {
                "significant_relationships": [
                    {
                        "factor": "case_start_day_of_week",
                        "metric": "case_cycle_time_hours",
                        "segments": [
                            {"condition": "case_start_day_of_week = Monday"},
                            {"condition": "case_start_day_of_week = Tuesday"},
                        ],
                    }
                ]
            }
        )

        validation = validate_kpi_generation_semantics(
            result,
            simulation_goal="Reduce cycle time.",
            context_evidence=context_evidence,
        )

        issue_codes = {issue.code for issue in validation.issues}
        self.assertNotIn("unsupported_context_condition", issue_codes)
        self.assertNotIn("unsupported_context_evidence_pair", issue_codes)


if __name__ == "__main__":
    unittest.main()

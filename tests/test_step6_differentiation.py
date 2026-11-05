"""Integration tests for Step 6: context-differentiated parameter generation.

Tests the full pipeline from context evidence → differentiation briefing
→ prompt injection → schema validation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from knowledge.models import ContextAwareRule, ContextFactorScope
from knowledge.retrieval import (
    SecondLLMEvidence,
    _build_differentiation_briefing,
    build_second_llm_evidence,
    kpi_segments_exist,
)
from prompts.scenario_proposal_prompt import build_scenario_proposal_prompt
from second_llm.output_schema import (
    ContextDifferentiation,
    ScenarioProposal,
)


# ── Fixtures ────────────────────────────────────────────────────────

_CASE_LEVEL_EVIDENCE = {
    "significant_relationships": [
        {
            "factor": "customer_tier",
            "metric": "cycle_time",
            "adjusted_p_value": 0.001,
            "effect_size": 0.45,
            "segment_stats": {
                "premium": {"mean": 1.8, "count": 200},
                "standard": {"mean": 4.2, "count": 800},
            },
        },
    ],
    "detected_factors": [
        {"name": "customer_tier", "scope": "case_level"},
    ],
}

_TEMPORAL_EVIDENCE = {
    "significant_relationships": [
        {
            "factor": "day_of_week",
            "metric": "waiting_time",
            "adjusted_p_value": 0.01,
            "effect_size": 0.22,
            "segment_stats": {
                "Monday": {"mean": 3.1},
                "Friday": {"mean": 1.2},
            },
        },
    ],
    "detected_factors": [
        {"name": "day_of_week", "scope": "temporal"},
    ],
}

_KPIS_WITH_SEGMENTS = [
    {
        "name": "Cycle Time",
        "category": "time",
        "target_direction": "minimize",
        "context_segmentation": [
            {
                "evidence_factor": "customer_tier",
                "condition": "customer_tier = premium",
                "target": "< 2 days",
            },
            {
                "evidence_factor": "customer_tier",
                "condition": "customer_tier = standard",
                "target": "< 5 days",
            },
        ],
    },
]

_KPIS_WITHOUT_SEGMENTS = [
    {
        "name": "Cycle Time",
        "category": "time",
        "target_direction": "minimize",
    },
]

_CASE_RULE = ContextAwareRule(
    rule_id="ctx_case_resource_pool",
    description="Differentiate resource pools by case-level factors",
    trigger_factor_scope=ContextFactorScope.CASE_LEVEL,
    trigger_factor_examples=["customer_tier", "priority"],
    affected_parameters=["resource_count"],
    differentiation_strategy="Create separate resource pools per segment.",
    rationale="Aligns capacity with demand.",
)

_TEMPORAL_RULE = ContextAwareRule(
    rule_id="ctx_temporal_calendar",
    description="Adjust calendars for temporal factors",
    trigger_factor_scope=ContextFactorScope.TEMPORAL,
    trigger_factor_examples=["day_of_week", "hour_of_day"],
    affected_parameters=["resource_calendar"],
    differentiation_strategy="Adjust staffing for peak periods.",
    rationale="Reduces peak waiting times.",
)


# ── Test: kpi_segments_exist helper ─────────────────────────────────

def test_kpi_segments_exist_true():
    assert kpi_segments_exist(_KPIS_WITH_SEGMENTS) is True


def test_kpi_segments_exist_false():
    assert kpi_segments_exist(_KPIS_WITHOUT_SEGMENTS) is False
    assert kpi_segments_exist(None) is False
    assert kpi_segments_exist([]) is False


# ── Test: briefing builder ──────────────────────────────────────────

def test_briefing_case_level_factor():
    briefing = _build_differentiation_briefing(
        kpis=_KPIS_WITH_SEGMENTS,
        context_filtered=_CASE_LEVEL_EVIDENCE,
        kb_context_rules=[_CASE_RULE],
    )
    assert "### Factor: customer_tier" in briefing
    assert "premium" in briefing
    assert "standard" in briefing
    assert "KPI targets:" in briefing
    assert "segment-specific" in briefing.lower()


def test_briefing_temporal_factor():
    briefing = _build_differentiation_briefing(
        kpis=_KPIS_WITHOUT_SEGMENTS,
        context_filtered=_TEMPORAL_EVIDENCE,
        kb_context_rules=[_TEMPORAL_RULE],
    )
    assert "### Factor: day_of_week" in briefing
    assert "timetable" in briefing.lower()
    # No KPI targets for this factor
    assert "KPI targets:" not in briefing


def test_briefing_empty_when_no_evidence():
    result = _build_differentiation_briefing(
        kpis=_KPIS_WITHOUT_SEGMENTS,
        context_filtered=None,
        kb_context_rules=[_CASE_RULE],
    )
    assert result == ""


def test_briefing_empty_when_no_significant_rels():
    result = _build_differentiation_briefing(
        kpis=_KPIS_WITH_SEGMENTS,
        context_filtered={"significant_relationships": []},
        kb_context_rules=[_CASE_RULE],
    )
    assert result == ""


# ── Test: full evidence pipeline includes briefing ──────────────────

def test_evidence_pipeline_produces_briefing():
    evidence = build_second_llm_evidence(
        goal_structured="Reduce cycle time",
        kpis=_KPIS_WITH_SEGMENTS,
        context_profile=_CASE_LEVEL_EVIDENCE,
    )
    assert evidence.has_differentiation
    assert "customer_tier" in evidence.differentiation_briefing
    assert any("Differentiation briefing" in n for n in evidence.retrieval_notes)


def test_evidence_pipeline_no_briefing_without_context():
    evidence = build_second_llm_evidence(
        goal_structured="Reduce cycle time",
        kpis=_KPIS_WITHOUT_SEGMENTS,
    )
    assert not evidence.has_differentiation
    assert evidence.differentiation_briefing == ""


# ── Test: prompt injects differentiation section ────────────────────

def test_prompt_includes_differentiation_section():
    evidence = build_second_llm_evidence(
        goal_structured="Reduce cycle time",
        kpis=_KPIS_WITH_SEGMENTS,
        context_profile=_CASE_LEVEL_EVIDENCE,
    )
    _, _, user_prompt = build_scenario_proposal_prompt(
        first_llm_json='{"kpis": []}',
        evidence=evidence,
    )
    assert "Actionable Instructions" in user_prompt
    assert "FOLLOW these instructions" in user_prompt
    assert "customer_tier" in user_prompt


def test_prompt_no_differentiation_section_without_context():
    evidence = build_second_llm_evidence(
        goal_structured="Reduce cycle time",
        kpis=_KPIS_WITHOUT_SEGMENTS,
    )
    _, _, user_prompt = build_scenario_proposal_prompt(
        first_llm_json='{"kpis": []}',
        evidence=evidence,
    )
    assert "No statistically significant context factors" in user_prompt
    assert "context_differentiations empty" in user_prompt


# ── Test: schema validation for context consistency ─────────────────

def _minimal_scenario():
    """Build a minimal valid SimuBridgeScenario dict."""
    return {
        "scenarioName": "test",
        "resourceParameters": {
            "roles": [
                {"id": "Officer", "schedule": "tt1", "costHour": 10, "resources": [{"id": "r1"}]},
            ],
            "timeTables": [
                {"id": "tt1", "timeTableItems": [
                    {"startWeekday": "Monday", "startTime": 9, "endWeekday": "Friday", "endTime": 17},
                ]},
            ],
        },
        "models": [{
            "name": "test_process",
            "modelParameter": {
                "activities": [{
                    "id": "act1", "name": "Check", "resources": ["Officer"],
                    "duration": {"distributionType": "normal", "timeUnit": "hours",
                                 "values": [{"id": "mean", "value": 2}, {"id": "variance", "value": 0.5}]},
                }],
            },
        }],
    }


def test_validation_warns_when_differentiation_segments_not_in_scenario():
    scenario = _minimal_scenario()
    proposal_dict = {
        "scenario_name": "test",
        "reasoning": "test",
        "modifications": [{
            "parameter_type": "resource_count",
            "target_element": "Officer",
            "direction": "differentiate",
            "baseline_value": "3",
            "proposed_value": "premium: 2, standard: 3",
            "kpi_reference": "Cycle Time",
            "rationale": "Split by tier",
            "context_condition": "customer_tier = premium",
        }],
        "expected_kpi_impacts": [{
            "kpi_name": "Cycle Time",
            "direction": "decrease",
        }],
        "context_differentiations": [{
            "context_factor": "customer_tier",
            "factor_scope": "case_level",
            "segments": ["premium", "standard"],
            "affected_parameters": ["resource_count"],
            "strategy_applied": "separate pools",
        }],
        "scenario": scenario,
    }
    proposal = ScenarioProposal.model_validate(proposal_dict)
    # Should warn because "premium"/"standard" don't appear in role names
    assert any("segment names appear" in w for w in proposal.warnings)


def test_validation_no_warning_when_segments_reflected_in_roles():
    scenario = _minimal_scenario()
    # Add segment-named roles
    scenario["resourceParameters"]["roles"] = [
        {"id": "Officer_premium", "schedule": "tt1", "costHour": 15, "resources": [{"id": "r1"}]},
        {"id": "Officer_standard", "schedule": "tt1", "costHour": 10, "resources": [{"id": "r2"}]},
    ]
    scenario["models"][0]["modelParameter"]["activities"][0]["resources"] = [
        "Officer_premium", "Officer_standard",
    ]

    proposal_dict = {
        "scenario_name": "test",
        "reasoning": "test",
        "modifications": [{
            "parameter_type": "resource_count",
            "target_element": "Officer",
            "direction": "differentiate",
            "baseline_value": "3",
            "proposed_value": "premium: 2, standard: 3",
            "kpi_reference": "Cycle Time",
            "rationale": "Split by tier",
            "context_condition": "customer_tier = premium",
        }],
        "expected_kpi_impacts": [{
            "kpi_name": "Cycle Time",
            "direction": "decrease",
        }],
        "context_differentiations": [{
            "context_factor": "customer_tier",
            "factor_scope": "case_level",
            "segments": ["premium", "standard"],
            "affected_parameters": ["resource_count"],
            "strategy_applied": "separate pools",
        }],
        "scenario": scenario,
    }
    proposal = ScenarioProposal.model_validate(proposal_dict)
    # No warning about segment names
    assert not any("segment names appear" in w for w in proposal.warnings)

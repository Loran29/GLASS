"""Tests for Step 7: post-schema validation (constraints + directional consistency)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from second_llm.output_schema import ScenarioProposal
from second_llm.validation import (
    ValidationResult,
    _check_directional_consistency,
    _check_distribution_value_ranges,
    _check_kpi_impact_directions,
    _check_role_activity_references,
    _check_simulation_instances,
    _check_uniform_bounds,
    _extract_first_number,
    validate_proposal,
)


# ── Helper: build a minimal valid ScenarioProposal ──────────────────

def _scenario(
    *,
    roles=None,
    activities=None,
    events=None,
    modifications=None,
    kpi_impacts=None,
    instances=1000,
):
    """Build a ScenarioProposal dict with sensible defaults."""
    if roles is None:
        roles = [{"id": "Officer", "schedule": "tt1", "costHour": 10,
                  "resources": [{"id": "r1"}]}]
    if activities is None:
        activities = [{
            "id": "act1", "name": "Review", "resources": ["Officer"],
            "duration": {"distributionType": "normal", "timeUnit": "hours",
                         "values": [{"id": "mean", "value": 2.0},
                                    {"id": "variance", "value": 0.5}]},
        }]
    if events is None:
        events = [{
            "id": "start1",
            "interArrivalTime": {"distributionType": "exponential",
                                 "timeUnit": "hours",
                                 "values": [{"id": "mean", "value": 1.0}]},
        }]
    if modifications is None:
        modifications = [{
            "parameter_type": "activity_duration",
            "target_element": "Review",
            "direction": "decrease",
            "baseline_value": "2.0 hours",
            "proposed_value": "1.5 hours",
            "kpi_reference": "Cycle Time",
            "rationale": "Reduce bottleneck",
        }]
    if kpi_impacts is None:
        kpi_impacts = [{"kpi_name": "Cycle Time", "direction": "decrease"}]

    return ScenarioProposal.model_validate({
        "scenario_name": "test",
        "reasoning": "test reasoning",
        "modifications": modifications,
        "expected_kpi_impacts": kpi_impacts,
        "scenario": {
            "scenarioName": "test",
            "numberOfInstances": instances,
            "resourceParameters": {
                "roles": roles,
                "timeTables": [{"id": "tt1", "timeTableItems": [
                    {"startWeekday": "Monday", "startTime": 9,
                     "endWeekday": "Friday", "endTime": 17},
                ]}],
            },
            "models": [{
                "name": "proc",
                "modelParameter": {
                    "activities": activities,
                    "events": events,
                },
            }],
        },
    })


# ── Test: _extract_first_number ─────────────────────────────────────

def test_extract_number_simple():
    assert _extract_first_number("2.5 hours") == 2.5


def test_extract_number_negative():
    assert _extract_first_number("-1.3") == -1.3


def test_extract_number_from_complex_string():
    assert _extract_first_number("normal(mean=3.2h)") == 3.2


def test_extract_number_no_number():
    assert _extract_first_number("no numbers here") is None


# ── Test: distribution value ranges ─────────────────────────────────

def test_negative_mean_is_error():
    p = _scenario(activities=[{
        "id": "a1", "name": "Bad", "resources": ["Officer"],
        "duration": {"distributionType": "exponential", "timeUnit": "hours",
                     "values": [{"id": "mean", "value": -1.0}]},
    }])
    issues = _check_distribution_value_ranges(p)
    assert any(i.severity == "error" and "mean=-1.0" in i.message for i in issues)


def test_negative_variance_is_error():
    p = _scenario(activities=[{
        "id": "a1", "name": "Bad", "resources": ["Officer"],
        "duration": {"distributionType": "normal", "timeUnit": "hours",
                     "values": [{"id": "mean", "value": 2.0},
                                {"id": "variance", "value": -0.5}]},
    }])
    issues = _check_distribution_value_ranges(p)
    assert any(i.severity == "error" and "variance=-0.5" in i.message for i in issues)


def test_valid_distribution_no_issues():
    p = _scenario()
    issues = _check_distribution_value_ranges(p)
    assert len(issues) == 0


def test_negative_inter_arrival_mean_is_error():
    p = _scenario(events=[{
        "id": "s1",
        "interArrivalTime": {"distributionType": "exponential",
                             "timeUnit": "hours",
                             "values": [{"id": "mean", "value": -2.0}]},
    }])
    issues = _check_distribution_value_ranges(p)
    assert any(i.severity == "error" and "inter-arrival" in i.message for i in issues)


# ── Test: role-activity references ──────────────────────────────────

def test_invalid_role_reference_is_error():
    p = _scenario(activities=[{
        "id": "a1", "name": "Review", "resources": ["NonExistentRole"],
        "duration": {"distributionType": "constant", "timeUnit": "hours",
                     "values": [{"id": "constantValue", "value": 1.0}]},
    }])
    issues = _check_role_activity_references(p)
    assert any(i.severity == "error" and "NonExistentRole" in i.message for i in issues)


def test_valid_role_reference_no_issues():
    p = _scenario()
    issues = _check_role_activity_references(p)
    assert len(issues) == 0


# ── Test: uniform bounds ────────────────────────────────────────────

def test_uniform_lower_ge_upper_is_error():
    p = _scenario(activities=[{
        "id": "a1", "name": "Bad", "resources": ["Officer"],
        "duration": {"distributionType": "uniform", "timeUnit": "hours",
                     "values": [{"id": "lower", "value": 5.0},
                                {"id": "upper", "value": 3.0}]},
    }])
    issues = _check_uniform_bounds(p)
    assert any(i.severity == "error" and "lower=5.0 >= upper=3.0" in i.message for i in issues)


# ── Test: simulation instances ──────────────────────────────────────

def test_low_instances_warning():
    p = _scenario(instances=50)
    issues = _check_simulation_instances(p)
    assert any(i.severity == "warning" and "50" in i.message for i in issues)


def test_normal_instances_no_warning():
    p = _scenario(instances=1000)
    issues = _check_simulation_instances(p)
    assert len(issues) == 0


# ── Test: directional consistency ───────────────────────────────────

def test_decrease_but_value_increased():
    p = _scenario(modifications=[{
        "parameter_type": "activity_duration",
        "target_element": "Review",
        "direction": "decrease",
        "baseline_value": "2.0 hours",
        "proposed_value": "3.0 hours",
        "kpi_reference": "Cycle Time",
        "rationale": "test",
    }])
    issues = _check_directional_consistency(p)
    assert any("decrease" in i.message and "3.0" in i.message for i in issues)


def test_increase_but_value_decreased():
    p = _scenario(modifications=[{
        "parameter_type": "resource_count",
        "target_element": "Officer",
        "direction": "increase",
        "baseline_value": "5",
        "proposed_value": "3",
        "kpi_reference": "Cycle Time",
        "rationale": "test",
    }])
    issues = _check_directional_consistency(p)
    assert any("increase" in i.message and "3.0" in i.message for i in issues)


def test_correct_direction_no_issues():
    p = _scenario(modifications=[{
        "parameter_type": "activity_duration",
        "target_element": "Review",
        "direction": "decrease",
        "baseline_value": "2.0 hours",
        "proposed_value": "1.5 hours",
        "kpi_reference": "Cycle Time",
        "rationale": "test",
    }])
    issues = _check_directional_consistency(p)
    assert len(issues) == 0


def test_redistribute_skipped():
    p = _scenario(modifications=[{
        "parameter_type": "gateway_probabilities",
        "target_element": "Gateway1",
        "direction": "redistribute",
        "baseline_value": "yes: 0.6, no: 0.4",
        "proposed_value": "yes: 0.8, no: 0.2",
        "kpi_reference": "Cycle Time",
        "rationale": "test",
    }])
    issues = _check_directional_consistency(p)
    assert len(issues) == 0


# ── Test: KPI impact direction cross-check ──────────────────────────

def test_all_mods_decrease_but_kpi_increase_warns():
    p = _scenario(
        modifications=[{
            "parameter_type": "activity_duration",
            "target_element": "Review",
            "direction": "decrease",
            "baseline_value": "2.0",
            "proposed_value": "1.5",
            "kpi_reference": "Cycle Time",
            "rationale": "test",
        }],
        kpi_impacts=[{
            "kpi_name": "Cycle Time",
            "direction": "increase",
        }],
    )
    issues = _check_kpi_impact_directions(p)
    assert any("all modifications decrease" in i.message for i in issues)


# ── Test: full validate_proposal ────────────────────────────────────

def test_validate_proposal_clean():
    p = _scenario()
    vr = validate_proposal(p)
    assert not vr.has_errors
    assert isinstance(vr, ValidationResult)


def test_validate_proposal_catches_multiple_issues():
    p = _scenario(
        activities=[{
            "id": "a1", "name": "Bad", "resources": ["Ghost"],
            "duration": {"distributionType": "exponential", "timeUnit": "hours",
                         "values": [{"id": "mean", "value": -1.0}]},
        }],
        modifications=[{
            "parameter_type": "activity_duration",
            "target_element": "Bad",
            "direction": "decrease",
            "baseline_value": "2.0",
            "proposed_value": "5.0",
            "kpi_reference": "Cycle Time",
            "rationale": "test",
        }],
        instances=50,
    )
    vr = validate_proposal(p)
    assert vr.has_errors
    # Should have: negative mean error + invalid role ref error
    error_msgs = [i.message for i in vr.errors]
    assert any("mean=-1.0" in m for m in error_msgs)
    assert any("Ghost" in m for m in error_msgs)
    # Should have: directional warning + low instances warning
    warn_msgs = [i.message for i in vr.warnings]
    assert any("decrease" in m for m in warn_msgs)
    assert any("50" in m for m in warn_msgs)


def test_error_summary_format():
    p = _scenario(activities=[{
        "id": "a1", "name": "Bad", "resources": ["Ghost"],
        "duration": {"distributionType": "exponential", "timeUnit": "hours",
                     "values": [{"id": "mean", "value": -1.0}]},
    }])
    vr = validate_proposal(p)
    summary = vr.error_summary()
    assert "[constraint]" in summary
    assert "mean=-1.0" in summary

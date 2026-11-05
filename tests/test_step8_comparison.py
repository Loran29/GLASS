"""Tests for Step 8: KPI traceability & scenario comparison."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from second_llm.comparison import (
    ComparisonReport,
    KPITraceEntry,
    ParameterDelta,
    _build_kpi_traces,
    _build_parameter_deltas,
    build_comparison_report,
)
from second_llm.output_schema import ScenarioProposal


# ── Helper: build a minimal ScenarioProposal ──────────────────────────

def _scenario(
    *,
    modifications=None,
    kpi_impacts=None,
    scenario_name="test_scenario",
):
    """Build a ScenarioProposal with sensible defaults."""
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
        "scenario_name": scenario_name,
        "reasoning": "test reasoning",
        "modifications": modifications,
        "expected_kpi_impacts": kpi_impacts,
        "scenario": {
            "scenarioName": scenario_name,
            "numberOfInstances": 1000,
            "resourceParameters": {
                "roles": [{"id": "Officer", "schedule": "tt1", "costHour": 10,
                           "resources": [{"id": "r1"}]}],
                "timeTables": [{"id": "tt1", "timeTableItems": [
                    {"startWeekday": "Monday", "startTime": 9,
                     "endWeekday": "Friday", "endTime": 17},
                ]}],
            },
            "models": [{
                "name": "proc",
                "modelParameter": {
                    "activities": [{
                        "id": "act1", "name": "Review", "resources": ["Officer"],
                        "duration": {"distributionType": "normal", "timeUnit": "hours",
                                     "values": [{"id": "mean", "value": 1.5},
                                                {"id": "variance", "value": 0.3}]},
                    }],
                    "events": [{
                        "id": "start1",
                        "interArrivalTime": {"distributionType": "exponential",
                                             "timeUnit": "hours",
                                             "values": [{"id": "mean", "value": 1.0}]},
                    }],
                },
            }],
        },
    })


def _first_llm_json(kpis=None):
    """Build a first-LLM JSON string with given KPIs."""
    if kpis is None:
        kpis = [
            {
                "name": "Cycle Time",
                "category": "time",
                "target_direction": "minimize",
                "process_scope": "end_to_end",
                "suggested_formula": "avg(end - start)",
            },
        ]
    return json.dumps({
        "simulation_goal_structured": "Reduce cycle time by 20%",
        "kpis": kpis,
        "reasoning": "Focus on cycle time reduction",
    })


# ── Test: _build_parameter_deltas ──────────────────────────────────

def test_delta_basic():
    p = _scenario()
    deltas = _build_parameter_deltas(p)
    assert len(deltas) == 1
    d = deltas[0]
    assert d.modification_index == 1
    assert d.target_element == "Review"
    assert d.baseline_numeric == 2.0
    assert d.proposed_numeric == 1.5
    assert d.has_numeric_delta


def test_delta_change_pct():
    p = _scenario()
    deltas = _build_parameter_deltas(p)
    d = deltas[0]
    assert d.change_pct is not None
    assert abs(d.change_pct - (-25.0)) < 0.1


def test_delta_non_numeric_values():
    p = _scenario(modifications=[{
        "parameter_type": "gateway_probabilities",
        "target_element": "Gateway1",
        "direction": "redistribute",
        "baseline_value": "yes: 0.6, no: 0.4",
        "proposed_value": "yes: 0.8, no: 0.2",
        "kpi_reference": "Cycle Time",
        "rationale": "test",
    }])
    deltas = _build_parameter_deltas(p)
    d = deltas[0]
    # First number found is 0.6 and 0.8
    assert d.baseline_numeric == 0.6
    assert d.proposed_numeric == 0.8


def test_delta_multiple_modifications():
    p = _scenario(modifications=[
        {
            "parameter_type": "activity_duration",
            "target_element": "Review",
            "direction": "decrease",
            "baseline_value": "2.0 hours",
            "proposed_value": "1.5 hours",
            "kpi_reference": "Cycle Time",
            "rationale": "test1",
        },
        {
            "parameter_type": "resource_count",
            "target_element": "Officer",
            "direction": "increase",
            "baseline_value": "3",
            "proposed_value": "5",
            "kpi_reference": "Throughput",
            "rationale": "test2",
        },
    ], kpi_impacts=[
        {"kpi_name": "Cycle Time", "direction": "decrease"},
        {"kpi_name": "Throughput", "direction": "increase"},
    ])
    deltas = _build_parameter_deltas(p)
    assert len(deltas) == 2
    assert deltas[0].modification_index == 1
    assert deltas[1].modification_index == 2


# ── Test: _build_kpi_traces ────────────────────────────────────────

def test_kpi_trace_full_coverage():
    p = _scenario()
    deltas = _build_parameter_deltas(p)
    first_llm = json.loads(_first_llm_json())
    traces = _build_kpi_traces(first_llm, p, deltas)
    assert len(traces) == 1
    t = traces[0]
    assert t.kpi_name == "Cycle Time"
    assert t.coverage == "full"
    assert t.expected_direction == "decrease"
    assert len(t.modifications) == 1


def test_kpi_trace_unaddressed():
    """A KPI with no modifications targeting it should be unaddressed."""
    p = _scenario()
    deltas = _build_parameter_deltas(p)
    first_llm = json.loads(_first_llm_json(kpis=[
        {"name": "Cycle Time", "category": "time", "target_direction": "minimize",
         "process_scope": "end_to_end", "suggested_formula": "..."},
        {"name": "Cost Per Case", "category": "cost", "target_direction": "minimize",
         "process_scope": "end_to_end", "suggested_formula": "..."},
    ]))
    traces = _build_kpi_traces(first_llm, p, deltas)
    assert len(traces) == 2
    assert traces[0].coverage == "full"  # Cycle Time — has modification
    assert traces[1].coverage == "unaddressed"  # Cost Per Case — no modification


def test_kpi_trace_constraint_kpi():
    """Maintain-direction KPIs should be flagged as constraints."""
    p = _scenario(
        kpi_impacts=[
            {"kpi_name": "Cycle Time", "direction": "decrease"},
            {"kpi_name": "Quality Score", "direction": "maintain"},
        ],
    )
    deltas = _build_parameter_deltas(p)
    first_llm = json.loads(_first_llm_json(kpis=[
        {"name": "Cycle Time", "category": "time", "target_direction": "minimize",
         "process_scope": "end_to_end", "suggested_formula": "..."},
        {"name": "Quality Score", "category": "quality", "target_direction": "maintain",
         "process_scope": "end_to_end", "suggested_formula": "..."},
    ]))
    traces = _build_kpi_traces(first_llm, p, deltas)
    assert not traces[0].is_constraint  # Cycle Time is not a constraint
    assert traces[1].is_constraint  # Quality Score IS a constraint


# ── Test: direction alignment ──────────────────────────────────────

def test_direction_aligned_minimize_decrease():
    t = KPITraceEntry(
        kpi_name="CT", target_direction="minimize",
        category="time", process_scope="end_to_end",
        expected_direction="decrease",
    )
    assert t.direction_aligned is True


def test_direction_aligned_maximize_increase():
    t = KPITraceEntry(
        kpi_name="TP", target_direction="maximize",
        category="quality", process_scope="end_to_end",
        expected_direction="increase",
    )
    assert t.direction_aligned is True


def test_direction_misaligned():
    t = KPITraceEntry(
        kpi_name="CT", target_direction="minimize",
        category="time", process_scope="end_to_end",
        expected_direction="increase",
    )
    assert t.direction_aligned is False


def test_direction_maintain_aligned():
    t = KPITraceEntry(
        kpi_name="QS", target_direction="maintain",
        category="quality", process_scope="end_to_end",
        expected_direction="maintain",
    )
    assert t.direction_aligned is True


def test_direction_maintain_misaligned():
    t = KPITraceEntry(
        kpi_name="QS", target_direction="maintain",
        category="quality", process_scope="end_to_end",
        expected_direction="decrease",
    )
    assert t.direction_aligned is False


def test_direction_unknown():
    t = KPITraceEntry(
        kpi_name="CT", target_direction="minimize",
        category="time", process_scope="end_to_end",
        expected_direction="",
    )
    assert t.direction_aligned is None


# ── Test: build_comparison_report (full pipeline) ──────────────────

def test_full_report_clean():
    p = _scenario()
    report = build_comparison_report(_first_llm_json(), p)
    assert isinstance(report, ComparisonReport)
    assert report.total_kpis == 1
    assert report.addressed_kpis == 1
    assert report.coverage_pct == 100.0
    assert len(report.parameter_deltas) == 1
    assert len(report.misaligned_kpis) == 0


def test_full_report_unaddressed_kpi():
    p = _scenario()
    first_json = _first_llm_json(kpis=[
        {"name": "Cycle Time", "category": "time", "target_direction": "minimize",
         "process_scope": "end_to_end", "suggested_formula": "..."},
        {"name": "Employee Satisfaction", "category": "quality",
         "target_direction": "maximize", "process_scope": "end_to_end",
         "suggested_formula": "..."},
    ])
    report = build_comparison_report(first_json, p)
    assert report.total_kpis == 2
    assert report.addressed_kpis == 1
    assert len(report.unaddressed_kpis) == 1
    assert report.unaddressed_kpis[0].kpi_name == "Employee Satisfaction"
    assert any("Unaddressed" in n for n in report.notes)


def test_full_report_direction_mismatch():
    """Scenario says increase, but KPI target is minimize."""
    p = _scenario(
        kpi_impacts=[{"kpi_name": "Cycle Time", "direction": "increase"}],
    )
    first_json = _first_llm_json()
    report = build_comparison_report(first_json, p)
    assert len(report.misaligned_kpis) == 1
    assert any("mismatch" in n.lower() or "misalign" in n.lower() for n in report.notes)


def test_full_report_constraint_warning():
    """Maintain-KPI should warn if expected direction is not maintain."""
    p = _scenario(
        kpi_impacts=[
            {"kpi_name": "Cycle Time", "direction": "decrease"},
            {"kpi_name": "Quality", "direction": "decrease"},
        ],
    )
    first_json = _first_llm_json(kpis=[
        {"name": "Cycle Time", "category": "time", "target_direction": "minimize",
         "process_scope": "end_to_end", "suggested_formula": "..."},
        {"name": "Quality", "category": "quality", "target_direction": "maintain",
         "process_scope": "end_to_end", "suggested_formula": "..."},
    ])
    report = build_comparison_report(first_json, p)
    assert any("constraint" in n.lower() or "maintain" in n.lower() for n in report.notes)


def test_full_report_invalid_json():
    """Gracefully handle unparseable first-LLM JSON."""
    p = _scenario()
    report = build_comparison_report("not valid json", p)
    assert report.total_kpis == 0
    assert any("parse" in n.lower() for n in report.notes)


def test_full_report_empty_kpis():
    """Handle first-LLM JSON with no KPIs."""
    p = _scenario()
    first_json = json.dumps({"simulation_goal_structured": "test", "kpis": []})
    report = build_comparison_report(first_json, p)
    assert report.total_kpis == 0
    assert report.coverage_pct == 0.0


def test_parameter_delta_zero_baseline():
    """Change percentage should be None when baseline is 0."""
    p = _scenario(modifications=[{
        "parameter_type": "resource_cost",
        "target_element": "Intern",
        "direction": "increase",
        "baseline_value": "0",
        "proposed_value": "15",
        "kpi_reference": "Cycle Time",
        "rationale": "test",
    }])
    deltas = _build_parameter_deltas(p)
    assert deltas[0].change_pct is None  # Can't divide by 0


def test_report_scenario_metadata():
    p = _scenario(scenario_name="Custom Scenario")
    report = build_comparison_report(_first_llm_json(), p)
    assert report.scenario_name == "Custom Scenario"
    assert report.scenario_reasoning == "test reasoning"

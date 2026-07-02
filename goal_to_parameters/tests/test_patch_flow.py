"""Tests for the delta/patch architecture.

Covers:

* valid patch merges cleanly and applies every modification,
* missing baseline element is rejected,
* wrong ``baseline_value`` surfaces a warning in tolerant mode and an
  error in strict mode,
* no-op modifications are rejected (schema or merger),
* invalid distribution / gateway probabilities / resource counts are
  rejected,
* unsupported KPI is routed through ``unresolved_kpis``,
* untouched baseline fields are preserved byte-for-byte after merge,
* the patch prompt asks only for a patch and does not reference a full
  scenario body.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from second_llm.output_schema import (
    Activity,
    DistributionParameter,
    DistributionType,
    Gateway,
    ModelParameter,
    ProcessModel,
    Resource,
    ResourceParameters,
    Role,
    SimuBridgeScenario,
    StartEvent,
    TimeDistribution,
    TimeUnit,
    Timetable,
    TimetableItem,
    Weekday,
)
from second_llm.output_schema_patch import (
    PatchModification,
    PatchParameterType,
    ScenarioPatch,
)
from second_llm.compatibility_adapter import build_legacy_proposal
from second_llm.patch_validator import validate_patch
from second_llm.scenario_merger import apply_patch
from second_llm.simod_to_simubridge import build_baseline_scenario


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------

def _make_baseline() -> SimuBridgeScenario:
    tt = Timetable(
        id="standard_cal",
        timeTableItems=[TimetableItem(
            startWeekday=Weekday.MONDAY,
            endWeekday=Weekday.FRIDAY,
            startTime=9,
            endTime=17,
        )],
    )
    role = Role(
        id="Analyst",
        schedule="standard_cal",
        costHour=50.0,
        resources=[Resource(id="Analyst_1"), Resource(id="Analyst_2")],
    )
    act = Activity(
        id="ReviewApplication",
        name="Review Application",
        resources=["Analyst"],
        cost=0.0,
        duration=TimeDistribution(
            distributionType=DistributionType.NORMAL,
            timeUnit=TimeUnit.MINUTES,
            values=[
                DistributionParameter(id="mean", value=45.0),
                DistributionParameter(id="variance", value=5.0),
            ],
        ),
    )
    gw = Gateway(
        id="ApprovalDecision",
        name="Approval Decision",
        probabilities={"approve": 0.7, "reject": 0.3},
    )
    start = StartEvent(
        id="StartEvent_1",
        interArrivalTime=TimeDistribution(
            distributionType=DistributionType.EXPONENTIAL,
            timeUnit=TimeUnit.MINUTES,
            values=[DistributionParameter(id="mean", value=30.0)],
        ),
    )
    return SimuBridgeScenario(
        scenarioName="Baseline",
        numberOfInstances=500,
        resourceParameters=ResourceParameters(
            roles=[role],
            resources=[Resource(id="Analyst_1"), Resource(id="Analyst_2")],
            timeTables=[tt],
        ),
        models=[ProcessModel(
            name="TestProcess",
            modelParameter=ModelParameter(
                activities=[act],
                gateways=[gw],
                events=[start],
            ),
            BPMN="<test/>",
        )],
    )


def _mk_mod(**overrides) -> PatchModification:
    defaults = dict(
        parameter_type=PatchParameterType.RESOURCE_COUNT,
        target_element="Analyst",
        direction="increase",
        baseline_value="2",
        proposed_value="4",
        kpi_reference="Throughput",
        rationale="Add capacity to reduce queue.",
        evidence_source="SIMOD: Analyst count = 2",
        literature_support=[1],
    )
    defaults.update(overrides)
    return PatchModification.model_validate(defaults)


def _mk_patch(mods: list[PatchModification]) -> ScenarioPatch:
    return ScenarioPatch(
        scenario_id="TestScenario",
        baseline_reference="SIMOD",
        reasoning="Minimal test patch.",
        modifications=mods,
        expected_kpi_impacts=[
            {"kpi_name": m.kpi_reference, "direction": "decrease",
             "estimated_magnitude": "", "confidence": "medium", "reasoning": ""}
            for m in mods
        ],
    )


# -------------------------------------------------------------------
# Happy path
# -------------------------------------------------------------------

def test_valid_patch_applies_cleanly():
    baseline = _make_baseline()
    patch = _mk_patch([_mk_mod()])
    result = apply_patch(baseline, patch, strict=True)
    assert result.scenario is not None
    assert not result.has_errors
    assert result.applied_modifications == [1]
    # Headcount changed from 2 -> 4
    role = result.scenario.resourceParameters.roles[0]
    assert len(role.resources) == 4
    # Untouched fields preserved
    assert role.id == "Analyst"
    assert role.schedule == "standard_cal"
    assert role.costHour == 50.0


def test_untouched_fields_preserved():
    baseline = _make_baseline()
    before_snapshot = baseline.model_dump()
    patch = _mk_patch([_mk_mod()])
    result = apply_patch(baseline, patch, strict=False)
    after = result.scenario.model_dump()

    # Gateway probabilities untouched
    gw_before = before_snapshot["models"][0]["modelParameter"]["gateways"][0]
    gw_after = after["models"][0]["modelParameter"]["gateways"][0]
    assert gw_before["probabilities"] == gw_after["probabilities"]

    # Activity duration untouched
    act_before = before_snapshot["models"][0]["modelParameter"]["activities"][0]
    act_after = after["models"][0]["modelParameter"]["activities"][0]
    assert act_before["duration"] == act_after["duration"]

    # Timetable untouched
    assert before_snapshot["resourceParameters"]["timeTables"] == \
           after["resourceParameters"]["timeTables"]

    # Baseline itself was not mutated
    assert baseline.model_dump() == before_snapshot


# -------------------------------------------------------------------
# Missing element
# -------------------------------------------------------------------

def test_missing_element_rejected():
    baseline = _make_baseline()
    patch = _mk_patch([_mk_mod(target_element="GhostRole")])
    result = apply_patch(baseline, patch, strict=True)
    assert 1 in result.skipped_modifications
    assert any(
        d.category == "missing_element"
        for d in result.diagnostics
    )


# -------------------------------------------------------------------
# Baseline value mismatch
# -------------------------------------------------------------------

def test_baseline_value_mismatch_warning_in_tolerant_mode():
    baseline = _make_baseline()
    # Baseline has 2 analysts; claim 10 in the quote
    patch = _mk_patch([_mk_mod(baseline_value="10", proposed_value="4")])
    result = apply_patch(baseline, patch, strict=False)
    assert any(
        d.category == "baseline_value_mismatch" and d.severity == "warning"
        for d in result.diagnostics
    )
    # The merger still applies the change in tolerant mode
    assert 1 in result.applied_modifications


def test_baseline_value_mismatch_error_in_strict_mode():
    baseline = _make_baseline()
    patch = _mk_patch([_mk_mod(baseline_value="10", proposed_value="4")])
    result = apply_patch(baseline, patch, strict=True)
    assert 1 in result.skipped_modifications
    assert any(
        d.category == "baseline_value_mismatch" and d.severity == "error"
        for d in result.diagnostics
    )


# -------------------------------------------------------------------
# No-op
# -------------------------------------------------------------------

def test_schema_rejects_trivial_no_op():
    with pytest.raises(ValidationError):
        PatchModification.model_validate(dict(
            parameter_type=PatchParameterType.RESOURCE_COUNT,
            target_element="Analyst",
            direction="increase",
            baseline_value="2",
            proposed_value="2",
            kpi_reference="Throughput",
            rationale="x",
        ))


def test_merger_detects_effective_no_op():
    # Schema allows proposed_value="2.0" vs baseline_value="2" (different
    # strings).  The merger resolves target_count=2 and emits a no_op.
    baseline = _make_baseline()
    patch = _mk_patch([_mk_mod(baseline_value="2", proposed_value="2.0")])
    result = apply_patch(baseline, patch, strict=False)
    assert any(
        d.category == "no_op" for d in result.diagnostics
    )


# -------------------------------------------------------------------
# Invalid structured payloads
# -------------------------------------------------------------------

def test_invalid_gateway_probabilities_rejected():
    baseline = _make_baseline()
    mod = _mk_mod(
        parameter_type=PatchParameterType.GATEWAY_PROBABILITIES,
        target_element="ApprovalDecision",
        direction="redistribute",
        baseline_value="approve=0.7, reject=0.3",
        proposed_value="not_a_probability_map",
        proposed_structured=None,
    )
    patch = _mk_patch([mod])
    result = apply_patch(baseline, patch, strict=False)
    assert any(
        d.category == "invalid_value" for d in result.diagnostics
    )
    assert 1 in result.skipped_modifications


def test_invalid_resource_count_rejected():
    baseline = _make_baseline()
    mod = _mk_mod(proposed_value="abc")
    patch = _mk_patch([mod])
    result = apply_patch(baseline, patch, strict=False)
    assert any(
        d.category == "invalid_value" for d in result.diagnostics
    )


def test_invalid_activity_duration_rejected():
    baseline = _make_baseline()
    mod = _mk_mod(
        parameter_type=PatchParameterType.ACTIVITY_DURATION,
        target_element="ReviewApplication",
        direction="decrease",
        baseline_value="45 min",
        proposed_value="not a number",
        proposed_structured=None,
    )
    patch = _mk_patch([mod])
    result = apply_patch(baseline, patch, strict=False)
    assert any(
        d.category == "invalid_value" for d in result.diagnostics
    )


# -------------------------------------------------------------------
# Unsupported KPI abstention
# -------------------------------------------------------------------

def test_unresolved_kpis_supported_without_modifications():
    # Build a patch where EVERY declared KPI is unresolved.
    baseline = _make_baseline()
    patch = ScenarioPatch(
        scenario_id="AllUnresolved",
        reasoning="No grounded change available.",
        modifications=[],
        unresolved_kpis=[{
            "kpi_name": "UncomputableKPI",
            "reason": "not_computable_from_baseline",
            "explanation": "The baseline doesn't expose this measure.",
        }],
    )
    # Pre-merge validation sees the KPI as explicitly unresolved -> OK.
    pv = validate_patch(patch, baseline=baseline, declared_kpis=["UncomputableKPI"])
    assert not pv.has_errors
    # Adapter produces a legal ScenarioProposal even with zero modifications.
    merged = apply_patch(baseline, patch)
    assert merged.scenario is not None
    legacy = build_legacy_proposal(patch, merged.scenario)
    assert legacy.unresolved_kpis[0].kpi_name == "UncomputableKPI"


def test_kpi_coverage_error_when_neither_targeted_nor_unresolved():
    baseline = _make_baseline()
    patch = _mk_patch([_mk_mod(kpi_reference="SomeOtherKPI")])
    pv = validate_patch(
        patch, baseline=baseline, declared_kpis=["UnrelatedKPI"],
    )
    # kpi_uncovered is a warning (not an error) so generation can still
    # proceed and produce a partial scenario rather than failing outright.
    assert any(d.category == "kpi_uncovered" for d in pv.diagnostics)
    assert any(d.category == "kpi_uncovered" and d.severity == "warning"
               for d in pv.diagnostics)


# -------------------------------------------------------------------
# Prompt content
# -------------------------------------------------------------------

def test_patch_prompt_asks_only_for_patch():
    from prompts.scenario_patch_prompt import build_scenario_patch_prompt

    system, _, user = build_scenario_patch_prompt(
        first_llm_json='{"simulation_goal_structured": "test", "kpis": []}',
        evidence=None,
    )
    full = (system + "\n" + user).lower()
    # The prompt must brand itself as patch-only.
    assert "scenariopatch" in full or "patch-only" in full
    # Must not instruct the model to emit a full scenario body.
    assert "scenario: a complete simubridge configuration" not in full
    assert "carrying over all" not in full


# -------------------------------------------------------------------
# Baseline builder smoke
# -------------------------------------------------------------------

def test_simod_to_simubridge_minimal():
    simod = {
        "resource_profiles": [{
            "id": "Analyst",
            "name": "Analyst",
            "resource_list": [
                {"id": "A1", "cost_per_hour": 50, "calendar": "cal1"},
                {"id": "A2", "cost_per_hour": 50, "calendar": "cal1"},
            ],
        }],
        "resource_calendars": [{
            "id": "cal1",
            "time_periods": [{
                "from": "MONDAY", "to": "FRIDAY",
                "beginTime": "09:00:00", "endTime": "17:00:00",
            }],
        }],
        "task_resource_distributions": [{
            "task_id": "T1",
            "task_name": "ReviewApplication",
            "resources": [{
                "resource_id": "Analyst",
                "duration_distribution": {
                    "distribution_name": "expon",
                    "distribution_params": [{"value": 45.0}],
                    "time_unit": "mins",
                },
            }],
        }],
        "gateway_branching_probabilities": [{
            "gateway_id": "GW1",
            "probabilities": {"path_a": 0.6, "path_b": 0.4},
        }],
        "arrival_time_distribution": {
            "distribution_name": "expon",
            "distribution_params": [{"value": 30.0}],
        },
    }
    result = build_baseline_scenario(simod)
    assert result.ok
    assert result.scenario is not None
    s = result.scenario
    assert s.resourceParameters.roles[0].id == "Analyst"
    assert len(s.resourceParameters.roles[0].resources) == 2
    assert s.models[0].modelParameter.activities[0].name == "ReviewApplication"


# -------------------------------------------------------------------
# Merge stability
# -------------------------------------------------------------------

def test_merge_stability_all_applied():
    """All modifications applied -> stability == 1.0."""
    baseline = _make_baseline()
    patch = _mk_patch([_mk_mod()])
    mr = apply_patch(baseline, patch, strict=False)

    total = len(patch.modifications)
    applied = len(mr.applied_modifications)
    stability = round(applied / total if total > 0 else 1.0, 4)

    assert total == 1
    assert applied == 1
    assert stability == 1.0
    assert mr.skipped_modifications == []


def test_merge_stability_partial_applied():
    """One valid mod + one missing-element mod -> stability == 0.5."""
    baseline = _make_baseline()
    good_mod = _mk_mod()                                           # "Analyst" exists
    bad_mod = _mk_mod(target_element="GhostRole",
                      kpi_reference="AnotherKPI")                  # does not exist
    patch = _mk_patch([good_mod, bad_mod])
    mr = apply_patch(baseline, patch, strict=False)

    total = len(patch.modifications)
    applied = len(mr.applied_modifications)
    stability = round(applied / total if total > 0 else 1.0, 4)

    assert total == 2
    assert applied == 1
    assert stability == 0.5
    assert 1 in mr.applied_modifications
    assert 2 in mr.skipped_modifications


def test_merge_stability_none_before_merge():
    """ScenarioGenerationResult.merge_stability defaults to None (no merge attempted)."""
    from second_llm.scenario_generator import ScenarioGenerationResult
    result = ScenarioGenerationResult()
    assert result.merge_stability is None

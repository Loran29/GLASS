"""Patch-only (delta) output schema for the second LLM step.

Design motivation
-----------------
The previous schema asked the LLM to produce a full ``SimuBridgeScenario``
alongside the list of modifications. That forces the model to re-emit
every unchanged baseline element (activities, roles, gateways,
timetables), which is a large surface for drift, hallucination, and
silent regressions of the as-is configuration.

The patch-only schema asks the LLM for exactly what it is reasoning
about — the minimal set of parameter changes — and leaves the
construction of the final simulatable scenario to deterministic
application code that merges the patch into the SIMOD baseline.

Pipeline
~~~~~~~~

``SIMOD baseline (source of truth) + LLM patch --> deterministic merge
--> final SimuBridgeScenario``

``ScenarioPatch`` is the LLM's output. ``apply_patch`` in
:mod:`second_llm.scenario_merger` produces the executable scenario.

Field mapping
~~~~~~~~~~~~~
This schema is intentionally aligned field-by-field with the
``ParameterModification`` used in the legacy full-scenario schema so
that downstream comparison/cost/UI code can be driven by either shape
through a compatibility adapter.  The one new field is ``target_field``:
it lets the merger know exactly which attribute of the target element
to update (``duration``, ``count``, ``costHour``, ...).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Reuse the existing enumerations and sub-models so we do not duplicate
# aliasing / validation logic.
from second_llm.output_schema import (
    ContextDifferentiation,
    KPIImpact,
    ModificationDirection,
    TimeDistribution,
    UnresolvedKPI,
)


# ===================================================================
# Parameter-type taxonomy (canonical names used for merge dispatch)
# ===================================================================

class PatchParameterType(str, Enum):
    """Canonical parameter kinds that the merger knows how to apply."""

    ACTIVITY_DURATION = "activity_duration"
    INTER_ARRIVAL_TIME = "inter_arrival_time"
    GATEWAY_PROBABILITIES = "gateway_probabilities"
    RESOURCE_COUNT = "resource_count"
    RESOURCE_CALENDAR = "resource_calendar"
    RESOURCE_ACTIVITY_ASSIGNMENT = "resource_activity_assignment"
    RESOURCE_COST = "resource_cost"


class PatchTargetKind(str, Enum):
    """Which BPMN/SimuBridge element class is being patched."""

    ACTIVITY = "activity"
    GATEWAY = "gateway"
    ROLE = "role"
    TIMETABLE = "timetable"
    START_EVENT = "start_event"


# Mapping from parameter_type to the expected element kind.
_PARAM_TO_KIND: dict[PatchParameterType, PatchTargetKind] = {
    PatchParameterType.ACTIVITY_DURATION: PatchTargetKind.ACTIVITY,
    PatchParameterType.INTER_ARRIVAL_TIME: PatchTargetKind.START_EVENT,
    PatchParameterType.GATEWAY_PROBABILITIES: PatchTargetKind.GATEWAY,
    PatchParameterType.RESOURCE_COUNT: PatchTargetKind.ROLE,
    PatchParameterType.RESOURCE_CALENDAR: PatchTargetKind.TIMETABLE,
    PatchParameterType.RESOURCE_ACTIVITY_ASSIGNMENT: PatchTargetKind.ACTIVITY,
    PatchParameterType.RESOURCE_COST: PatchTargetKind.ROLE,
}


def expected_target_kind(parameter_type: PatchParameterType) -> PatchTargetKind:
    """Return the element kind the merger will look for, given a parameter type."""
    return _PARAM_TO_KIND[parameter_type]


# ===================================================================
# A single patch modification
# ===================================================================

class PatchModification(BaseModel):
    """A single proposed baseline change with full traceability.

    Semantic invariants enforced by the merger:

      * ``target_element`` must resolve to a concrete baseline element
        whose kind matches ``parameter_type`` (see ``_PARAM_TO_KIND``),
        unless ``direction == DIFFERENTIATE`` (segment-derivation) or
        ``direction == ADD_NEW`` (greenfield addition).
      * ``baseline_value`` is what the model observed in the SIMOD
        baseline.  In strict mode the merger compares this to the
        actual baseline value and rejects the modification on mismatch.
      * ``proposed_value`` replaces the baseline value after validation.
        For structured parameters (distributions, probability maps) the
        LLM fills ``proposed_structured`` instead.
      * ``baseline_value == proposed_value`` is a no-op and is rejected.
    """

    parameter_type: PatchParameterType
    target_element: str = Field(
        description=(
            "Exact name/id of the baseline element being patched "
            "(activity, role, gateway, etc.)."
        ),
    )
    target_field: str = Field(
        default="",
        description=(
            "Specific attribute being updated — optional free-text "
            "label used by humans and the compatibility adapter, e.g. "
            "'mean duration', 'approve branch', 'costHour'."
        ),
    )
    direction: ModificationDirection

    baseline_value: str = Field(
        description="Current value as observed in the SIMOD baseline (readable).",
    )
    proposed_value: str = Field(
        description="New value after the patch is applied (readable).",
    )

    # Structured variant — preferred when the proposed value is not a
    # single scalar (e.g. full distribution or full probability map).
    proposed_structured: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional structured proposed value. For activity_duration "
            "and inter_arrival_time this is a TimeDistribution dict; "
            "for gateway_probabilities it is {sequence_flow_id: prob}."
        ),
    )

    kpi_reference: str = Field(
        description="Name of the SMART KPI this modification targets.",
    )
    rationale: str = Field(
        description="Why this change is expected to move the target KPI.",
    )
    evidence_source: str = Field(
        default="",
        description="Concrete grounding: SIMOD value quoted, log finding, KB paper, or user statement.",
    )
    literature_support: list[int] = Field(
        default_factory=list,
        description="Paper IDs from the knowledge base supporting this change.",
    )
    context_condition: str | None = Field(
        default=None,
        description="Segmentation condition when the mod is context-differentiated.",
    )
    feasibility_assumptions: str = Field(
        default="",
        description="Operational assumptions that must hold for this change to be feasible.",
    )
    intervention: str = Field(
        default="",
        description="Short action label, e.g. 'add two analysts'. Backfilled if empty.",
    )
    mechanism_rationale: str = Field(
        default="",
        description="Queueing/routing/capacity mechanism by which the change moves the KPI.",
    )

    # ----- Validation -----

    @field_validator("direction", mode="before")
    @classmethod
    def _normalise_direction(cls, v: Any) -> Any:
        if isinstance(v, str):
            _aliases: dict[str, str] = {
                "reassign": "redistribute",
                "reallocate": "redistribute",
                "rebalance": "redistribute",
            }
            return _aliases.get(v.lower(), v)
        return v

    @field_validator("baseline_value", "proposed_value", mode="before")
    @classmethod
    def _coerce_to_str(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v
        if isinstance(v, (list, dict)):
            import json
            return json.dumps(v)
        return str(v)

    @model_validator(mode="after")
    def _sanity_checks(self) -> "PatchModification":
        # No-op rejection is enforced at merge time against the resolved
        # baseline value (string comparisons here are unreliable), but
        # we can catch the trivial same-string case up front.
        if (
            self.proposed_structured is None
            and self.baseline_value.strip() == self.proposed_value.strip()
            and self.baseline_value.strip() != ""
        ):
            raise ValueError(
                f"No-op modification on '{self.target_element}': "
                f"baseline_value == proposed_value ('{self.baseline_value}')"
            )
        # A DIFFERENTIATE mod must carry a context_condition.
        if (
            self.direction == ModificationDirection.DIFFERENTIATE
            and not self.context_condition
        ):
            raise ValueError(
                f"Modification on '{self.target_element}' has "
                f"direction='differentiate' but no context_condition."
            )
        # Backfill intervention / mechanism from rationale if empty so
        # downstream UI has something to show.
        if not self.intervention:
            self.intervention = f"{self.direction.value} {self.target_element}"
        if not self.mechanism_rationale:
            self.mechanism_rationale = self.rationale
        return self


# ===================================================================
# Diagnostic entries returned by the merger / patch validator
# ===================================================================

class PatchDiagnostic(BaseModel):
    """One finding from patch validation or merge."""

    severity: Literal["error", "warning", "info"] = "info"
    category: str = Field(
        description="Category tag, e.g. 'missing_element', 'no_op', 'value_mismatch'.",
    )
    message: str
    modification_index: int | None = Field(
        default=None,
        description="1-based index of the patch modification, if applicable.",
    )
    element: str = ""


# ===================================================================
# Top-level patch object (the new LLM output)
# ===================================================================

class ScenarioPatch(BaseModel):
    """Delta-only proposal produced by the second LLM.

    This is what the LLM returns. It does NOT contain a full scenario —
    unchanged baseline fields are carried over by deterministic merge
    code, not by the model.
    """

    scenario_id: str = Field(
        description="Descriptive identifier for the what-if scenario.",
    )
    baseline_reference: str = Field(
        default="SIMOD",
        description="Where the baseline parameters came from.",
    )
    reasoning: str = Field(
        description="2-4 sentences: overall strategy, KPIs targeted, trade-offs.",
    )

    modifications: list[PatchModification] = Field(
        default_factory=list,
        description="Minimal set of baseline changes. May be empty only if all KPIs are unresolved.",
    )
    expected_kpi_impacts: list[KPIImpact] = Field(
        default_factory=list,
        description="Expected direction and magnitude for each verified KPI.",
    )
    unresolved_kpis: list[UnresolvedKPI] = Field(
        default_factory=list,
        description=(
            "Optimisation-target KPIs that cannot be addressed with a "
            "grounded modification. Prefer listing here over fabricating."
        ),
    )
    context_differentiations: list[ContextDifferentiation] = Field(
        default_factory=list,
        description="Context-aware splits applied by DIFFERENTIATE modifications.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues the model itself flags about the patch.",
    )

    # ---- Integrity checks ----

    @model_validator(mode="after")
    def _at_least_something(self) -> "ScenarioPatch":
        if not self.modifications and not self.unresolved_kpis:
            raise ValueError(
                "ScenarioPatch must contain either at least one modification "
                "or at least one unresolved_kpis entry. An empty patch carries "
                "no information."
            )
        # KPIs cannot be both targeted and unresolved.
        targeted = {m.kpi_reference for m in self.modifications}
        unresolved = {u.kpi_name for u in self.unresolved_kpis}
        overlap = targeted & unresolved
        if overlap:
            self.warnings.append(
                f"KPIs {sorted(overlap)} appear in both modifications and "
                f"unresolved_kpis — a KPI must be one or the other."
            )
        return self


# ===================================================================
# Schema appendix for prompt injection
# ===================================================================

SCENARIO_PATCH_JSON_SCHEMA: str = """\
{
  "scenario_id": "<descriptive scenario name>",
  "baseline_reference": "SIMOD",
  "reasoning": "<2-4 sentences: strategy, KPIs targeted, trade-offs>",

  "modifications": [
    {
      "parameter_type": "<activity_duration | inter_arrival_time | gateway_probabilities | resource_count | resource_calendar | resource_activity_assignment | resource_cost>",
      "target_element": "<exact baseline element name>",
      "target_field": "<human-readable field, e.g. 'mean duration'>",
      "direction": "<increase | decrease | redistribute | add_new | remove | change_distribution | differentiate>",
      "baseline_value": "<value quoted from the SIMOD baseline>",
      "proposed_value": "<new value>",
      "proposed_structured": null,
      "kpi_reference": "<SMART KPI name>",
      "rationale": "<why this moves the KPI>",
      "evidence_source": "<SIMOD/log/KB/user quote>",
      "literature_support": [<paper IDs>],
      "context_condition": null,
      "feasibility_assumptions": "<operational assumption, or ''>",
      "intervention": "<short action label>",
      "mechanism_rationale": "<queueing/capacity mechanism>"
    }
  ],

  "expected_kpi_impacts": [
    {
      "kpi_name": "<SMART KPI name>",
      "direction": "<decrease | increase | maintain>",
      "estimated_magnitude": "<e.g. '~20% reduction' or ''>",
      "confidence": "<high | medium | low>",
      "reasoning": "<brief>"
    }
  ],

  "unresolved_kpis": [
    {
      "kpi_name": "<SMART KPI name>",
      "reason": "<not_computable_from_baseline | no_literature_match | blocked_by_operational_constraint | out_of_simulation_scope | other>",
      "explanation": "<concrete reason>"
    }
  ],

  "context_differentiations": [
    {
      "context_factor": "<factor>",
      "factor_scope": "<case_level | event_level | temporal>",
      "segments": ["<segment>"],
      "affected_parameters": ["<parameter>"],
      "statistical_evidence": "<p-value / effect size>",
      "strategy_applied": "<how encoded>"
    }
  ],

  "warnings": ["<optional>"]
}
"""


def get_patch_constrained_decoding_schema() -> dict:
    """Return the Pydantic JSON Schema for constrained decoding of ScenarioPatch."""
    return ScenarioPatch.model_json_schema()

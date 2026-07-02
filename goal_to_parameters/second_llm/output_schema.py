"""Formal output schema for the second LLM step.

Defines the structured JSON that the second LLM must produce: a set of
goal-oriented parameter modifications to a SIMOD-discovered baseline,
serialisable into a SimuBridge-compatible scenario configuration.

The schema has two layers:

  1. **Modification intent** — human-readable, KPI-traceable parameter
     changes with justification.  This is what the LLM reasons about.
  2. **SimuBridge scenario** — the machine-readable configuration that
     SimuBridge can execute.  Produced by applying the modifications to
     the SIMOD baseline.

SimuBridge format reference:
  Repository:   INSM-TUM/SimuBridge--Main
  Data model:   dataModel/SimulationModelDescriptor.js
  Test fixture: defaultTestScenario.json

SIMOD-to-SimuBridge conversion reference:
  INSM-TUM/SimuBridge--Main/simodConverter
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# ===================================================================
# Enumerations (matching SimuBridge's SimulationModelDescriptor.js)
# ===================================================================

class DistributionType(str, Enum):
    """Probability distribution types supported by SimuBridge."""

    EXPONENTIAL = "exponential"    # params: mean
    NORMAL = "normal"              # params: mean, variance
    UNIFORM = "uniform"            # params: lower, upper
    CONSTANT = "constant"          # params: constantValue
    ERLANG = "erlang"              # params: order, mean
    TRIANGULAR = "triangular"      # params: lower, peak, upper
    BINOMIAL = "binomial"          # params: probability, amount
    ARBITRARY = "arbitraryFiniteProbabilityDistribution"


class TimeUnit(str, Enum):
    """Time units for duration distributions."""

    SECONDS = "secs"
    MINUTES = "mins"
    HOURS = "hours"


class Weekday(str, Enum):
    MONDAY = "Monday"
    TUESDAY = "Tuesday"
    WEDNESDAY = "Wednesday"
    THURSDAY = "Thursday"
    FRIDAY = "Friday"
    SATURDAY = "Saturday"
    SUNDAY = "Sunday"


class Currency(str, Enum):
    EURO = "euro"
    DOLLAR = "dollar"
    MONEY_UNIT = "Money Unit"


# ===================================================================
# SimuBridge scenario format (layer 2: machine-readable)
# ===================================================================

class DistributionParameter(BaseModel):
    """A single named parameter of a probability distribution.

    SimuBridge stores distribution parameters as ``{id, value}`` pairs
    where ``id`` is the parameter name (e.g. "mean", "variance").
    """

    id: str = Field(description="Parameter name: mean, variance, lower, upper, peak, constantValue, order, probability, amount")
    value: float

    @field_validator("id", mode="before")
    @classmethod
    def _normalise_id(cls, v: Any) -> str:
        if isinstance(v, str):
            return _PARAM_ID_ALIASES.get(v.strip().lower(), v.strip())
        return v


# Normalise common LLM/SIMOD aliases to SimuBridge canonical names.
_PARAM_ID_ALIASES: dict[str, str] = {
    "mean": "mean",
    "mean_hours": "mean",
    "mean_minutes": "mean",
    "mean_secs": "mean",
    "avg": "mean",
    "average": "mean",
    "variance": "variance",
    "var": "variance",
    "std": "variance",
    "std_hours": "variance",
    "std_minutes": "variance",
    "std_dev": "variance",
    "standard_deviation": "variance",
    "stdev": "variance",
    "sigma": "variance",
    "constantvalue": "constantValue",
    "constant_value": "constantValue",
    "constant": "constantValue",
    "fixed": "constantValue",
    "value": "constantValue",
    "lower": "lower",
    "min": "lower",
    "minimum": "lower",
    "upper": "upper",
    "max": "upper",
    "maximum": "upper",
    "peak": "peak",
    "mode": "peak",
    "order": "order",
    "shape": "order",
    "probability": "probability",
    "prob": "probability",
    "amount": "amount",
    "count": "amount",
    "rate": "mean",
    "mean_inter_arrival": "mean",
    "mean_inter_arrival_hours": "mean",
    "mean_inter_arrival_minutes": "mean",
    "mean_inter_arrival_secs": "mean",
    "mean_arrival": "mean",
    "inter_arrival_mean": "mean",
    "lambda": "mean",
}


# Normalise common LLM aliases for distribution type names.
_DISTRIBUTION_TYPE_ALIASES: dict[str, str] = {
    "fixed": "constant",
    "const": "constant",
    "deterministic": "constant",
    "exp": "exponential",
    "gamma": "erlang",
    "norm": "normal",
    "gaussian": "normal",
    "tri": "triangular",
    "triangle": "triangular",
    "uni": "uniform",
}


class TimeDistribution(BaseModel):
    """A time-based probability distribution (SimuBridge format).

    Used for activity durations and inter-arrival times.
    """

    distributionType: DistributionType
    timeUnit: TimeUnit = TimeUnit.MINUTES
    values: list[DistributionParameter] = Field(min_length=1)

    @field_validator("distributionType", mode="before")
    @classmethod
    def _normalise_distribution_type(cls, v: Any) -> Any:
        if isinstance(v, str):
            return _DISTRIBUTION_TYPE_ALIASES.get(v.strip().lower(), v.strip())
        return v

    @model_validator(mode="after")
    def _remap_params_for_distribution(self) -> "TimeDistribution":
        """Context-aware parameter remapping based on distribution type.

        Handles the common LLM mistake of using ``mean`` for constant
        distributions (which need ``constantValue``) and vice-versa.
        """
        actual = {v.id for v in self.values}
        expected = _DISTRIBUTION_PARAMS.get(self.distributionType)
        if expected is None:
            return self

        # constant distribution: remap 'mean' → 'constantValue'
        if (
            self.distributionType == DistributionType.CONSTANT
            and "mean" in actual
            and "constantValue" not in actual
        ):
            for v in self.values:
                if v.id == "mean":
                    v.id = "constantValue"

        # exponential/normal/erlang: remap 'constantValue' → 'mean'
        if (
            self.distributionType
            in (
                DistributionType.EXPONENTIAL,
                DistributionType.NORMAL,
                DistributionType.ERLANG,
            )
            and "constantValue" in actual
            and "mean" not in actual
        ):
            for v in self.values:
                if v.id == "constantValue":
                    v.id = "mean"

        # gamma-style params: shape -> order already handled above.
        # If the LLM provides gamma(shape, scale), convert to erlang(order, mean)
        # using mean = order * scale so the SimuBridge schema can validate it.
        if (
            self.distributionType == DistributionType.ERLANG
            and "order" in actual
            and "mean" not in actual
        ):
            order_param = next((v for v in self.values if v.id == "order"), None)
            scale_param = next((v for v in self.values if v.id == "scale"), None)
            if order_param is not None and scale_param is not None:
                scale_param.id = "mean"
                scale_param.value = order_param.value * scale_param.value

        # gamma-style params from LLMs often arrive as mean + variance.
        # Convert them into SimuBridge's erlang(order, mean) form when possible.
        if (
            self.distributionType == DistributionType.ERLANG
            and "order" not in actual
            and "mean" in actual
            and "variance" in actual
        ):
            mean_param = next((v for v in self.values if v.id == "mean"), None)
            variance_param = next((v for v in self.values if v.id == "variance"), None)
            if mean_param is not None and variance_param is not None:
                if variance_param.value > 0 and mean_param.value > 0:
                    inferred_order = max(1.0, round((mean_param.value ** 2) / variance_param.value))
                    self.values.append(
                        DistributionParameter(id="order", value=float(inferred_order))
                    )
                elif variance_param.value == 0 and mean_param.value >= 0:
                    # Zero-variance gamma is effectively deterministic.
                    self.distributionType = DistributionType.CONSTANT
                    mean_param.id = "constantValue"

        return self

    @model_validator(mode="after")
    def _validate_params_match_distribution(self) -> "TimeDistribution":
        expected = _DISTRIBUTION_PARAMS.get(self.distributionType)
        if expected is not None:
            actual = {v.id for v in self.values}
            missing = expected - actual
            if missing:
                raise ValueError(
                    f"Distribution '{self.distributionType.value}' requires "
                    f"parameters {expected}, but missing: {missing}. "
                    f"Got: {actual}"
                )
        return self


# Parameter names required for each distribution type.
_DISTRIBUTION_PARAMS: dict[DistributionType, set[str]] = {
    DistributionType.EXPONENTIAL: {"mean"},
    DistributionType.NORMAL: {"mean", "variance"},
    DistributionType.UNIFORM: {"lower", "upper"},
    DistributionType.CONSTANT: {"constantValue"},
    DistributionType.ERLANG: {"order", "mean"},
    DistributionType.TRIANGULAR: {"lower", "peak", "upper"},
    DistributionType.BINOMIAL: {"probability", "amount"},
}


class TimetableItem(BaseModel):
    """A single time window in a weekly timetable (SimuBridge format)."""

    startWeekday: Weekday
    startTime: int = Field(ge=0, le=24, description="Hour of day (0-24)")
    endWeekday: Weekday
    endTime: int = Field(ge=0, le=24, description="Hour of day (0-24)")

    @field_validator("startWeekday", "endWeekday", mode="before")
    @classmethod
    def _normalise_weekday(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().title()
        return v


class Timetable(BaseModel):
    """A named weekly timetable defining resource availability."""

    id: str
    timeTableItems: list[TimetableItem] = Field(min_length=1)


class Resource(BaseModel):
    """An individual resource (worker or machine)."""

    id: str


class Role(BaseModel):
    """A resource pool with shared schedule and cost."""

    id: str
    schedule: str = Field(description="References a Timetable.id")
    costHour: float = Field(ge=0, default=0.0)
    resources: list[Resource] = Field(min_length=1)


class ResourceParameters(BaseModel):
    """All resource-related parameters (SimuBridge format)."""

    roles: list[Role] = Field(min_length=1)
    resources: list[Resource] = Field(default_factory=list)
    timeTables: list[Timetable] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_schedule_references(self) -> "ResourceParameters":
        timetable_ids = {tt.id for tt in self.timeTables}
        for role in self.roles:
            if role.schedule not in timetable_ids:
                raise ValueError(
                    f"Role '{role.id}' references timetable '{role.schedule}' "
                    f"which is not defined. Available: {timetable_ids}"
                )
        return self


class Activity(BaseModel):
    """An activity with its resource assignments and duration distribution."""

    id: str = Field(description="BPMN element ID")
    name: str = Field(default="", description="Human-readable activity name")
    resources: list[str] = Field(
        min_length=1,
        description="List of Role IDs that can perform this activity",
    )
    cost: float = Field(ge=0, default=0.0)
    duration: TimeDistribution


class Gateway(BaseModel):
    """A decision gateway with outgoing path probabilities."""

    id: str = Field(description="BPMN gateway element ID")
    name: str = Field(default="", description="Human-readable gateway name")
    probabilities: dict[str, float] = Field(
        description="Map of outgoing sequence flow ID to probability",
    )

    @model_validator(mode="after")
    def _validate_probabilities_sum(self) -> "Gateway":
        if self.probabilities:
            total = sum(self.probabilities.values())
            if abs(total - 1.0) > 0.01:
                raise ValueError(
                    f"Gateway '{self.id}' probabilities sum to {total:.4f}, "
                    f"expected 1.0 (tolerance 0.01)"
                )
            for path_id, prob in self.probabilities.items():
                if prob < 0 or prob > 1:
                    raise ValueError(
                        f"Gateway '{self.id}' path '{path_id}' has "
                        f"probability {prob}, must be in [0, 1]"
                    )
        return self


class StartEvent(BaseModel):
    """A start event with its inter-arrival time distribution."""

    id: str = Field(description="BPMN start event element ID")
    interArrivalTime: TimeDistribution


class ModelParameter(BaseModel):
    """All process-model-level parameters for one BPMN model."""

    activities: list[Activity] = Field(default_factory=list)
    gateways: list[Gateway] = Field(default_factory=list)
    events: list[StartEvent] = Field(default_factory=list)


class ProcessModel(BaseModel):
    """A single BPMN process model with its simulation parameters."""

    name: str
    modelParameter: ModelParameter
    BPMN: str = Field(default="", description="BPMN 2.0 XML (carried over from SIMOD)")


class SimuBridgeScenario(BaseModel):
    """Complete SimuBridge-compatible scenario configuration.

    This is the machine-readable layer that SimuBridge can load and
    simulate directly.  Structural elements (BPMN XML, element IDs)
    are carried over from the SIMOD baseline; the second LLM modifies
    only the parameter values.
    """

    scenarioName: str
    startingDate: str = Field(default="01-01-0000")
    startingTime: str = Field(default="00:00")
    numberOfInstances: int = Field(ge=1, default=1000)
    currency: Currency = Currency.EURO
    resourceParameters: ResourceParameters
    models: list[ProcessModel] = Field(min_length=1)


# ===================================================================
# Modification intent (layer 1: human-readable, KPI-traceable)
# ===================================================================

class ModificationDirection(str, Enum):
    """How a parameter is being changed from the baseline."""

    INCREASE = "increase"
    DECREASE = "decrease"
    REDISTRIBUTE = "redistribute"
    ADD_NEW = "add_new"
    REMOVE = "remove"
    CHANGE_DISTRIBUTION = "change_distribution"
    DIFFERENTIATE = "differentiate"


class ParameterModification(BaseModel):
    """A single proposed parameter change with full traceability.

    Each modification traces back to:
      - A KPI (why this change is needed)
      - A baseline value (what is being changed from)
      - A knowledge-base recommendation (literature support)
      - Optionally a context condition (for whom)
    """

    intervention: str = Field(
        default="",
        description=(
            "Concrete intervention label phrased as an action, e.g. "
            "'extend packing-staff calendar' or 'add one evening triage nurse'."
        ),
    )
    changed_parameters: str = Field(
        default="",
        description=(
            "Human-readable parameter(s) changed by this intervention, e.g. "
            "'resource availability calendar' or 'activity duration distribution'."
        ),
    )
    parameter_type: str = Field(
        description=(
            "Canonical parameter name from the knowledge base taxonomy: "
            "activity_duration, inter_arrival_time, gateway_probabilities, "
            "resource_count, resource_calendar, resource_activity_assignment, "
            "resource_cost"
        ),
    )
    target_element: str = Field(
        description=(
            "The specific process element being modified: an activity name, "
            "resource role name, or gateway name."
        ),
    )
    direction: ModificationDirection
    baseline_value: str = Field(
        description="Current value from the SIMOD baseline (as readable string)",
    )
    proposed_value: str = Field(
        description="New proposed value (as readable string)",
    )
    kpi_reference: str = Field(
        description="Name of the SMART KPI this modification targets",
    )
    mechanism_rationale: str = Field(
        default="",
        description=(
            "How this change is expected to improve the KPI in process terms, "
            "grounded in queueing, capacity, routing, batching, or timing effects."
        ),
    )
    rationale: str = Field(
        description=(
            "Why this change is expected to improve the target KPI. "
            "Must reference either literature evidence, log-derived "
            "baselines, or both."
        ),
    )
    evidence_source: str = Field(
        default="",
        description=(
            "Specific evidence supporting the intervention, such as SIMOD "
            "baseline values, event-log findings, knowledge-base papers, "
            "or user-provided operational context."
        ),
    )
    literature_support: list[int] = Field(
        default_factory=list,
        description="Paper IDs from the knowledge base supporting this change",
    )
    feasibility_assumptions: str = Field(
        default="",
        description=(
            "Operational assumptions that must hold for the intervention to "
            "be feasible, such as overtime permission, staffing flexibility, "
            "or policy approval."
        ),
    )
    context_condition: str | None = Field(
        default=None,
        description=(
            "If this modification is context-differentiated, the condition "
            "under which it applies (e.g. 'loan_amount >= 50000'). "
            "None for universal modifications."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _normalise_display_field_aliases(cls, data: object) -> object:
        """Accept a few synonymous field names for the human-readable structure."""
        if not isinstance(data, dict):
            return data

        aliases = {
            "changed_parameter": "changed_parameters",
            "mechanism": "mechanism_rationale",
            "evidence": "evidence_source",
            "assumption": "feasibility_assumptions",
            "assumptions": "feasibility_assumptions",
        }
        normalised = dict(data)
        for source, target in aliases.items():
            if source in normalised and target not in normalised:
                normalised[target] = normalised[source]
        return normalised

    @model_validator(mode="after")
    def _backfill_human_readable_fields(self) -> "ParameterModification":
        """Keep the richer display structure populated without breaking legacy outputs."""
        parameter_labels = {
            "activity_duration": "activity duration",
            "inter_arrival_time": "inter-arrival time",
            "gateway_probabilities": "gateway probabilities",
            "resource_count": "resource count",
            "resource_calendar": "resource availability calendar",
            "resource_activity_assignment": "resource activity assignment",
            "resource_cost": "resource cost",
        }
        direction_labels = {
            ModificationDirection.INCREASE: "increase",
            ModificationDirection.DECREASE: "reduce",
            ModificationDirection.REDISTRIBUTE: "redistribute",
            ModificationDirection.ADD_NEW: "add",
            ModificationDirection.REMOVE: "remove",
            ModificationDirection.CHANGE_DISTRIBUTION: "change",
            ModificationDirection.DIFFERENTIATE: "differentiate",
        }

        readable_parameter = parameter_labels.get(
            self.parameter_type,
            self.parameter_type.replace("_", " "),
        )

        if not self.changed_parameters:
            self.changed_parameters = readable_parameter

        if not self.intervention:
            action = direction_labels.get(self.direction, "change")
            self.intervention = f"{action} {self.target_element} {readable_parameter}".strip()

        if not self.mechanism_rationale and self.rationale:
            self.mechanism_rationale = self.rationale

        if not self.rationale and self.mechanism_rationale:
            self.rationale = self.mechanism_rationale

        if not self.evidence_source:
            evidence_parts: list[str] = []
            if self.literature_support:
                paper_ids = ", ".join(str(pid) for pid in self.literature_support)
                evidence_parts.append(f"Knowledge-base papers {paper_ids}")
            self.evidence_source = "; ".join(evidence_parts) if evidence_parts else "Not specified"

        if not self.feasibility_assumptions:
            self.feasibility_assumptions = self.context_condition or "Not specified"

        return self


class KPIImpact(BaseModel):
    """Expected impact of the scenario on a specific KPI."""

    kpi_name: str
    direction: str = Field(description="Expected change: 'decrease', 'increase', 'maintain'")
    estimated_magnitude: str = Field(
        default="",
        description=(
            "Rough estimate of the expected change, if the literature or "
            "log baselines support one (e.g. '~20% reduction'). "
            "Empty string if not estimable."
        ),
    )
    confidence: str = Field(
        default="medium",
        description="How confident the estimate is: 'high', 'medium', 'low'",
    )
    reasoning: str = Field(
        default="",
        description="Brief explanation of why this impact is expected",
    )


class UnresolvedKPI(BaseModel):
    """A verified KPI that the scenario deliberately does NOT address.

    Populated when no grounded parameter change can target the KPI given
    the current baseline, log evidence, literature, and operational
    constraints. Prevents the model from fabricating weakly-grounded
    modifications just to achieve full KPI coverage.
    """

    kpi_name: str = Field(description="Name of the verified SMART KPI")
    reason: str = Field(
        description=(
            "Category of why the KPI cannot be addressed: "
            "'not_computable_from_baseline', 'no_literature_match', "
            "'blocked_by_operational_constraint', "
            "'out_of_simulation_scope', or 'other'."
        ),
    )
    explanation: str = Field(
        description=(
            "Concrete explanation citing the specific baseline element, "
            "constraint, or evidence gap that makes the KPI unresolvable."
        ),
    )


class ContextDifferentiation(BaseModel):
    """A context-aware parameter split applied in the scenario.

    Records when and how parameters were differentiated by context
    factors, linking back to the statistical evidence from the first
    LLM's context analysis.
    """

    context_factor: str = Field(description="The factor used for differentiation (e.g. 'customer_tier')")
    factor_scope: str = Field(description="case_level, event_level, or temporal")
    segments: list[str] = Field(
        description="The distinct segments (e.g. ['premium', 'standard'])",
    )
    affected_parameters: list[str] = Field(
        description="Which parameters are differentiated by this factor",
    )
    statistical_evidence: str = Field(
        default="",
        description=(
            "Summary of the statistical evidence: p-value, effect size, "
            "observed baseline differences"
        ),
    )
    strategy_applied: str = Field(
        description=(
            "How the differentiation was implemented "
            "(e.g. 'separate resource pools per segment')"
        ),
    )


# ===================================================================
# Complete second LLM output (both layers combined)
# ===================================================================

class ScenarioProposal(BaseModel):
    """The complete structured output of the second LLM step.

    Combines the modification intent layer (traceable, human-readable)
    with the SimuBridge scenario layer (machine-readable, executable).

    This is the JSON schema that the second LLM prompt enforces.  The
    LLM fills in both layers: the modifications explain *what* and *why*,
    while the scenario provides the *runnable configuration*.
    """

    # --- Metadata ---
    scenario_name: str = Field(
        description="Descriptive name for the what-if scenario",
    )
    baseline_source: str = Field(
        default="SIMOD",
        description="Where the baseline parameters came from",
    )
    reasoning: str = Field(
        description=(
            "2-4 sentence explanation of the overall scenario design strategy: "
            "which KPIs are targeted, which trade-offs were considered, and "
            "how the parameter changes work together."
        ),
    )

    # --- Modification intent (layer 1) ---
    modifications: list[ParameterModification] = Field(
        min_length=1,
        description="The proposed parameter changes with traceability",
    )
    expected_kpi_impacts: list[KPIImpact] = Field(
        min_length=1,
        description="Expected impact on each verified KPI",
    )
    context_differentiations: list[ContextDifferentiation] = Field(
        default_factory=list,
        description="Context-aware parameter splits (empty if no context evidence)",
    )
    unresolved_kpis: list[UnresolvedKPI] = Field(
        default_factory=list,
        description=(
            "Optimisation-target KPIs that no modification addresses, with "
            "an explicit reason. Prefer listing a KPI here over fabricating "
            "a weakly-grounded modification."
        ),
    )

    # --- SimuBridge scenario (layer 2) ---
    scenario: SimuBridgeScenario = Field(
        description="The complete SimuBridge-compatible scenario configuration",
    )

    # --- Validation metadata ---
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues detected during generation",
    )

    @model_validator(mode="after")
    def _validate_modification_coverage(self) -> "ScenarioProposal":
        """Every KPI in expected_kpi_impacts should be targeted by at
        least one modification, OR explicitly listed in unresolved_kpis."""
        targeted_kpis = {m.kpi_reference for m in self.modifications}
        unresolved_kpis = {u.kpi_name for u in self.unresolved_kpis}

        overlap = targeted_kpis & unresolved_kpis
        if overlap:
            self.warnings.append(
                f"KPIs {sorted(overlap)} appear in both modifications and "
                f"unresolved_kpis — a KPI must be one or the other."
            )

        for impact in self.expected_kpi_impacts:
            if (
                impact.kpi_name not in targeted_kpis
                and impact.kpi_name not in unresolved_kpis
            ):
                self.warnings.append(
                    f"KPI '{impact.kpi_name}' has an expected impact but no "
                    f"modification targets it and it is not listed in "
                    f"unresolved_kpis."
                )
        return self

    @model_validator(mode="after")
    def _validate_context_consistency(self) -> "ScenarioProposal":
        """Validate that context differentiations are consistently encoded.

        Checks:
          1. Modifications with context_condition have a matching
             ContextDifferentiation entry.
          2. Each ContextDifferentiation's segments are reflected in
             the scenario (e.g. segment-named roles or activities exist).
        """
        diff_factors = {cd.context_factor for cd in self.context_differentiations}

        # Check 1: modifications with conditions need differentiation entries
        for mod in self.modifications:
            if mod.context_condition and not diff_factors:
                self.warnings.append(
                    f"Modification on '{mod.target_element}' has context "
                    f"condition '{mod.context_condition}' but no "
                    f"ContextDifferentiation entries are defined."
                )
                break

        # Check 2: differentiation segments should be reflected in scenario
        if self.context_differentiations:
            scenario_role_ids = {
                r.id.lower()
                for r in self.scenario.resourceParameters.roles
            }
            scenario_activity_names = set()
            for model in self.scenario.models:
                for act in model.modelParameter.activities:
                    scenario_activity_names.add(act.name.lower())
                    scenario_activity_names.add(act.id.lower())

            for cd in self.context_differentiations:
                # Check if any segment name appears in role or activity names
                segment_reflected = False
                for seg in cd.segments:
                    seg_lower = seg.lower()
                    if any(seg_lower in rid for rid in scenario_role_ids):
                        segment_reflected = True
                        break
                    if any(seg_lower in aname for aname in scenario_activity_names):
                        segment_reflected = True
                        break

                if not segment_reflected:
                    self.warnings.append(
                        f"ContextDifferentiation for '{cd.context_factor}' "
                        f"declares segments {cd.segments}, but none of "
                        f"these segment names appear in the scenario's "
                        f"roles or activities. The scenario may not "
                        f"reflect this differentiation."
                    )

        return self


# ===================================================================
# Schema export for LLM prompt injection
# ===================================================================

# The JSON schema string that gets embedded in the second LLM's system
# prompt so it knows exactly what structure to produce.

SCENARIO_PROPOSAL_JSON_SCHEMA: str = """\
{
  "scenario_name": "<descriptive name for the what-if scenario>",
  "baseline_source": "SIMOD",
  "reasoning": "<2-4 sentences: which KPIs targeted, trade-offs considered, how changes work together>",

  "modifications": [
    {
      "intervention": "<concrete action label, e.g. 'extend packing-staff calendar'>",
      "changed_parameters": "<human-readable parameter(s) changed>",
      "parameter_type": "<canonical name: activity_duration | inter_arrival_time | gateway_probabilities | resource_count | resource_calendar | resource_activity_assignment | resource_cost>",
      "target_element": "<activity name, role name, or gateway name>",
      "direction": "<increase | decrease | redistribute | add_new | remove | change_distribution | differentiate>",
      "baseline_value": "<current value from SIMOD, as readable string>",
      "proposed_value": "<new proposed value, as readable string>",
      "kpi_reference": "<name of the SMART KPI this targets>",
      "mechanism_rationale": "<how the intervention improves the KPI in process terms>",
      "rationale": "<why this change helps — must cite evidence or log baselines>",
      "evidence_source": "<specific supporting evidence: SIMOD/log/KB/user clarification>",
      "literature_support": [<paper IDs from knowledge base>],
      "feasibility_assumptions": "<operational assumption that must hold, or 'Not specified'>",
      "context_condition": "<condition if differentiated, or null>"
    }
  ],

  "expected_kpi_impacts": [
    {
      "kpi_name": "<SMART KPI name>",
      "direction": "<decrease | increase | maintain>",
      "estimated_magnitude": "<e.g. '~20% reduction', or '' if unknown>",
      "confidence": "<high | medium | low>",
      "reasoning": "<brief explanation>"
    }
  ],

  "context_differentiations": [
    {
      "context_factor": "<factor name>",
      "factor_scope": "<case_level | event_level | temporal>",
      "segments": ["<segment values>"],
      "affected_parameters": ["<parameter names>"],
      "statistical_evidence": "<p-value, effect size summary>",
      "strategy_applied": "<how differentiation was implemented>"
    }
  ],

  "unresolved_kpis": [
    {
      "kpi_name": "<SMART KPI name>",
      "reason": "<not_computable_from_baseline | no_literature_match | blocked_by_operational_constraint | out_of_simulation_scope | other>",
      "explanation": "<concrete explanation citing the specific gap>"
    }
  ],

  "scenario": {
    "scenarioName": "<same as scenario_name>",
    "startingDate": "01-01-0000",
    "startingTime": "00:00",
    "numberOfInstances": <integer, >= 100>,
    "currency": "euro",
    "resourceParameters": {
      "roles": [
        {
          "id": "<role name>",
          "schedule": "<timetable ID>",
          "costHour": <float>,
          "resources": [{"id": "<resource name>"}]
        }
      ],
      "resources": [{"id": "<resource name>"}],
      "timeTables": [
        {
          "id": "<timetable name>",
          "timeTableItems": [
            {
              "startWeekday": "<Monday-Sunday>",
              "startTime": <0-24>,
              "endWeekday": "<Monday-Sunday>",
              "endTime": <0-24>
            }
          ]
        }
      ]
    },
    "models": [
      {
        "name": "<process name>",
        "modelParameter": {
          "activities": [
            {
              "id": "<BPMN element ID>",
              "name": "<activity name>",
              "resources": ["<role IDs>"],
              "cost": <float>,
              "duration": {
                "distributionType": "<exponential | normal | uniform | constant | triangular>",
                "timeUnit": "<secs | mins | hours>",
                "values": [{"id": "<param name>", "value": <float>}]
              }
            }
          ],
          "gateways": [
            {
              "id": "<BPMN gateway ID>",
              "name": "<gateway name>",
              "probabilities": {"<sequence flow ID>": <float, 0-1>}
            }
          ],
          "events": [
            {
              "id": "<BPMN start event ID>",
              "interArrivalTime": {
                "distributionType": "<distribution type>",
                "timeUnit": "<time unit>",
                "values": [{"id": "<param name>", "value": <float>}]
              }
            }
          ]
        },
        "BPMN": "<carried over from SIMOD — do not generate>"
      }
    ]
  }
}\
"""


def get_constrained_decoding_schema() -> dict:
    """Return the Pydantic-derived JSON Schema for constrained decoding.

    This schema is passed to providers that support structured outputs
    (e.g. OpenAI ``response_format.json_schema``, Ollama ``format``).
    The provider guarantees the response conforms to this schema at
    generation time, eliminating JSON parse failures.
    """
    return ScenarioProposal.model_json_schema()

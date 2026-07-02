"""Pydantic data models for the simulation parameter knowledge base.

These models define the structured representations for:
  - Literature references (academic papers providing evidence)
  - Simulation parameter taxonomy (what can be changed)
  - Goal-to-parameter mappings (which changes address which goals)
  - Context-aware rules (when to differentiate parameters by context)
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class GoalCategory(str, Enum):
    """High-level simulation goal categories derived from the literature."""

    WAITING_TIME = "waiting_time"
    PROCESSING_TIME = "processing_time"
    COST = "cost"
    PROCESSING_CAPACITY = "processing_capacity"
    RESOURCE_UTILISATION = "resource_utilisation"
    QUALITY_COMPLIANCE = "quality_compliance"
    THROUGHPUT = "throughput"


class ParameterCategory(str, Enum):
    """Top-level grouping of simulation parameters."""

    PROCESS_MODEL = "process_model"
    RESOURCE = "resource"
    SCENARIO = "scenario"


class ChangeDirection(str, Enum):
    """How a parameter should be modified to achieve a goal."""

    INCREASE = "increase"
    DECREASE = "decrease"
    REDISTRIBUTE = "redistribute"
    ADD = "add"
    REMOVE = "remove"
    REASSIGN = "reassign"


class ContextFactorScope(str, Enum):
    """Where a context factor originates."""

    CASE_LEVEL = "case_level"
    EVENT_LEVEL = "event_level"
    TEMPORAL = "temporal"


# ---------------------------------------------------------------------------
# Literature
# ---------------------------------------------------------------------------

class LiteratureReference(BaseModel):
    """An academic paper providing evidence for goal-to-parameter mappings."""

    paper_id: int = Field(description="Numeric identifier matching the baseline repo's paper numbering")
    authors: str
    year: int
    title: str
    domain: str = Field(description="Application domain (e.g. healthcare, logistics, manufacturing)")
    key_finding: str = Field(description="One-sentence summary of the simulation-relevant finding")
    parameters_tested: list[str] = Field(
        default_factory=list,
        description="Which simulation parameters were varied in the study",
    )
    quantitative_result: str = Field(
        default="",
        description="Key quantitative outcome (e.g. '48% reduction in waiting time')",
    )
    source_location: str = Field(
        default="",
        description="Page, table, or section in the paper where the quantitative_result was verified (e.g. 'Table III, p. 803')",
    )


# ---------------------------------------------------------------------------
# Parameter taxonomy
# ---------------------------------------------------------------------------

class SimodFieldMapping(BaseModel):
    """Cross-reference between a taxonomy parameter and SIMOD output fields."""

    simod_json_path: str = Field(
        description="Dot-notation path in SIMOD JSON output (e.g. 'resource_profiles.*.count')",
    )
    description: str = Field(default="", description="What this SIMOD field represents")


class SimulationParameter(BaseModel):
    """A single simulation parameter in the taxonomy."""

    name: str = Field(description="Canonical parameter name")
    category: ParameterCategory
    description: str
    value_type: str = Field(
        description="Expected value type: 'integer', 'float', 'distribution', 'probability', 'schedule', 'assignment'",
    )
    unit: str = Field(default="", description="Unit of measurement if applicable")
    constraints: str = Field(
        default="",
        description="Value constraints (e.g. '>= 0', 'sums to 1.0', 'valid cron')",
    )
    examples: list[str] = Field(default_factory=list)
    simod_fields: list[SimodFieldMapping] = Field(
        default_factory=list,
        description="Which SIMOD output fields correspond to this parameter",
    )
    supports_differentiation: bool = Field(
        default=False,
        description="Whether this parameter can be differentiated by context factors",
    )


# ---------------------------------------------------------------------------
# Goal-to-parameter mappings
# ---------------------------------------------------------------------------

class ParameterChange(BaseModel):
    """A specific parameter modification recommended for a goal."""

    parameter_name: str = Field(description="References a SimulationParameter.name")
    direction: ChangeDirection
    rationale: str = Field(description="Why this change helps achieve the goal")
    paper_ids: list[int] = Field(
        default_factory=list,
        description="Literature references supporting this recommendation",
    )
    quantitative_evidence: str = Field(
        default="",
        description="Concrete result from the literature (e.g. '10-min reduction in access time')",
    )


class GoalParameterMapping(BaseModel):
    """Links a specific simulation goal to recommended parameter changes."""

    goal_description: str = Field(description="Natural-language goal (e.g. 'Reduce patient waiting times')")
    goal_category: GoalCategory
    parameter_changes: list[ParameterChange]
    domain: str = Field(default="general", description="Domain where this mapping was validated")
    notes: str = Field(default="")


# ---------------------------------------------------------------------------
# Context-aware rules
# ---------------------------------------------------------------------------

class ContextAwareRule(BaseModel):
    """A rule that triggers differentiated parameter generation when a
    context factor is statistically significant.

    These rules extend the baseline's goal-to-parameter mappings with
    context-awareness — the thesis contribution beyond the existing work.
    """

    rule_id: str
    description: str
    trigger_factor_scope: ContextFactorScope
    trigger_factor_examples: list[str] = Field(
        description="Example factor names that activate this rule",
    )
    affected_parameters: list[str] = Field(
        description="Which SimulationParameter names this rule modifies",
    )
    differentiation_strategy: str = Field(
        description="How the parameter should be split by context (e.g. 'per-segment resource pool')",
    )
    rationale: str


# ---------------------------------------------------------------------------
# Knowledge base container
# ---------------------------------------------------------------------------

class ParameterKnowledgeBase(BaseModel):
    """The complete structured knowledge base for the second LLM step.

    Aggregates literature evidence, parameter taxonomy, goal-to-parameter
    mappings, and context-aware differentiation rules into a single
    queryable container.
    """

    literature: list[LiteratureReference] = Field(default_factory=list)
    parameters: list[SimulationParameter] = Field(default_factory=list)
    goal_mappings: list[GoalParameterMapping] = Field(default_factory=list)
    context_rules: list[ContextAwareRule] = Field(default_factory=list)

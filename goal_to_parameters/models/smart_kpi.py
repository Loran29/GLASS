from __future__ import annotations

from enum import Enum
from typing import Any, List

from pydantic import BaseModel, Field, field_validator, model_validator


class KPICategory(str, Enum):
    TIME = "time"
    COST = "cost"
    QUALITY = "quality"
    UTILIZATION = "utilization"
    THROUGHPUT = "throughput"
    COMPLIANCE = "compliance"
    FLEXIBILITY = "flexibility"


class EvidenceBasis(str, Enum):
    PROCESS_DESCRIPTION_ONLY = "process_description_only"
    EVENT_LOG_ONLY = "event_log_only"
    BOTH = "both"
    PROXY_FROM_LOG = "proxy_from_log"


class ProcessScope(str, Enum):
    END_TO_END = "end_to_end"
    SUBPROCESS = "subprocess"
    ACTIVITY_LEVEL = "activity_level"


class TargetDirection(str, Enum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    MAINTAIN = "maintain"


_COMPAT_CATEGORY_ALIASES = {
    "occurrence": KPICategory.THROUGHPUT.value,
    "volume": KPICategory.THROUGHPUT.value,
    "frequency": KPICategory.THROUGHPUT.value,
}


def _normalize_enum_like(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return value.strip().lower().replace("-", "_").replace(" ", "_")


class SMARTBreakdown(BaseModel):
    specific: str = Field(
        description="What exactly is being measured and in which part of the process"
    )
    measurable: str = Field(
        description="The metric, unit of measurement, and how it is quantified"
    )
    achievable: str = Field(
        description="Why this target is realistic given the process context"
    )
    relevant: str = Field(description="How this KPI connects to the stated simulation goal")
    time_bound: str = Field(description="The time frame or period for measurement")


class ContextTargetSegment(BaseModel):
    condition: str = Field(description="A context condition such as customer_type = premium or Mondays")
    target: str = Field(description="A relative improvement goal grounded in the evidence, e.g. 'below the observed baseline of 22h for this segment' or 'above current level'. Do not use an invented absolute number.")
    rationale: str | None = Field(
        default=None,
        description="Optional short explanation grounded in the contextual evidence",
    )
    evidence_factor: str | None = Field(
        default=None,
        description="Optional traceability field identifying which evidence factor supported this segment",
    )
    evidence_metric: str | None = Field(
        default=None,
        description="Optional traceability field identifying which metric supported this segment",
    )
    adjusted_p_value: float | None = Field(
        default=None,
        description="Optional adjusted p-value for the supporting statistical association",
    )
    effect_size: float | None = Field(
        default=None,
        description="Optional practical effect size for the supporting statistical association",
    )
    sample_size: int | None = Field(
        default=None,
        description="Optional sample size for the supporting evidence segment or relationship",
    )
    observed_baseline: float | None = Field(
        default=None,
        description="Optional observed baseline such as a median from the supporting evidence",
    )
    target_type: str | None = Field(
        default=None,
        description="Optional traceability label such as direct or proxy",
    )


class SMARTKpi(BaseModel):
    name: str = Field(description="Short descriptive name of the KPI")
    description: str = Field(description="Natural language description of what the KPI measures")
    category: KPICategory
    smart_breakdown: SMARTBreakdown
    target_direction: TargetDirection = Field(description="One of: 'minimize', 'maximize', 'maintain'")
    suggested_formula: str = Field(description="How to compute this KPI if data were available")
    supported_by_log: bool = Field(
        default=False,
        description="Whether the KPI is directly or reasonably supportable by the available event-log evidence",
    )
    evidence_basis: EvidenceBasis = Field(
        default=EvidenceBasis.PROCESS_DESCRIPTION_ONLY,
        description="Whether the KPI is grounded in the process description, event log, both, or a log-based proxy",
    )
    process_scope: ProcessScope = Field(
        description="Whether the KPI applies end-to-end, to a subprocess, or to an activity-level step",
    )
    context_segmentation: List[ContextTargetSegment] = Field(
        default_factory=list,
        description="Optional context-specific relative improvement goals derived from significant contextual evidence",
    )
    measurable_as: str | None = Field(
        default=None,
        description="Exact name of the computed KPI this maps to for simulation evaluation, or null if not computable from a Prosimos event log",
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_compatibility_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        payload = dict(data)
        payload["category"] = _COMPAT_CATEGORY_ALIASES.get(
            _normalize_enum_like(payload.get("category")),
            payload.get("category"),
        )

        evidence_basis = payload.get("evidence_basis")
        if evidence_basis in (None, ""):
            evidence_basis = (
                EvidenceBasis.BOTH.value if payload.get("supported_by_log") is True
                else EvidenceBasis.PROCESS_DESCRIPTION_ONLY.value
            )
        payload["evidence_basis"] = evidence_basis

        if "supported_by_log" not in payload or payload.get("supported_by_log") is None:
            payload["supported_by_log"] = evidence_basis in {
                EvidenceBasis.EVENT_LOG_ONLY.value,
                EvidenceBasis.BOTH.value,
                EvidenceBasis.PROXY_FROM_LOG.value,
            }

        if payload.get("context_segmentation") in (None, ""):
            payload["context_segmentation"] = []

        # Utilization is a run-level metric — process_scope must be end_to_end.
        if _normalize_enum_like(payload.get("category")) == KPICategory.UTILIZATION.value:
            payload["process_scope"] = ProcessScope.END_TO_END.value

        return payload

    @model_validator(mode="after")
    def _validate_context_segmentation_grounding(self) -> "SMARTKpi":
        """Enforce that context segmentation is only present when log-grounded evidence supports it."""

        if not self.context_segmentation:
            return self

        if not self.supported_by_log:
            raise ValueError(
                "context_segmentation requires supported_by_log to be true; "
                "context-specific targets cannot exist without event-log evidence."
            )

        if self.evidence_basis == EvidenceBasis.PROCESS_DESCRIPTION_ONLY:
            raise ValueError(
                "context_segmentation requires evidence_basis to be one of "
                "'event_log_only', 'both', or 'proxy_from_log'; "
                "'process_description_only' does not provide sufficient grounding "
                "for context-specific targets."
            )

        for idx, segment in enumerate(self.context_segmentation):
            if not segment.evidence_factor or not segment.evidence_factor.strip():
                raise ValueError(
                    f"context_segmentation[{idx}] is missing a non-empty evidence_factor; "
                    "each context segment must trace back to a specific evidence factor."
                )
            if not segment.evidence_metric or not segment.evidence_metric.strip():
                raise ValueError(
                    f"context_segmentation[{idx}] is missing a non-empty evidence_metric; "
                    "each context segment must trace back to a specific evidence metric."
                )

        return self

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, value: Any) -> Any:
        normalized_value = _normalize_enum_like(value)
        return _COMPAT_CATEGORY_ALIASES.get(normalized_value, normalized_value)

    @field_validator("evidence_basis", "process_scope", "target_direction", mode="before")
    @classmethod
    def _normalize_enum_fields(cls, value: Any) -> Any:
        return _normalize_enum_like(value)


class KPIGenerationResult(BaseModel):
    simulation_goal_structured: str = Field(description="The goal decomposed into precise sub-objectives: primary metric(s) to optimise with direction, explicit constraints to maintain, and process scope")
    kpis: List[SMARTKpi]
    reasoning: str = Field(
        description="Concise 2-4 sentence explanation of why these KPIs were selected for the goal"
    )

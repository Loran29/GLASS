"""Data containers returned by the retrieval stages.

Separated from :mod:`knowledge.retrieval` so the models can be imported
without pulling in the retrievers, the knowledge base, or the filter
implementations.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from knowledge.models import (
    ContextAwareRule,
    GoalParameterMapping,
    LiteratureReference,
    SimulationParameter,
)


class RetrievalResult(BaseModel):
    """The relevant knowledge subset selected for a specific second LLM call.

    Carries not only the retrieved items but also per-item retrieval
    scores (from the hybrid BM25 + dense retriever) plus the list of
    queries that produced them.  The prompt serialisation surfaces
    these scores so the LLM can weigh highly-ranked evidence more
    heavily than tangential hits.
    """

    goal_mappings: list[GoalParameterMapping] = Field(default_factory=list)
    parameters: list[SimulationParameter] = Field(default_factory=list)
    context_rules: list[ContextAwareRule] = Field(default_factory=list)
    literature: list[LiteratureReference] = Field(default_factory=list)
    matched_goal_categories: list[str] = Field(default_factory=list)

    # Retrieval provenance — lets the prompt cite scores and the LLM
    # distinguish "strongly retrieved" from "weakly retrieved" evidence.
    mapping_scores: list[float] = Field(default_factory=list)
    literature_scores: list[float] = Field(default_factory=list)
    parameter_scores: list[float] = Field(default_factory=list)
    rule_scores: list[float] = Field(default_factory=list)

    retrieval_queries: list[str] = Field(default_factory=list)
    retrieval_backend: str = Field(default="")
    retrieval_model: str = Field(default="")

    # True when the hybrid retriever returned no goal_mappings and the
    # keyword category-filter was used to backfill evidence.  Surfaces
    # the fallback in eval metrics and in prompt provenance.
    backfilled: bool = Field(default=False)

    # Full-text PDF chunks from the academic PDFs (parallel to chunk scores).
    pdf_chunks: list[str] = Field(default_factory=list)
    pdf_chunk_scores: list[float] = Field(default_factory=list)
    # paper_id for each chunk (used in source_excerpts output)
    pdf_chunk_paper_ids: list[int] = Field(default_factory=list)

    def _score_of(self, scores: list[float], idx: int) -> float | None:
        return scores[idx] if idx < len(scores) else None

    def to_prompt_json(self, *, compact: bool = True) -> str:
        """Serialise as JSON for LLM prompt injection."""
        indent = None if compact else 2

        def _round(x: float | None) -> float | None:
            return round(x, 4) if x is not None else None

        payload: dict[str, Any] = {
            "matched_goal_categories": self.matched_goal_categories,
            "retrieval": {
                "backend": self.retrieval_backend,
                "model": self.retrieval_model,
                "queries": self.retrieval_queries,
                "method": "hybrid BM25 + dense, Reciprocal Rank Fusion",
                "backfilled": self.backfilled,
            },
            "goal_parameter_recommendations": [
                {
                    "goal": m.goal_description,
                    "category": m.goal_category.value,
                    "domain": m.domain,
                    "retrieval_score": _round(self._score_of(self.mapping_scores, i)),
                    "changes": [
                        {
                            "parameter": c.parameter_name,
                            "direction": c.direction.value,
                            "rationale": c.rationale,
                            **({"evidence": c.quantitative_evidence} if c.quantitative_evidence else {}),
                            **({"papers": c.paper_ids} if c.paper_ids else {}),
                        }
                        for c in m.parameter_changes
                    ],
                    **({"notes": m.notes} if m.notes else {}),
                }
                for i, m in enumerate(self.goal_mappings)
            ],
            "parameter_definitions": [
                {
                    "name": p.name,
                    "category": p.category.value,
                    "description": p.description,
                    "value_type": p.value_type,
                    "retrieval_score": _round(self._score_of(self.parameter_scores, i)),
                    **({"unit": p.unit} if p.unit else {}),
                    **({"constraints": p.constraints} if p.constraints else {}),
                    "simod_fields": [
                        {"path": sf.simod_json_path, "description": sf.description}
                        for sf in p.simod_fields
                    ],
                    "supports_context_differentiation": p.supports_differentiation,
                }
                for i, p in enumerate(self.parameters)
            ],
            "context_differentiation_rules": [
                {
                    "rule": r.rule_id,
                    "description": r.description,
                    "trigger_scope": r.trigger_factor_scope.value,
                    "trigger_examples": r.trigger_factor_examples,
                    "affected_parameters": r.affected_parameters,
                    "strategy": r.differentiation_strategy,
                    "rationale": r.rationale,
                    "retrieval_score": _round(self._score_of(self.rule_scores, i)),
                }
                for i, r in enumerate(self.context_rules)
            ],
            "supporting_literature": [
                {
                    "id": lit.paper_id,
                    "authors": lit.authors,
                    "year": lit.year,
                    "domain": lit.domain,
                    "finding": lit.key_finding,
                    "retrieval_score": _round(self._score_of(self.literature_scores, i)),
                    **({"result": lit.quantitative_result} if lit.quantitative_result else {}),
                    **(
                        {"source": lit.source_location}
                        if lit.source_location and "not available" not in lit.source_location
                        else {}
                    ),
                }
                for i, lit in enumerate(self.literature)
            ],
        }
        if self.pdf_chunks:
            payload["source_excerpts"] = [
                {
                    "paper_id": self.pdf_chunk_paper_ids[i] if i < len(self.pdf_chunk_paper_ids) else None,
                    "score": _round(self._score_of(self.pdf_chunk_scores, i)),
                    "excerpt": chunk,
                }
                for i, chunk in enumerate(self.pdf_chunks)
            ]
        return json.dumps(payload, indent=indent, ensure_ascii=False)


class SecondLLMEvidence(BaseModel):
    """All retrieved and filtered evidence for a second LLM prompt call.

    Each field is a prompt-ready JSON string (or empty string if the
    source data was not available).
    """

    kb_json: str = Field(
        default="",
        description="Knowledge-base retrieval: goal mappings, parameters, context rules, literature",
    )
    simod_json: str = Field(
        default="",
        description="Filtered SIMOD baseline with bottleneck annotations",
    )
    log_json: str = Field(
        default="",
        description="Filtered log evidence relevant to the matched goals",
    )
    context_json: str = Field(
        default="",
        description="Filtered context evidence (significant relationships only)",
    )
    differentiation_briefing: str = Field(
        default="",
        description=(
            "Actionable context-differentiation briefing synthesised from "
            "KPI segment targets, statistical evidence, and KB strategies"
        ),
    )
    matched_goal_categories: list[str] = Field(default_factory=list)
    retrieval_notes: list[str] = Field(
        default_factory=list,
        description="Human-readable notes about what was retrieved and filtered",
    )

    @property
    def has_context(self) -> bool:
        return bool(self.context_json)

    @property
    def has_differentiation(self) -> bool:
        return bool(self.differentiation_briefing)


def kpi_segments_exist(kpis: list[dict[str, Any]] | None) -> bool:
    """Return True if any KPI has non-empty context_segmentation."""
    if not kpis:
        return False
    return any(kpi.get("context_segmentation") for kpi in kpis)

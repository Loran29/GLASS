"""RAG-style retrieval for the second LLM step.

Three retrieval stages, unified into one entry point:

  1. **Knowledge base** — goal-category matching + hybrid BM25/dense
     retrieval selects parameter recommendations, literature, and
     context rules.
  2. **SIMOD baseline** — filters the SIMOD-discovered model to
     prioritise parameters that the matched goal categories target.
  3. **Log evidence** — selects the log profile sections relevant to
     the KPIs (durations for time goals, rework for quality goals, etc.).

Usage::

    from knowledge import build_second_llm_evidence

    evidence = build_second_llm_evidence(
        goal_structured=first_llm_parsed["simulation_goal_structured"],
        kpis=first_llm_parsed["kpis"],
        simod_json=simod_dict,
        log_profile=log_profile_dict,
        context_profile=context_profile_dict,
    )

The body of this module is deliberately small: it orchestrates the
helpers in ``retrieval_result``, ``goal_matching``, and
``evidence_filters`` and re-exports their public API so existing
imports (``from knowledge.retrieval import …``) keep working.
"""

from __future__ import annotations

import json
import os
from typing import Any

from knowledge.evidence_filters import (
    _build_differentiation_briefing,
    _identify_bottleneck_activities,
    _identify_rework_gateways,
    filter_context_evidence,
    filter_log_evidence,
    filter_simod_baseline,
)
from knowledge.goal_matching import (
    _GOAL_KEYWORDS,
    _KPI_CATEGORY_TO_GOAL,
    _collect_referenced_literature,
    _collect_referenced_parameters,
    _match_context_rules,
    _match_goal_categories,
)
from knowledge.hybrid_retrieval import (
    HybridRetrievalResult,
    HybridRetriever,
    ScoredItem,
    build_queries_from_kpis,
    get_default_retriever,
)
from knowledge.kb_data import build_knowledge_base
from knowledge.models import (
    ContextAwareRule,
    GoalParameterMapping,
    LiteratureReference,
    SimulationParameter,
)
from knowledge.retrieval_result import (
    RetrievalResult,
    SecondLLMEvidence,
    kpi_segments_exist,
)

__all__ = [
    # Public API
    "RetrievalResult",
    "SecondLLMEvidence",
    "build_second_llm_evidence",
    "retrieve_for_second_llm",
    "kpi_segments_exist",
    "filter_simod_baseline",
    "filter_log_evidence",
    "filter_context_evidence",
    # Tested internals (kept re-exported for backwards compatibility)
    "_match_goal_categories",
    "_match_context_rules",
    "_build_differentiation_briefing",
    "_GOAL_KEYWORDS",
    "_KPI_CATEGORY_TO_GOAL",
]


# ---------------------------------------------------------------------------
# Per-kind caps for the hybrid retriever.  Chosen so the prompt stays
# compact while each knowledge kind is represented: mappings and
# literature carry the most guidance, parameters and rules provide the
# supporting taxonomy.
# ---------------------------------------------------------------------------

_DEFAULT_PER_KIND_CAPS: dict[str, int] = {
    "mapping": 6,
    "literature": 6,
    "parameter": 8,
    "rule": 4,
    "pdf_chunk": 4,
}

#: When the hybrid retriever is unavailable (import error, missing
#: embedder backends with no fallback, etc.) we fall back to the
#: category-keyword selection.  Opt out via the ``RAG_DISABLE_HYBRID``
#: environment variable (useful for regression-testing the baseline).
_HYBRID_DISABLED_ENV = "RAG_DISABLE_HYBRID"


def _split_hybrid_result(
    result: HybridRetrievalResult,
) -> tuple[
    list[GoalParameterMapping], list[float],
    list[LiteratureReference], list[float],
    list[SimulationParameter], list[float],
    list[ContextAwareRule], list[float],
    list[Any], list[float], list[int],
]:
    """Split a hybrid result into per-kind lists with aligned scores."""
    mappings: list[GoalParameterMapping] = []
    mapping_scores: list[float] = []
    literature: list[LiteratureReference] = []
    lit_scores: list[float] = []
    parameters: list[SimulationParameter] = []
    param_scores: list[float] = []
    rules: list[ContextAwareRule] = []
    rule_scores: list[float] = []
    pdf_chunks: list[Any] = []
    pdf_scores: list[float] = []
    pdf_paper_ids: list[int] = []

    for si in result.ranked_items:
        kind = si.item.kind
        if kind == "mapping":
            mappings.append(si.item.payload)
            mapping_scores.append(si.score)
        elif kind == "literature":
            literature.append(si.item.payload)
            lit_scores.append(si.score)
        elif kind == "parameter":
            parameters.append(si.item.payload)
            param_scores.append(si.score)
        elif kind == "rule":
            rules.append(si.item.payload)
            rule_scores.append(si.score)
        elif kind == "pdf_chunk":
            pdf_chunks.append(si.item.payload)
            pdf_scores.append(si.score)
            pdf_paper_ids.append(si.item.payload.paper_id)

    return (
        mappings, mapping_scores,
        literature, lit_scores,
        parameters, param_scores,
        rules, rule_scores,
        pdf_chunks, pdf_scores, pdf_paper_ids,
    )


def _per_kpi_retrieve(
    retriever: HybridRetriever,
    goal_structured: str,
    kpis: list[dict[str, Any]] | None,
    category_prior: set,
    top_k: int,
    per_kind_caps: dict[str, int],
) -> HybridRetrievalResult:
    """Run one retrieval per KPI, merge results by max score, then cap.

    Instead of fusing all KPI queries in a single RRF call (where the
    dominant KPI can dilute evidence for minority KPIs), we run a
    separate retrieval per KPI and keep the highest score any retrieval
    assigned to each item.  Per-kind caps are applied after merging so
    every KPI gets a fair chance to surface its best matches.
    """
    # Uncapped budget for individual queries — caps applied after merge.
    uncapped = {k: 9999 for k in per_kind_caps}
    large_k = max(top_k * 3, 60)

    all_queries: list[str] = []
    best: dict[str, ScoredItem] = {}  # uid → highest-scored ScoredItem
    backend = ""
    model = ""

    def _run(queries: list[str]) -> None:
        nonlocal backend, model
        if not queries:
            return
        r = retriever.retrieve(
            queries=queries,
            category_prior=category_prior or None,
            top_k=large_k,
            per_kind_caps=uncapped,
        )
        backend = r.backend_name
        model = r.model_name
        all_queries.extend(r.queries)
        for si in r.ranked_items:
            if si.item.uid not in best or si.score > best[si.item.uid].score:
                best[si.item.uid] = si

    # Goal-level query
    if goal_structured and goal_structured.strip():
        _run([goal_structured.strip()])

    # One query per KPI
    for kpi in (kpis or []):
        parts = [
            str(kpi.get(f, "")).strip()
            for f in ("name", "category", "target_direction", "process_scope")
            if str(kpi.get(f, "")).strip()
        ]
        if parts:
            _run([" ".join(parts)])

    # Merge: sort by max score, apply per-kind caps, global top-k
    merged = sorted(best.values(), key=lambda x: -x.score)
    kept: list[ScoredItem] = []
    counts: dict[str, int] = {}
    for si in merged:
        cap = per_kind_caps.get(si.item.kind)
        if cap is None or counts.get(si.item.kind, 0) < cap:
            kept.append(si)
            counts[si.item.kind] = counts.get(si.item.kind, 0) + 1
    kept = kept[:top_k]

    return HybridRetrievalResult(
        queries=all_queries,
        matched_categories=sorted(c.value for c in (category_prior or set())),
        ranked_items=kept,
        backend_name=backend,
        model_name=model,
    )


def retrieve_for_second_llm(
    goal_structured: str,
    kpis: list[dict[str, Any]] | None = None,
    context_profile: dict[str, Any] | None = None,
    *,
    top_k: int = 20,
    per_kind_caps: dict[str, int] | None = None,
    retriever: HybridRetriever | None = None,
) -> RetrievalResult:
    """Select the relevant knowledge base subset for a second LLM prompt.

    Uses the hybrid BM25 + dense retriever with per-KPI queries and a
    category-prior boost.  The previous keyword-matcher's category
    output is kept as a soft prior so category-aligned items are
    preferred on ties, and its context-rule triggering logic is
    preserved so statistically significant factors still surface the
    matching differentiation rules.
    """
    matched_categories = _match_goal_categories(goal_structured, kpis)

    triggered_rules = _match_context_rules(context_profile, kpis)

    if not os.getenv(_HYBRID_DISABLED_ENV):
        try:
            retriever = retriever or get_default_retriever()
            caps = per_kind_caps or _DEFAULT_PER_KIND_CAPS
            hybrid = _per_kpi_retrieve(
                retriever=retriever,
                goal_structured=goal_structured,
                kpis=kpis,
                category_prior=matched_categories,
                top_k=top_k,
                per_kind_caps=caps,
            )
        except Exception as exc:  # pragma: no cover — defensive
            # Any retriever/backend failure → fall back to the legacy
            # keyword-only path.  Never crash the scenario pipeline.
            import logging
            logging.getLogger(__name__).warning(
                "Hybrid retrieval failed (%s) — falling back to keyword path.", exc,
            )
        else:
            (mappings, m_scores,
             literature, l_scores,
             parameters, p_scores,
             rules, r_scores,
             pdf_chunks, pdf_scores, pdf_paper_ids) = _split_hybrid_result(hybrid)

            # Ensure context-rules triggered by actual evidence are
            # included even if retrieval missed them on text alone.
            existing_rule_ids = {r.rule_id for r in rules}
            for r in triggered_rules:
                if r.rule_id not in existing_rule_ids:
                    rules.append(r)
                    r_scores.append(0.0)

            # Backfill: if retrieval returned *no* mappings (e.g. a
            # degenerate query), keep the old category-filter so the
            # LLM still sees some guidance.  The backfill is marked in
            # the result's provenance so eval and prompt consumers can
            # distinguish it from a pure hybrid hit.
            backfilled = False
            if not mappings and matched_categories:
                backfilled = True
                kb = build_knowledge_base()
                mappings = [
                    m for m in kb.goal_mappings
                    if m.goal_category in matched_categories
                ]
                m_scores = [0.0] * len(mappings)
                if not parameters:
                    parameters = _collect_referenced_parameters(mappings, rules)
                    p_scores = [0.0] * len(parameters)
                if not literature:
                    literature = _collect_referenced_literature(mappings)
                    l_scores = [0.0] * len(literature)

            backend_label = hybrid.backend_name
            if backfilled:
                backend_label = f"{backend_label}+keyword_backfill"

            return RetrievalResult(
                goal_mappings=mappings,
                parameters=parameters,
                context_rules=rules,
                literature=literature,
                matched_goal_categories=[
                    c.value for c in sorted(matched_categories, key=lambda c: c.value)
                ],
                mapping_scores=m_scores,
                literature_scores=l_scores,
                parameter_scores=p_scores,
                rule_scores=r_scores,
                retrieval_queries=hybrid.queries,
                retrieval_backend=backend_label,
                retrieval_model=hybrid.model_name,
                backfilled=backfilled,
                pdf_chunks=[c.text for c in pdf_chunks],
                pdf_chunk_scores=pdf_scores,
                pdf_chunk_paper_ids=pdf_paper_ids,
            )

    # --- Legacy keyword-only fallback (disabled hybrid or failure) ---
    kb = build_knowledge_base()
    relevant_mappings = [
        m for m in kb.goal_mappings
        if m.goal_category in matched_categories
    ]
    parameters = _collect_referenced_parameters(relevant_mappings, triggered_rules)
    literature = _collect_referenced_literature(relevant_mappings)

    return RetrievalResult(
        goal_mappings=relevant_mappings,
        parameters=parameters,
        context_rules=triggered_rules,
        literature=literature,
        matched_goal_categories=[c.value for c in sorted(matched_categories, key=lambda c: c.value)],
        retrieval_backend="keyword",
        retrieval_model="goal_keywords",
    )


def build_second_llm_evidence(
    goal_structured: str,
    kpis: list[dict[str, Any]] | None = None,
    simod_json: dict[str, Any] | None = None,
    log_profile: dict[str, Any] | None = None,
    context_profile: dict[str, Any] | None = None,
    bpmn_xml: str = "",
) -> SecondLLMEvidence:
    """Build the complete filtered evidence package for the second LLM.

    Orchestrates all retrieval stages:

      1. Goal-category matching
      2. Knowledge-base retrieval (parameter recommendations + literature)
      3. SIMOD baseline filtering (prioritise goal-relevant parameters)
      4. Log evidence filtering (select goal-relevant profile sections)
      5. Context evidence filtering (keep only relevant relationships)
      6. Differentiation briefing synthesis

    Parameters
    ----------
    goal_structured:
        The ``simulation_goal_structured`` field from the first LLM.
    kpis:
        The verified KPI list from the first LLM (improves filtering).
    simod_json:
        Parsed SIMOD output as a dict. Can be None if not available.
    log_profile:
        The full event-log profile from ``profile_event_log()``.
    context_profile:
        The context evidence profile (from the log profile's
        ``context_profile`` key, or a standalone profile dict).
    """
    notes: list[str] = []

    # --- Stage 1: Goal-category matching ---
    matched = _match_goal_categories(goal_structured, kpis)
    cat_names = sorted(c.value for c in matched)
    notes.append(f"Matched goal categories: {', '.join(cat_names)}")

    # --- Stage 2: Knowledge-base retrieval ---
    kb_result = retrieve_for_second_llm(goal_structured, kpis, context_profile)
    kb_json = kb_result.to_prompt_json()
    notes.append(
        f"KB: {len(kb_result.goal_mappings)} mappings, "
        f"{len(kb_result.parameters)} params, "
        f"{len(kb_result.context_rules)} context rules, "
        f"{len(kb_result.literature)} papers"
        + (" [keyword backfill]" if kb_result.backfilled else "")
    )

    # --- Stage 3: SIMOD baseline filtering ---
    simod_filtered_json = ""
    if simod_json:
        filtered_simod = filter_simod_baseline(simod_json, matched, kpis, bpmn_xml=bpmn_xml)
        simod_filtered_json = json.dumps(filtered_simod, indent=2, ensure_ascii=False)

        annotations = filtered_simod.get("_annotations", {})
        bottlenecks = annotations.get("bottleneck_activities", [])
        rework = annotations.get("probable_rework_gateways", [])
        parts = [f"SIMOD: {len(filtered_simod) - (1 if annotations else 0)} sections"]
        if bottlenecks:
            parts.append(f"bottlenecks: {', '.join(bottlenecks)}")
        if rework:
            parts.append(f"rework gateways: {', '.join(rework)}")
        notes.append("; ".join(parts))
    else:
        notes.append("SIMOD: not available")

    # --- Stage 4: Log evidence filtering ---
    log_filtered_json = ""
    if log_profile:
        filtered_log = filter_log_evidence(log_profile, matched, kpis)
        log_filtered_json = json.dumps(filtered_log, indent=2, ensure_ascii=False)

        sections = [k for k in filtered_log if not k.startswith("_")]
        kpi_acts = filtered_log.get("_kpi_relevant_activities", [])
        note = f"Log: {len(sections)} sections"
        if kpi_acts:
            note += f", KPI-relevant activities: {', '.join(kpi_acts)}"
        notes.append(note)
    else:
        notes.append("Log evidence: not available")

    # --- Stage 5: Context evidence filtering ---
    context_filtered_json = ""
    effective_context = context_profile
    if not effective_context and log_profile:
        effective_context = log_profile.get("context_profile", {})

    if effective_context:
        filtered_ctx = filter_context_evidence(effective_context, matched, kpis)
        if filtered_ctx:
            sig_count = len(filtered_ctx.get("significant_relationships", []))
            context_filtered_json = json.dumps(filtered_ctx, indent=2, ensure_ascii=False)
            notes.append(f"Context: {sig_count} significant relationships")
        else:
            notes.append("Context: no significant relationships found")
    else:
        notes.append("Context evidence: not available")

    # --- Stage 6: Differentiation briefing ---
    diff_briefing = ""
    filtered_ctx_dict = None
    if context_filtered_json:
        try:
            filtered_ctx_dict = json.loads(context_filtered_json)
        except (json.JSONDecodeError, TypeError):
            pass
    if filtered_ctx_dict or kpi_segments_exist(kpis):
        diff_briefing = _build_differentiation_briefing(
            kpis=kpis,
            context_filtered=filtered_ctx_dict,
            kb_context_rules=kb_result.context_rules,
        )
        if diff_briefing:
            factor_count = diff_briefing.count("### Factor:")
            notes.append(
                f"Differentiation briefing: {factor_count} factor(s) "
                f"with actionable instructions"
            )

    return SecondLLMEvidence(
        kb_json=kb_json,
        simod_json=simod_filtered_json,
        log_json=log_filtered_json,
        context_json=context_filtered_json,
        differentiation_briefing=diff_briefing,
        matched_goal_categories=cat_names,
        retrieval_notes=notes,
    )

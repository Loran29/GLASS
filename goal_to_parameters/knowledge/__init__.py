"""Parameter knowledge base for the second LLM step.

This package restructures the literature-derived goal-to-parameter
mappings from the baseline repository (muruvetg/from-simulation-goals-
to-parameters) into a typed, queryable knowledge base with:

  - **Literature references** — academic papers with quantitative evidence
  - **Parameter taxonomy** — simulation parameter types with SIMOD
    cross-references and constraints
  - **Goal-to-parameter mappings** — which parameter changes address which
    simulation goals, with paper attribution
  - **Context-aware rules** — thesis extension: when to differentiate
    parameters based on statistically significant context factors
  - **Retrieval** — RAG-style selection of the relevant knowledge subset
    for inclusion in the second LLM prompt

Quick start::

    from knowledge import retrieve_for_second_llm

    result = retrieve_for_second_llm(
        goal_structured="Minimise cycle time ...",
        kpis=[...],
        context_profile={...},
    )
    prompt_json = result.to_prompt_json()
"""

from knowledge.embeddings import (
    Embedder,
    OllamaEmbedder,
    SentenceTransformersEmbedder,
    TfidfEmbedder,
    build_default_embedder,
)
from knowledge.hybrid_retrieval import (
    HybridRetrievalResult,
    HybridRetriever,
    KBItem,
    ScoredItem,
    build_kb_items,
    build_kb_items_with_pdfs,
    build_queries_from_kpis,
    get_default_retriever,
)
from knowledge.kb_data import build_knowledge_base
from knowledge.cross_encoder import CrossEncoderReranker, get_default_reranker
from knowledge.pdf_indexer import PDFChunk, load_pdf_chunks
from knowledge.models import (
    ChangeDirection,
    ContextAwareRule,
    ContextFactorScope,
    GoalCategory,
    GoalParameterMapping,
    LiteratureReference,
    ParameterCategory,
    ParameterChange,
    ParameterKnowledgeBase,
    SimodFieldMapping,
    SimulationParameter,
)
from knowledge.retrieval import (
    RetrievalResult,
    SecondLLMEvidence,
    build_second_llm_evidence,
    kpi_segments_exist,
    retrieve_for_second_llm,
)

__all__ = [
    # Data
    "build_knowledge_base",
    # Models
    "ChangeDirection",
    "ContextAwareRule",
    "ContextFactorScope",
    "GoalCategory",
    "GoalParameterMapping",
    "LiteratureReference",
    "ParameterCategory",
    "ParameterChange",
    "ParameterKnowledgeBase",
    "SimodFieldMapping",
    "SimulationParameter",
    # Retrieval
    "RetrievalResult",
    "SecondLLMEvidence",
    "build_second_llm_evidence",
    "kpi_segments_exist",
    "retrieve_for_second_llm",
    # Hybrid RAG
    "Embedder",
    "HybridRetrievalResult",
    "HybridRetriever",
    "KBItem",
    "OllamaEmbedder",
    "ScoredItem",
    "SentenceTransformersEmbedder",
    "TfidfEmbedder",
    "build_default_embedder",
    "build_kb_items",
    "build_kb_items_with_pdfs",
    "build_queries_from_kpis",
    "get_default_retriever",
    # PDF indexing + cross-encoder
    "CrossEncoderReranker",
    "PDFChunk",
    "get_default_reranker",
    "load_pdf_chunks",
]

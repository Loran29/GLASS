"""Hybrid dense + lexical retrieval over the parameter knowledge base.

Combines three signals and fuses them with Reciprocal Rank Fusion
(Cormack et al., 2009):

  1. **Dense similarity** — cosine similarity between the query
     embedding and every item embedding.  Captures paraphrase and
     semantic overlap (e.g. "speed up" ↔ "reduce waiting time").
  2. **BM25 lexical score** — Okapi BM25 over the same item corpus.
     Catches exact-term matches (activity names, KPI vocabulary)
     where dense models generalise away useful specificity.
  3. **Category prior** — small boost for items whose goal category
     matches the keyword-derived category set (the previous
     retrieval signal is kept as a soft prior, not a hard gate).

Five item types are indexed:

  - :class:`GoalParameterMapping` — the literature-derived mappings.
  - :class:`LiteratureReference` — individual papers.
  - :class:`SimulationParameter` — taxonomy entries.
  - :class:`ContextAwareRule` — thesis extension.
  - :class:`PDFChunk` — full-text chunks from the academic PDFs.

Retrieval is executed **per query**: the structured goal produces one
query; each verified KPI (name + formula + direction) produces another.
Per-query rankings are fused by RRF and the top-k surface into the
prompt with their scores so the LLM can weigh highly-ranked evidence
more heavily than tangential hits.

After RRF fusion, an optional cross-encoder re-ranker can blend a
joint query↔document score (sigmoid-normalised) into the final score.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

import numpy as np

from knowledge.bm25 import BM25Index
from knowledge.embeddings import Embedder, build_default_embedder, tokenize
from knowledge.kb_data import build_knowledge_base
from knowledge.models import (
    ContextAwareRule,
    GoalCategory,
    GoalParameterMapping,
    LiteratureReference,
    ParameterKnowledgeBase,
    SimulationParameter,
)

logger = logging.getLogger(__name__)


# ===================================================================
# Indexable item
# ===================================================================

ItemKind = Literal["mapping", "literature", "parameter", "rule", "pdf_chunk"]


@dataclass(frozen=True)
class KBItem:
    """A single retrievable entry in the knowledge base.

    ``payload`` is the underlying Pydantic object; ``text`` is the
    concatenated searchable representation.  ``categories`` carries
    the goal categories this item belongs to (empty for items with no
    category affinity, e.g. generic parameter definitions).
    """

    uid: str
    kind: ItemKind
    text: str
    categories: frozenset[GoalCategory]
    payload: Any = field(repr=False)


# ===================================================================
# Indexing: flatten the KB into KBItems
# ===================================================================

def _mapping_text(m: GoalParameterMapping) -> str:
    parts = [m.goal_description, f"domain: {m.domain}", f"category: {m.goal_category.value}"]
    for c in m.parameter_changes:
        parts.append(
            f"{c.parameter_name} {c.direction.value}: {c.rationale}"
        )
        if c.quantitative_evidence:
            parts.append(c.quantitative_evidence)
    if m.notes:
        parts.append(m.notes)
    return " ".join(parts)


def _literature_text(lit: LiteratureReference) -> str:
    parts = [
        lit.title,
        lit.key_finding,
        f"domain: {lit.domain}",
        "parameters: " + ", ".join(lit.parameters_tested),
    ]
    if lit.quantitative_result:
        parts.append(lit.quantitative_result)
    return " ".join(parts)


def _parameter_text(p: SimulationParameter) -> str:
    parts = [p.name.replace("_", " "), p.description]
    if p.examples:
        parts.append("examples: " + "; ".join(p.examples))
    if p.constraints:
        parts.append(f"constraints: {p.constraints}")
    return " ".join(parts)


def _rule_text(r: ContextAwareRule) -> str:
    return " ".join([
        r.rule_id,
        r.description,
        r.rationale,
        f"scope: {r.trigger_factor_scope.value}",
        f"triggers: {', '.join(r.trigger_factor_examples)}",
        f"strategy: {r.differentiation_strategy}",
    ])


# Map literature to its cited goal categories (indirectly, via mappings).
def _lit_categories(
    kb: ParameterKnowledgeBase,
) -> dict[int, frozenset[GoalCategory]]:
    out: dict[int, set[GoalCategory]] = {}
    for m in kb.goal_mappings:
        for c in m.parameter_changes:
            for pid in c.paper_ids:
                out.setdefault(pid, set()).add(m.goal_category)
    return {pid: frozenset(cats) for pid, cats in out.items()}


# Parameter names → the categories that reference them (via mappings).
def _param_categories(
    kb: ParameterKnowledgeBase,
) -> dict[str, frozenset[GoalCategory]]:
    out: dict[str, set[GoalCategory]] = {}
    for m in kb.goal_mappings:
        for c in m.parameter_changes:
            out.setdefault(c.parameter_name, set()).add(m.goal_category)
    return {name: frozenset(cats) for name, cats in out.items()}


def build_kb_items(kb: ParameterKnowledgeBase | None = None) -> list[KBItem]:
    """Flatten the KB into a list of indexable items."""
    if kb is None:
        kb = build_knowledge_base()

    items: list[KBItem] = []

    for i, m in enumerate(kb.goal_mappings):
        items.append(KBItem(
            uid=f"mapping:{i}",
            kind="mapping",
            text=_mapping_text(m),
            categories=frozenset({m.goal_category}),
            payload=m,
        ))

    lit_cats = _lit_categories(kb)
    for lit in kb.literature:
        items.append(KBItem(
            uid=f"literature:{lit.paper_id}",
            kind="literature",
            text=_literature_text(lit),
            categories=lit_cats.get(lit.paper_id, frozenset()),
            payload=lit,
        ))

    param_cats = _param_categories(kb)
    for p in kb.parameters:
        items.append(KBItem(
            uid=f"parameter:{p.name}",
            kind="parameter",
            text=_parameter_text(p),
            categories=param_cats.get(p.name, frozenset()),
            payload=p,
        ))

    for r in kb.context_rules:
        items.append(KBItem(
            uid=f"rule:{r.rule_id}",
            kind="rule",
            text=_rule_text(r),
            categories=frozenset(),
            payload=r,
        ))

    return items


def _pdf_chunk_text(chunk: Any) -> str:
    return chunk.kb_text


def build_kb_items_with_pdfs(
    kb: ParameterKnowledgeBase | None = None,
    pdf_chunks: list[Any] | None = None,
) -> list[KBItem]:
    """Return KB items extended with PDF chunk items.

    Parameters
    ----------
    kb:
        The structured knowledge base.  Defaults to ``build_knowledge_base()``.
    pdf_chunks:
        List of :class:`~knowledge.pdf_indexer.PDFChunk` objects.
        If None or empty, returns the same result as ``build_kb_items()``.
    """
    items = build_kb_items(kb)
    if not pdf_chunks:
        return items

    for chunk in pdf_chunks:
        items.append(KBItem(
            uid=f"pdf:{chunk.paper_id}:{chunk.chunk_index}",
            kind="pdf_chunk",
            text=_pdf_chunk_text(chunk),
            categories=frozenset(),
            payload=chunk,
        ))

    return items
# ===================================================================

def _dense_scores(
    query_vec: np.ndarray,
    doc_matrix: np.ndarray,
) -> np.ndarray:
    """Cosine similarity (== dot product of L2-normalised vectors)."""
    if doc_matrix.shape[0] == 0 or query_vec.shape[0] == 0:
        return np.zeros(doc_matrix.shape[0], dtype=np.float32)

    # Align dims when the fallback TF-IDF embedder produced a query
    # from a *refit* (different vocab size) — in practice the same
    # embedder is reused for both sides, so this is a safety net.
    if doc_matrix.shape[1] != query_vec.shape[0]:
        d = min(doc_matrix.shape[1], query_vec.shape[0])
        return doc_matrix[:, :d] @ query_vec[:d]
    return doc_matrix @ query_vec


def _ranks_from_scores(scores: np.ndarray | list[float]) -> np.ndarray:
    """Return 1-based dense ranks (higher score = better = smaller rank)."""
    arr = np.asarray(scores, dtype=np.float32)
    if arr.size == 0:
        return arr.astype(np.int32)
    # argsort descending, then invert to get each item's rank.
    order = np.argsort(-arr, kind="stable")
    ranks = np.empty_like(order)
    ranks[order] = np.arange(1, arr.size + 1)
    return ranks.astype(np.int32)


def _rrf_fuse(
    *rank_arrays: np.ndarray,
    k: int = 60,
) -> np.ndarray:
    """Reciprocal Rank Fusion over multiple rank arrays.

    RRF(d) = Σ_i  1 / (k + rank_i(d))

    ``k=60`` is the standard default from the original paper; it
    prevents top-1 from dominating when one ranker is noisy.
    """
    if not rank_arrays:
        return np.zeros(0, dtype=np.float32)
    fused = np.zeros(rank_arrays[0].shape[0], dtype=np.float32)
    for ranks in rank_arrays:
        fused += 1.0 / (k + ranks.astype(np.float32))
    return fused


# ===================================================================
# Retrieval result
# ===================================================================

@dataclass
class ScoredItem:
    """One retrieved item with its fused RRF score."""

    item: KBItem
    score: float
    dense_score: float = 0.0
    bm25_score: float = 0.0
    category_boost: float = 0.0


@dataclass
class HybridRetrievalResult:
    """Full retrieval output for one or more queries."""

    queries: list[str] = field(default_factory=list)
    matched_categories: list[str] = field(default_factory=list)
    ranked_items: list[ScoredItem] = field(default_factory=list)
    backend_name: str = ""
    model_name: str = ""

    # --- Convenience accessors, split by item kind -----------------
    def by_kind(self, kind: ItemKind, top_k: int | None = None) -> list[ScoredItem]:
        filtered = [si for si in self.ranked_items if si.item.kind == kind]
        return filtered[:top_k] if top_k else filtered

    @property
    def mappings(self) -> list[GoalParameterMapping]:
        return [si.item.payload for si in self.by_kind("mapping")]

    @property
    def literature(self) -> list[LiteratureReference]:
        return [si.item.payload for si in self.by_kind("literature")]

    @property
    def parameters(self) -> list[SimulationParameter]:
        return [si.item.payload for si in self.by_kind("parameter")]

    @property
    def context_rules(self) -> list[ContextAwareRule]:
        return [si.item.payload for si in self.by_kind("rule")]


# ===================================================================
# Hybrid retriever
# ===================================================================

class HybridRetriever:
    """BM25 + dense + RRF over the parameter knowledge base.

    Construct once and reuse — the embedding matrix and BM25 index
    are built lazily on first query and cached on disk
    (see :mod:`knowledge.embeddings`).
    """

    #: Multiplicative contribution of the category prior to the fused
    #: score.  Small enough that semantic/lexical signal dominates but
    #: meaningful enough to break ties in favour of the right category.
    CATEGORY_BOOST_WEIGHT: float = 0.25

    def __init__(
        self,
        items: list[KBItem] | None = None,
        *,
        embedder: Embedder | None = None,
        reranker: Any | None = None,
    ) -> None:
        self.items: list[KBItem] = items if items is not None else build_kb_items()
        self._embedder = embedder  # lazy
        self._reranker = reranker  # optional CrossEncoderReranker
        self._doc_matrix: np.ndarray | None = None
        self._bm25: BM25Index | None = None

    # -----------------------------------------------------------------
    # Lazy index construction
    # -----------------------------------------------------------------
    def _ensure_indexed(self) -> None:
        if self._doc_matrix is not None and self._bm25 is not None:
            return

        texts = [item.text for item in self.items]

        if self._embedder is None:
            self._embedder = build_default_embedder()

        # TF-IDF needs fitting on the corpus before encoding queries;
        # encode_corpus_cached() triggers _fit() internally via encode().
        self._doc_matrix = self._embedder.encode_corpus_cached(texts)
        self._bm25 = BM25Index(texts)
        logger.debug(
            "Hybrid index ready: %d items, embedder=%s/%s, dim=%d",
            len(self.items),
            self._embedder.backend_name,
            self._embedder.model_name,
            self._doc_matrix.shape[1] if self._doc_matrix.size else 0,
        )

    # -----------------------------------------------------------------
    # Single-query retrieval
    # -----------------------------------------------------------------
    def _rank_one_query(
        self,
        query: str,
        category_prior: set[GoalCategory] | None,
    ) -> np.ndarray:
        """Return the fused RRF score for every item, for one query."""
        self._ensure_indexed()
        assert self._embedder is not None and self._doc_matrix is not None
        assert self._bm25 is not None

        if not query.strip():
            return np.zeros(len(self.items), dtype=np.float32)

        # 1. Dense scores.  For TF-IDF we re-use the already-fitted
        #    embedder so the vocab matches; encode() on a singleton
        #    list does the right thing.
        q_vec = self._embedder.encode([query])
        dense = _dense_scores(q_vec[0], self._doc_matrix)

        # 2. BM25 scores.
        bm25 = np.asarray(self._bm25.score(query), dtype=np.float32)

        # 3. Rank each signal independently, then RRF-fuse.
        dense_ranks = _ranks_from_scores(dense)
        bm25_ranks = _ranks_from_scores(bm25)
        fused = _rrf_fuse(dense_ranks, bm25_ranks)

        # 4. Category prior — add a small boost for category-matching
        #    items.  Items with no category affinity are not penalised.
        if category_prior:
            for i, item in enumerate(self.items):
                if item.categories and item.categories & category_prior:
                    fused[i] += self.CATEGORY_BOOST_WEIGHT * (1.0 / (60 + 1))
        return fused

    # -----------------------------------------------------------------
    # Multi-query retrieval (aggregates with RRF across queries)
    # -----------------------------------------------------------------
    def retrieve(
        self,
        queries: list[str],
        *,
        category_prior: set[GoalCategory] | None = None,
        top_k: int = 15,
        per_kind_caps: dict[str, int] | None = None,
    ) -> HybridRetrievalResult:
        """Run multi-query hybrid retrieval and return the top-k items.

        Parameters
        ----------
        queries:
            One or more free-text queries.  Typically the structured
            goal plus one query per verified KPI.
        category_prior:
            Optional set of :class:`GoalCategory` values from the
            existing keyword-based matcher.  Used as a soft boost,
            not a hard filter.
        top_k:
            Global cap on the combined ranked list.
        per_kind_caps:
            Optional per-kind caps, e.g. ``{"mapping": 6,
            "literature": 5, "parameter": 8, "rule": 4}``.  Applied
            after the global ranking so each kind is represented.
        """
        self._ensure_indexed()
        assert self._embedder is not None

        non_empty = [q for q in queries if q and q.strip()]
        if not non_empty:
            return HybridRetrievalResult(
                queries=[],
                matched_categories=sorted(c.value for c in (category_prior or set())),
                backend_name=self._embedder.backend_name,
                model_name=self._embedder.model_name,
            )

        # For each query, get a fused-rank array; then aggregate the
        # per-query arrays with a second round of RRF across queries.
        per_query_scores = [
            self._rank_one_query(q, category_prior) for q in non_empty
        ]
        per_query_ranks = [_ranks_from_scores(s) for s in per_query_scores]
        multi_query_scores = _rrf_fuse(*per_query_ranks)

        # Per-signal diagnostic scores for the top items (nice for the
        # prompt): reuse the *first* query's dense/bm25 scores as the
        # representative tuple — cheap and interpretable.
        self._ensure_indexed()
        assert self._doc_matrix is not None and self._bm25 is not None
        q_vec = self._embedder.encode([non_empty[0]])
        diag_dense = _dense_scores(q_vec[0], self._doc_matrix)
        diag_bm25 = np.asarray(self._bm25.score(non_empty[0]), dtype=np.float32)

        order = np.argsort(-multi_query_scores, kind="stable")
        all_ranked: list[ScoredItem] = []
        for idx in order:
            item = self.items[int(idx)]
            boost = 0.0
            if category_prior and item.categories and item.categories & category_prior:
                boost = self.CATEGORY_BOOST_WEIGHT / 61.0
            all_ranked.append(ScoredItem(
                item=item,
                score=float(multi_query_scores[idx]),
                dense_score=float(diag_dense[idx]),
                bm25_score=float(diag_bm25[idx]),
                category_boost=boost,
            ))

        # Cross-encoder re-ranking: blend sigmoid(ce_score) with RRF score
        # on the top-30 candidates before applying per-kind caps.
        if self._reranker is not None and self._reranker.is_available() and all_ranked:
            import math
            top_n = min(30, len(all_ranked))
            texts = [si.item.text for si in all_ranked[:top_n]]
            ce_scores = self._reranker.rerank(non_empty[0], texts)
            ce_norm = [1.0 / (1.0 + math.exp(-s)) for s in ce_scores]
            for i in range(top_n):
                si = all_ranked[i]
                blended = 0.5 * si.score + 0.5 * ce_norm[i]
                all_ranked[i] = ScoredItem(si.item, blended, si.dense_score, si.bm25_score, si.category_boost)
            all_ranked[:top_n] = sorted(all_ranked[:top_n], key=lambda x: -x.score)

        # Apply per-kind caps while preserving the global order.
        caps = per_kind_caps or {}
        if caps:
            kept: list[ScoredItem] = []
            counts: dict[str, int] = {}
            for si in all_ranked:
                cap = caps.get(si.item.kind)
                if cap is None:
                    kept.append(si)
                    continue
                if counts.get(si.item.kind, 0) < cap:
                    kept.append(si)
                    counts[si.item.kind] = counts.get(si.item.kind, 0) + 1
            all_ranked = kept

        # Global top-k (after per-kind caps so each kind is represented).
        ranked = all_ranked[: max(top_k, 0)]

        return HybridRetrievalResult(
            queries=non_empty,
            matched_categories=sorted(c.value for c in (category_prior or set())),
            ranked_items=ranked,
            backend_name=self._embedder.backend_name,
            model_name=self._embedder.model_name,
        )


# ===================================================================
# Query construction from KPIs
# ===================================================================

def build_queries_from_kpis(
    goal_structured: str,
    kpis: list[dict[str, Any]] | None,
) -> list[str]:
    """Build the per-KPI query list.

    The structured goal becomes the first query.  Each KPI contributes
    one query built from ``name``, ``suggested_formula``,
    ``target_direction``, ``process_scope``.  This makes retrieval
    per-KPI rather than one coarse match against the whole goal.
    """
    queries: list[str] = []
    if goal_structured and goal_structured.strip():
        queries.append(goal_structured.strip())

    for kpi in kpis or []:
        name = str(kpi.get("name", "")).strip()
        formula = str(kpi.get("suggested_formula", "")).strip()
        direction = str(kpi.get("target_direction", "")).strip()
        scope = str(kpi.get("process_scope", "")).strip()
        category = str(kpi.get("category", "")).strip()

        parts = [p for p in (name, category, direction, scope, formula) if p]
        if parts:
            queries.append(" ".join(parts))

    return queries


# ===================================================================
# Module-level singleton (lazy)
# ===================================================================

_default_retriever: HybridRetriever | None = None


def get_default_retriever() -> HybridRetriever:
    """Return a process-wide :class:`HybridRetriever` singleton.

    Builds KB items extended with PDF full-text chunks and wires in the
    cross-encoder re-ranker.  Falls back gracefully if PDF indexing or
    the cross-encoder are unavailable.
    """
    global _default_retriever
    if _default_retriever is None:
        from pathlib import Path

        from knowledge.cross_encoder import get_default_reranker
        from knowledge.pdf_indexer import load_pdf_chunks

        kb = build_knowledge_base()
        papers_dir = Path(__file__).parent.parent.parent / "Papers" / "CaseStudy"
        pdf_chunks = load_pdf_chunks(papers_dir, kb.literature)
        items = build_kb_items_with_pdfs(kb, pdf_chunks)

        _default_retriever = HybridRetriever(items=items, reranker=get_default_reranker())
    return _default_retriever


__all__ = [
    "HybridRetrievalResult",
    "HybridRetriever",
    "KBItem",
    "ScoredItem",
    "build_kb_items",
    "build_kb_items_with_pdfs",
    "build_queries_from_kpis",
    "get_default_retriever",
]

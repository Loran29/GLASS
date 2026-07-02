"""Tests for the hybrid BM25 + dense retriever.

Covers:
  - BM25 scoring correctness on a trivial corpus.
  - TF-IDF embedder returning L2-normalised vectors + cached reuse.
  - KB item flattening (all four kinds present, literature categories
    derived from mappings).
  - Per-KPI query construction from first-LLM output shape.
  - End-to-end hybrid retrieval returns items in score order and
    respects per-kind caps.
  - retrieve_for_second_llm surfaces retrieval provenance (scores,
    queries, backend) and degrades cleanly when the hybrid path is
    disabled.
  - Context-rule triggering via statistical evidence is preserved
    when the hybrid path replaces the keyword path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from knowledge.bm25 import BM25Index
from knowledge.embeddings import TfidfEmbedder, tokenize
from knowledge.hybrid_retrieval import (
    HybridRetriever,
    build_kb_items,
    build_queries_from_kpis,
    get_default_retriever,
)
from knowledge.models import ContextFactorScope, GoalCategory
from knowledge.retrieval import retrieve_for_second_llm


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------

class TestBM25:
    def test_exact_term_match_wins(self):
        docs = [
            "the quick brown fox jumps over the lazy dog",
            "simulation reduces patient waiting time",
            "loan approval process redesign",
        ]
        index = BM25Index(docs)
        scores = index.score("loan approval")
        assert scores[2] > scores[0]
        assert scores[2] > scores[1]

    def test_empty_query_returns_zeros(self):
        index = BM25Index(["hello world"])
        assert index.score("") == [0.0]

    def test_stopwords_dont_dominate(self):
        docs = ["the the the the the", "loan approval document review"]
        index = BM25Index(docs)
        # "the" is a stopword -> tokenised away, "loan" hits doc 2
        scores = index.score("the loan")
        assert scores[1] > scores[0]

    def test_rare_terms_score_higher(self):
        docs = [
            "waiting time waiting time waiting time",
            "rework rework rework",
            "waiting rework",
        ]
        index = BM25Index(docs)
        # doc 2 matches both; doc 1 only "rework"; doc 0 only "waiting"
        scores = index.score("waiting rework")
        assert scores[2] > scores[0]
        assert scores[2] > scores[1]


# ---------------------------------------------------------------------------
# TF-IDF embedder
# ---------------------------------------------------------------------------

class TestTfidfEmbedder:
    def test_tokenize_drops_stopwords_and_numbers(self):
        toks = tokenize("The 2024 SIMOD model reduces WAITING time by 37%")
        # lowercased, letter-leading, stopwords gone
        assert "the" not in toks
        assert "simod" in toks
        assert "waiting" in toks
        # pure numbers dropped
        assert "2024" not in toks
        assert "37" not in toks

    def test_encode_returns_l2_normalised(self):
        emb = TfidfEmbedder()
        docs = ["loan approval process", "patient waiting time", "CNC manufacturing line"]
        mat = emb.encode(docs)
        assert mat.shape[0] == 3
        norms = np.linalg.norm(mat, axis=1)
        np.testing.assert_allclose(norms, np.ones_like(norms), atol=1e-5)

    def test_cosine_similarity_paraphrase(self):
        emb = TfidfEmbedder()
        mat = emb.encode([
            "reduce patient waiting time in emergency department",
            "emergency department waiting time reduction for patients",
            "manufacturing CNC capacity increase",
        ])
        sim_01 = float(mat[0] @ mat[1])
        sim_02 = float(mat[0] @ mat[2])
        # Paraphrase must score higher than the unrelated doc.
        assert sim_01 > sim_02


# ---------------------------------------------------------------------------
# KB item flattening
# ---------------------------------------------------------------------------

class TestKBItems:
    def test_all_kinds_present(self):
        items = build_kb_items()
        kinds = {item.kind for item in items}
        assert {"mapping", "literature", "parameter", "rule"} <= kinds

    def test_literature_inherits_categories_from_mappings(self):
        items = build_kb_items()
        lit_items = [i for i in items if i.kind == "literature"]
        # Papers referenced by any waiting-time mapping should carry
        # WAITING_TIME as one of their categories.
        waiting_lits = [
            i for i in lit_items
            if GoalCategory.WAITING_TIME in i.categories
        ]
        assert waiting_lits, "expected at least one literature item tagged waiting_time"

    def test_uids_unique(self):
        items = build_kb_items()
        uids = [i.uid for i in items]
        assert len(uids) == len(set(uids))


# ---------------------------------------------------------------------------
# Per-KPI query construction
# ---------------------------------------------------------------------------

class TestQueryBuilder:
    def test_goal_always_first(self):
        queries = build_queries_from_kpis("reduce waiting time", kpis=[])
        assert queries == ["reduce waiting time"]

    def test_one_query_per_kpi(self):
        kpis = [
            {"name": "Cycle time", "category": "time", "target_direction": "minimize",
             "process_scope": "end-to-end", "suggested_formula": "avg(end - start)"},
            {"name": "Cost per case", "category": "cost", "target_direction": "minimize",
             "process_scope": "end-to-end", "suggested_formula": "sum(hours * rate)"},
        ]
        queries = build_queries_from_kpis("reduce cycle time and cost", kpis=kpis)
        assert len(queries) == 3  # goal + 2 KPIs
        assert "Cycle time" in queries[1]
        assert "Cost per case" in queries[2]

    def test_empty_goal_and_kpis_returns_empty(self):
        assert build_queries_from_kpis("", kpis=None) == []
        assert build_queries_from_kpis("  ", kpis=[]) == []


# ---------------------------------------------------------------------------
# Hybrid retriever end-to-end
# ---------------------------------------------------------------------------

class TestHybridRetriever:
    def test_retrieve_orders_by_score(self):
        retriever = HybridRetriever()
        result = retriever.retrieve(
            queries=["reduce patient waiting time in emergency department"],
            top_k=10,
        )
        scores = [si.score for si in result.ranked_items]
        assert scores == sorted(scores, reverse=True)

    def test_loan_query_finds_loan_paper(self):
        """The semantic test that motivates the upgrade: 'loan approval'
        was NOT a keyword in the legacy list — hybrid must still find
        paper 22 (Pihir 2010, loan-application process)."""
        retriever = HybridRetriever()
        result = retriever.retrieve(
            queries=["speed up the loan approval process"],
            top_k=30,
            per_kind_caps={"mapping": 0, "parameter": 0, "rule": 0, "literature": 30},
        )
        paper_ids = [si.item.payload.paper_id for si in result.ranked_items]
        assert 22 in paper_ids[:5], f"expected paper 22 in top-5, got {paper_ids[:5]}"

    def test_per_kind_caps_enforced(self):
        retriever = HybridRetriever()
        result = retriever.retrieve(
            queries=["reduce waiting time"],
            top_k=20,
            per_kind_caps={"mapping": 2, "literature": 2, "parameter": 0, "rule": 0},
        )
        counts: dict[str, int] = {}
        for si in result.ranked_items:
            counts[si.item.kind] = counts.get(si.item.kind, 0) + 1
        assert counts.get("mapping", 0) <= 2
        assert counts.get("literature", 0) <= 2
        assert counts.get("parameter", 0) == 0
        assert counts.get("rule", 0) == 0

    def test_empty_queries_yield_empty_result(self):
        retriever = HybridRetriever()
        result = retriever.retrieve(queries=[""], top_k=10)
        assert result.ranked_items == []

    def test_category_prior_boosts_category_matches(self):
        retriever = HybridRetriever()
        # Without any category prior
        no_prior = retriever.retrieve(
            queries=["improve operations"],
            category_prior=None,
            top_k=50,
        )
        # With a quality-compliance prior
        with_prior = retriever.retrieve(
            queries=["improve operations"],
            category_prior={GoalCategory.QUALITY_COMPLIANCE},
            top_k=50,
        )

        def score_of(uid: str, items) -> float:
            for si in items:
                if si.item.uid == uid:
                    return si.score
            return 0.0

        # Find a mapping whose sole category is quality_compliance.
        q_items = [
            si for si in with_prior.ranked_items
            if si.item.kind == "mapping"
            and GoalCategory.QUALITY_COMPLIANCE in si.item.categories
        ]
        assert q_items, "expected at least one quality mapping in results"
        uid = q_items[0].item.uid
        assert score_of(uid, with_prior.ranked_items) >= score_of(uid, no_prior.ranked_items)


# ---------------------------------------------------------------------------
# retrieve_for_second_llm integration
# ---------------------------------------------------------------------------

class TestRetrieveForSecondLLM:
    def test_returns_provenance(self):
        res = retrieve_for_second_llm(
            goal_structured="speed up loan approval process",
            kpis=[{"name": "Cycle time", "category": "time",
                   "target_direction": "minimize", "process_scope": "e2e"}],
        )
        assert res.retrieval_backend, "backend name must be set"
        assert res.retrieval_queries, "queries must be surfaced"
        # Scores align with list lengths.
        assert len(res.mapping_scores) == len(res.goal_mappings)
        assert len(res.literature_scores) == len(res.literature)

    def test_to_prompt_json_includes_scores(self):
        res = retrieve_for_second_llm(
            goal_structured="reduce patient waiting time",
            kpis=[{"name": "Waiting", "category": "time",
                   "target_direction": "minimize", "process_scope": "ED"}],
        )
        payload = res.to_prompt_json(compact=False)
        assert "retrieval_score" in payload
        assert "hybrid BM25 + dense" in payload

    def test_disabled_falls_back_to_keyword(self):
        os.environ["RAG_DISABLE_HYBRID"] = "1"
        try:
            res = retrieve_for_second_llm(
                goal_structured="reduce patient waiting time",
                kpis=[{"name": "Waiting", "category": "time",
                       "target_direction": "minimize", "process_scope": "ED"}],
            )
        finally:
            os.environ.pop("RAG_DISABLE_HYBRID", None)
        assert res.retrieval_backend == "keyword"
        # Category match should still populate mappings.
        assert res.goal_mappings

    def test_context_rules_still_triggered_from_statistical_evidence(self):
        """When the log profile shows a significant case-level factor,
        the matching context rule must be in the result even if the
        retriever didn't surface it on text alone."""
        context_profile = {
            "significant_relationships": [
                {"factor": "customer_tier", "metric": "cycle_time",
                 "adjusted_p_value": 0.001, "effect_size": 0.6,
                 "segments": {"premium": 3.2, "standard": 6.1}},
            ],
            "detected_factors": [
                {"name": "customer_tier", "scope": "case_level"},
            ],
        }
        res = retrieve_for_second_llm(
            goal_structured="reduce cycle time by segmenting customer tiers",
            kpis=[{"name": "Cycle time", "category": "time",
                   "target_direction": "minimize", "process_scope": "end-to-end"}],
            context_profile=context_profile,
        )
        assert res.context_rules, "expected at least one context rule to fire"
        scopes = {r.trigger_factor_scope for r in res.context_rules}
        assert ContextFactorScope.CASE_LEVEL in scopes


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

def test_default_retriever_is_singleton():
    r1 = get_default_retriever()
    r2 = get_default_retriever()
    assert r1 is r2

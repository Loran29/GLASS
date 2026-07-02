"""Standalone test for PDF indexing + cross-encoder re-ranking.

Run from goal_to_parameters/ directory:
    python knowledge/test_rag_improvements.py
"""

from __future__ import annotations

import sys
import os

# Ensure goal_to_parameters/ is on the path when run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def separator(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def test_pdf_chunks() -> list:
    separator("Test 1 — PDF chunks loaded")
    from pathlib import Path
    from knowledge.kb_data import build_knowledge_base
    from knowledge.pdf_indexer import load_pdf_chunks

    kb = build_knowledge_base()
    papers_dir = Path(__file__).parent.parent.parent / "Papers" / "CaseStudy"
    print(f"Papers directory: {papers_dir}")
    print(f"Directory exists: {papers_dir.exists()}")

    chunks = load_pdf_chunks(papers_dir, kb.literature)
    print(f"\nTotal chunks loaded: {len(chunks)}")

    by_paper: dict[int, int] = {}
    for c in chunks:
        by_paper[c.paper_id] = by_paper.get(c.paper_id, 0) + 1

    if by_paper:
        print(f"\nChunks per paper:")
        for pid in sorted(by_paper):
            title_short = next((lit.title[:50] for lit in kb.literature if lit.paper_id == pid), "?")
            print(f"  Paper {pid:2d}: {by_paper[pid]:4d} chunks  ({title_short})")
    else:
        print("  No chunks loaded. Check that Papers/CaseStudy/ exists and contains PDFs.")

    return chunks


def test_query_comparison(pdf_chunks: list) -> None:
    separator("Test 2 — Query comparison")
    from knowledge.hybrid_retrieval import (
        build_kb_items_with_pdfs,
        build_queries_from_kpis,
        HybridRetriever,
    )
    from knowledge.kb_data import build_knowledge_base
    from knowledge.cross_encoder import get_default_reranker

    kb = build_knowledge_base()
    items = build_kb_items_with_pdfs(kb, pdf_chunks)
    reranker = get_default_reranker()
    retriever = HybridRetriever(items=items, reranker=reranker)

    print(f"Cross-encoder available: {'YES' if reranker.is_available() else 'NO'}")
    print(f"Total indexed items: {len(items)} "
          f"(of which {sum(1 for i in items if i.kind == 'pdf_chunk')} PDF chunks)")

    queries = [
        "reduce patient waiting time in emergency department",
        "increase production throughput by reassigning operators",
    ]

    for query in queries:
        print(f"\n--- Query: '{query}' ---")
        result = retriever.retrieve(
            [query],
            top_k=20,
            per_kind_caps={"mapping": 5, "literature": 4, "parameter": 4, "rule": 2, "pdf_chunk": 4},
        )
        print(f"Backend: {result.backend_name}")

        mappings = result.by_kind("mapping", top_k=5)
        print(f"\n  Top mappings ({len(mappings)}):")
        for si in mappings:
            print(f"    [{si.score:.4f}] {si.item.payload.goal_description[:70]}")

        lit = result.by_kind("literature", top_k=4)
        print(f"\n  Top literature ({len(lit)}):")
        for si in lit:
            print(f"    [{si.score:.4f}] Paper {si.item.payload.paper_id}: {si.item.payload.title[:55]}")

        chunks = result.by_kind("pdf_chunk", top_k=4)
        print(f"\n  Top PDF chunks ({len(chunks)}):")
        for si in chunks:
            c = si.item.payload
            excerpt = c.text[:200].replace('\n', ' ').encode('ascii', errors='replace').decode('ascii')
            print(f"    [{si.score:.4f}] Paper {c.paper_id} p.{c.page}: {excerpt}")


def test_reranking_effect(pdf_chunks: list) -> None:
    separator("Test 3 — Cross-encoder changes ranking vs pure RRF")
    from knowledge.hybrid_retrieval import (
        build_kb_items_with_pdfs,
        HybridRetriever,
    )
    from knowledge.kb_data import build_knowledge_base
    from knowledge.cross_encoder import get_default_reranker

    kb = build_knowledge_base()
    items = build_kb_items_with_pdfs(kb, pdf_chunks)
    reranker = get_default_reranker()

    if not reranker.is_available():
        print("Cross-encoder not available — skipping ranking comparison.")
        return

    query = "reduce patient waiting time in emergency department"

    # Without reranker
    retriever_plain = HybridRetriever(items=items, reranker=None)
    result_plain = retriever_plain.retrieve([query], top_k=10)
    top3_plain = [si.item.uid for si in result_plain.ranked_items[:3]]

    # With reranker
    retriever_ce = HybridRetriever(items=items, reranker=reranker)
    result_ce = retriever_ce.retrieve([query], top_k=10)
    top3_ce = [si.item.uid for si in result_ce.ranked_items[:3]]

    print(f"Query: '{query}'")
    print(f"\n  Pure-RRF top 3:        {top3_plain}")
    print(f"  Cross-encoder top 3:   {top3_ce}")

    if top3_plain != top3_ce:
        print("\n  [YES] Cross-encoder CHANGED the ranking vs pure RRF.")
    else:
        print("\n  [ - ] Top-3 order unchanged (CE scores may agree with RRF on this query).")


if __name__ == "__main__":
    try:
        chunks = test_pdf_chunks()
        test_query_comparison(chunks)
        test_reranking_effect(chunks)
        print("\n\nAll tests completed.")
    except Exception as exc:
        import traceback
        print(f"\n[ERROR] {exc}")
        traceback.print_exc()
        sys.exit(1)

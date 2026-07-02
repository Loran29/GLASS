"""Retrieval evaluation harness.

Measures retrieval quality of the RAG component against a hand-
labelled set of ``(goal, KPIs) → relevant paper IDs`` pairs in
``tests/benchmark_cases/retrieval_queries.jsonl``.

Reports standard IR metrics (recall@k, precision@k, nDCG@k, MRR) and
compares the hybrid BM25 + dense retriever against the legacy
keyword-only baseline so the thesis can defend the upgrade with
numbers rather than adjectives.

Usage (from the repo root)::

    python -m scripts.eval_retrieval

Optional overrides::

    RAG_EMBEDDER=tfidf python -m scripts.eval_retrieval
    RAG_EMBEDDER=st    python -m scripts.eval_retrieval
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make the goal_to_parameters package importable when run from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PKG_ROOT = _REPO_ROOT / "goal_to_parameters"
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from knowledge.hybrid_retrieval import (  # noqa: E402
    HybridRetriever,
    build_queries_from_kpis,
)
from knowledge.retrieval import (  # noqa: E402
    _match_goal_categories,
    retrieve_for_second_llm,
)


QUERIES_PATH = _REPO_ROOT / "tests" / "benchmark_cases" / "retrieval_queries.jsonl"

K_VALUES = (3, 5, 10)


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

@dataclass
class LabelledQuery:
    query_id: str
    goal: str
    kpis: list[dict[str, Any]]
    relevant_paper_ids: set[int]


def load_queries(path: Path = QUERIES_PATH) -> list[LabelledQuery]:
    out: list[LabelledQuery] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            out.append(LabelledQuery(
                query_id=obj["query_id"],
                goal=obj["goal"],
                kpis=obj.get("kpis", []),
                relevant_paper_ids=set(obj.get("relevant_paper_ids", [])),
            ))
    return out


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def recall_at_k(retrieved: list[int], relevant: set[int], k: int) -> float:
    if not relevant:
        return 0.0
    top_k = set(retrieved[:k])
    return len(top_k & relevant) / len(relevant)


def precision_at_k(retrieved: list[int], relevant: set[int], k: int) -> float:
    if k == 0:
        return 0.0
    top_k = retrieved[:k]
    if not top_k:
        return 0.0
    return sum(1 for pid in top_k if pid in relevant) / len(top_k)


def ndcg_at_k(retrieved: list[int], relevant: set[int], k: int) -> float:
    if not relevant:
        return 0.0
    dcg = 0.0
    for rank, pid in enumerate(retrieved[:k], start=1):
        if pid in relevant:
            dcg += 1.0 / math.log2(rank + 1)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(r + 1) for r in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def reciprocal_rank(retrieved: list[int], relevant: set[int]) -> float:
    for rank, pid in enumerate(retrieved, start=1):
        if pid in relevant:
            return 1.0 / rank
    return 0.0


# ---------------------------------------------------------------------------
# Retrievers (both return a list of paper_ids ordered by score desc)
# ---------------------------------------------------------------------------

def hybrid_rank(
    retriever: HybridRetriever,
    goal: str,
    kpis: list[dict[str, Any]],
) -> list[int]:
    queries = build_queries_from_kpis(goal, kpis)
    cats = _match_goal_categories(goal, kpis)
    # Literature-only: zero out the other kinds so papers aren't
    # crowded out of the global top_k by mappings/parameters/rules.
    result = retriever.retrieve(
        queries=queries,
        category_prior=cats or None,
        top_k=50,
        per_kind_caps={"mapping": 0, "parameter": 0, "rule": 0, "literature": 50},
    )
    return [
        si.item.payload.paper_id
        for si in result.ranked_items
        if si.item.kind == "literature"
    ]


def keyword_rank(goal: str, kpis: list[dict[str, Any]]) -> list[int]:
    """Legacy baseline: keyword-only retrieval, papers collected via
    citation chain from the matched mappings (the previous behaviour)."""
    os.environ["RAG_DISABLE_HYBRID"] = "1"
    try:
        result = retrieve_for_second_llm(goal, kpis, context_profile=None)
    finally:
        os.environ.pop("RAG_DISABLE_HYBRID", None)
    return [lit.paper_id for lit in result.literature]


# ---------------------------------------------------------------------------
# Evaluation loop
# ---------------------------------------------------------------------------

def evaluate(
    retrieve_fn: Any,
    queries: list[LabelledQuery],
) -> dict[str, float]:
    metrics: dict[str, list[float]] = {
        **{f"recall@{k}": [] for k in K_VALUES},
        **{f"precision@{k}": [] for k in K_VALUES},
        **{f"ndcg@{k}": [] for k in K_VALUES},
        "mrr": [],
        "hit@1": [],
    }

    for q in queries:
        ranked = retrieve_fn(q.goal, q.kpis)
        for k in K_VALUES:
            metrics[f"recall@{k}"].append(recall_at_k(ranked, q.relevant_paper_ids, k))
            metrics[f"precision@{k}"].append(precision_at_k(ranked, q.relevant_paper_ids, k))
            metrics[f"ndcg@{k}"].append(ndcg_at_k(ranked, q.relevant_paper_ids, k))
        metrics["mrr"].append(reciprocal_rank(ranked, q.relevant_paper_ids))
        metrics["hit@1"].append(1.0 if ranked and ranked[0] in q.relevant_paper_ids else 0.0)

    return {name: (sum(vals) / len(vals) if vals else 0.0) for name, vals in metrics.items()}


def format_row(label: str, m: dict[str, float]) -> str:
    return (
        f"{label:<16}"
        f"R@3={m['recall@3']:.3f}  R@5={m['recall@5']:.3f}  R@10={m['recall@10']:.3f}  "
        f"P@5={m['precision@5']:.3f}  "
        f"nDCG@5={m['ndcg@5']:.3f}  nDCG@10={m['ndcg@10']:.3f}  "
        f"MRR={m['mrr']:.3f}  Hit@1={m['hit@1']:.3f}"
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    queries = load_queries()
    print(f"Loaded {len(queries)} labelled queries from {QUERIES_PATH.name}.")
    print()

    # Hybrid retriever is built once and reused across all queries.
    retriever = HybridRetriever()
    retriever._ensure_indexed()  # triggers lazy build + reports backend

    backend = retriever._embedder.backend_name if retriever._embedder else "?"
    model = retriever._embedder.model_name if retriever._embedder else "?"
    print(f"Hybrid embedder: {backend} / {model}")
    print()

    print("Running hybrid BM25 + dense retrieval ...")
    hybrid_metrics = evaluate(
        lambda g, k: hybrid_rank(retriever, g, k),
        queries,
    )

    print("Running legacy keyword-only baseline ...")
    baseline_metrics = evaluate(keyword_rank, queries)

    # --- Report ----------------------------------------------------
    print()
    print("=" * 110)
    print(format_row("System", {
        "recall@3": 0, "recall@5": 0, "recall@10": 0,
        "precision@5": 0, "ndcg@5": 0, "ndcg@10": 0,
        "mrr": 0, "hit@1": 0,
    }).replace("0.000", "     "))
    print("-" * 110)
    print(format_row(f"hybrid ({backend})", hybrid_metrics))
    print(format_row("keyword baseline", baseline_metrics))
    print("=" * 110)

    # Per-query deltas — helpful for chapter discussion.
    print()
    print("Per-query nDCG@5 (hybrid vs keyword):")
    for q in queries:
        h = ndcg_at_k(hybrid_rank(retriever, q.goal, q.kpis), q.relevant_paper_ids, 5)
        b = ndcg_at_k(keyword_rank(q.goal, q.kpis), q.relevant_paper_ids, 5)
        if h > b + 1e-6:
            arrow = "+"
        elif h < b - 1e-6:
            arrow = "-"
        else:
            arrow = "="
        print(f"  {q.query_id}  {arrow}  hybrid={h:.3f}  keyword={b:.3f}  goal={q.goal[:60]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

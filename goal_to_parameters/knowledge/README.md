# `knowledge/` — parameter knowledge base + RAG retrieval

The knowledge-base package turns a user's simulation goal + verified
KPIs into a compact, literature-grounded prompt for the second LLM.
It is the R in RAG: everything here exists so the LLM receives only
the evidence that matches the goal, not the full knowledge base.

## Architecture

```
  goal text ──┐
              ├─► goal-category matching ────────────┐
  KPIs ───────┘        (soft prior)                  │
                                                     ▼
  kb_data.build_knowledge_base()                 hybrid retrieval
       │                                        ┌────────────────┐
       │  mappings, parameters,                 │ BM25  +  dense │
       │  literature, context rules             │    RRF fusion  │
       ▼                                        │   (k = 60)     │
  build_kb_items()  ───► KBItem (kind-tagged) ─►│  category prior│
                                                │  per-kind caps │
                                                └───────┬────────┘
                                                        │
  context_profile ─► _match_context_rules ──┐           │
  (triggered rules always kept)             │           │
                                            ▼           ▼
                                ┌────────────────────────────┐
                                │       RetrievalResult      │
                                │  + per-item RRF scores     │
                                │  + retrieval_queries       │
                                │  + backfilled flag         │
                                └────────┬───────────────────┘
                                         │
  SIMOD JSON ──► filter_simod_baseline ──┤
  log profile ─► filter_log_evidence ────┼─► SecondLLMEvidence
  context     ─► filter_context_evidence ┤   (kb / simod / log /
                                         │    context / diff briefing)
  diff briefing ◄── _build_differentiation_briefing
```

Split across small modules so each file has one job:

| File                      | Role                                              |
|---------------------------|---------------------------------------------------|
| `models.py`               | Pydantic knowledge-base types                     |
| `kb_data.py`              | Hand-curated KB (mappings, papers, rules)         |
| `bm25.py`                 | Pure-Python BM25 over flattened KB items          |
| `embeddings.py`           | Embedder backends + auto-selection                |
| `hybrid_retrieval.py`     | KBItem flattening, RRF fusion, multi-query search |
| `goal_matching.py`        | Keyword → `GoalCategory` + context-rule triggers  |
| `evidence_filters.py`     | SIMOD / log / context filters + diff briefing     |
| `retrieval_result.py`     | `RetrievalResult`, `SecondLLMEvidence` containers |
| `retrieval.py`            | Public orchestrator: `build_second_llm_evidence` |

## Fallback chain

Retrieval degrades rather than crashes the pipeline:

1. **Dense embedder** — `sentence-transformers` → Ollama → TF-IDF.
   Override with `RAG_EMBEDDER={st,ollama,tfidf}`.
2. **Hybrid retrieval** — BM25 ∪ dense fused with RRF (k=60).  If a
   hybrid call raises for any reason (missing torch, transient
   embedder failure, etc.), retrieval logs a warning and falls back
   to the keyword-only path. Opt out of hybrid entirely with
   `RAG_DISABLE_HYBRID=1` (useful for baseline ablations).
3. **Keyword backfill** — if hybrid returned zero `mappings` (a
   degenerate query), the category-keyword filter backfills
   mappings/parameters/literature so the prompt is not empty.  This
   is surfaced to downstream consumers as
   `RetrievalResult.backfilled == True` and a backend label of
   `"<hybrid>+keyword_backfill"`.

## Provenance surfaced to the prompt

The JSON serialised by `RetrievalResult.to_prompt_json()` carries:

- `matched_goal_categories` — soft-prior categories
- `retrieval.backend`, `retrieval.model` — which embedder was used
- `retrieval.queries` — the per-KPI queries that were fused
- `retrieval.backfilled` — true if keyword fallback fired
- per-item `retrieval_score` — RRF-fused score so the LLM can weigh
  strongly-retrieved evidence over tangential hits.

## Running the retrieval eval

From the repo root:

```bash
python -m scripts.eval_retrieval            # default auto backend
RAG_EMBEDDER=tfidf python -m scripts.eval_retrieval   # TF-IDF only
RAG_DISABLE_HYBRID=1 python -m scripts.eval_retrieval # keyword baseline
```

Reads the labelled queries from
`tests/benchmark_cases/retrieval_queries.jsonl` and prints
recall@{3,5,10}, precision@{3,5,10}, nDCG@{3,5,10}, and MRR for the
hybrid retriever vs. the keyword-only baseline.

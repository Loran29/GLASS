# Knowledge-base option: consistency with the stored runs

GLASS v3.3 switches the knowledge base off with its own option (`use_knowledge_base`, `knowledge/switch.py`) instead of the monkey-patch the V_no_kb runs used. `scripts/replay_kb_option.py` re-executes stored runs through their batch script, with the option set as the run had it.

- **Inputs:** the LLM answers are replayed from the record, and the simulations are re-run with the batch's seed shim.
- **What is compared:** the prompts GLASS sends, against the stored ones. That covers every LLM call of iteration 1 (system prompt, user prompt, temperature), the refinement feedback of iteration 1, and the first call of iteration 2, which carries that feedback.
- **Where the replay stops:** at that call, or when the loop ends, before the 10-seed evaluation. Nothing is written to the records.

| Records | Knowledge base | Records | Byte-identical | Identical up to set order | Differ | LLM calls compared | Feedbacks compared | Feedbacks identical (byte / up to set order) |
|---|---|---|---|---|---|---|---|---|
| RAG comparison V_no_kb (off) | off | 70 | 2 | 68 | 0 | 376 | 27 | 27 / 0 |
| Real-life ablation V_no_kb (off) | off | 45 | 0 | 45 | 0 | 212 | 14 | 14 / 0 |
| RAG comparison V_full (on) | on | 10 | 1 | 9 | 0 | 53 | 5 | 5 / 0 |

Replays ended: {'loop ended': 70, 'first call of iteration 2': 55}.

## Two sources of variation that are not the option

1. **Set order (Python hash randomization).** GLASS builds parts of the prompts' JSON blocks from Python sets; for example, `knowledge.evidence_filters.filter_log_evidence` collects the log-profile sections in a set. Python randomizes string hashing per process, so the order of those object keys and string lists differs between processes.
   - The original batch processes' hash seeds are not recorded, so their exact order cannot be recreated.
   - "Identical up to set order" compares the prompts byte for byte after rewriting every JSON block with sorted keys and sorted lists of plain values. The text outside the blocks, and the contents, are compared unchanged.
   - **Check:** replaying one record (V_full, Procure2Pay_G1G2G3 rep 5) under `PYTHONHASHSEED` 0, 1 and 2 gave byte-different prompts that are identical after this rewriting; the same seed twice gave byte-identical prompts.
2. **The TF-IDF embedder's vocabulary (knowledge base on only).** Without sentence-transformers, retrieval uses GLASS's TF-IDF fallback. When the corpus vectors come from the disk cache, its vocabulary is fitted on the first query the process encodes (`knowledge/embeddings.py`), so retrieval depends on that query.
   - The batch's four worker processes each started with a LoanApp_G1 run, whose first query is the goal sentence.
   - The V_full replays encode that query first. Without it, the knowledge-base block differs from the stored one; with it, the block is reproduced.
   - The V_no_kb runs make no retrieval, so this does not affect them.

## Differences beyond set order (0)

None.

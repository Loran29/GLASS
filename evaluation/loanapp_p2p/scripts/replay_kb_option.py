"""
Consistency check for GLASS's option use_knowledge_base (v3.3): the option reproduces the stored runs.

The knowledge base used to be switched off by a monkey-patch (V_no_kb of the real-life ablation and of the v3.2
RAG comparison); since v3.3 both batch scripts use GLASS's option instead. This script re-executes stored runs
through their batch script's own run_rep with the option set as the run had it and compares, byte for byte, the
prompts GLASS sends with the prompts stored in the record:

  - every LLM call of iteration 1 (clarification summary, scenario generation and its retries, compliance
    check): system prompt, user prompt and temperature;
  - the refinement feedback of iteration 1 (when the loop continued) and the first LLM call of iteration 2, whose
    prompt carries that feedback.

The LLM is replaced by a replay of the record's own answers (in order), so GLASS takes the same path; the
simulations are re-run with the batch's seed shim (seeded, reproducible). The replay stops at the first call of
iteration 2 or when the loop ends, before the 10-seed evaluation. Nothing is written to the records: the replay
works in a scratch folder with copies of the cached baseline and reference logs.

Records: all 70 V_no_kb runs of results/stage2_results_v32_rag/V_no_kb and the 45 V_no_kb runs of the real-life
ablation with the option off; 10 V_full runs of the RAG comparison with the option on (repetition (i mod 5) + 1
of the i-th configuration, i = 0..9).

Writes results/kb_option_consistency.csv (one row per record) and results/kb_option_consistency.md.

Usage (repo root): python evaluation/loanapp_p2p/scripts/replay_kb_option.py [--workers 4] [--scratch DIR]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
REPO = BASE.parent.parent
RAG = BASE / "results" / "stage2_results_v32_rag"
ABL = REPO / "evaluation" / "real_life_ablation"


class StopReplay(BaseException):
    """Ends a replay (a BaseException, so that GLASS's and the batch's error handling let it through)."""


def _replay(task: dict) -> dict:
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(ABL / "scripts"))
    import run_stage1_batch as s1
    import run_stage2_batch as s2
    import second_llm.iterative_evaluator as ie

    rec = json.loads(Path(task["record"]).read_text(encoding="utf-8"))
    calls = rec["llm_calls"]
    captured, feedbacks = [], []
    s1.PAUSE_S = 0
    make_provider = s1.make_provider

    def replaying_provider(model):
        inner = make_provider(model)                       # the run's provider class; its network call is replaced

        def generate(system_prompt, user_prompt, temperature=0.3, **kwargs):
            i = len(captured)
            captured.append({"system_prompt": system_prompt, "user_prompt": user_prompt, "temperature": temperature})
            if i >= len(calls):
                raise StopReplay("more LLM calls than recorded")
            if (calls[i].get("iteration") or 0) >= 2:
                raise StopReplay("first call of iteration 2")
            if task.get("stop_after") and i + 1 > task["stop_after"]:
                raise StopReplay(f"after {task['stop_after']} calls")
            return calls[i]["raw"]
        inner.generate = generate
        return inner
    s1.make_provider = replaying_provider

    build_feedback = ie._build_evaluation_feedback

    def feedback(*a, **k):
        text = build_feedback(*a, **k)
        feedbacks.append(text)
        return text
    ie._build_evaluation_feedback = feedback
    loop = ie.run_iterative_evaluation

    def loop_then_stop(*a, **k):
        loop(*a, **k)
        raise StopReplay("loop ended")
    ie.run_iterative_evaluation = loop_then_stop

    s2.quiet()
    app, _ = s1.load_app_stage1()
    if task.get("prime_query"):
        # The TF-IDF fallback embedder fits its vocabulary on the first query a process encodes (the corpus
        # vectors come from the disk cache without a fit). The batch's worker processes first encoded the goal
        # of their first job; replay that query first so this process has the same vocabulary.
        from knowledge.hybrid_retrieval import get_default_retriever
        get_default_retriever()._rank_one_query(task["prime_query"], None)
    out = Path(task["scratch"])
    reason = "no stop"
    try:
        if task["kind"] == "stage2":
            config = json.loads(s2.CONFIG_PATH.read_text(encoding="utf-8"))
            cfg = next(c for c in config["configurations"] if c["config_id"] == rec["config_id"])
            shared = {cfg["config_id"]: s2.prepare_config(cfg, config, out, app)}
            log_profile = {cfg["config_id"]: shared[cfg["config_id"]].pop("log_profile")}
            job = {"config": config, "cfg": cfg, "rep": rec["rep"], "out": str(out), "glass_commit": "",
                   "use_kb": task["use_kb"], "loop_seeds": 1, "config_sha": "", "script_sha": "",
                   "log_profile": log_profile, "shared": shared,
                   "healthy_sha": {cfg["process"]: s2.prepare_healthy(cfg["process"], cfg["healthy_dir"], config, out)},
                   "healthy_scenario": {cfg["process"]: s2.healthy_scenario(cfg["process"], cfg["healthy_dir"], config)}}
            s2.run_rep(job)
        else:
            import run_ablation_batch as ab
            config = json.loads(ab.CONFIG_PATH.read_text(encoding="utf-8"))
            kpis = json.loads(ab.KPI_PATH.read_text(encoding="utf-8"))
            cfg = next(c for c in config["configurations"] if c["config_id"] == rec["config_id"])
            shared = {cfg["dataset"]: ab.prepare_dataset(cfg["dataset"], config, kpis, out, app)}
            job = {"config": config, "kpis": kpis, "out": str(out), "glass_commit": "", "shared": shared,
                   "config_sha": "", "kpis_sha": "", "script_sha": "", "cfg": cfg, "variant": rec["variant"],
                   "rep": rec["rep"]}
            ab.run_rep(job)
    except StopReplay as stop:
        reason = str(stop)

    if task.get("dump"):
        Path(task["dump"]).write_text(json.dumps({"captured": captured, "feedbacks": feedbacks}), encoding="utf-8")
    diffs, order_only = [], []
    for i, cap in enumerate(captured[:len(calls)]):
        for field in ("system_prompt", "user_prompt", "temperature"):
            if cap[field] != calls[i].get(field):
                if field != "temperature" and canonical(cap[field]) == canonical(calls[i].get(field) or ""):
                    order_only.append(f"call {i + 1} {field}")
                else:
                    diffs.append(f"call {i + 1} {field}")
    if len(captured) > len(calls):
        diffs.append("more calls than recorded")
    stored_fb = ((rec.get("loop") or {}).get("iterations") or [{}])[0].get("feedback_text") or ""
    if stored_fb:
        fb_state = "identical" if feedbacks and feedbacks[0] == stored_fb else (
            "identical up to set order" if feedbacks and canonical(feedbacks[0]) == canonical(stored_fb) else "differs")
        if fb_state == "differs":
            diffs.append("feedback of iteration 1")
        elif fb_state != "identical":
            order_only.append("feedback of iteration 1")
    else:
        fb_state = "none (loop stopped after iteration 1)" if not feedbacks else "built but not stored"
        if feedbacks:
            diffs.append("feedback built but none stored")
    return {"set": task["set"], "record": Path(task["record"]).relative_to(REPO).as_posix(),
            "use_knowledge_base": task["use_kb"], "stored_calls": len(calls), "compared_calls": len(captured[:len(calls)]),
            "compared_iteration_2_calls": sum(1 for c in calls[:len(captured)] if (c.get("iteration") or 0) >= 2),
            "feedback": fb_state, "stop": reason, "byte_identical": not diffs and not order_only,
            "identical": not diffs, "set_order_only": "; ".join(order_only), "differences": "; ".join(diffs)}


JSON_BLOCK = re.compile(r"```json\n(.*?)\n```", re.S)


def _canon(obj):
    """Order-free form of a JSON value: object keys sorted, lists of plain values sorted."""
    if isinstance(obj, dict):
        return {k: _canon(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        items = [_canon(x) for x in obj]
        return sorted(items, key=json.dumps) if all(not isinstance(x, (dict, list)) for x in items) else items
    return obj


def canonical(text: str) -> str:
    """The prompt with every JSON block rewritten in order-free form (text outside the blocks unchanged).

    GLASS builds parts of these blocks from Python sets (e.g. the log-evidence sections in
    knowledge.evidence_filters.filter_log_evidence); their order follows the per-process string hash seed.
    """
    def repl(m):
        try:
            return "```json\n" + json.dumps(_canon(json.loads(m.group(1))), indent=2, ensure_ascii=False) + "\n```"
        except ValueError:
            return m.group(0)
    return JSON_BLOCK.sub(repl, text)


def tasks(scratch: Path) -> list[dict]:
    out = []
    for p in sorted((RAG / "V_no_kb" / "raw").glob("*/rep_*.json")):
        out.append({"set": "RAG comparison V_no_kb (off)", "kind": "stage2", "record": str(p), "use_kb": False,
                    "scratch": str(scratch / "rag_V_no_kb")})
    for p in sorted((ABL / "results" / "stage2" / "raw").glob("*__V_no_kb/rep_*.json")):
        out.append({"set": "Real-life ablation V_no_kb (off)", "kind": "ablation", "record": str(p), "use_kb": False,
                    "scratch": str(scratch / "ablation")})
    first = json.loads((RAG / "V_full" / "raw" / "LoanApp_G1" / "rep_1.json").read_text(encoding="utf-8"))
    prime = first_query(first)
    cfgs = sorted(d.name for d in (RAG / "V_full" / "raw").iterdir())
    for i, cid in enumerate(cfgs[:10]):
        out.append({"set": "RAG comparison V_full (on)", "kind": "stage2",
                    "record": str(RAG / "V_full" / "raw" / cid / f"rep_{i % 5 + 1}.json"), "use_kb": True,
                    "scratch": str(scratch / "rag_V_full"), "prime_query": prime})
    return out


def first_query(rec: dict) -> str:
    """The first retrieval query of a run: retrieval.queries[0] of the knowledge-base block of its first prompt."""
    for call in rec["llm_calls"]:
        m = re.search(r"Literature-Backed Parameter Recommendations.*?```json\n(.*?)\n```", call["user_prompt"], re.S)
        if m:
            return json.loads(m.group(1))["retrieval"]["queries"][0]
    raise ValueError("no knowledge-base block in the record's prompts")


def copy_caches(scratch: Path) -> None:
    """The cached baseline and reference logs the batches read (copies; the originals are not touched)."""
    for arm in ("V_no_kb", "V_full"):
        for sub in ("baseline_logs", "healthy_logs"):
            shutil.copytree(RAG / arm / sub, scratch / f"rag_{arm}" / sub, dirs_exist_ok=True)
    shutil.copytree(ABL / "results" / "stage2" / "baseline_logs", scratch / "ablation" / "baseline_logs",
                    dirs_exist_ok=True)


def write_report(rows: list[dict]) -> None:
    with open(BASE / "results" / "kb_option_consistency.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    sets = list(dict.fromkeys(r["set"] for r in rows))
    L = ["# Knowledge-base option: consistency with the stored runs", "",
         "GLASS v3.3 switches the knowledge base off with its own option (`use_knowledge_base`, "
         "`knowledge/switch.py`) instead of the monkey-patch the V_no_kb runs used. `scripts/replay_kb_option.py` "
         "re-executes stored runs through their batch script, with the option set as the run had it.",
         "",
         "- **Inputs:** the LLM answers are replayed from the record, and the simulations are re-run with the batch's "
         "seed shim.",
         "- **What is compared:** the prompts GLASS sends, against the stored ones. That covers every LLM call of "
         "iteration 1 (system prompt, user prompt, temperature), the refinement feedback of iteration 1, and the first "
         "call of iteration 2, which carries that feedback.",
         "- **Where the replay stops:** at that call, or when the loop ends, before the 10-seed evaluation. Nothing is "
         "written to the records.",
         "", "| Records | Knowledge base | Records | Byte-identical | Identical up to set order | Differ | LLM calls compared | Feedbacks compared | Feedbacks identical (byte / up to set order) |",
         "|---|---|---|---|---|---|---|---|---|"]
    for s in sets:
        rs = [r for r in rows if r["set"] == s]
        fb = [r for r in rs if r["feedback"] in ("identical", "identical up to set order", "differs")]
        L.append(f"| {s} | {'on' if rs[0]['use_knowledge_base'] else 'off'} | {len(rs)} | "
                 f"{sum(r['byte_identical'] for r in rs)} | {sum(r['identical'] and not r['byte_identical'] for r in rs)} | "
                 f"{sum(not r['identical'] for r in rs)} | {sum(r['compared_calls'] for r in rs)} | {len(fb)} | "
                 f"{sum(r['feedback'] == 'identical' for r in fb)} / {sum(r['feedback'] == 'identical up to set order' for r in fb)} |")
    stops = Counter(r["stop"] for r in rows)
    L += ["", f"Replays ended: {dict(stops)}.", "",
          "## Two sources of variation that are not the option", "",
          "1. **Set order (Python hash randomization).** GLASS builds parts of the prompts' JSON blocks from Python "
          "sets; for example, `knowledge.evidence_filters.filter_log_evidence` collects the log-profile sections in a "
          "set. Python randomizes string hashing per process, so the order of those object keys and string lists "
          "differs between processes.",
          "   - The original batch processes' hash seeds are not recorded, so their exact order cannot be recreated.",
          "   - \"Identical up to set order\" compares the prompts byte for byte after rewriting every JSON block with "
          "sorted keys and sorted lists of plain values. The text outside the blocks, and the contents, are compared "
          "unchanged.",
          "   - **Check:** replaying one record (V_full, Procure2Pay_G1G2G3 rep 5) under `PYTHONHASHSEED` 0, 1 and 2 "
          "gave byte-different prompts that are identical after this rewriting; the same seed twice gave "
          "byte-identical prompts.",
          "2. **The TF-IDF embedder's vocabulary (knowledge base on only).** Without sentence-transformers, retrieval "
          "uses GLASS's TF-IDF fallback. When the corpus vectors come from the disk cache, its vocabulary is fitted on "
          "the first query the process encodes (`knowledge/embeddings.py`), so retrieval depends on that query.",
          "   - The batch's four worker processes each started with a LoanApp_G1 run, whose first query is the goal "
          "sentence.",
          "   - The V_full replays encode that query first. Without it, the knowledge-base block differs from the "
          "stored one; with it, the block is reproduced.",
          "   - The V_no_kb runs make no retrieval, so this does not affect them."]
    bad = [r for r in rows if not r["identical"]]
    L += ["", f"## Differences beyond set order ({len(bad)})", ""] + ([f"- `{r['record']}`: {r['differences']}" for r in bad] or ["None."])
    (BASE / "results" / "kb_option_consistency.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--scratch", type=Path, default=None, help="working folder (default: a temporary folder)")
    args = ap.parse_args()
    scratch = args.scratch or Path(tempfile.mkdtemp(prefix="kb_replay_"))
    copy_caches(scratch)
    todo = tasks(scratch)
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers, max_tasks_per_child=1) as pool:
        for n, row in enumerate(pool.map(_replay, todo), 1):
            rows.append(row)
            print(f"[{n}/{len(todo)}] {row['record']}: {'identical' if row['identical'] else 'DIFFERS: ' + row['differences']} "
                  f"({row['compared_calls']} calls, feedback {row['feedback']}, stop: {row['stop']})", flush=True)
    write_report(rows)
    print({s: (sum(r["identical"] for r in rows if r["set"] == s), sum(r["set"] == s for r in rows))
           for s in dict.fromkeys(r["set"] for r in rows)})


if __name__ == "__main__":
    main()

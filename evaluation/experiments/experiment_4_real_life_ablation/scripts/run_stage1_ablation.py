"""
Stage-1 ablation on the real-life datasets: 3 datasets x 3 goals x 2 variants x 5 repetitions (90 calls).

Adapted from evaluation/experiments/experiment_1_expected_kpi_generation/scripts/run_stage1_batch.py, whose functions are reused unchanged: the app's
Stage-1 path compiled from goal_to_parameters/app.py (validate_generation_scope -> extract_log_artifacts ->
build_smart_kpi_prompt(sanitize_user_input(...)) -> parse_with_retries), the recording provider, run_rep and the
scoring (strict and lenient rules, extras). Nothing under goal_to_parameters/ is modified.

Variants (recorded in every run record as `variant` and `use_log_evidence`):
  full    the dataset's test log is passed as an uploaded log: log profile, log evidence and context evidence
          (extract_log_artifacts on the decompressed CSV)
  no_log  no log: no log profile, no log evidence, no context evidence (the app without an uploaded log)

Consistency per cell (dataset x goal x variant, over its repetitions), reported side by side:
  jaccard_mean           mean pairwise Jaccard similarity of the returned KPI sets (measurable_as after GLASS's
                         fill step; a null measurable_as is represented by the KPI name)
  category_stability     |intersection| / |union| of the KPI category sets over all repetitions (thesis M4)
  distinct_measurable_as the measurable_as values returned, with counts

KPI objects for Stage 2 (setup/stage2_kpis.json), per configuration (dataset x goal), from variant `full`:
the KPI object matching each expected KPI in repetition 1 by the strict rule; if repetition 1 has no strict
match, the first repetition with one; if no repetition of the configuration has a strict match, the strict match
of the dataset's single-goal configuration that expects that KPI (G1: Average Cycle Time, G2: X waiting time;
repetition 1 first) -- lead author's decision after AcademicCredentials_G1G2, whose only X match was lenient with
a measurable_as GLASS resolves to the process-wide Average Waiting Time; only then the first lenient match.
The source cell, repetition and match mode are recorded per KPI; the goal structure and reasoning passed with
them are those of the configuration's own repetition of the first expected KPI.

Usage (repo root):
  python evaluation/experiments/experiment_4_real_life_ablation/scripts/run_stage1_ablation.py [--reps N] [--only CONFIG_ID] [--workers N]
Re-running resumes (reps with a final status are skipped).
"""
from __future__ import annotations

import argparse
import gzip
import io
import itertools
import json
import logging
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

BASE = Path(__file__).resolve().parent.parent                 # evaluation/real_life_ablation
sys.path.insert(0, str(BASE.parent / "shared" / "loanapp_p2p"))
import lp_paths as P  # noqa: E402  (puts the LoanApp/P2P scripts on sys.path)
REPO = P.REPO
import run_stage1_batch as s1  # noqa: E402  (Step 1 batch: app loader, provider, run_rep, scoring)

CONFIG_PATH = BASE / "setup" / "ablation_config.json"
OUT = BASE / "results" / "stage1"
VARIANTS = ("full", "no_log")


class _UploadedLog(io.BytesIO):
    """The decompressed test log as the file object Streamlit's uploader hands to the app."""

    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


def cells(config: dict) -> list[dict]:
    out = []
    for c in config["configurations"]:
        ds = config["datasets"][c["dataset"]]
        for v in VARIANTS:
            out.append({"id": f"{c['config_id']}__{v}", "config_id": c["config_id"], "process": c["dataset"],
                        "goal_config": c["goal"], "variant": v, "simulation_goal": c["simulation_goal"],
                        "event_log": ds["test_log"], "expected_measurable_as": c["expected_measurable_as"]})
    return out


def prepare(app, cell: dict, config: dict) -> tuple:
    """Log artifacts and prompt package, built as handle_generation builds them (no log for no_log)."""
    log_profile = log_evidence = context_evidence = None
    if cell["variant"] == "full":
        path = BASE / cell["event_log"]
        log_profile, log_evidence, context_evidence = app["extract_log_artifacts"](
            _UploadedLog(gzip.open(path).read(), path.name.replace(".gz", "")))
    prompts = app["build_smart_kpi_prompt"](
        process_description=app["sanitize_user_input"](config["datasets"][cell["process"]]["process_description"]),
        simulation_goal=app["sanitize_user_input"](cell["simulation_goal"]),
        num_kpis=None, log_evidence=log_evidence, context_evidence=context_evidence)
    return log_profile, context_evidence, prompts


def run_rep(job: dict, ctx: dict) -> None:
    """Step 1's run_rep (unchanged), then the ablation fields are added to the record."""
    s1.run_rep(job, ctx)
    rec = s1.load_json(job["path"])
    cell = job["cfg"]
    rec.update({"variant": cell["variant"], "dataset": cell["process"], "goal": cell["goal_config"],
                "config_id_ablation": cell["config_id"], "use_log_evidence": cell["variant"] == "full"})
    s1.write_json(job["path"], rec)


def kpi_key(k: dict) -> str:
    return (k.get("measurable_as") or f"(name) {k.get('name')}").lower()


def summaries(config: dict) -> list[dict]:
    rows, per_cell = [], []
    for cell in cells(config):
        files = sorted((OUT / "raw" / cell["id"]).glob("rep_*.json"), key=lambda p: int(p.stem.split("_")[1]))
        recs = [s1.load_json(p) for p in files]
        if not recs:
            continue
        r_rows = []
        for rec in recs:
            row = {"dataset": cell["process"], "goal": cell["goal_config"], "variant": cell["variant"],
                   **s1.score(rec, cell)}
            r_rows.append(row)
        rows += r_rows
        ok = [r for r in recs if r["status"] == "ok"]
        sets = [{kpi_key(k) for k in r["result"]["kpis"]} for r in ok]
        pairs = list(itertools.combinations(sets, 2))
        cats = [{(k.get("category") or "").lower() for k in r["result"]["kpis"]} for r in ok]
        distinct = Counter(m for s in sets for m in s)
        pc = {"dataset": cell["process"], "goal": cell["goal_config"], "variant": cell["variant"],
              "cell": cell["id"], "reps": len(recs), "reps_ok": len(ok),
              "expected_measurable_as": "|".join(cell["expected_measurable_as"])}
        for mode in ("strict", "lenient"):
            counts = Counter(e for r in r_rows for e in r[f"{mode}_found"].split("|") if e)
            pc[f"{mode}_found_counts"] = "|".join(f"{e}:{counts[e]}" for e in cell["expected_measurable_as"])
            pc[f"{mode}_reps_all_expected_found"] = sum(r[f"{mode}_all_expected_found"] for r in r_rows)
            pc[f"{mode}_mean_n_extra"] = round(mean(r[f"{mode}_n_extra"] for r in r_rows if r["status"] == "ok"), 2) \
                if ok else ""
        pc["mean_n_kpis"] = round(mean(len(r["result"]["kpis"]) for r in ok), 2) if ok else ""
        pc["mean_attempts"] = round(mean(r["attempts"] for r in r_rows), 2)
        pc["jaccard_mean"] = round(mean(len(a & b) / len(a | b) if a | b else 1.0 for a, b in pairs), 3) if pairs else ""
        pc["category_stability"] = round(len(set.intersection(*cats)) / len(set.union(*cats)), 3) \
            if cats and set.union(*cats) else ""
        pc["distinct_measurable_as"] = "|".join(f"{m}:{c}" for m, c in distinct.most_common())
        per_cell.append(pc)
    s1.write_csv(OUT / "stage1_summary.csv", rows)
    s1.write_csv(OUT / "stage1_per_cell.csv", per_cell)
    return per_cell


def select_stage2_kpis(config: dict) -> dict:
    """KPI objects for Stage 2 from variant full: strict match in rep 1, else first strict, else first lenient."""
    def full_recs(config_id: str) -> list[dict]:
        files = sorted((OUT / "raw" / f"{config_id}__full").glob("rep_*.json"), key=lambda p: int(p.stem.split("_")[1]))
        return [r for r in (s1.load_json(p) for p in files) if r["status"] == "ok"]

    def first_match(recs: list[dict], exp: str, mode: str):
        for rec in recs:
            for k in rec["result"]["kpis"]:
                if ((k.get("measurable_as") or "").lower() == exp.lower()) if mode == "strict" \
                        else s1.lenient_match(exp, k):
                    return rec, k
        return None

    single_goal = {exp: c["config_id"] for c in config["configurations"] if len(c["expected_measurable_as"]) == 1
                   for exp in c["expected_measurable_as"]}
    out = {}
    for c in config["configurations"]:
        cell = f"{c['config_id']}__full"
        recs = full_recs(c["config_id"])
        picked, sources = [], []
        for exp in c["expected_measurable_as"]:
            fallback = single_goal.get(exp) if single_goal.get(exp) != c["config_id"] else None
            tries = [(c["config_id"], recs, "strict")] + \
                    ([(fallback, full_recs(fallback), "strict")] if fallback else []) + [(c["config_id"], recs, "lenient")]
            hit = next(((cid, *m, mode) for cid, rr, mode in tries if (m := first_match(rr, exp, mode))), None)
            if hit is None:
                raise SystemExit(f"{c['config_id']}: expected KPI {exp!r} not found in any repetition of {cell}")
            cid, rec, k, mode = hit
            picked.append(k)
            sources.append({"expected": exp, "source_cell": f"{cid}__full", "rep": rec["rep"], "match": mode,
                            "name": k.get("name"), "measurable_as": k.get("measurable_as"),
                            "from_own_cell": cid == c["config_id"], "from_rep_1": rec["rep"] == 1})
        own = next(s for s in sources if s["from_own_cell"]) if any(s["from_own_cell"] for s in sources) else None
        first = next(r for r in recs if r["rep"] == (own["rep"] if own else 1))
        out[c["config_id"]] = {"source_cell": cell, "sources": sources,
                               "stage1_result": {"simulation_goal_structured": first["result"]["simulation_goal_structured"],
                                                 "kpis": picked, "reasoning": first["result"]["reasoning"]}}
    (BASE / "setup" / "stage2_kpis.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str) + "\n",
                                                     encoding="utf-8", newline="\n")
    return out


def main() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reps", type=int, default=config["settings"]["repetitions"])
    ap.add_argument("--only", help="run one configuration id (both variants)")
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()
    selected = [c for c in cells(config) if args.only in (None, c["config_id"])]
    if not selected:
        sys.exit(f"Unknown configuration id: {args.only}")
    patch = s1.glass_patch()
    if patch:
        sys.exit("goal_to_parameters/ has local changes; the ablation must run on committed GLASS code")
    sys.stdout = s1._MuteCostTracker(sys.stdout)
    logging.getLogger("utils.parsing").addHandler(s1._ParseWarnings())
    app, app_sources = s1.load_app_stage1()
    temperature = s1.app_temperature()
    settings = {"model": config["settings"]["model"], "num_kpis": None, "use_log_evidence": None}
    jobs, total = [], len(selected) * args.reps
    for n, (cell, rep) in enumerate(((c, r) for c in selected for r in range(1, args.reps + 1)), 1):
        path = OUT / "raw" / cell["id"] / f"rep_{rep}.json"
        prefix = f"[{n}/{total}] {cell['id']} rep {rep} ..."
        if (existing := s1.load_json(path)) and existing.get("status") in s1.FINAL_STATUSES:
            s1.log(f"{prefix} skipped (exists: {existing['status']})")
        else:
            jobs.append({"cfg": cell, "rep": rep, "path": path, "prefix": prefix})
    pending = {j["cfg"]["id"]: j["cfg"] for j in jobs}
    ctx = {"app": app, "settings": settings, "temperature": temperature,
           "descriptions": {ds: d["process_description"] for ds, d in config["datasets"].items()},
           "prepared": {cid: prepare(app, c, config) for cid, c in pending.items()},
           "log_sha": {cid: s1.sha256((BASE / c["event_log"]).read_bytes()) for cid, c in pending.items()},
           "script_sha": s1.sha256(Path(__file__).read_bytes()), "app_sha": s1.sha256(s1.APP_PATH.read_bytes()),
           "patch_sha": None}
    s1.log(f"{len(jobs)} call(s) to do; model {settings['model']}; temperature {temperature}; workers {args.workers}")
    with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="worker") as pool:
        for future in [pool.submit(run_rep, job, ctx) for job in jobs]:
            future.result()
    per_cell = summaries(config)
    s1.log(f"wrote {len(per_cell)} cells to {OUT / 'stage1_per_cell.csv'}")
    if all(len(list((OUT / "raw" / f"{c['config_id']}__full").glob("rep_*.json"))) for c in config["configurations"]):
        picked = select_stage2_kpis(config)
        s1.log("stage2_kpis.json: " + json.dumps({k: v["sources"] for k, v in picked.items()}, ensure_ascii=False))
    (OUT / "run_info.json").write_text(json.dumps({
        "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"), "temperature": temperature,
        "model": settings["model"], "config_sha256": s1.sha256(CONFIG_PATH.read_bytes()),
        "script_sha256": ctx["script_sha"], "step1_batch_script_sha256": s1.sha256(Path(s1.__file__).read_bytes()),
        "app_py_sha256": ctx["app_sha"], "app_sources_sha256": {n: s1.sha256(t) for n, t in app_sources.items()},
        "glass_commit": __import__("subprocess").run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                                                     text=True).stdout.strip()}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

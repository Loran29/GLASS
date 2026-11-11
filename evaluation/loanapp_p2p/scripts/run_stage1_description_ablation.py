"""
Stage-1 description ablation: every configuration in setup/stage1_config.json x variant x repetition, run through
the Step 1 batch's own Stage-1 path (run_stage1_batch.py: app.py's validate_generation_scope ->
extract_log_artifacts -> build_smart_kpi_prompt(sanitize_user_input(...)) -> parse_with_retries, compiled from
app.py's source), with the same model, temperature, num_kpis and log evidence as the Step 1 batch. GLASS is used
as it is (no change under goal_to_parameters/; the script refuses to run with local changes there).

Variants (the only difference between them is the process description passed to the prompt builder):
  with_description  the configuration's process description, as in the Step 1 batch
  no_description    an empty string, passed to the existing prompt builder unchanged. The builder then still
                    writes the "Process Description:" heading with an empty quoted block (recorded per run as
                    description_block_in_prompt). The UI's keyword gate (validate_generation_scope) rejects an
                    empty description; as in the Step 1 batch, its verdict is recorded, not enforced.

Records go to <out>/raw/<variant>/<config_id>/rep_<k>.json (the Step 1 record plus variant fields); reruns resume
(final statuses are skipped, llm_error reps are retried).

Usage (repo root):
  python evaluation/loanapp_p2p/scripts/run_stage1_description_ablation.py [--variant V[,V]] [--only ID[,ID]]
         [--reps N] [--workers N] [--out DIR] [--smoke] [--report-only]
--smoke = --only LoanApp_G1 --reps 1, both variants; prints the description block of each prompt.

SCORING (analysis only; the recording is never changed by scoring)
Per run, as the Step 1 batch scores it (run_stage1_batch.score, rules in that script's header):
  strict    an expected KPI is identified if its measurable_as equals (case-insensitively) the canonical
            measurable_as of a returned KPI after GLASS's normalisation (the delivered result); extras =
            returned non-null measurable_as values not expected
  lenient   an expected KPI is identified if a returned KPI satisfies its name / scope / formula rule; extras =
            returned KPIs that satisfy no expected rule
  all expected found = every expected KPI of the configuration identified (a run without an answer: none)
Per configuration and variant, over the repetitions with a delivered result:
  KPI-set Jaccard      mean over all pairs of repetitions of |A n B| / |A u B|, where a repetition's set holds
                       one key per returned KPI: its canonical measurable_as (lower case), or
                       "(<category>, <process_scope>)" when measurable_as is null (lead author's decision)
  category stability   mean over all pairs of repetitions of the multiset Jaccard of the KPI categories,
                       sum(min(count)) / sum(max(count)) (lead author's decision)
  (two empty sets: 1.0; fewer than two repetitions with a result: not computed)
Tests between the variants (two-sided):
  per configuration    Fisher exact test on runs with all expected found (5 vs 5) and on expected-KPI instances
                       identified (5 x number of expected KPIs per variant), strict and lenient
  pooled               the same Fisher tests over all runs; sign test over the configurations (ties dropped) for
                       runs with all expected found, expected KPIs identified, mean extras, mean KPIs returned,
                       KPI-set Jaccard and category stability (difference with_description - no_description)
END SCORING
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import logging
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_stage1_batch as s1  # noqa: E402  (Step 1 batch: app loader, provider, run_rep, score)
from compare_stage2_rag import sign_test  # noqa: E402

VARIANTS = ("with_description", "no_description")
OUT_DEFAULT = s1.RESULTS_DIR / "stage1_description_ablation"
MODES = ("strict", "lenient")


def description_for(variant: str, description: str) -> str:
    return description if variant == "with_description" else ""


def description_block(user_prompt: str) -> str:
    """The prompt text from the description heading up to the goal heading (what the description contributes)."""
    start = user_prompt.find("Process Description:")
    end = user_prompt.find("Simulation Goal:")
    return user_prompt[start:end] if start >= 0 and end > start else ""


# ----------------------------------------------------------------- scoring ---

def kpi_key(k: dict) -> str:
    return (k.get("measurable_as") or "").lower() or f"({k.get('category')}, {k.get('process_scope')})"


def pairwise(sets: list) -> float | None:
    if len(sets) < 2:
        return None
    vals = []
    for a, b in itertools.combinations(sets, 2):
        if isinstance(a, Counter):
            union = sum((a | b).values())
            vals.append(sum((a & b).values()) / union if union else 1.0)
        else:
            vals.append(len(a & b) / len(a | b) if a | b else 1.0)
    return round(mean(vals), 4)


def score_run(rec: dict, cfg: dict) -> dict:
    row = {"variant": rec["variant"], **s1.score(rec, cfg)}
    kpis = (rec.get("result") or {}).get("kpis") or []
    has_answer = rec["status"] in ("ok", "out_of_scope")
    for m in MODES:
        row[f"{m}_n_expected_found"] = len([e for e in row[f"{m}_found"].split("|") if e]) if has_answer else 0
    row["n_expected"] = len(cfg["expected_measurable_as"])
    row["kpi_keys"] = "|".join(sorted({kpi_key(k) for k in kpis}))
    row["categories"] = "|".join(sorted(str(k.get("category")) for k in kpis))
    row["description_passed_chars"] = len(rec.get("process_description_passed") or "")
    return row


def per_config(rows: list[dict], records: list[dict], cfg: dict, variant: str) -> dict:
    ok = [r for r in rows if r["status"] == "ok"]
    with_result = [rec for rec in records if rec.get("result")]
    out = {"config_id": cfg["id"], "process": cfg["process"], "goal_config": cfg["goal_config"], "variant": variant,
           "expected_measurable_as": "|".join(cfg["expected_measurable_as"]), "reps": len(rows), "reps_ok": len(ok),
           "scope_gate_passed": sum(bool(r["scope_gate_passed"]) for r in rows)}
    for m in MODES:
        counts = Counter(e for r in rows for e in r[f"{m}_found"].split("|") if e)
        out[f"{m}_found_counts"] = "|".join(f"{e}:{counts[e]}" for e in cfg["expected_measurable_as"])
        out[f"{m}_expected_identified"] = sum(r[f"{m}_n_expected_found"] for r in rows)
        out[f"{m}_expected_instances"] = len(rows) * len(cfg["expected_measurable_as"])
        out[f"{m}_reps_all_expected_found"] = sum(bool(r[f"{m}_all_expected_found"]) for r in rows)
        out[f"{m}_mean_n_extra"] = round(mean(r[f"{m}_n_extra"] for r in ok), 2) if ok else None
    out["mean_n_returned"] = round(mean(r["n_returned"] for r in ok), 2) if ok else None
    out["mean_attempts"] = round(mean(r["attempts"] for r in rows), 2)
    out["kpi_set_jaccard"] = pairwise([{kpi_key(k) for k in rec["result"]["kpis"]} for rec in with_result])
    out["category_stability"] = pairwise([Counter(str(k.get("category")) for k in rec["result"]["kpis"])
                                          for rec in with_result])
    return out


COUNT_TESTS = [(f"{m}_{what}", m, what) for m in MODES for what in ("reps_all_expected_found", "expected_identified")]
SIGN_MEASURES = ([f"{m}_reps_all_expected_found" for m in MODES] + [f"{m}_expected_identified" for m in MODES]
                 + [f"{m}_mean_n_extra" for m in MODES] + ["mean_n_returned", "kpi_set_jaccard", "category_stability"])


def totals(per_rows: list[dict], key: str) -> tuple[int, int]:
    """(successes, trials) of a count measure summed over configurations."""
    m, what = key.split("_", 1)
    n = sum(r["reps"] if what == "reps_all_expected_found" else r[f"{m}_expected_instances"] for r in per_rows)
    return sum(r[key] for r in per_rows), n


def fisher(a: int, na: int, b: int, nb: int) -> float:
    from scipy.stats import fisher_exact
    return float(fisher_exact([[a, na - a], [b, nb - b]])[1])


def write_csv(path: Path, rows: list[dict]) -> None:
    if rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)


def load_all(out: Path, configs: list[dict]) -> dict:
    data = {}
    for v in VARIANTS:
        for cfg in configs:
            files = sorted((out / "raw" / v / cfg["id"]).glob("rep_*.json"), key=lambda p: int(p.stem.split("_")[1]))
            data[(v, cfg["id"])] = [s1.load_json(p) for p in files]
    return data


def summarise(out: Path, configs: list[dict]) -> dict:
    data = load_all(out, configs)
    runs, per, cmp_rows = [], {v: [] for v in VARIANTS}, []
    for cfg in configs:
        for v in VARIANTS:
            recs = data[(v, cfg["id"])]
            if not recs:
                continue
            rows = [score_run(r, cfg) for r in recs]
            runs += rows
            per[v].append(per_config(rows, recs, cfg, v))
    by = {v: {r["config_id"]: r for r in per[v]} for v in VARIANTS}
    common = [c["id"] for c in configs if all(c["id"] in by[v] for v in VARIANTS)]
    for cid in common:
        a, b = by["with_description"][cid], by["no_description"][cid]
        row = {"config_id": cid}
        for key, m, what in COUNT_TESTS:
            na, nb = (a["reps"], b["reps"]) if what == "reps_all_expected_found" else \
                (a[f"{m}_expected_instances"], b[f"{m}_expected_instances"])
            row[f"{key}_with"], row[f"{key}_without"] = f"{a[key]}/{na}", f"{b[key]}/{nb}"
            row[f"{key}_fisher_p"] = round(fisher(a[key], na, b[key], nb), 4)
        for k in ("strict_mean_n_extra", "lenient_mean_n_extra", "mean_n_returned", "kpi_set_jaccard",
                  "category_stability"):
            row[f"{k}_with"], row[f"{k}_without"] = a[k], b[k]
        cmp_rows.append(row)
    variant_rows, tests = [], []
    for v in VARIANTS:
        rows = [r for r in runs if r["variant"] == v]
        if not rows:
            continue
        ok = [r for r in rows if r["status"] == "ok"]
        pr = per[v]
        vr = {"variant": v, "configurations": len(pr), "runs": len(rows), "runs_ok": len(ok),
              "runs_parse_error": sum(r["status"] == "parse_error" for r in rows),
              "runs_scope_gate_passed": sum(bool(r["scope_gate_passed"]) for r in rows)}
        for m in MODES:
            s, n = totals(pr, f"{m}_expected_identified")
            vr[f"{m}_expected_identified"] = f"{s}/{n}"
            s, n = totals(pr, f"{m}_reps_all_expected_found")
            vr[f"{m}_runs_all_expected_found"] = f"{s}/{n}"
            vr[f"{m}_mean_n_extra"] = round(mean(r[f"{m}_n_extra"] for r in ok), 2) if ok else None
        vr["mean_n_returned"] = round(mean(r["n_returned"] for r in ok), 2) if ok else None
        vr["mean_attempts"] = round(mean(r["attempts"] for r in rows), 2)
        for k in ("kpi_set_jaccard", "category_stability"):
            vals = [r[k] for r in pr if r[k] is not None]
            vr[f"{k}_mean_over_configs"] = round(mean(vals), 4) if vals else None
        variant_rows.append(vr)
    if common:
        A = [by["with_description"][c] for c in common]
        B = [by["no_description"][c] for c in common]
        for key, m, what in COUNT_TESTS:
            (a, na), (b, nb) = totals(A, key), totals(B, key)
            tests.append({"test": "Fisher exact, pooled", "measure": key, "with_description": f"{a}/{na}",
                          "no_description": f"{b}/{nb}", "n": "", "with_greater": "", "p": round(fisher(a, na, b, nb), 4)})
        for key in SIGN_MEASURES:
            diffs = [x[key] - y[key] for x, y in zip(A, B) if x[key] is not None and y[key] is not None]
            n, k, p = sign_test(diffs)
            tests.append({"test": "sign test over configurations", "measure": key, "with_description": "",
                          "no_description": "", "n": n, "with_greater": k, "p": round(p, 4)})
    write_csv(out / "description_ablation_runs.csv", runs)
    write_csv(out / "description_ablation_per_config.csv", per["with_description"] + per["no_description"])
    write_csv(out / "description_ablation_per_config_comparison.csv", cmp_rows)
    write_csv(out / "description_ablation_per_variant.csv", variant_rows)
    write_csv(out / "description_ablation_tests.csv", tests)
    return {"runs": runs, "per": per, "cmp": cmp_rows, "variant": variant_rows, "tests": tests}


# -------------------------------------------------------------- recording ---

def run_job(job: dict, ctxs: dict) -> None:
    ctx = ctxs[job["variant"]]
    s1.run_rep(job, ctx)
    rec = s1.load_json(job["path"])
    rec.update({"variant": job["variant"],
                "process_description_passed": ctx["descriptions"][job["cfg"]["process"]],
                "description_block_in_prompt": description_block(rec["user_prompt"]),
                "glass_commit": ctx["glass_commit"], "step1_batch_script_sha256": ctx["s1_sha"]})
    s1.write_json(job["path"], rec)


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=s1.REPO_ROOT, capture_output=True, text=True).stdout.strip()


def main() -> None:
    config = json.loads(s1.CONFIG_PATH.read_text(encoding="utf-8"))
    settings, configs = config["settings"], config["configurations"]
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--variant", default=",".join(VARIANTS))
    ap.add_argument("--only")
    ap.add_argument("--reps", type=int, default=settings["repetitions"])
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    if args.smoke:
        args.only, args.reps = "LoanApp_G1", 1
    variants = args.variant.split(",")
    if unknown := set(variants) - set(VARIANTS):
        sys.exit(f"Unknown variant(s): {sorted(unknown)}")
    selected = [c for c in configs if args.only is None or c["id"] in args.only.split(",")]
    if not args.report_only:
        if s1.glass_patch():
            sys.exit("goal_to_parameters/ has local changes; this ablation runs GLASS as committed")
        if settings["temperature"] != s1.app_temperature():
            sys.exit("config temperature differs from app.py's")
        sys.stdout = s1._MuteCostTracker(sys.stdout)
        logging.getLogger("utils.parsing").addHandler(s1._ParseWarnings())
        app, _ = s1.load_app_stage1()
        base = {"app": app, "settings": settings, "temperature": settings["temperature"],
                "log_sha": {c["id"]: s1.sha256((s1.LOGS_DIR / c["event_log"]).read_bytes()) for c in selected},
                "script_sha": s1.sha256(Path(__file__).read_bytes()), "app_sha": s1.sha256(s1.APP_PATH.read_bytes()),
                "patch_sha": None, "glass_commit": git("rev-parse", "HEAD"),
                "s1_sha": s1.sha256(Path(s1.__file__).read_bytes())}
        ctxs = {}
        for v in variants:
            descs = {p: description_for(v, d) for p, d in config["process_descriptions"].items()}
            ctxs[v] = {**base, "descriptions": descs,
                       "prepared": {c["id"]: s1.prepare_config(app, c, descs[c["process"]],
                                                               s1.LOGS_DIR / c["event_log"], settings)
                                    for c in selected}}
        jobs, total = [], len(variants) * len(selected) * args.reps
        combos = [(v, c, r) for v in variants for c in selected for r in range(1, args.reps + 1)]
        for n, (v, cfg, rep) in enumerate(combos, 1):
            path = args.out / "raw" / v / cfg["id"] / f"rep_{rep}.json"
            prefix = f"[{n}/{total}] {v} {cfg['id']} rep {rep} ..."
            if (old := s1.load_json(path)) and old.get("status") in s1.FINAL_STATUSES:
                s1.log(f"{prefix} skipped (exists: {old['status']})")
            else:
                jobs.append({"variant": v, "cfg": cfg, "rep": rep, "path": path, "prefix": prefix})
        s1.log(f"Settings: {settings}  workers={args.workers}  GLASS {base['glass_commit'][:7]} (no local changes)")
        with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="worker") as pool:
            futures = [pool.submit(run_job, j, ctxs) for j in jobs]
            try:
                for f in futures:
                    f.result()
            except s1.FatalAPIError as exc:
                pool.shutdown(wait=True, cancel_futures=True)
                sys.exit(f"{exc}\nAborted; rerun to resume.")
        if args.smoke:
            for v in variants:
                rec = s1.load_json(args.out / "raw" / v / "LoanApp_G1" / "rep_1.json")
                s1.log(f"\n--- {v}: status {rec['status']}, scope gate passed {rec['scope_gate_passed']} "
                       f"({rec['scope_gate_message']})\ndescription block in the prompt:\n{rec['description_block_in_prompt']!r}")
    summarise(args.out, configs)
    s1.log(f"Wrote the CSV files in {args.out}")


if __name__ == "__main__":
    main()

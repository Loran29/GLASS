"""
Stage-2 ablation on the synthetic processes (LoanApp / Procure2Pay), knowledge base off in every variant.

The real-life ablation's variants (evaluation/real_life_ablation/scripts/run_ablation_batch.py), applied to the
LoanApp/P2P setup of the v3.2 batch: each run is run_stage2_batch.run_rep, unchanged (setup/stage2_config.json,
the validated baselines, the Stage-1 KPIs with the 0.85 utilization target, the business context as the
clarification chat, GLASS's loop and the 10-seed evaluation, the v3.2 validity, recall and distances to the
healthy model), with the variant's switches applied from outside at the points run_ablation_batch.py switches:

  V_main        log evidence + clarification context + refinement loop (= the v3.2 batch without the knowledge
                base, i.e. its V_no_kb arm)
  V_no_log      no log profile: log_profile = None for the draft payload and the loop (no log or context evidence
                in the Stage-2 prompt)
  V_no_context  no clarification chat (GLASS defaults: no operational-constraint summary). The business context
                still defines validity, as in the real-life ablation
  V_no_refine   max_iterations = 1
  V_old_refine  run_iterative_evaluation(legacy_refinement=True): stop at the first iteration whose overall status
                is improved, best iteration by score alone (the v2 rules)
Every variant: GLASS's option use_knowledge_base = false (run_stage2_batch --no-knowledge-base).

Configurations: LoanApp_G1, LoanApp_G2, LoanApp_G1G2, Procure2Pay_G1, Procure2Pay_G2, Procure2Pay_G1G2 (lead
author's selection); 5 repetitions; seeds 42..51 for the evaluation.

Output: <out>/<variant>/ is a complete v3.2 batch folder (raw/<config>/rep_<k>.json, baseline_logs/,
healthy_logs/, logs/, log_archives/, stage2_summary.csv, stage2_per_config.csv with stage2_reporting.py's
columns, README.md), so verify_stage2_results.py checks each variant folder as it checks the v3.2 folders. The
baseline and healthy logs are simulated once (first variant) and copied to the other variant folders; they are
deterministic and their SHA-256 is in every record. Each record is run_rep's record with `variant` and
`switches` set to the ablation variant (run_rep's own label, V_no_kb, is kept as stage2_batch_variant),
`log_profile_passed` and `stage2_batch_script_sha256`. Reruns resume (final statuses are skipped).

Usage (repo root):
  python evaluation/loanapp_p2p/scripts/run_stage2_synthetic_ablation.py [--variant V[,V]] [--only ID[,ID]]
         [--reps N] [--workers N] [--out DIR] [--smoke]
--smoke = --only LoanApp_G1G2 --reps 1, all variants; writes smoke_report.txt (switches and final patch per variant).
Reporting: synthetic_ablation_reporting.py; check: verify_synthetic_ablation.py.
"""
from __future__ import annotations

import argparse
import copy
import functools
import json
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_stage2_batch as s2  # noqa: E402  (v3.2 batch: run_rep, caches, summaries)

s1 = s2.s1
CONFIGS = ["LoanApp_G1", "LoanApp_G2", "LoanApp_G1G2", "Procure2Pay_G1", "Procure2Pay_G2", "Procure2Pay_G1G2"]
VARIANTS = {
    "V_main":       {"use_log_profile": True,  "use_context": True,  "use_kb": False, "max_iterations": None, "legacy_refinement": False},
    "V_no_log":     {"use_log_profile": False, "use_context": True,  "use_kb": False, "max_iterations": None, "legacy_refinement": False},
    "V_no_context": {"use_log_profile": True,  "use_context": False, "use_kb": False, "max_iterations": None, "legacy_refinement": False},
    "V_no_refine":  {"use_log_profile": True,  "use_context": True,  "use_kb": False, "max_iterations": 1,    "legacy_refinement": False},
    "V_old_refine": {"use_log_profile": True,  "use_context": True,  "use_kb": False, "max_iterations": None, "legacy_refinement": True},
}
OUT_DEFAULT = s2.BASE / "results" / "stage2_synthetic_ablation"


def switches_of(variant: str, settings: dict) -> dict:
    sw = dict(VARIANTS[variant])
    sw["max_iterations"] = sw["max_iterations"] or settings["max_iterations"]
    return sw


def run_variant_rep(job: dict) -> dict:
    """One (variant, configuration, rep) in a worker process: run_rep with the variant's switches."""
    import second_llm.iterative_evaluator as ie
    sw = job["switches"]
    orig_load, orig_loop = s2.load_inputs, ie.run_iterative_evaluation
    if not sw["use_context"]:
        s2.load_inputs = lambda cfg, config: {**orig_load(cfg, config), "chat": []}
    if sw["legacy_refinement"]:
        ie.run_iterative_evaluation = functools.partial(orig_loop, legacy_refinement=True)
    try:
        res = s2.run_rep(job)
    finally:
        s2.load_inputs, ie.run_iterative_evaluation = orig_load, orig_loop
    if "fatal" in res:
        return res
    path = Path(job["out"]) / "raw" / job["cfg"]["config_id"] / f"rep_{job['rep']}.json"
    rec = s1.load_json(path)
    rec["stage2_batch_variant"], rec["stage2_batch_switches"] = rec.get("variant"), rec.get("switches")
    rec.update({"variant": job["variant"], "switches": sw,
                "log_profile_passed": job["log_profile"][job["cfg"]["config_id"]] is not None,
                "stage2_batch_script_sha256": job["s2_sha"]})
    s1.write_json(path, rec)
    return {**res, "variant": job["variant"]}


def final_patch_text(rec: dict) -> str:
    loop = rec.get("loop") or {}
    best = loop.get("best_iteration")
    lines = [f"status {rec['status']}; iterations {len(loop.get('iterations') or [])}; best iteration {best}; "
             f"no_improving_scenario {loop.get('no_improving_scenario')}"]
    if best:
        it = loop["iterations"][best - 1]
        for m in (it.get("patch") or {}).get("modifications") or []:
            lines.append(f"    {m.get('parameter_type')} {m.get('target_element')}"
                         f"{'.' + m['target_field'] if m.get('target_field') else ''}: "
                         f"{json.dumps(m.get('baseline_value'))} -> {json.dumps(m.get('proposed_value'))}")
    return "\n".join(lines)


def smoke_report(out: Path, variants: list[str], cid: str) -> str:
    parts = []
    for v in variants:
        rec = s1.load_json(out / v / "raw" / cid / "rep_1.json")
        its = (rec.get("loop") or {}).get("iterations") or []
        calls = rec.get("llm_calls") or []
        gen = [c for c in calls if "Verified KPI Targets" in (c.get("user_prompt") or "")]
        prompt = gen[0]["user_prompt"] if gen else ""
        parts.append("\n".join([
            f"=== {v} ({cid} rep 1)",
            f"switches: {json.dumps(rec['switches'])}",
            f"settings.max_iterations {rec['settings']['max_iterations']}; log_profile_passed {rec['log_profile_passed']}; "
            f"chat_user_turns {len(rec['chat_user_turns'])}; kb_switch {rec.get('kb_switch')}",
            f"first generation prompt: log evidence section {'Event Log Evidence (filtered)' in prompt}, "
            f"constraints section {'## Hard Operational Constraints' in prompt}, "
            f"knowledge-base section {'Literature-Backed Parameter Recommendations' in prompt}",
            f"iterations (status, score): {[(it['iteration'], it.get('status'), it.get('score')) for it in its]}",
            "final patch: " + final_patch_text(rec)]))
    return "\n\n".join(parts)


def rel(path) -> str:
    """Path relative to the repository root (as written in READMEs); absolute outside it (smoke folders)."""
    p = Path(path).resolve()
    return p.relative_to(s1.REPO_ROOT).as_posix() if p.is_relative_to(s1.REPO_ROOT) else p.as_posix()


def write_variant_readme(out_v: Path, variant: str, sw: dict, config: dict, commit: str, started: str, ended: str) -> None:
    recs = [s1.load_json(p) for p in sorted((out_v / "raw").glob("*/rep_*.json"))]
    stamps = sorted(r["timestamp"] for r in recs)
    L = [f"# Stage 2 synthetic ablation, variant {variant}", "",
         f"Written by `{rel(Path(__file__))}`. One folder of the ablation in `..` (see `../README.md`).", "",
         f"- GLASS commit: `{commit}` (v3.3, no local changes under `goal_to_parameters/`)",
         f"- Switches: `{json.dumps(sw)}`",
         f"- Configurations: {', '.join(c['config_id'] for c in config['configurations'])}; "
         f"{config['settings']['repetitions']} repetitions; evaluation seeds 42..{41 + config['settings']['seeds']}",
         f"- Records: {len(recs)}; first / last run started {stamps[0] if stamps else ''} / {stamps[-1] if stamps else ''}",
         f"- This invocation: {started} to {ended}",
         "- Each run is `run_stage2_batch.run_rep` with the switches above (see the header of "
         f"`{rel(Path(__file__))}`); `stage2_summary.csv` and `stage2_per_config.csv` are the v3.2 batch's "
         "summaries plus `stage2_reporting.py`'s columns.",
         f"- Check: `python evaluation/loanapp_p2p/scripts/verify_stage2_results.py --results {rel(out_v)} "
         f"--only {','.join(CONFIGS)}`", ""]
    (out_v / "README.md").write_text("\n".join(L), encoding="utf-8", newline="\n")


def main() -> None:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    config = json.loads(s2.CONFIG_PATH.read_text(encoding="utf-8"))
    settings = config["settings"]
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--variant", default=",".join(VARIANTS))
    ap.add_argument("--only", default=",".join(CONFIGS))
    ap.add_argument("--reps", type=int, default=settings["repetitions"])
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()
    if args.smoke:
        args.only, args.reps = "LoanApp_G1G2", 1
    variants = args.variant.split(",")
    only = args.only.split(",")
    if set(variants) - set(VARIANTS) or set(only) - set(CONFIGS):
        sys.exit(f"variants must be in {list(VARIANTS)}, configurations in {CONFIGS}")
    if s2.git("status", "--porcelain", "--", "goal_to_parameters"):
        sys.exit("goal_to_parameters/ has local changes; the ablation runs on committed GLASS code")
    s2.quiet()
    commit, config_sha = s2.git("rev-parse", "HEAD"), s1.sha256(s2.CONFIG_PATH.read_bytes())
    config_sel = copy.deepcopy(config)
    config_sel["configurations"] = [c for c in config["configurations"] if c["config_id"] in CONFIGS]
    selected = [c for c in config_sel["configurations"] if c["config_id"] in only]
    app, _ = s1.load_app_stage1()

    prepared, first_v = {}, None
    for v in variants:
        out_v = args.out / v
        if first_v is not None:                   # deterministic caches: copy the first variant's
            for cfg in selected:
                src = args.out / first_v / "baseline_logs" / cfg["config_id"]
                if src.exists() and not (out_v / "baseline_logs" / cfg["config_id"] / "baseline.json").exists():
                    shutil.copytree(src, out_v / "baseline_logs" / cfg["config_id"], dirs_exist_ok=True)
            for process in {c["process"] for c in selected}:
                src = args.out / first_v / "healthy_logs" / process
                if src.exists():
                    shutil.copytree(src, out_v / "healthy_logs" / process, dirs_exist_ok=True)
        healthy_sha = {}
        for process in sorted({c["process"] for c in selected}):
            s1.log(f"{v}: healthy logs for {process} ...")
            healthy_sha[process] = s2.prepare_healthy(process, next(c["healthy_dir"] for c in selected
                                                                   if c["process"] == process), config, out_v)
        shared = {}
        for cfg in selected:
            s1.log(f"{v}: baseline runs for {cfg['config_id']} ...")
            shared[cfg["config_id"]] = s2.prepare_config(cfg, config, out_v, app)
        log_profile = {cid: sh.pop("log_profile") for cid, sh in shared.items()}
        healthy_scn = {p: s2.healthy_scenario(p, next(c["healthy_dir"] for c in selected if c["process"] == p), config)
                       for p in healthy_sha}
        prepared[v] = (out_v, healthy_sha, shared, log_profile, healthy_scn)
        first_v = first_v or v

    jobs = []
    script_sha, s2_sha = s1.sha256(Path(__file__).read_bytes()), s1.sha256(Path(s2.__file__).read_bytes())
    for rep in range(1, args.reps + 1):
        for cfg in selected:
            for v in variants:
                out_v, healthy_sha, shared, log_profile, healthy_scn = prepared[v]
                path = out_v / "raw" / cfg["config_id"] / f"rep_{rep}.json"
                if (old := s1.load_json(path)) and old.get("status") in s2.FINAL_STATUSES:
                    s1.log(f"{v} {cfg['config_id']} rep {rep} skipped (exists: {old['status']})")
                    continue
                sw = switches_of(v, settings)
                cfg_v = copy.deepcopy(config)
                cfg_v["settings"]["max_iterations"] = sw["max_iterations"]
                jobs.append({"config": cfg_v, "cfg": cfg, "rep": rep, "out": str(out_v), "glass_commit": commit,
                             "use_kb": sw["use_kb"], "loop_seeds": 1, "config_sha": config_sha,
                             "script_sha": script_sha, "s2_sha": s2_sha, "variant": v, "switches": sw,
                             "log_profile": {cid: (lp if sw["use_log_profile"] else None)
                                             for cid, lp in log_profile.items()},
                             "shared": shared, "healthy_sha": healthy_sha, "healthy_scenario": healthy_scn})
    s1.log(f"{len(jobs)} run(s) to do; variants {variants}; knowledge base off; workers {args.workers}; GLASS {commit[:7]}")
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_variant_rep, j) for j in jobs]
        for done, fut in enumerate(futures, 1):
            res = fut.result()
            if "fatal" in res:
                pool.shutdown(wait=True, cancel_futures=True)
                sys.exit(f"Fatal API error: {res['fatal']}\nAborted; runs written so far are kept, rerun to resume.")
            s1.log(f"[{done}/{len(jobs)}] {res['variant']} {res['config_id']} rep {res['rep']}: {res['status']} "
                   f"({res['elapsed_s']} s) {' '.join(res['error'])}")
    ended = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for v in variants:
        out_v = prepared[v][0]
        s2.write_summaries(out_v, config_sel)
        s2.zip_logs(out_v, config_sel)
        write_variant_readme(out_v, v, switches_of(v, settings), config_sel, commit, started, ended)
        subprocess.run([sys.executable, str(Path(__file__).with_name("stage2_reporting.py")), "--results", str(out_v)],
                       check=True, cwd=s1.REPO_ROOT)
    if args.smoke:
        text = smoke_report(args.out, variants, "LoanApp_G1G2")
        (args.out / "smoke_report.txt").write_text(text, encoding="utf-8")
        s1.log(text)


if __name__ == "__main__":
    main()

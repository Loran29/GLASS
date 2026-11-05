"""Run GLASS on the LoanApp_G3 example from the command line.

  stage1   Stage 1 (goal -> KPIs): one LLM call through GLASS's own Stage-1 path, as the UI's
           "Generate SMART KPIs" button runs it.
  stage23  Stage 2 (scenario generation) and Stage 3 (simulation-based evaluation and refinement):
           the clarification answers from business_context.json, patch generation, merging, Prosimos
           simulation, the refinement loop (up to 4 iterations, one seed each) and the final
           multi-seed evaluation against the baseline on --seeds seeds.

Both commands call the per-run functions of the evaluation batch scripts
(evaluation/loanapp_p2p/scripts/run_stage1_batch.py and run_stage2_batch.py) with the inputs of this folder,
so a run is recorded exactly like a run of the LoanApp/P2P evaluation. No git checkout is needed.

  python examples/loanapp/run_example.py stage1 [--out DIR]
  python examples/loanapp/run_example.py stage23 [--seeds N] [--stage1-record FILE] [--kpis NAME[,NAME...]]
                                                 [--use-knowledge-base | --no-knowledge-base] [--out DIR]

stage23 uses, by default, the Stage-1 record the evaluation used for LoanApp_G3
(stage1_record_LoanApp_G3_rep1.json) and the KPI selected there. Pass --stage1-record with the record written
by `stage1` to continue from your own Stage-1 run; its utilization KPIs are then passed (or the KPIs named
with --kpis). The knowledge base (RAG) is off by default, as in GLASS (config.yaml use_knowledge_base);
--use-knowledge-base switches it on for this run. The API key is read from goal_to_parameters/.env
(OPENAI_API_KEY or OPENROUTER_API_KEY).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "evaluation" / "loanapp_p2p" / "scripts"))
CONFIG_FILE = HERE / "example_config.json"
CONFIG = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def shown(path: Path) -> str:
    """A path for messages: relative to the repository root when it lies inside it."""
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return str(path)


def stage1(args) -> None:
    import run_stage1_batch as s1

    settings = CONFIG["stage1"]["settings"]
    cfg = dict(CONFIG["stage1"]["configuration"])
    cfg["simulation_goal"] = (HERE / "goal.txt").read_text(encoding="utf-8").strip()
    description = (HERE / "process_description.txt").read_text(encoding="utf-8").strip()
    log_path = HERE / cfg["event_log"]
    sys.stdout = s1._MuteCostTracker(sys.stdout)
    logging.getLogger("utils.parsing").addHandler(s1._ParseWarnings())
    app, _ = s1.load_app_stage1()
    ctx = {
        "app": app, "settings": settings, "temperature": settings["temperature"],
        "descriptions": {cfg["process"]: description},
        "prepared": {cfg["id"]: s1.prepare_config(app, cfg, description, log_path, settings)},
        "log_sha": {cfg["id"]: s1.sha256(log_path.read_bytes())},
        "script_sha": s1.sha256(Path(s1.__file__).read_bytes()), "app_sha": s1.sha256(s1.APP_PATH.read_bytes()),
        "patch_sha": None,
    }
    path = args.out / "raw" / cfg["id"] / "rep_1.json"
    s1.run_rep({"cfg": cfg, "rep": 1, "path": path, "prefix": f"Stage 1, {cfg['id']}:"}, ctx)
    rec = s1.load_json(path)
    print(f"\nrecord: {shown(path)}")
    print(f"status: {rec['status']}   scope gate passed: {rec['scope_gate_passed']}   "
          f"LLM attempts: {len(rec['attempts'])}")
    for k in (rec.get("result") or {}).get("kpis", []):
        print(f"  KPI: {k['name']} | category {k['category']} | measurable_as {k['measurable_as']} | "
              f"direction {k['target_direction']}")


def choose_kpis(cfg: dict, record_path: Path, requested: str | None) -> list[str]:
    rec = json.loads(record_path.read_text(encoding="utf-8"))
    if not rec.get("result"):
        sys.exit(f"{record_path} has no KPI result (status {rec.get('status')})")
    kpis = rec["result"]["kpis"]
    names = [k["name"] for k in kpis]
    if requested:
        chosen = [n.strip() for n in requested.split(",")]
        if missing := [n for n in chosen if n not in names]:
            sys.exit(f"KPI(s) {missing} not in {record_path}; available: {names}")
        return chosen
    if all(n in names for n in cfg["kpi_names_to_pass"]):
        return cfg["kpi_names_to_pass"]
    return [k["name"] for k in kpis if k.get("category") == "utilization"] or names


def stage23(args) -> None:
    import run_stage2_batch as s2
    s1 = s2.s1

    s2.BASE = HERE                     # the configuration's relative paths point into this folder
    st = CONFIG["stage2"]
    cfg = dict(st["configuration"])
    if args.stage1_record:
        cfg["kpi_source_record"] = str(args.stage1_record.resolve())
    cfg["kpi_names_to_pass"] = choose_kpis(cfg, s2.BASE / s2.kpi_source(cfg), args.kpis)
    cfg["kpi_inputs"] = {n: v for n, v in (cfg.get("kpi_inputs") or {}).items() if n in cfg["kpi_names_to_pass"]}
    config = {"settings": {**st["settings"], "seeds": args.seeds},
              "business_context": {cfg["process"]: json.loads((HERE / "business_context.json").read_text(encoding="utf-8"))},
              "expected_fix": st["expected_fix"], "configurations": [cfg]}
    out, cid, process = args.out, cfg["config_id"], cfg["process"]
    s2.quiet()
    s2.load_inputs(cfg, config)        # checks the KPI objects before any LLM call
    app, _ = s1.load_app_stage1()
    s1.log(f"healthy reference logs for {process} ({args.seeds} seeds) ...")
    healthy_sha = {process: s2.prepare_healthy(process, cfg["healthy_dir"], config, out)}
    s1.log(f"baseline runs for {cid} ({args.seeds} seeds) ...")
    shared = {cid: s2.prepare_config(cfg, config, out, app)}
    log_profile = {cid: shared[cid].pop("log_profile")}
    healthy_scn = {process: s2.healthy_scenario(process, cfg["healthy_dir"], config)}
    job = {"config": config, "cfg": cfg, "rep": 1, "out": str(out), "glass_commit": s2.git("rev-parse", "HEAD"),
           "use_kb": args.use_kb, "config_sha": s1.sha256(CONFIG_FILE.read_bytes()),
           "script_sha": s1.sha256(Path(s2.__file__).read_bytes()), "log_profile": log_profile,
           "shared": shared, "healthy_sha": healthy_sha, "healthy_scenario": healthy_scn}
    s1.log(f"Stages 2+3, {cid}, KPIs {cfg['kpi_names_to_pass']}, knowledge base {'on' if args.use_kb else 'off'} ...")
    res = s2.run_rep(job)
    if "fatal" in res:
        sys.exit(f"Fatal API error: {res['fatal']}")
    path = out / "raw" / cid / "rep_1.json"
    rec = s1.load_json(path)
    report = out / "report.txt"
    report.write_text(s2.smoke_report(rec), encoding="utf-8")
    loop = rec.get("loop") or {}
    print(f"\nrecord: {shown(path)}\nfull report: {shown(report)}")
    print(f"status: {rec['status']}   elapsed: {rec['elapsed_s']} s   iterations: {len(loop.get('iterations', []))}   "
          f"final iteration: {loop.get('best_iteration')}"
          + ("   (no iteration improved on the baseline; baseline kept)" if loop.get("no_improving_scenario") else ""))
    for it in loop.get("iterations", []):
        mods = "; ".join(f"{m['parameter_type']} {m['target_element']}: {m['baseline_value']} -> {m['proposed_value']}"
                         for m in (it.get("patch") or {}).get("modifications", []))
        print(f"  iteration {it['iteration']}: status {it['status']}, score {it['score']:.1f} | {mods}")
    for phase in ("initial", "final"):
        ev = (rec.get("evaluation") or {}).get(phase)
        if not ev:
            continue
        print(f"  {phase} (iteration {ev.get('iteration')}, {args.seeds} seeds): valid {ev.get('valid')}")
        for k in ev.get("kpis", []):
            print(f"    {k['kpi']}: baseline {k['baseline_mean']} -> proposed {k['proposed_mean']} "
                  f"({k['pct_change']} %), improved {k['improved']}")
        if ev.get("utilization_rule"):
            u = ev["utilization_rule"]
            print(f"    target-role utilization: {u['baseline_mean_utilization']} -> {u['proposed_mean_utilization']} "
                  f"({u['verdict']})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p1 = sub.add_parser("stage1", help="Stage 1: goal -> KPIs")
    p1.add_argument("--out", type=Path, default=HERE / "output" / "stage1")
    p2 = sub.add_parser("stage23", help="Stages 2+3: scenario generation, simulation-based evaluation and refinement")
    p2.add_argument("--seeds", type=int, default=2, help="seeds of the final multi-seed evaluation (evaluation: 10)")
    p2.add_argument("--stage1-record", type=Path, help="Stage-1 record to take the KPIs from")
    p2.add_argument("--kpis", help="KPI names to pass, comma-separated")
    kb = p2.add_mutually_exclusive_group()
    kb.add_argument("--use-knowledge-base", dest="use_kb", action="store_true", help="knowledge base (RAG) on")
    kb.add_argument("--no-knowledge-base", dest="use_kb", action="store_false", help="knowledge base off (default)")
    p2.set_defaults(use_kb=False)
    p2.add_argument("--out", type=Path, default=HERE / "output" / "stage23")
    args = ap.parse_args()
    if args.command == "stage1":
        stage1(args)
    else:
        if args.seeds < 2:
            ap.error("--seeds must be at least 2 (95 % confidence intervals)")
        stage23(args)


if __name__ == "__main__":
    main()

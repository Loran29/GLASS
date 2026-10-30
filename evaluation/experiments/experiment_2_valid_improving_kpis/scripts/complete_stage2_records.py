"""
Completion step for Stage-2 records whose iteration 1 produced no scenario (status "no_initial_proposal").

run_stage2_batch.py stops evaluating a run when iteration 1 has no scenario, even if a later iteration (the
loop's best = final) has one. For those runs this script evaluates the final exactly as the batch evaluates a
phase (same functions, same seeding, cached paired baseline, no LLM calls) and records:
  evaluation.initial = {"iteration": 1, "error": "no scenario"} (scored as not valid),
  evaluation.final   = the batch's phase evaluation,
  status "ok", status_at_run "no_initial_proposal", initial_proposal false, and completed_by (script, SHA-256,
  timestamp). Everything the batch recorded (loop, LLM calls, patches) is kept unchanged.
Runs in which no iteration produced a scenario keep the status "no_initial_proposal".
Afterwards the summary CSVs are rewritten with the batch's own write_summaries; run stage2_reporting.py next.

Usage (repo root, same environment as the batch):
  python evaluation/experiments/experiment_2_valid_improving_kpis/scripts/complete_stage2_records.py [--results <folder>]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_stage2_batch as b  # noqa: E402
from run_stage2_batch import s1  # noqa: E402


def complete(path: Path, out: Path, config: dict) -> str:
    import second_llm.multi_seed_evaluation as mse
    from second_llm.orchestrator import SecondLLMWorkspaceOrchestrator
    from second_llm.output_schema import SimuBridgeScenario
    from second_llm.state import reset_workspace
    from ui.evaluation_panel import _build_cost_map, _get_kpi_targets_from_workspace

    record = json.loads(path.read_text(encoding="utf-8"))
    iterations = (record.get("loop") or {}).get("iterations") or []
    best = (record.get("loop") or {}).get("best_iteration")
    if record.get("status") != "no_initial_proposal" or not best or iterations[best - 1]["simulated_scenario"] is None:
        return "skipped"
    cfg = next(c for c in config["configurations"] if c["config_id"] == record["config_id"])
    settings, cid, rep = config["settings"], cfg["config_id"], record["rep"]
    inputs = b.load_inputs(cfg, config)
    if inputs["stage1_result"] != record["stage1_result_passed"]:
        raise SystemExit(f"{cid} rep {rep}: inputs differ from the recorded run")
    reset_workspace()
    orch = SecondLLMWorkspaceOrchestrator(provider=None)
    orch.parse_first_llm_json(json.dumps(inputs["stage1_result"], ensure_ascii=False))
    eval_targets = b.apply_thresholds(_get_kpi_targets_from_workspace(), inputs["kpi_thresholds"])
    baseline = b.load_scenario(inputs["sim_params_text"], inputs["bpmn"])
    base_json, cost_map = baseline.model_dump(mode="json"), _build_cost_map(baseline)
    shared = json.loads((out / "baseline_logs" / cid / "baseline.json").read_text(encoding="utf-8"))
    hscn = b.healthy_scenario(cfg["process"], cfg["healthy_dir"], config)

    cached = {s: pd.read_csv(out / "baseline_logs" / cid / f"seed_{s}.csv.gz") for s in record["seeds"]}
    healthy_raw = [b.prep_log(pd.read_csv(out / "healthy_logs" / cfg["process"] / f"seed_{s}.csv.gz"))
                   for s in record["seeds"]]
    healthy, _ = b.corrected(healthy_raw, hscn)
    logs_dir = out / "logs" / cid / f"rep_{rep}"
    patches = b.Patches()
    evaluation, log_sha = {"initial": {"iteration": 1, "error": "no scenario"}}, {}
    it_no, it = best, iterations[best - 1]
    proposed = SimuBridgeScenario.model_validate(it["simulated_scenario"])
    logs: dict[int, pd.DataFrame] = {}
    b.seed_shim(patches, mse, baseline=baseline, cached=cached, logs=logs)
    try:
        res = mse.evaluate_multi_seed(baseline, proposed, inputs["bpmn"], eval_targets, num_seeds=settings["seeds"],
                                      base_seed=b.BASE_SEED, total_cases=settings["cases"],
                                      start_time=settings["start"], cost_per_hour=cost_map)
    finally:
        patches.restore()
    util = None
    if inputs["util_kpis"] and res.ok:
        prop_u = [b.pool_utilization(b.prep_log(logs[s]), it["simulated_scenario"], inputs["util_pool"])
                  for s in record["seeds"]]
        util = b.utilization_verdict(shared["baseline_pool_utilization"], [u["utilization"] for u in prop_u])
        util["proposed_detail"] = prop_u
    for s in record["seeds"]:
        if s in logs:
            p = logs_dir / f"final_seed_{s}.csv.gz"
            p.parent.mkdir(parents=True, exist_ok=True)
            logs[s].to_csv(p, index=False, compression="gzip")
            log_sha[p.name] = s1.sha256(p.read_bytes())
    dist = mapped = None
    if res.ok:
        raw_logs = [b.prep_log(logs[s]) for s in record["seeds"]]
        corr_logs, mapped = b.corrected(raw_logs, it["simulated_scenario"])
        dist = b.distances(corr_logs, healthy, raw_logs, healthy_raw)
    violations = b.check_validity(it["simulated_scenario"], base_json, cfg["process"], inputs["business_context"])
    evaluation["final"] = {
        "iteration": it_no, "same_as_initial": False, "multi_seed_ok": res.ok, "multi_seed_error": res.error,
        "kpis": b.kpi_table(res, util, inputs["util_kpis"]) if res.ok else [], "utilization_rule": util,
        "summary": b.dump(res.summary), "simulation_settings": res.simulation_settings, "n_logs": len(logs),
        "patch_validator_passed": it["patch_validator_passed"],
        "post_merge_validation_errors": [i["message"] for i in
                                         ((it["generation"] or {}).get("post_merge_validation") or {}).get("issues", [])
                                         if i.get("severity") == "error"],
        "violations": violations,
        "valid": bool(it["patch_validator_passed"]) and res.ok and not violations,
        "scores": b.score_patch((it["patch"] or {}).get("modifications", []), cfg,
                                config["expected_fix"][cfg["process"]], base_json),
        "distance_to_healthy": {k: v["mean"] for k, v in dist.items()} if dist else None,
        "midnight_ends_mapped_per_seed": mapped,
        "distance_to_healthy_per_seed": {k: v["per_seed"] for k, v in dist.items()} if dist else None,
        "improvement_ratio": {k: (round(1 - dist[k]["mean"] / shared["baseline_distance"][k], 4)
                                  if shared["baseline_distance"][k] else None)
                              for k in b.MEASURES + ("CWD_raw",)} if dist else None}
    record.update(evaluation=evaluation, baseline_distance_to_healthy=shared["baseline_distance"],
                  baseline_pool_utilization=shared["baseline_pool_utilization"],
                  logs_dir=logs_dir.relative_to(out).as_posix(), logs_sha256=log_sha,
                  status="ok", status_at_run="no_initial_proposal", initial_proposal=False,
                  completed_by={"script": s1.rel(__file__), "sha256": s1.sha256(Path(__file__).read_bytes()),
                                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "reason": "iteration 1 produced no scenario; final (best iteration) evaluated "
                                          "with the batch's phase evaluation"})
    s1.write_json(path, record)
    return f"completed (final = iteration {it_no}, valid {evaluation['final']['valid']})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=b.P.OLDER / "stage2_results")
    out = ap.parse_args().results
    b.quiet()
    config = json.loads(b.CONFIG_PATH.read_text(encoding="utf-8"))
    for path in sorted((out / "raw").glob("*/rep_*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec.get("status") == "no_initial_proposal":
            print(f"{rec['config_id']} rep {rec['rep']}: {complete(path, out, config)}", flush=True)
    b.write_summaries(out, config)      # the batch's own summary code, rerun over the completed records
    b.zip_logs(out, config)             # archives now include the logs written here
    print("summary CSVs and log archives rewritten", flush=True)


if __name__ == "__main__":
    main()

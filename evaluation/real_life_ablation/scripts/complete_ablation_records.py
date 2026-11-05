"""
Completion step for ablation records whose iteration 1 produced no scenario (status "no_initial_proposal").

run_ablation_batch.py (like the Stage-2 batch it derives from) stops evaluating a run when iteration 1 has no
scenario, even if a later iteration (the loop's best = final) has one, or the loop kept the baseline. For those
runs this script completes the evaluation from the record, without LLM calls:
  evaluation.initial = {"iteration": 1, "scenario_produced": false, ...} (not valid, not admissible);
  evaluation.final   = the loop kept the baseline (no_improving_scenario): no scenario, reason recorded;
                       otherwise the final iteration evaluated exactly as the batch evaluates a phase (the batch's
                       own functions, same seeding, the cached paired 10-seed baseline, distances to the test log);
  status "ok", status_at_run "no_initial_proposal", initial_proposal false, and completed_by (script, SHA-256,
  timestamp, reason). Everything the batch recorded (loop, LLM calls, patches, merge counts) is kept unchanged.
Runs in which no iteration produced a scenario keep the status "no_initial_proposal".

Usage (repo root, same environment as the batch):
  python evaluation/real_life_ablation/scripts/complete_ablation_records.py [--results DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_ablation_batch as rb  # noqa: E402
from run_ablation_batch import s1, s2  # noqa: E402


def complete(path: Path, out: Path, config: dict, kpis: dict) -> str:
    import second_llm.multi_seed_evaluation as mse
    from second_llm.orchestrator import SecondLLMWorkspaceOrchestrator
    from second_llm.output_schema import SimuBridgeScenario
    from second_llm.state import reset_workspace
    from ui.evaluation_panel import _build_cost_map, _get_kpi_targets_from_workspace

    record = json.loads(path.read_text(encoding="utf-8"))
    loop = record.get("loop") or {}
    iterations = loop.get("iterations") or []
    best, kept = loop.get("best_iteration"), bool(loop.get("no_improving_scenario"))
    has_final = bool(best) and iterations[best - 1].get("simulated_scenario") is not None
    if record.get("status") != "no_initial_proposal" or not (has_final or kept):
        return "skipped (no iteration produced a scenario)"
    cfg = next(c for c in config["configurations"] if c["config_id"] == record["config_id"])
    ds, rep, cell = cfg["dataset"], record["rep"], f"{record['config_id']}__{record['variant']}"
    settings = rb.dataset_settings(config, ds)
    inputs = rb.load_inputs(cfg, config, kpis)
    if inputs["stage1_result"] != record["stage1_result_passed"]:
        raise SystemExit(f"{cell} rep {rep}: inputs differ from the recorded run")
    no_scenario = {"scenario_produced": False, "valid": False, "executable": False, "admissible": False}
    evaluation = {"initial": {"iteration": 1, **no_scenario, "error": "no scenario"}}
    log_sha, logs_dir = {}, out / "logs" / cell / f"rep_{rep}"
    reason = "iteration 1 produced no scenario; "
    if kept:
        evaluation["final"] = {"iteration": None, **no_scenario, "reason": "no_improving_scenario",
                               "candidate_iteration": loop.get("best_candidate_iteration")}
        reason += "the loop kept the baseline (no_improving_scenario)"
    else:
        reset_workspace()
        orch = SecondLLMWorkspaceOrchestrator(provider=None)
        orch.parse_first_llm_json(json.dumps(inputs["stage1_result"], ensure_ascii=False))
        eval_targets = _get_kpi_targets_from_workspace()
        baseline = s2.load_scenario(inputs["sim_params_text"], inputs["bpmn"])
        base_json, cost_map = baseline.model_dump(mode="json"), _build_cost_map(baseline)
        cached = {s: pd.read_csv(out / "baseline_logs" / ds / f"seed_{s}.csv.gz") for s in record["seeds"]}
        test = rb.read_test_log(config, ds)
        it = iterations[best - 1]
        proposed = SimuBridgeScenario.model_validate(it["simulated_scenario"])
        logs: dict[int, pd.DataFrame] = {}
        patches = s2.Patches()
        s2.seed_shim(patches, mse, baseline=baseline, cached=cached, logs=logs)
        try:
            res = mse.evaluate_multi_seed(baseline, proposed, inputs["bpmn"], eval_targets,
                                          num_seeds=settings["seeds"], base_seed=s2.BASE_SEED,
                                          total_cases=settings["cases"], start_time=settings["start"],
                                          cost_per_hour=cost_map)
        finally:
            patches.restore()
        for s in record["seeds"]:
            if s in logs:
                p = logs_dir / f"final_seed_{s}.csv.gz"
                p.parent.mkdir(parents=True, exist_ok=True)
                logs[s].to_csv(p, index=False, compression="gzip")
                log_sha[p.name] = s1.sha256(p.read_bytes())
        executable = bool(res.ok) and all(s in logs for s in record["seeds"])
        dist = mapped = None
        if executable:
            raw_logs = [s2.prep_log(logs[s]) for s in record["seeds"]]
            for lg in raw_logs:
                lg["resource"] = lg["resource"].astype(str)
            corr_logs, mapped = s2.corrected(raw_logs, it["simulated_scenario"])
            dist = rb.test_log_distances(corr_logs, test)
        mods = (it["patch"] or {}).get("modifications", [])
        violations = rb.check_validity(it["simulated_scenario"], base_json)
        kpi_rows = s2.kpi_table(res, None, []) if res.ok else []
        dhr, nis = rb.dhr_nis(kpi_rows)
        counts = it.get("merge_counts") or {}
        stability = counts.get("merge_stability")
        valid = bool(it["patch_validator_passed"]) and not violations
        evaluation["final"] = {
            "iteration": best, "same_as_initial": False, "scenario_produced": True,
            "multi_seed_ok": res.ok, "multi_seed_error": res.error, "n_logs": len(logs),
            "patch_validator_passed": it["patch_validator_passed"],
            "post_merge_validation_errors": [i["message"] for i in
                                             ((it["generation"] or {}).get("post_merge_validation") or {}).get("issues", [])
                                             if i.get("severity") == "error"],
            "violations": violations, "demand_changed": any(v.startswith("demand_changed") for v in violations),
            "demand_proposed": any(m.get("parameter_type") == "inter_arrival_time" for m in mods),
            "rate_changed": any(v.startswith("rate_changed") for v in violations),
            "staff_cost_per_week": {"baseline": rb.staff_cost_per_week(base_json),
                                    "proposed": rb.staff_cost_per_week(it["simulated_scenario"])},
            "valid": valid, "executable": executable, "merge_stability": stability,
            "glass_merge_stability": counts.get("glass_merge_stability"), "merge_counts": counts or None,
            "admissible": valid and executable and (stability or 0) > 0,
            "kpis": kpi_rows, "all_kpis_improved": bool(kpi_rows) and all(k["glass_improved"] is True for k in kpi_rows),
            "DHR": dhr, "NIS": nis, "summary": s2.dump(res.summary), "simulation_settings": res.simulation_settings,
            "change_profile": rb.change_profile(mods, rb.x_aliases(base_json, inputs["x_activity"])),
            "distance_to_test_log": {k: v["mean"] for k, v in dist.items()} if dist else None,
            "distance_to_test_log_per_seed": {k: v["per_seed"] for k, v in dist.items()} if dist else None,
            "midnight_ends_mapped_per_seed": mapped}
        reason += f"final (best iteration {best}) evaluated with the batch's phase evaluation"
    shared = json.loads((out / "baseline_logs" / ds / "baseline.json").read_text(encoding="utf-8"))
    flag, cond = rb.discarded(evaluation, kept_baseline=kept)
    record.update(evaluation=evaluation, loop_discarded_better_initial=flag, loop_discarded_condition=cond,
                  baseline_distance_to_test_log=shared["baseline_distance"],
                  logs_dir=logs_dir.relative_to(out).as_posix(), logs_sha256=log_sha,
                  status="ok", status_at_run="no_initial_proposal", initial_proposal=False,
                  completed_by={"script": s1.rel(__file__), "sha256": s1.sha256(Path(__file__).read_bytes()),
                                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "reason": reason})
    s1.write_json(path, record)
    fin = evaluation["final"]
    return f"completed: {reason} (admissible {fin['admissible']})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=rb.BASE / "results" / "stage2")
    out = ap.parse_args().results
    s2.quiet()
    config = json.loads(rb.CONFIG_PATH.read_text(encoding="utf-8"))
    kpis = json.loads(rb.KPI_PATH.read_text(encoding="utf-8"))
    for path in sorted((out / "raw").glob("*/rep_*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec.get("status") == "no_initial_proposal":
            print(f"{rec['config_id']}__{rec['variant']} rep {rec['rep']}: {complete(path, out, config, kpis)}", flush=True)


if __name__ == "__main__":
    main()

"""
Independent check of a Stage-2 result folder (report only; nothing is changed).

Checks
  1. files: one record per configuration x rep with a final status; if the simulated logs are present
     (they are not in git): 10 baseline logs per configuration, 10 healthy logs per process, 10 logs per
     evaluated phase, each matching the SHA-256 stored in the records
  2. GLASS commit: equal in every record and in README.md
  3. KPI objects: the objects passed equal the named objects of the source record, except that utilization
     KPIs carry target_direction "minimize" and the overload sentence (and, in records with kpi_thresholds,
     i.e. batch v3, the configuration's kpi_inputs description_append); stage1_target_direction equals the
     source direction; measurable_as is non-null
  3b. targets (records with kpi_thresholds): the recorded thresholds equal the configuration's kpi_inputs
     and are set on the loop's and the multi-seed evaluation's targets; GLASS's achieved verdict per KPI is
     recomputed from the stored means (minimize: proposed <= threshold, maximize: >=)
  4. re-merge: for initial and final, GLASS's apply_patch(baseline, stored patch) reproduces the stored merged
     scenario; differences between the merged and the simulated scenario are listed
  5. summary CSVs: rebuilt from the records with this script's own code and compared cell for cell, including
     the reporting columns added by stage2_reporting.py (GLASS vs pool utilization, loop_discarded_better_initial)
  6. business-context limits: re-implemented over the stored simulated scenario JSON and compared with
     the stored violations / valid flags
  7. utilization rule: verdicts recomputed from the stored per-seed utilizations; if the logs are present, the
     utilizations are recomputed from the logs with a separate implementation (per-day overlap), and the
     midnight end-time mapping counts are recomputed

Usage (repo root):  python evaluation/loanapp_p2p/scripts/verify_stage2_results.py [--results <folder>]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import warnings
from datetime import timedelta
from pathlib import Path
from statistics import mean

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE.parent.parent
sys.path.insert(0, str(REPO_ROOT / "goal_to_parameters"))

IMMUTABLE = {"LoanApp": ["AML check", "Approve loan offer"], "Procure2Pay": ["Authorize Supplier's Invoice payment"]}
FINAL = {"ok", "no_initial_proposal", "app_error"}
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
LIMIT = 0.85
OVERLOAD_SENTENCE = (" The resource is currently overloaded; bring its utilization down to a sustainable level "
                     "without idling the team.")


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fmt(v) -> str:
    return "" if v is None else str(v)


# ---------------------------------------------------------------- 5. summaries (own implementation)
def own_discarded(rec: dict) -> tuple[bool, str]:
    ev = rec.get("evaluation") or {}
    a, b = ev.get("initial") or {}, ev.get("final") or {}
    if rec.get("status") != "ok" or not a.get("kpis") or b.get("same_as_initial") or b.get("iteration") == 1:
        return False, ""
    imp = lambda e: len([k for k in e.get("kpis") or [] if k.get("improved") is True])
    if (rec.get("loop") or {}).get("no_improving_scenario"):
        kept = a.get("valid") is True and imp(a) > 0
        return kept, f"primary: final not valid (baseline kept), KPIs improved {imp(a)}/0" if kept else ""
    why = []
    if a.get("valid") is True and (b.get("valid") is not True or imp(a) > imp(b)):
        why.append(f"primary: valid {a.get('valid')}/{b.get('valid')}, KPIs improved {imp(a)}/{imp(b)}")
    for key, label in (("recall", "recall"), ("CTD", "CTD ratio")):
        x = (a.get("scores") or {}).get(key) if key == "recall" else (a.get("improvement_ratio") or {}).get(key)
        y = (b.get("scores") or {}).get(key) if key == "recall" else (b.get("improvement_ratio") or {}).get(key)
        if x is not None and y is not None and x > y:
            why.append(f"{label} {x:g} > {y:g}")
    if not why:
        return False, ""
    util = [k for k in a.get("kpis") or [] if k.get("category") == "utilization"]
    pool = a.get("utilization_rule") or {}
    if util and util[0]["proposed_mean"] > util[0]["baseline_mean"] and pool \
            and pool["proposed_mean_utilization"] < pool["baseline_mean_utilization"]:
        why.append(f"GLASS utilization metric rose as backlog cleared (GLASS {util[0]['baseline_mean']:g} -> "
                   f"{util[0]['proposed_mean']:g}; pool {pool['baseline_mean_utilization']:g} -> "
                   f"{pool['proposed_mean_utilization']:g})")
    return True, "; ".join(why)


def own_summary_rows(rec: dict, cfg: dict) -> list[dict]:
    rows = []
    iterations = (rec.get("loop") or {}).get("iterations") or []
    for phase in ("initial", "final"):
        ev = (rec.get("evaluation") or {}).get(phase) or {}
        sc, util, kpis = ev.get("scores") or {}, ev.get("utilization_rule") or {}, ev.get("kpis") or []
        row = {"config_id": rec["config_id"], "process": rec["process"], "goal_config": rec["goal_config"],
               "rep": rec["rep"], "status": rec["status"], "phase": phase, "iteration": ev.get("iteration"),
               "iterations_used": len(iterations), "valid": ev.get("valid"),
               "patch_validator_passed": ev.get("patch_validator_passed"), "simulation_ran": ev.get("multi_seed_ok"),
               "violations": " | ".join(ev.get("violations") or []),
               "post_merge_validation_errors": len(ev.get("post_merge_validation_errors") or [])}
        for i in range(3):
            k = kpis[i] if i < len(kpis) else {}
            row.update({f"kpi{i + 1}": k.get("kpi", ""), f"kpi{i + 1}_scored_by": k.get("scored_by", ""),
                        f"kpi{i + 1}_improved": k.get("improved", ""), f"kpi{i + 1}_p_value": k.get("p_value", ""),
                        f"kpi{i + 1}_pct_change": k.get("pct_change", "")})
        row["all_kpis_improved"] = bool(kpis) and all(k.get("improved") is True for k in kpis)
        row["utilization_verdict"] = util.get("verdict", "")
        row["utilization_baseline"] = util.get("baseline_mean_utilization", "")
        row["utilization_proposed"] = util.get("proposed_mean_utilization", "")
        for g in ("G1", "G2", "G3"):
            row[f"addressed_{g}"] = sc.get(f"addressed_{g}", "") if g in cfg["injected_problems"] else ""
        for key in ("precision", "recall", "n_modifications", "n_off_target"):
            row[key] = sc.get(key)
        for m in ("CTD", "RED", "CWD", "NGD"):
            row[f"improvement_ratio_{m}"] = (ev.get("improvement_ratio") or {}).get(m)
        util = [k for k in kpis if k.get("category") == "utilization"]
        row["glass_utilization_baseline"] = util[0]["baseline_mean"] if util else ""
        row["glass_utilization_proposed"] = util[0]["proposed_mean"] if util else ""
        row["loop_discarded_better_initial"], row["loop_discarded_reason"] = own_discarded(rec)
        u = [k for k in kpis if k.get("category") == "utilization"]
        row["util_improved_glass"] = (u[0].get("glass_improved") is True) if u else ""
        row["util_improved_batch"] = (u[0].get("improved") is True) if u else ""
        row["all_kpis_improved_glass_rule"] = len(kpis) > 0 and all(k.get("glass_improved") is True for k in kpis)
        for i in range(3):
            k = kpis[i] if i < len(kpis) else {}
            row[f"kpi{i + 1}_target_achieved"] = own_achieved(k) if k.get("threshold") is not None else ""
        has_t = bool(u) and u[0].get("threshold") is not None
        row["util_target_achieved_glass"] = own_achieved(u[0]) if has_t else ""
        band = ev.get("utilization_rule") or {}
        row["util_target_achieved_batch"] = (mean(band["proposed_per_seed"]) < LIMIT if band else "") if has_t else ""
        rows.append(row)
    return rows


def own_achieved(k: dict) -> bool:
    """GLASS's achieved rule, re-implemented on the stored mean of the proposed values."""
    if k["direction"] == "minimize":
        return k["proposed_mean"] <= k["threshold"]
    return k["proposed_mean"] >= k["threshold"]


def own_per_config(rows: list[dict], recs: list[dict], cfg: dict) -> dict:
    out = {"config_id": cfg["config_id"], "process": cfg["process"], "goal_config": cfg["goal_config"],
           "injected_problems": "|".join(cfg["injected_problems"]), "reps": len(recs),
           "reps_ok": sum(r["status"] == "ok" for r in recs),
           "mean_iterations_used": round(mean(len((r.get("loop") or {}).get("iterations") or []) for r in recs), 2)}
    for phase in ("initial", "final"):
        pr = [r for r in rows if r["phase"] == phase and r["status"] == "ok"]
        out[f"{phase}_valid"] = sum(r["valid"] is True for r in pr)
        out[f"{phase}_all_kpis_improved"] = sum(r["all_kpis_improved"] is True for r in pr)
        out[f"{phase}_valid_and_improved"] = sum(r["valid"] is True and r["all_kpis_improved"] is True for r in pr)
        for g in cfg["injected_problems"]:
            out[f"{phase}_addressed_{g}"] = sum(r[f"addressed_{g}"] is True for r in pr)
        for key in ["precision", "recall"] + [f"improvement_ratio_{m}" for m in ("CTD", "RED", "CWD", "NGD")]:
            vals = [r[key] for r in pr if r[key] is not None]
            out[f"{phase}_mean_{key}"] = round(mean(vals), 3) if vals else ""
    out["reps_loop_discarded_better_initial"] = sum(own_discarded(r)[0] for r in recs)
    has_util = any(k.get("category") == "utilization" for r in recs
                   for k in (r.get("stage1_result_passed") or {}).get("kpis", []))
    for phase in ("initial", "final"):
        evs = [(r.get("evaluation") or {}).get(phase) or {} for r in recs if r["status"] == "ok"]
        utils = [[k for k in e.get("kpis") or [] if k.get("category") == "utilization"] for e in evs]
        out[f"{phase}_util_improved_glass"] = sum(bool(u) and u[0].get("glass_improved") is True for u in utils) \
            if has_util else ""
        out[f"{phase}_util_improved_batch"] = sum(bool(u) and u[0].get("improved") is True for u in utils) \
            if has_util else ""
        out[f"{phase}_all_kpis_improved_glass_rule"] = sum(
            len(e.get("kpis") or []) > 0 and all(k.get("glass_improved") is True for k in e["kpis"]) for e in evs)
        has_target = any(r.get("kpi_thresholds") for r in recs)
        pr = [r for r in rows if r["phase"] == phase and r["status"] == "ok"]
        out[f"{phase}_util_target_achieved_glass"] = sum(r["util_target_achieved_glass"] is True for r in pr) \
            if has_target else ""
        out[f"{phase}_util_target_achieved_batch"] = sum(r["util_target_achieved_batch"] is True for r in pr) \
            if has_target else ""
    return out


def compare_csv(path: Path, rows: list[dict], key_fields: list[str]) -> list[str]:
    if not path.exists():
        return [f"{path.name} missing"]
    with open(path, newline="", encoding="utf-8") as f:
        stored = list(csv.DictReader(f))
    issues = [] if len(stored) == len(rows) else [f"{path.name}: {len(stored)} rows stored, {len(rows)} rebuilt"]
    for s, r in zip(stored, rows):
        for k, v in r.items():
            if s.get(k) != fmt(v):
                issues.append(f"{path.name} {[s.get(x) for x in key_fields]} {k}: stored {s.get(k)!r}, rebuilt {fmt(v)!r}")
        for k in set(s) - set(r):
            if s[k] != "":
                issues.append(f"{path.name} {[s.get(x) for x in key_fields]}: unexpected column {k}={s[k]!r}")
    return issues


# ---------------------------------------------------------------- 6. limits (own implementation, JSON level)
def calendars_of(scn: dict) -> dict[str, list[dict]]:
    tts = {t["id"]: t["timeTableItems"] for t in scn["resourceParameters"]["timeTables"]}
    return {res["id"]: tts[res.get("calendar") or role["schedule"]]
            for role in scn["resourceParameters"]["roles"] for res in role["resources"]}


def day_hours(items: list[dict]) -> dict[str, float]:
    h = dict.fromkeys(DAYS, 0.0)
    for it in items:
        d = DAYS.index(it["startWeekday"])
        while True:
            h[DAYS[d]] += it["endTime"] - it["startTime"]
            if DAYS[d] == it["endWeekday"]:
                break
            d = (d + 1) % 7
    return h


def own_violations(scn: dict, base: dict, process: str, bc: dict) -> list[str]:
    v = []
    roles = {r["id"]: r for r in scn["resourceParameters"]["roles"]}
    for b in base["resourceParameters"]["roles"]:
        r = roles.get(b["id"])
        if r is None:
            v.append(f"role {b['id']} removed")
            continue
        n0, n1 = len(b["resources"]), len(r["resources"])
        internal = bc["cost_per_hour"].get(b["id"], 0) > 0
        if internal and n1 > n0 + 3:
            v.append(f"{b['id']}: {n0} -> {n1} (> +3)")
        if not internal and n1 != n0:
            v.append(f"external role {b['id']}: {n0} -> {n1}")
        c0, c1 = calendars_of(base), calendars_of(scn)
        sched0 = next(t["timeTableItems"] for t in base["resourceParameters"]["timeTables"] if t["id"] == b["schedule"])
        for res in r["resources"]:
            h0, h1 = day_hours(c0.get(res["id"], sched0)), day_hours(c1[res["id"]])
            v += [f"{b['id']}/{res['id']}: {d} {h0[d]:g} h -> {h1[d]:g} h (> +2 h/day)"
                  for d in DAYS if h1[d] > h0[d] + 2 + 1e-9]
    cost = lambda s: sum(len(r["resources"]) * r["costHour"] for r in s["resourceParameters"]["roles"])
    if cost(scn) > 1.35 * cost(base) + 1e-9:
        v.append(f"hourly staff cost {cost(base):g} -> {cost(scn):g} (> x1.35)")
    acts = {a["name"]: a for m in scn["models"] for a in m["modelParameter"]["activities"]}
    base_acts = {a["name"]: a for m in base["models"] for a in m["modelParameter"]["activities"]}
    for step in IMMUTABLE[process]:
        if step not in acts:
            v.append(f"immutable step '{step}' missing")
        elif not set(base_acts[step]["resources"]) <= set(acts[step]["resources"]):
            v.append(f"immutable step '{step}': roles {base_acts[step]['resources']} -> {acts[step]['resources']}")
    return v


# ---------------------------------------------------------------- 7. band (own implementation)
def own_utilization(log_path: Path, scn: dict, role_id: str) -> float:
    """Per-day overlap of each task with its resource's working hours, over the log's span."""
    df = pd.read_csv(log_path)
    s = pd.to_datetime(df["start_time"], format="ISO8601").dt.tz_localize(None)
    e = pd.to_datetime(df["end_time"], format="ISO8601").dt.tz_localize(None)
    t0, t1 = s.min(), e.max()
    role = next(r for r in scn["resourceParameters"]["roles"] if r["id"] == role_id)
    cals = calendars_of(scn)
    avail = busy = 0.0
    for rid in [r["id"] for r in role["resources"]]:
        a, b_ = _resource_util(cals[rid], s[df["resource"].astype(str) == rid], e[df["resource"].astype(str) == rid], t0, t1)
        avail += a
        busy += b_
    return busy / avail


def _resource_util(items, starts, ends, t0, t1):
    def windows(day):
        wd = DAYS[day.weekday()]
        out = []
        for it in items:
            d = DAYS.index(it["startWeekday"])
            covered = set()
            while True:
                covered.add(DAYS[d])
                if DAYS[d] == it["endWeekday"]:
                    break
                d = (d + 1) % 7
            if wd in covered:
                out.append((max(day + timedelta(hours=it["startTime"]), t0), min(day + timedelta(hours=it["endTime"]), t1)))
        return [(a, b) for a, b in out if b > a]

    avail = sum((b - a).total_seconds() for day in pd.date_range(t0.normalize(), t1.normalize(), freq="D")
                for a, b in windows(day))
    busy = 0.0
    for a0, b0 in zip(starts, ends):
        for day in pd.date_range(a0.normalize(), b0.normalize(), freq="D"):
            for a, b in windows(day):
                busy += max(0.0, (min(b, b0) - max(a, a0)).total_seconds())
    return avail, busy


def own_rule(base_u: list[float], prop_u: list[float]) -> str:
    bu, pu = sum(base_u) / len(base_u), sum(prop_u) / len(prop_u)
    return "improved" if pu < LIMIT and pu < bu else ("worsened" if pu > bu else "unchanged")


def own_midnight_count(log_path: Path, scn: dict) -> int:
    """Ends at exactly 00:00:00 right after the resource's working-day end on the previous day."""
    df = pd.read_csv(log_path)
    s = pd.to_datetime(df["start_time"], format="ISO8601").dt.tz_localize(None)
    e = pd.to_datetime(df["end_time"], format="ISO8601").dt.tz_localize(None)
    cals = calendars_of(scn)
    n = 0
    for res, a, b in zip(df["resource"].astype(str), s, e):
        if b != b.normalize() or res not in cals:
            continue
        prev = b - timedelta(days=1)
        ends = [it["endTime"] for it in cals[res] if day_hours([it])[DAYS[prev.weekday()]] > 0]
        if ends and max(ends) < 24 and a <= prev + timedelta(hours=max(ends)):
            n += 1
    return n


def main() -> None:
    warnings.filterwarnings("ignore")
    import logging
    logging.getLogger("streamlit").setLevel(logging.ERROR)
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=BASE / "results" / "stage2_results")
    args = ap.parse_args()
    out = args.results
    config = load(BASE / "setup" / "stage2_config.json")
    reps, n_seeds = config["settings"]["repetitions"], config["settings"]["seeds"]
    logs_present = (out / "logs").exists()

    from second_llm.output_schema_patch import ScenarioPatch
    from second_llm.scenario_merger import apply_patch
    from second_llm.simod_input import accept_simod_raw_input
    from second_llm.simod_to_simubridge import build_baseline_scenario, build_flow_name_map

    S = {k: [] for k in ("files", "commit", "kpis", "targets", "remerge", "csv", "limits", "band")}
    notes, all_rows, per_config, commits = [], [], [], set()
    counts = dict.fromkeys(("records", "remerged", "limits", "band", "band_from_logs", "midnight", "logs_hashed",
                            "achieved"), 0)
    for cfg in config["configurations"]:
        cid = cfg["config_id"]
        files = sorted((out / "raw" / cid).glob("rep_*.json"), key=lambda p: int(p.stem.split("_")[1]))
        if len(files) != reps:
            S["files"].append(f"{cid}: {len(files)} records, expected {reps}")
        recs = [load(p) for p in files]
        counts["records"] += len(recs)
        src_path = cfg["kpi_source_record"].replace("results/stage1_results/", "results/stage1_results_normalised/")
        src = load(BASE / src_path)["result"]
        named = [next(k for k in src["kpis"] if k["name"] == n) for n in cfg["kpi_names_to_pass"]]
        kpi_inputs = cfg.get("kpi_inputs") or {}
        cfg_thresholds = {n: v["target_threshold"] for n, v in kpi_inputs.items()
                          if v.get("target_threshold") is not None}

        def expected(with_inputs: bool) -> list[dict]:
            out_k = []
            for k in named:
                k = json.loads(json.dumps(k))
                if k["category"] == "utilization":
                    k["target_direction"] = "minimize"
                    k["description"] = k["description"].rstrip() + OVERLOAD_SENTENCE
                if with_inputs and kpi_inputs.get(k["name"], {}).get("description_append"):
                    k["description"] = k["description"].rstrip() + " " + kpi_inputs[k["name"]]["description_append"]
                out_k.append(k)
            return out_k
        bdir, bc = BASE / cfg["baseline_dir"], config["business_context"][cfg["process"]]
        base_meta = out / "baseline_logs" / cid / "baseline.json"
        if logs_present:
            for name, h in (load(base_meta)["baseline_logs_sha256"] if base_meta.exists() else {}).items():
                p = out / "baseline_logs" / cid / name
                counts["logs_hashed"] += 1
                if not p.exists() or sha(p) != h:
                    S["files"].append(f"{cid}: baseline log {name} missing or SHA-256 differs")
        for rec in recs:
            tag = f"{cid} rep {rec['rep']}"
            commits.add(rec.get("glass_commit"))
            if rec["status"] not in FINAL:
                S["files"].append(f"{tag}: status {rec['status']} is not final")
            passed = rec["stage1_result_passed"]
            v3 = "kpi_thresholds" in rec
            expected_kpis = expected(v3)
            if v3:
                if rec["kpi_thresholds"] != cfg_thresholds:
                    S["targets"].append(f"{tag}: kpi_thresholds {rec['kpi_thresholds']} differ from the config "
                                        f"{cfg_thresholds}")
                for key in ("loop_targets", "eval_targets"):
                    got = {t["name"]: t["threshold"] for t in rec.get(key) or [] if t.get("threshold") is not None}
                    if got != cfg_thresholds:
                        S["targets"].append(f"{tag}: thresholds on {key} {got}, config {cfg_thresholds}")
            if rec.get("kpi_source_record") != src_path or passed["kpis"] != expected_kpis \
                    or passed["simulation_goal_structured"] != src["simulation_goal_structured"] \
                    or passed["reasoning"] != src["reasoning"]:
                S["kpis"].append(f"{tag}: Stage-1 result passed differs from {src_path} (+ utilization rule)")
            if rec.get("stage1_target_direction") != {k["name"]: k["target_direction"] for k in named}:
                S["kpis"].append(f"{tag}: stage1_target_direction differs from the source")
            if any(not k.get("measurable_as") for k in passed["kpis"]):
                S["kpis"].append(f"{tag}: null measurable_as")
            if rec["status"] != "ok":
                continue
            if logs_present:
                for name, h in rec["logs_sha256"].items():
                    p = out / rec["logs_dir"] / name
                    counts["logs_hashed"] += 1
                    if not p.exists() or sha(p) != h:
                        S["files"].append(f"{tag}: log {name} missing or SHA-256 differs")
                for name, h in rec["healthy_logs_sha256"].items():
                    p = out / "healthy_logs" / rec["process"] / name
                    if not p.exists() or sha(p) != h:
                        S["files"].append(f"{tag}: healthy log {name} missing or SHA-256 differs")
            n_phase = sum(1 for ev in rec["evaluation"].values() if not ev.get("same_as_initial") and "error" not in ev)
            if rec.get("completed_by") and (rec.get("status_at_run") != "no_initial_proposal"
                                            or rec["evaluation"]["initial"].get("error") != "no scenario"):
                S["files"].append(f"{tag}: completed record without a failed iteration 1")
            if len(rec["logs_sha256"]) != n_phase * n_seeds:
                S["files"].append(f"{tag}: {len(rec['logs_sha256'])} log hashes, expected {n_phase * n_seeds}")
            sp = json.loads((bdir / "sim_params.json").read_text(encoding="utf-8"))
            for prof in sp["resource_profiles"]:
                for res in prof["resource_list"]:
                    res["cost_per_hour"] = bc["cost_per_hour"][prof["name"]]
            if sp != rec["sim_params_with_costs"]:
                S["remerge"].append(f"{tag}: stored sim_params differ from baseline file + business-context costs")
            bpmn = (bdir / "model.bpmn").read_text(encoding="utf-8")
            raw = accept_simod_raw_input(json.dumps(sp, indent=1), bpmn)
            baseline = build_baseline_scenario(json.loads(raw.simod_result.json_params_content),
                                               bpmn_xml=raw.simod_result.bpmn_content).scenario
            base_json, flow_map = baseline.model_dump(mode="json"), build_flow_name_map(bpmn)
            iterations = rec["loop"]["iterations"]
            for phase, ev in rec["evaluation"].items():
                if "error" in ev:          # no scenario in that iteration (scored as not valid)
                    continue
                it = iterations[ev["iteration"] - 1]
                merged = apply_patch(baseline, ScenarioPatch.model_validate(it["patch"]), strict=False,
                                     element_name_map=flow_map)
                counts["remerged"] += 1
                if merged.scenario is None or merged.scenario.model_dump(mode="json") != it["merged_scenario"]:
                    S["remerge"].append(f"{tag} {phase}: re-merge differs from the stored merged scenario")
                if it["merged_scenario"] != it["simulated_scenario"]:
                    notes.append(f"{tag} {phase}: simulated scenario differs from the merged one (GLASS post-merge "
                                 "repair): " + "; ".join(n for n in it["generation"]["generation_notes"] if "repair" in n)[:300])
                own = own_violations(it["simulated_scenario"], base_json, cfg["process"], bc)
                counts["limits"] += 1
                if own != ev["violations"]:
                    S["limits"].append(f"{tag} {phase}: violations stored {ev['violations']}, re-checked {own}")
                own_valid = bool(it["patch_validator_passed"]) and bool(ev["multi_seed_ok"]) and not own
                if own_valid != ev["valid"]:
                    S["limits"].append(f"{tag} {phase}: valid stored {ev['valid']}, re-checked {own_valid}")
                if logs_present and not ev.get("same_as_initial") and ev.get("midnight_ends_mapped_per_seed") is not None:
                    own_counts = [own_midnight_count(out / rec["logs_dir"] / f"{phase}_seed_{s}.csv.gz",
                                                     it["simulated_scenario"]) for s in rec["seeds"]]
                    counts["midnight"] += 1
                    if own_counts != ev["midnight_ends_mapped_per_seed"]:
                        S["band"].append(f"{tag} {phase}: midnight mappings stored {ev['midnight_ends_mapped_per_seed']}, "
                                         f"recounted {own_counts}")
                for k in ev.get("kpis") or []:
                    if k.get("threshold") is not None:
                        counts["achieved"] += 1
                        if own_achieved(k) != k.get("glass_achieved"):
                            S["targets"].append(f"{tag} {phase}: {k['kpi']} glass_achieved {k.get('glass_achieved')}, "
                                                f"recomputed {own_achieved(k)}")
                band = ev.get("utilization_rule")
                if band:
                    counts["band"] += 1
                    verdict = own_rule(rec["baseline_pool_utilization"], band["proposed_per_seed"])
                    if verdict != band["verdict"]:
                        S["band"].append(f"{tag} {phase}: utilization verdict stored {band['verdict']}, recomputed {verdict}")
                    for k in ev["kpis"]:
                        if k["scored_by"] == "utilization_rule" and k["improved"] != (verdict == "improved"):
                            S["band"].append(f"{tag} {phase}: KPI {k['kpi']} improved={k['improved']} vs rule {verdict}")
                    if logs_present and not ev.get("same_as_initial"):
                        counts["band_from_logs"] += 1
                        pool = rec["util_pool"]
                        prop = [own_utilization(out / rec["logs_dir"] / f"{phase}_seed_{s}.csv.gz",
                                                it["simulated_scenario"], pool) for s in rec["seeds"]]
                        base = [own_utilization(out / "baseline_logs" / cid / f"seed_{s}.csv.gz", base_json, pool)
                                for s in rec["seeds"]]
                        if max(abs(a - b) for a, b in zip(prop, band["proposed_per_seed"])) > 1e-5 or \
                                max(abs(a - b) for a, b in zip(base, rec["baseline_pool_utilization"])) > 1e-5:
                            S["band"].append(f"{tag} {phase}: utilization from logs differs from the stored values")
        rows = [r for rec in recs for r in own_summary_rows(rec, cfg)]
        all_rows += rows
        if recs:
            per_config.append(own_per_config(rows, recs, cfg))
    if logs_present:
        for process in sorted({c["process"] for c in config["configurations"]}):
            n = len(list((out / "healthy_logs" / process).glob("seed_*.csv.gz")))
            if n != n_seeds:
                S["files"].append(f"healthy {process}: {n} logs, expected {n_seeds}")
    readme = (out / "README.md").read_text(encoding="utf-8") if (out / "README.md").exists() else ""
    m = re.search(r"GLASS commit: `([0-9a-f]{40})`", readme)
    if len(commits) != 1 or not m or m.group(1) not in commits:
        S["commit"].append(f"commits in records {sorted(map(str, commits))}, README {m.group(1) if m else None}")
    S["csv"] += compare_csv(out / "stage2_summary.csv", all_rows, ["config_id", "rep", "phase"])
    S["csv"] += compare_csv(out / "stage2_per_config.csv", per_config, ["config_id"])

    titles = {"files": "Files and log SHA-256", "commit": "GLASS commit", "kpis": "KPI objects passed",
              "targets": "Target thresholds and achieved verdicts",
              "remerge": "Re-merge of initial/final scenarios", "csv": "Summary CSVs rebuilt cell for cell",
              "limits": "Business-context limits re-checked",
              "band": "Utilization rule and midnight mapping recomputed"}
    lines = ["# Stage-2 results: verification report", "",
             "Generated by `evaluation/loanapp_p2p/scripts/verify_stage2_results.py` (report only).", "",
             f"Records: {counts['records']}; scenarios re-merged: {counts['remerged']}; limit checks: {counts['limits']}; "
             f"utilization verdicts: {counts['band']} (from logs: {counts['band_from_logs']}); midnight-mapping "
             f"checks: {counts['midnight']}; achieved verdicts: {counts['achieved']}; log files hashed: "
             f"{counts['logs_hashed']}{'' if logs_present else ' (logs not present: log checks skipped)'}; "
             f"GLASS commit: {', '.join(sorted(map(str, commits)))}", ""]
    for key, title in titles.items():
        lines.append(f"- **{title}:** " + ("OK" if not S[key] else f"{len(S[key])} issue(s)"))
        lines += [f"  - {i}" for i in S[key]]
    if notes:
        lines += ["", "Notes (not failures):"] + [f"- {n}" for n in notes]
    (out / "verification_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

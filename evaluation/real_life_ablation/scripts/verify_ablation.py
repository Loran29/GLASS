"""
Independent verification of the real-life ablation, Stage 2 (does not import run_ablation_batch.py or
ablation_reporting.py; reads the run records, the simulated logs and the inputs).

Re-computed per run and phase (initial, final) and compared with what the record says:
  validity        own implementation of the business-context rules on the merged scenario against the baseline
                  (GLASS's baseline import is used only to rebuild the baseline scenario): headcount per role
                  <= baseline + 1; working time per resource and weekday <= baseline
                  + 2 h; weekly staff cost at 20 EUR/h <= 1.35 x baseline; every role's costHour 20; the same activities as the
                  baseline; start-event arrival unchanged; plus the recorded patch-validator verdict
  executable      10 logs present with the recorded SHA-256 and the multi-seed evaluation ok
  merge stability GLASS's "[merge stability] a/b" generation note of the iteration
  KPIs            per KPI from the per-seed values: means, % change, improved (direction rule), paired t-test p;
                  all_kpis_improved, DHR, NIS
  change profile  counts per parameter_type and the share on X (own alias set from the baseline)
  distances       means of the recorded per-seed values; for --distance-sample runs (default 3, chosen with
                  seed 0) CTD, RED and CWD are recomputed from the saved logs and the test log with an own
                  midnight-end mapping
  loop discarded  own implementation of the rule (admissible initial; a / b / c; a kept baseline counts only
                  when the initial was better than the baseline on 10 seeds)
  kept baseline   no_improving_scenario iff no iteration has a positive score (GLASS v3.2), and then no final
                  scenario; direction_inconsistent rejections recounted from the validator diagnostics
Re-computed from the records and compared with ablation_per_variant.csv: every rate and mean of the per-variant
table (V_full minus variant differences included).

Usage (repo root): python evaluation/real_life_ablation/scripts/verify_ablation.py [--results DIR]
                   [--distance-sample N]
Exit code 1 when any mismatch is found; writes <results>/verification.md.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import mean

import numpy as np
import pandas as pd
from scipy import stats

BASE = Path(__file__).resolve().parent.parent
REPO = BASE.parent.parent
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TOL = 1e-6


def day_hours(items: list[dict]) -> dict[str, float]:
    out = dict.fromkeys(DAYS, 0.0)
    for it in items:
        i, j = DAYS.index(it["startWeekday"]), DAYS.index(it["endWeekday"])
        k = i
        while True:
            out[DAYS[k]] += it["endTime"] - it["startTime"]
            if k == j:
                break
            k = (k + 1) % 7
    return out


def calendars(scn: dict) -> dict[str, list[dict]]:
    tables = {t["id"]: t["timeTableItems"] for t in scn["resourceParameters"]["timeTables"]}
    return {res["id"]: tables.get(res.get("calendar") or role["schedule"], [])
            for role in scn["resourceParameters"]["roles"] for res in role["resources"]}


def violations(scn: dict, base: dict) -> list[str]:
    out = []
    b_roles = {r["id"]: r for r in base["resourceParameters"]["roles"]}
    c0, c1 = calendars(base), calendars(scn)
    longest = {d: max(day_hours(v)[d] for v in c0.values()) for d in DAYS}
    for role in scn["resourceParameters"]["roles"]:
        b = b_roles.get(role["id"])
        if len(role["resources"]) > (len(b["resources"]) if b else 0) + 1:
            out.append("staffing")
        for res in role["resources"]:
            if res["id"] in c0:
                ref = day_hours(c0[res["id"]])
            elif b:
                ref = {d: max(day_hours(c0[r["id"]])[d] for r in b["resources"]) for d in DAYS}
            else:
                ref = longest
            new = day_hours(c1[res["id"]])
            if any(new[d] > ref[d] + 2 + 1e-9 for d in DAYS):
                out.append("hours")
        if abs(float(role["costHour"]) - 20.0) > 1e-9:
            out.append("rate_changed")
    cost = lambda s: sum(20.0 * sum(day_hours(v).values()) for v in calendars(s).values())
    if cost(scn) > 1.35 * cost(base) + 1e-6:
        out.append("staff cost")
    names = {a["name"] for m in scn["models"] for a in m["modelParameter"]["activities"]}
    base_names = {a["name"] for m in base["models"] for a in m["modelParameter"]["activities"]}
    if names != base_names:
        out.append("immutable")
    arr = lambda s: [(e.get("interArrivalTime"), e.get("arrivalCalendar")) for m in s["models"]
                     for e in m["modelParameter"].get("events", [])]
    if arr(scn) != arr(base):
        out.append("demand_changed")
    return sorted(set(out))


def x_alias_set(base: dict, x: str) -> set[str]:
    acts = [a for m in base["models"] for a in m["modelParameter"]["activities"] if a["name"].lower() == x.lower()]
    roles = {r["id"].lower(): r for r in base["resourceParameters"]["roles"]}
    al = set()
    for a in acts:
        al |= {a["name"].lower(), a["id"].lower()}
        for rid in a["resources"]:
            al.add(rid.lower())
            if rid.lower() in roles:
                al |= {res["id"].lower() for res in roles[rid.lower()]["resources"]}
    return al


def kpi_checks(kpis: list[dict]) -> tuple[list[str], list[bool], list[float]]:
    issues, improved, pct = [], [], []
    for k in kpis:
        b, p = k.get("baseline_values") or [], k.get("proposed_values") or []
        if len(b) != 10 or len(p) != 10:
            issues.append(f"{k['kpi']}: {len(b)}/{len(p)} seed values")
            continue
        mb, mp = float(np.mean(b)), float(np.mean(p))
        ch = (mp - mb) / abs(mb) * 100 if mb else 0.0
        imp = mp < mb if k["direction"] == "minimize" else mp > mb
        if abs(mb - k["baseline_mean"]) > 1e-6 * max(1, abs(mb)) or abs(mp - k["proposed_mean"]) > 1e-6 * max(1, abs(mp)):
            issues.append(f"{k['kpi']}: means")
        if k.get("pct_change") is not None and abs(ch - k["pct_change"]) > 0.01:
            issues.append(f"{k['kpi']}: % change {ch:.3f} vs {k['pct_change']}")
        if bool(k.get("glass_improved")) != imp:
            issues.append(f"{k['kpi']}: improved {imp} vs {k.get('glass_improved')}")
        d = np.subtract(p, b)
        pv = float(stats.ttest_rel(p, b).pvalue) if np.ptp(d) > 0 else None
        rp = k.get("p_value")
        if pv is not None and rp is not None and not (isinstance(rp, float) and np.isnan(rp)) and abs(pv - rp) > 1e-6:
            issues.append(f"{k['kpi']}: p {pv:.6g} vs {rp}")
        improved.append(imp)
        pct.append(round(ch, 2))   # NIS uses GLASS's reported % change, which GLASS rounds to 2 decimals
    return issues, improved, pct


def map_midnight(log: pd.DataFrame, scn: dict) -> pd.DataFrame:
    cals, out = calendars(scn), log.copy()
    for idx in out.index[out["end_time"] == out["end_time"].dt.normalize()]:
        items = cals.get(str(out.at[idx, "resource"]))
        if not items:
            continue
        prev = out.at[idx, "end_time"] - timedelta(days=1)
        ends = [it["endTime"] for it in items if day_hours([it])[DAYS[prev.weekday()]] > 0]
        if ends and max(ends) < 24 and out.at[idx, "start_time"] <= prev + timedelta(hours=max(ends)):
            out.at[idx, "end_time"] = prev + timedelta(hours=max(ends))
    return out


def recompute_distances(log_paths: list[Path], scn: dict, test: pd.DataFrame) -> dict[str, float]:
    from log_distance_measures.circadian_workforce_distribution import circadian_workforce_distribution_distance
    from log_distance_measures.config import EventLogIDs
    from log_distance_measures.cycle_time_distribution import cycle_time_distribution_distance
    from log_distance_measures.relative_event_distribution import relative_event_distribution_distance
    ids = EventLogIDs(case="case_id", activity="activity", start_time="start_time", end_time="end_time",
                      resource="resource")
    vals = defaultdict(list)
    for p in log_paths:
        lg = pd.read_csv(p)
        for c in ("start_time", "end_time"):
            lg[c] = pd.to_datetime(lg[c], format="ISO8601").dt.tz_localize(None)
        lg["resource"] = lg["resource"].astype(str)
        lg = map_midnight(lg, scn)
        vals["CTD"].append(cycle_time_distribution_distance(lg, ids, test, ids, bin_size=timedelta(hours=1)))
        vals["RED"].append(relative_event_distribution_distance(lg, ids, test, ids))
        vals["CWD"].append(circadian_workforce_distribution_distance(lg, ids, test, ids))
    return {m: float(np.mean(v)) for m, v in vals.items()}


def baseline_scenarios(config: dict) -> dict[str, dict]:
    sys.path.insert(0, str(REPO / "goal_to_parameters"))
    from second_llm.simod_input import accept_simod_raw_input
    from second_llm.simod_to_simubridge import build_baseline_scenario
    out = {}
    for ds, d in config["datasets"].items():
        sp = json.loads((BASE / d["baseline_json"]).read_text(encoding="utf-8"))
        for prof in sp["resource_profiles"]:
            for res in prof["resource_list"]:
                res["cost_per_hour"] = 20
        raw = accept_simod_raw_input(json.dumps(sp, indent=1), (BASE / d["baseline_bpmn"]).read_text(encoding="utf-8"))
        out[ds] = build_baseline_scenario(json.loads(raw.simod_result.json_params_content),
                                          bpmn_xml=raw.simod_result.bpmn_content).scenario.model_dump(mode="json")
    return out


def own_merge_stability(it: dict, calls: list[dict]) -> float | None:
    """applied / proposed: the last attempt with a merge note; proposed from that attempt's raw LLM answer."""
    attempt, applied = None, None
    for n in (it.get("generation") or {}).get("generation_notes") or []:
        if n.startswith("Patch attempt "):
            current = int(n.split()[2].split("/")[0])
        elif n.startswith("[merge stability]"):
            attempt, applied = current, int(n.split()[2].split("/")[0])
    if attempt is None:
        return None
    gen = [c for c in calls if c.get("iteration") == it["iteration"] and "Verified KPI Targets" in (c.get("user_prompt") or "")]
    raw = gen[attempt - 1]["raw"] if attempt <= len(gen) else ""
    text = raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else ""
    try:
        proposed = len(json.loads(text).get("modifications") or [])
    except ValueError:
        return None
    return round(applied / proposed, 4) if proposed else None


def own_phase(rec: dict, phase: str, base: dict, aliases: set[str], results: Path) -> tuple[dict, list[str]]:
    ev = (rec.get("evaluation") or {}).get(phase) or {}
    its = (rec.get("loop") or {}).get("iterations") or []
    it_no = ev.get("iteration")
    it = its[it_no - 1] if it_no and it_no <= len(its) else None
    issues = []
    if it is None or it.get("simulated_scenario") is None:
        own = {"scenario_produced": False, "valid": False, "executable": False, "admissible": False,
               "merge_stability": None, "all_kpis_improved": False, "DHR": None, "NIS": None}
    else:
        scn = it["simulated_scenario"]
        v = violations(scn, base)
        rec_v = sorted({x.split(":")[0].split(" ")[0] if not x.startswith("staff cost")
                        else " ".join(x.split(" ")[:2]) for x in ev.get("violations") or []})
        if v != rec_v:
            issues.append(f"{phase}: violations {v} vs recorded {rec_v}")
        stab = own_merge_stability(it, rec.get("llm_calls") or [])
        rec_stab = ev.get("merge_stability")
        if (stab is None) != (rec_stab is None) or (stab is not None and abs(stab - rec_stab) > 1e-4):
            issues.append(f"{phase}: merge stability {stab} vs {rec_stab}")
        prefix = "final" if phase == "final" and not ev.get("same_as_initial") else "initial"
        shas = rec.get("logs_sha256") or {}
        logs = [results / rec["logs_dir"] / f"{prefix}_seed_{s}.csv.gz" for s in rec["seeds"]]
        logs_ok = all(p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == shas.get(p.name) for p in logs)
        executable = bool(ev.get("multi_seed_ok")) and logs_ok
        kissues, improved, pct = kpi_checks(ev.get("kpis") or [])
        issues += [f"{phase}: {x}" for x in kissues]
        dhr = sum(improved) / len(improved) if improved else None
        nis = mean(abs(c) * (1 if i else -1) for c, i in zip(pct, improved)) if improved else None
        valid = bool(it.get("patch_validator_passed")) and not v
        mods = (it.get("patch") or {}).get("modifications", [])
        cp = ev.get("change_profile") or {}
        if dict(Counter(m.get("parameter_type") for m in mods)) != (cp.get("per_parameter_type") or {}):
            issues.append(f"{phase}: parameter-type counts")
        n_x = sum(str(m.get("target_element", "")).lower() in aliases for m in mods)
        if n_x != cp.get("n_targeting_x"):
            issues.append(f"{phase}: modifications on X {n_x} vs {cp.get('n_targeting_x')}")
        for m, d in (ev.get("distance_to_test_log_per_seed") or {}).items():
            if abs(float(np.mean(d)) - ev["distance_to_test_log"][m]) > TOL:
                issues.append(f"{phase}: {m} mean")
        own = {"scenario_produced": True, "valid": valid, "executable": executable, "merge_stability": stab,
               "admissible": valid and executable and (stab or 0) > 0,
               "all_kpis_improved": bool(improved) and all(improved), "DHR": dhr, "NIS": nis}
    for k in ("scenario_produced", "valid", "executable", "admissible", "all_kpis_improved"):
        if bool(ev.get(k)) != own[k]:
            issues.append(f"{phase}: {k} {own[k]} vs recorded {ev.get(k)}")
    for k in ("DHR", "NIS"):
        a, b = own[k], ev.get(k)
        if (a is None) != (b is None) or (a is not None and abs(a - b) > 0.01):
            issues.append(f"{phase}: {k} {a} vs recorded {b}")
    return own, issues


def own_discarded(ini: dict, fin: dict, same: bool, final_it, kept: bool) -> str:
    if not ini["admissible"]:
        return ""
    if kept:
        better = (ini["DHR"] or 0) > 0 or (ini["NIS"] is not None and ini["NIS"] > 1.0)
        return "a" if better else ""
    if same or final_it in (None, 1):
        return ""
    if not fin["admissible"]:
        return "a"
    if ini["DHR"] is not None and fin["DHR"] is not None and ini["DHR"] > fin["DHR"]:
        return "b"
    if ini["DHR"] == fin["DHR"] and ini["NIS"] is not None and fin["NIS"] is not None and ini["NIS"] - fin["NIS"] > 1.0:
        return "c"
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=BASE / "results" / "stage2")
    ap.add_argument("--distance-sample", type=int, default=3)
    args = ap.parse_args()
    config = json.loads((BASE / "setup" / "ablation_config.json").read_text(encoding="utf-8"))
    bases = baseline_scenarios(config)
    aliases = {ds: x_alias_set(bases[ds], d["x_activity"]) for ds, d in config["datasets"].items()}
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((args.results / "raw").glob("*/rep_*.json"))]
    problems, own_rows = [], []
    for rec in records:
        tag = f"{rec['config_id']}__{rec['variant']} rep {rec['rep']}"
        ds = rec["dataset"]
        ev = rec.get("evaluation") or {}
        ini, i1 = own_phase(rec, "initial", bases[ds], aliases[ds], args.results)
        fin, i2 = own_phase(rec, "final", bases[ds], aliases[ds], args.results)
        problems += [f"{tag}: {x}" for x in i1 + i2]
        loop = rec.get("loop") or {}
        kept = bool(loop.get("no_improving_scenario"))
        cond = own_discarded(ini, fin, bool((ev.get("final") or {}).get("same_as_initial")),
                             loop.get("best_iteration"), kept)
        scores = [it.get("score") for it in loop.get("iterations") or []]
        if rec["status"] == "ok" and scores and kept != (not any(s is not None and s > 0 for s in scores)):
            problems.append(f"{tag}: no_improving_scenario {kept} but iteration scores {scores}")
        if kept and (loop.get("best_iteration") is not None or (ev.get("final") or {}).get("scenario_produced")):
            problems.append(f"{tag}: baseline kept but a final scenario is recorded")
        n_dir = sum(d.get("category") == "direction_inconsistent" for it in loop.get("iterations") or []
                    for a in it.get("all_patch_attempts") or [] for d in ((a.get("result") or {}).get("diagnostics") or []))
        if rec["status"] == "ok" and n_dir != rec.get("direction_inconsistent_rejections"):
            problems.append(f"{tag}: direction rejections {n_dir} vs {rec.get('direction_inconsistent_rejections')}")
        rec_cond = (rec.get("loop_discarded_condition") or "")[:1]
        if cond != rec_cond or bool(cond) != bool(rec.get("loop_discarded_better_initial")):
            problems.append(f"{tag}: loop discarded '{cond}' vs recorded '{rec_cond}'")
        sw = rec["switches"]
        expected = {"V_full": (True, True, True, 4, False), "V_no_log": (False, True, True, 4, False),
                    "V_no_context": (True, False, True, 4, False), "V_no_kb": (True, True, False, 4, False),
                    "V_no_refine": (True, True, True, 1, False), "V_old_refine": (True, True, True, 4, True)}
        if tuple(sw[k] for k in ("use_log_profile", "use_context", "use_kb", "max_iterations", "legacy_refinement")) \
                != expected[rec["variant"]]:
            problems.append(f"{tag}: switches {sw}")
        if rec.get("log_profile_passed") != sw["use_log_profile"] or bool(rec.get("chat_user_turns")) != sw["use_context"]:
            problems.append(f"{tag}: log / context inputs do not match the switches")
        for p in rec.get("prompt_sections") or []:
            if p["log_evidence"] != sw["use_log_profile"] or p["kb_section"] != sw["use_kb"] \
                    or (not sw["use_kb"] and p["feedback_kb_section"]):
                problems.append(f"{tag}: prompt sections {p} do not match the switches")
        if len((rec.get("loop") or {}).get("iterations") or []) > sw["max_iterations"]:
            problems.append(f"{tag}: more iterations than max_iterations")
        own_rows.append({"variant": rec["variant"], "config_id": rec["config_id"], "rep": rec["rep"], **fin})
    rng = random.Random(0)
    sample = rng.sample([r for r in records if (r.get("evaluation") or {}).get("final", {}).get("distance_to_test_log")],
                        min(args.distance_sample, len(records)))
    for rec in sample:
        ev = rec["evaluation"]["final"]
        prefix = "final" if not ev.get("same_as_initial") else "initial"
        it = rec["loop"]["iterations"][ev["iteration"] - 1]
        test = pd.read_csv(BASE / config["datasets"][rec["dataset"]]["test_log"])
        for c in ("start_time", "end_time"):
            test[c] = pd.to_datetime(test[c], utc=True, format="mixed").dt.tz_localize(None)
        test["resource"] = test["resource"].astype(str)
        own = recompute_distances([args.results / rec["logs_dir"] / f"{prefix}_seed_{s}.csv.gz" for s in rec["seeds"]],
                                  it["simulated_scenario"], test)
        for m, v in own.items():
            if abs(v - ev["distance_to_test_log"][m]) > 1e-6 * max(1, abs(v)):
                problems.append(f"{rec['config_id']}__{rec['variant']} rep {rec['rep']}: {m} recomputed {v} vs "
                                f"{ev['distance_to_test_log'][m]}")
    # per-variant table
    pv_path = args.results / "ablation_per_variant.csv"
    table = {r["variant"]: r for r in csv.DictReader(open(pv_path, encoding="utf-8"))} if pv_path.exists() else {}
    labels = {"scenario_produced": "Scenario produced", "valid": "Valid", "executable": "Executable",
              "all_kpis_improved": "All KPIs improved"}
    for v in table:
        rows = [r for r in own_rows if r["variant"] == v]
        for k, label in labels.items():
            n_yes = sum(r[k] for r in rows)
            if not table[v][label].startswith(f"{n_yes}/{len(rows)} "):
                problems.append(f"per-variant {v} {label}: own {n_yes}/{len(rows)} vs table {table[v][label]}")
        pos = sum(r["scenario_produced"] and (r["merge_stability"] or 0) > 0 for r in rows)
        if not table[v]["Merge stability > 0"].startswith(f"{pos}/{len(rows)} "):
            problems.append(f"per-variant {v} merge stability > 0: own {pos} vs table {table[v]['Merge stability > 0']}")
        for k in ("DHR", "NIS"):
            vals = [r[k] for r in rows if r[k] is not None]
            if vals and abs(mean(vals) - float(table[v][k])) > 1e-3:
                problems.append(f"per-variant {v} {k}: own {mean(vals):.4f} vs table {table[v][k]}")
    lines = ["# Verification of the real-life ablation, Stage 2", "",
             f"Records: {len(records)}; distance recomputation sample: "
             + ", ".join(f"{r['config_id']}__{r['variant']} rep {r['rep']}" for r in sample),
             f"Per-variant table checked: {'yes' if table else 'not found'}", "",
             f"Mismatches: {len(problems)}", ""] + [f"- {p}" for p in problems]
    (args.results / "verification.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:6]))
    for p in problems[:30]:
        print(" ", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()

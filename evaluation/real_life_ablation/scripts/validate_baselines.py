"""
Validate the chosen baselines (data/<dataset>/) through GLASS's direct-input path, as in the Stage 2 evaluation:
accept_simod_raw_input -> build_baseline_scenario -> scenario_to_prosimos_json, then one Prosimos run with the
test log's case count and first start, seeded like the Stage 2 batch's shim (Python random and NumPy, seed 42).

Compares cases, events per case and mean cycle time (first start to last end per case) of that run with the
test log and with the package's own simulated mean for the variant (metrics-cycle-time.csv, mean over its 10
logs); checks that every timer survives (BPMN timer events, event_distribution, imported intermediate events,
exported event_distribution); records the whole-hour calendar rounding (weekly available hours of all
resources before / after, arrival calendar). Writes baselines/validation.json and baselines/validation_report.md.

Usage (repo root):
  python evaluation/real_life_ablation/scripts/validate_baselines.py <path to the extracted package>
"""
from __future__ import annotations

import json
import random
import re
import sys
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
REPO = BASE.parent.parent
sys.path.insert(0, str(REPO / "goal_to_parameters"))
DATASETS = ["AcademicCredentials", "BPIC_2012_W", "BPIC_2017_W"]
DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
SEED = 42


def load(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in ("start_time", "end_time"):
        df[c] = pd.to_datetime(df[c], utc=True, format="mixed")
    return df


def stats(df: pd.DataFrame) -> dict:
    g = df.groupby("case_id")
    ct = (g["end_time"].max() - g["start_time"].min()).dt.total_seconds()
    return {"cases": int(df["case_id"].nunique()), "events": int(len(df)),
            "events_per_case": round(len(df) / df["case_id"].nunique(), 3), "mean_cycle_time_s": round(float(ct.mean()), 1)}


def hours(t) -> float:
    h, m, s = (str(t) + ":00:00").split(":")[:3]
    return int(h) + int(m) / 60 + float(s) / 3600


def weekly(periods: list[dict]) -> float:
    total = 0.0
    for p in periods:
        days = (DAYS.index(p["to"]) - DAYS.index(p["from"])) % 7 + 1
        end = hours(p["endTime"])
        end = 24.0 if end >= 23.99 else end
        total += days * max(0.0, end - hours(p["beginTime"]))
    return total


def main() -> None:
    from log_distance_measures.config import EventLogIDs
    from log_distance_measures.cycle_time_distribution import cycle_time_distribution_distance
    from second_llm.prosimos_runner import run_prosimos_simulation, scenario_to_prosimos_json
    from second_llm.simod_input import accept_simod_raw_input
    from second_llm.simod_to_simubridge import build_baseline_scenario
    from importlib.metadata import version

    pkg = Path(sys.argv[1]).resolve()
    paper = pd.read_csv(pkg / "real-life-evaluation" / "metrics-cycle-time.csv").set_index("name")
    choice = json.loads((BASE / "baselines" / "baseline_selection.json").read_text(encoding="utf-8"))
    ids = EventLogIDs(case="case_id", activity="activity", start_time="start_time", end_time="end_time", resource="resource")
    report = {}
    for ds in DATASETS:
        variant = choice[ds]["variant"]
        d = BASE / "data" / ds
        test = load(pd.read_csv(d / f"{ds}_test.csv.gz"))
        bpmn = (d / f"{ds}_{variant}.bpmn").read_text(encoding="utf-8")
        jtext = (d / f"{ds}_{variant}.json").read_text(encoding="utf-8")
        src = json.loads(jtext)
        raw = accept_simod_raw_input(jtext, bpmn)
        built = build_baseline_scenario(json.loads(raw.simod_result.json_params_content), bpmn_xml=raw.simod_result.bpmn_content)
        r = {"variant": variant, "selection_ctd_mean": choice[ds]["ctd_mean"], "import_ok": built.ok,
             "import_errors": built.errors}
        scn = built.scenario
        out = scenario_to_prosimos_json(scn, raw.simod_result.bpmn_content)
        r["timers"] = {"bpmn_timer_events": len(re.findall(r"timerEventDefinition", bpmn)),
                       "event_distribution_source": len(src.get("event_distribution") or []),
                       "imported_intermediate_events": sum(len(m.modelParameter.intermediateEvents) for m in scn.models),
                       "event_distribution_exported": len(out.get("event_distribution") or [])}
        r["timers_preserved"] = len(set(r["timers"].values())) == 1
        src_cal = {c["id"]: c["time_periods"] for c in src["resource_calendars"]}
        out_cal = {c["id"]: c["time_periods"] for c in out["resource_calendars"]}
        src_res = {x["id"]: x["calendar"] for p in src["resource_profiles"] for x in p["resource_list"]}
        out_res = {x["id"]: x["calendar"] for p in out["resource_profiles"] for x in p["resource_list"]}
        before = sum(weekly(src_cal[c]) for c in src_res.values())
        after = sum(weekly(out_cal[out_res[i]]) for i in src_res if i in out_res)
        r["calendar_rounding"] = {"resources": len(src_res), "resources_in_export": sum(i in out_res for i in src_res),
                                  "weekly_available_hours_before": round(before, 1),
                                  "weekly_available_hours_after": round(after, 1),
                                  "change_pct": round((after / before - 1) * 100, 2),
                                  "arrival_calendar_hours_before": round(weekly(src.get("arrival_time_calendar") or []), 1),
                                  "arrival_calendar_hours_after": round(weekly(out.get("arrival_time_calendar") or []), 1)}
        n_cases = int(test["case_id"].nunique())
        start = str(test["start_time"].min())
        random.seed(SEED)
        np.random.seed(SEED)
        t0 = time.time()
        res = run_prosimos_simulation(scn, raw.simod_result.bpmn_content, total_cases=n_cases, start_time=start, seed=SEED)
        r["simulation"] = {"cases_requested": n_cases, "start": start, "seed": SEED, "ok": bool(res and res.ok),
                           "error": None if res is None or res.ok else str(res.error)[:300],
                           "seconds": round(time.time() - t0, 1)}
        key = f"{ds}_{variant.replace('_enhanced', '')}"
        r["test_log"] = stats(test)
        r["package_sim_mean_cycle_time_s"] = round(float(paper.loc[key, "mean_mean"]), 1)
        if res and res.ok:
            sim = load(res.simulated_log)
            r["glass_run"] = stats(sim)
            r["glass_run"]["ctd_to_test"] = round(float(cycle_time_distribution_distance(test, ids, sim, ids,
                                                                                           bin_size=timedelta(hours=1))), 4)
            g, t, p = r["glass_run"]["mean_cycle_time_s"], r["test_log"]["mean_cycle_time_s"], r["package_sim_mean_cycle_time_s"]
            r["glass_vs_test_pct"] = round((g / t - 1) * 100, 1)
            r["glass_vs_package_pct"] = round((g / p - 1) * 100, 1)
        report[ds] = r
        print(ds, json.dumps(r, default=str)[:600], flush=True)
    meta = {"glass_commit": __import__("subprocess").run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                                                         text=True).stdout.strip(),
            "prosimos": version("prosimos"), "python": sys.version.split()[0]}
    (BASE / "baselines" / "validation.json").write_text(json.dumps({"meta": meta, "datasets": report}, indent=1, default=str),
                                                        encoding="utf-8")

    L = ["# Baseline validation", "",
         f"Generated by `scripts/validate_baselines.py` (GLASS `{meta['glass_commit'][:7]}`, Prosimos {meta['prosimos']}, "
         f"Python {meta['python']}).", "",
         "Each baseline went through GLASS's direct-input path, as in Stage 2: `accept_simod_raw_input` → "
         "`build_baseline_scenario` → `scenario_to_prosimos_json`. It was then simulated once with Prosimos, with the "
         f"test log's case count and first start, seeded with {SEED} (Python `random` and NumPy, as the Stage 2 seed shim).",
         "",
         "## Baselines", "",
         "| Dataset | Chosen variant | Selection CTD (package logs vs test log) | Import | Timers (BPMN / source / GLASS / export) | Preserved |",
         "|---|---|---|---|---|---|"]
    for ds, r in report.items():
        t = r["timers"]
        L.append(f"| {ds} | `{r['variant']}` | {r['selection_ctd_mean']:.2f} | {'OK' if r['import_ok'] else 'error'} | "
                 f"{t['bpmn_timer_events']} / {t['event_distribution_source']} / {t['imported_intermediate_events']} / "
                 f"{t['event_distribution_exported']} | {'yes' if r['timers_preserved'] else 'NO'} |")
    L += ["", "## One simulation run vs. the test log and the package's own simulation", "",
          "| Dataset | Cases (test / GLASS run) | Events per case (test / GLASS run) | Mean cycle time, test log | Mean cycle time, package's simulation (10 logs) | Mean cycle time, GLASS run | GLASS vs test | GLASS vs package | CTD, GLASS run vs test log |",
          "|---|---|---|---|---|---|---|---|---|"]
    for ds, r in report.items():
        g, t = r.get("glass_run", {}), r["test_log"]
        L.append(f"| {ds} | {t['cases']} / {g.get('cases')} | {t['events_per_case']} / {g.get('events_per_case')} | "
                 f"{t['mean_cycle_time_s'] / 86400:.2f} d | {r['package_sim_mean_cycle_time_s'] / 86400:.2f} d | "
                 f"{g.get('mean_cycle_time_s', 0) / 86400:.2f} d | {r.get('glass_vs_test_pct')} % | {r.get('glass_vs_package_pct')} % | "
                 f"{g.get('ctd_to_test')} |")
    L += ["", "## Whole-hour calendar rounding", "",
          "GLASS stores calendar times as whole hours (`TimetableItem`), so periods such as 21:00–21:45 or 08:00–18:48 "
          "become 21:00–22:00 or 08:00–19:00. Weekly available hours, summed over all resources:", "",
          "| Dataset | Resources | Before (package) | After (GLASS export) | Change | Arrival calendar hours/week (before / after) |",
          "|---|---|---|---|---|---|"]
    for ds, r in report.items():
        c = r["calendar_rounding"]
        L.append(f"| {ds} | {c['resources']} | {c['weekly_available_hours_before']:,.1f} h | {c['weekly_available_hours_after']:,.1f} h | "
                 f"{c['change_pct']:+.2f} % | {c['arrival_calendar_hours_before']} / {c['arrival_calendar_hours_after']} |")
    (BASE / "baselines" / "validation_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()

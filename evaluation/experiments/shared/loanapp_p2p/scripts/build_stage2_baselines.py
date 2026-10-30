"""Build and validate the Stage 2 baselines (BPMN + Prosimos sim_params.json) for every configuration.

For each configuration in setup/stage1_config.json:
  1. take the BIMP model named in the "Evaluation cases" sheet of
     setup/GLASS_expected_KPIs_and_evaluation_cases.xlsx (matched on the event-log column),
  2. write stage2/baselines/<config_id>/model.bpmn (merge_ends.py) and sim_params.json (qbp2prosimos.py),
  3. load it through GLASS's own path (accept_simod_raw_input -> build_baseline_scenario ->
     scenario_to_prosimos_json -> run_prosimos_simulation) and simulate 1000 cases from START, and
  4. compare that GLASS-path run with
       (i)  a direct Prosimos run of the same sim_params.json with the same seed (GLASS must not change
            anything the converter produced), and
       (ii) the configuration's BIMP input log (different engine),
     writing stage2/baselines/validation_report.md and validation_metrics.json.
The healthy originals are converted into stage2/healthy/<process>/ (scoring reference only).

Requires Prosimos (2.1.0: Python 3.11 or 3.12); see stage2/README.md for the environment used.
Usage: python scripts/build_stage2_baselines.py
"""
from __future__ import annotations

import json
import math
import random
import re
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent
REPO = EVAL.parents[3]
sys.path.insert(0, str(REPO / "goal_to_parameters"))
sys.path.insert(0, str(HERE))

from qbp2prosimos import convert  # noqa: E402
from second_llm.prosimos_runner import (  # noqa: E402
    ProsimosBackend,
    run_prosimos_simulation,
    scenario_to_prosimos_json,
)
from second_llm.scenario_generator import _parse_simod_json  # noqa: E402
from second_llm.simod_input import accept_simod_raw_input  # noqa: E402
from second_llm.simod_to_simubridge import build_baseline_scenario  # noqa: E402

START = "2023-02-09T10:00:00+02:00"
N_CASES = 1000
SEED = 20230209           # seeds Python's random and NumPy's global RNG before each Prosimos run
N_BOOT = 200              # bootstrap resamples for the standard error of a median
OUT = EVAL / "stage2"
# (i) GLASS path vs direct run
K_SE = 3.0                # medians: +-max(2 min, 3 SE); events per case: +-3 SE
DUR_FLOOR_MIN = 2.0
CYCLE_REL = 0.10          # mean cycle time: +-10 %
WAIT_REL, WAIT_ABS_H = 0.20, 5 / 60   # KPI waiting median: +-20 % or +-5 min, whichever is larger
# (ii) GLASS path vs BIMP input log
FACTOR = 2.0              # mean cycle time and KPI waiting median within a factor of 2
WAIT_FLOOR_H = 1 / 60     # waiting medians below 1 min count as 1 min in that ratio


# ---------------------------------------------------------------- inputs
def evaluation_cases() -> list[dict[str, str]]:
    """Rows of the 'Evaluation cases' sheet (stdlib xlsx reader; inline or shared strings)."""
    z = zipfile.ZipFile(EVAL / "setup" / "GLASS_expected_KPIs_and_evaluation_cases.xlsx")
    m = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    r = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        shared = ["".join(t.text or "" for t in si.iter(m + "t"))
                  for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(m + "si")]
    rels = {x.get("Id"): x.get("Target") for x in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    sheet = next(s for s in ET.fromstring(z.read("xl/workbook.xml")).iter(m + "sheet")
                 if s.get("name").strip().lower() == "evaluation cases")
    target = rels[sheet.get(r + "id")].lstrip("/")
    target = target if target.startswith("xl/") else "xl/" + target

    def cell(c):
        v = c.find(m + "v")
        if c.get("t") == "inlineStr":
            return "".join(t.text or "" for t in c.iter(m + "t"))
        if v is None:
            return ""
        return shared[int(v.text)] if c.get("t") == "s" else v.text

    rows = [[cell(c) for c in row.findall(m + "c")] for row in ET.fromstring(z.read(target)).iter(m + "row")]
    header = rows[0]
    return [dict(zip(header, row)) for row in rows[1:] if len(row) >= 4]


def read_input_log(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    return pd.DataFrame({
        "case_id": df["case_id"].astype(str), "activity": df["activity"], "resource": df["resource"].astype(str),
        "start": pd.to_datetime(df["Start_Time"]), "end": pd.to_datetime(df["End_Time"]),
        "pool": df["resource"].astype(str).str.replace(r"-\d+$", "", regex=True),
    })


def read_prosimos_log(df: pd.DataFrame, resource_pool: dict[str, str]) -> pd.DataFrame:
    def ts(col):
        return pd.to_datetime(df[col], format="ISO8601", utc=True).dt.tz_localize(None)
    return pd.DataFrame({
        "case_id": df["case_id"].astype(str), "activity": df["activity"], "resource": df["resource"].astype(str),
        "start": ts("start_time"), "end": ts("end_time"),
        "pool": df["resource"].astype(str).map(resource_pool).fillna("?"),
    })


# ---------------------------------------------------------------- metrics
def _median_se(values: np.ndarray, rng: np.random.Generator) -> float:
    if len(values) < 2:
        return 0.0
    idx = rng.integers(0, len(values), size=(N_BOOT, len(values)))
    return float(np.median(values[idx], axis=1).std(ddof=1))


def metrics(log: pd.DataFrame, task_names: set[str], kpi_activity: str | None) -> dict:
    rng = np.random.default_rng(SEED)
    log = log[log["activity"].isin(task_names)].copy()
    log["dur_min"] = (log["end"] - log["start"]).dt.total_seconds() / 60
    per_case = log.groupby("case_id")
    cycle_h = ((per_case["end"].max() - per_case["start"].min()).dt.total_seconds() / 3600)
    events = per_case.size()
    waits = []
    if kpi_activity:
        for _, g in per_case:
            g = g.sort_values("end")
            for _, row in g[g["activity"] == kpi_activity].iterrows():
                prev = g[(g["end"] <= row["start"]) & (g.index != row.name)]
                if len(prev):
                    waits.append((row["start"] - prev["end"].max()).total_seconds() / 3600)
    durations = {a: g["dur_min"].to_numpy() for a, g in log.groupby("activity")}
    return {
        "cases": int(log["case_id"].nunique()),
        "events_per_case": float(events.mean()),
        "events_per_case_se": float(events.std(ddof=1) / math.sqrt(len(events))),
        "duration_median_min": {a: float(np.median(v)) for a, v in durations.items()},
        "duration_median_se_min": {a: _median_se(v, rng) for a, v in durations.items()},
        "resources_per_pool": {p: int(v) for p, v in log.groupby("pool")["resource"].nunique().items()},
        "kpi_wait_median_h": float(np.median(waits)) if waits else None,
        "mean_cycle_h": float(cycle_h.mean()),
    }


def ratio(a: float | None, b: float | None, floor: float = 0.0) -> float | None:
    if a is None or b is None:
        return None
    a, b = max(a, floor), max(b, floor)
    if a == 0 or b == 0:
        return 1.0 if a == b else math.inf
    return a / b


def compare_direct(direct: dict, glass: dict) -> dict:
    """(i) GLASS path vs direct run of the same sim_params.json with the same seed."""
    acts = set(direct["duration_median_min"]) | set(glass["duration_median_min"])
    rows = {}
    for a in acts:
        if a not in direct["duration_median_min"] or a not in glass["duration_median_min"]:
            rows[a] = (math.inf, DUR_FLOOR_MIN)
            continue
        se = math.hypot(direct["duration_median_se_min"][a], glass["duration_median_se_min"][a])
        rows[a] = (abs(glass["duration_median_min"][a] - direct["duration_median_min"][a]),
                   max(DUR_FLOOR_MIN, K_SE * se))
    worst = max(rows, key=lambda a: rows[a][0] / rows[a][1])
    ev_tol = K_SE * math.hypot(direct["events_per_case_se"], glass["events_per_case_se"])
    ev_dev = abs(glass["events_per_case"] - direct["events_per_case"])
    cyc = glass["mean_cycle_h"] / direct["mean_cycle_h"] - 1
    dw, gw = direct["kpi_wait_median_h"], glass["kpi_wait_median_h"]
    w_tol = max(WAIT_REL * dw, WAIT_ABS_H) if dw is not None else None
    w_dev = abs(gw - dw) if dw is not None and gw is not None else None
    checks = {
        "duration_medians": rows[worst][0] <= rows[worst][1],
        "events_per_case": ev_dev <= ev_tol,
        "mean_cycle": abs(cyc) <= CYCLE_REL,
        "kpi_wait": (dw is None and gw is None) or (w_dev is not None and w_dev <= w_tol),
    }
    return {"checks": checks, "worst_activity": worst, "worst_dev_min": rows[worst][0],
            "worst_tol_min": rows[worst][1], "events_dev": ev_dev, "events_tol": ev_tol,
            "cycle_rel": cyc, "wait_dev_h": w_dev, "wait_tol_h": w_tol}


def compare_log(log: dict, sim: dict) -> dict:
    """(ii) A simulated run vs the BIMP input log."""
    w = ratio(sim["kpi_wait_median_h"], log["kpi_wait_median_h"], WAIT_FLOOR_H)
    c = ratio(sim["mean_cycle_h"], log["mean_cycle_h"])
    checks = {
        "cases": sim["cases"] == log["cases"],
        "resources_per_pool": sim["resources_per_pool"] == log["resources_per_pool"],
        "mean_cycle": c is not None and 1 / FACTOR <= c <= FACTOR,
        "kpi_wait": w is not None and 1 / FACTOR <= w <= FACTOR,
    }
    return {"checks": checks, "wait_ratio": w, "cycle_ratio": c}


# ---------------------------------------------------------------- distribution checks
def dist_mean_std(entry: dict) -> tuple[str, float, float | None]:
    p = [x["value"] for x in entry["distribution_params"]]
    name = entry["distribution_name"]
    if name == "norm":
        return name, p[0], p[1]
    if name == "uniform":
        return name, (p[0] + p[1]) / 2, None
    return name, p[0], None


def timer_rows(original: dict, glass: dict, names: dict[str, str]) -> list[dict]:
    g_events = {e["event_id"]: e for e in glass.get("event_distribution", [])}
    rows = []
    for e in original.get("event_distribution", []):
        o_name, o_mean, o_std = dist_mean_std(e)
        g = g_events.get(e["event_id"])
        g_name, g_mean, g_std = dist_mean_std(g) if g else ("missing", None, None)
        rows.append({"event_id": e["event_id"], "glass_name": names.get(e["event_id"], ""),
                     "orig": o_name, "orig_mean_s": o_mean, "orig_std_s": o_std,
                     "glass": g_name, "glass_mean_s": g_mean, "glass_std_s": g_std,
                     "match": g is not None and g_name == o_name and g_mean == o_mean and g_std == o_std})
    return rows


def norm_std_mismatches(original: dict, glass: dict) -> tuple[int, list[str]]:
    """Every norm entry (tasks and timers) of sim_params must keep mean and std on the GLASS side."""
    checked, bad = 0, []
    g_tasks = {t["task_id"]: t for t in glass["task_resource_distribution"]}
    for t in original["task_resource_distribution"]:
        for r in t["resources"]:
            if r["distribution_name"] != "norm":
                continue
            checked += 1
            o = dist_mean_std(r)
            for gr in g_tasks.get(t["task_id"], {}).get("resources", []):
                if dist_mean_std(gr) != o:
                    bad.append(f"{t['task_id']}: {o} -> {dist_mean_std(gr)}")
                    break
    g_events = {e["event_id"]: e for e in glass.get("event_distribution", [])}
    for e in original.get("event_distribution", []):
        if e["distribution_name"] == "norm":
            checked += 1
            if e["event_id"] not in g_events or dist_mean_std(g_events[e["event_id"]]) != dist_mean_std(e):
                bad.append(f"{e['event_id']}: {dist_mean_std(e)} -> "
                           f"{dist_mean_std(g_events[e['event_id']]) if e['event_id'] in g_events else 'missing'}")
    return checked, sorted(set(bad))


# ---------------------------------------------------------------- build + validate
def build(model: Path, dest: Path) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    merged = subprocess.run([sys.executable, str(HERE / "merge_ends.py"), str(model), str(dest / "model.bpmn")],
                            capture_output=True, text=True, check=True).stdout.strip()
    info = convert(str(model), str(dest / "sim_params.json"))
    return {"merge_ends": merged, "converter": {k: v for k, v in info.items() if k != "arrival"}}


def seed() -> None:
    random.seed(SEED)
    np.random.seed(SEED)


def run_direct(dest: Path) -> pd.DataFrame:
    """Simulate model.bpmn with sim_params.json directly in Prosimos (in-process, seeded)."""
    from prosimos.simulation_engine import run_simulation
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "log.csv"
        seed()
        run_simulation(bpmn_path=str(dest / "model.bpmn"), json_path=str(dest / "sim_params.json"),
                       total_cases=N_CASES, stat_out_path=str(Path(tmp) / "stats.csv"),
                       log_out_path=str(log), starting_at=START)
        return pd.read_csv(log)


def validate(cfg: dict, dest: Path, kpi_activity: str | None) -> dict:
    bpmn = (dest / "model.bpmn").read_text(encoding="utf-8")
    original = json.loads((dest / "sim_params.json").read_text())
    raw = accept_simod_raw_input((dest / "sim_params.json").read_text(), bpmn)
    simod = _parse_simod_json(raw.raw_text, raw.simod_result.json_params_content)
    built = build_baseline_scenario(simod, bpmn_xml=raw.simod_result.bpmn_content)
    if not built.ok:
        return {"error": "build_baseline_scenario: " + "; ".join(built.errors)}
    scenario = built.scenario
    glass_json = scenario_to_prosimos_json(scenario, raw.simod_result.bpmn_content)
    task_names = {a.name for m in scenario.models for a in m.modelParameter.activities}
    timer_names = {e.id: e.name for m in scenario.models for e in m.modelParameter.intermediateEvents}

    pool_of = {r["id"]: p["name"] for p in glass_json["resource_profiles"] for r in p["resource_list"]}
    # Prosimos logs the resource name; GLASS's export sets name = id, the converter does not.
    direct_pool_of = {r[k]: p["name"] for p in original["resource_profiles"] for r in p["resource_list"]
                      for k in ("id", "name")}
    log_m = metrics(read_input_log(EVAL / "input_logs" / cfg["event_log"]), task_names, kpi_activity)

    # GLASS's runner takes a seed argument but does not apply it; the Python backend runs
    # in-process, so seeding the global RNGs here gives both runs the same seed.
    seed()
    sim = run_prosimos_simulation(scenario, raw.simod_result.bpmn_content, total_cases=N_CASES,
                                  start_time=START, backend=ProsimosBackend.PYTHON)
    if not sim.ok:
        return {"error": f"GLASS Prosimos run: {sim.error}"}
    glass_m = metrics(read_prosimos_log(sim.simulated_log, pool_of), task_names, kpi_activity)
    direct_m = metrics(read_prosimos_log(run_direct(dest), direct_pool_of), task_names, kpi_activity)

    checked, bad = norm_std_mismatches(original, glass_json)
    return {"kpi_activity": kpi_activity, "import_notes": built.notes,
            "log": log_m, "glass": glass_m, "direct": direct_m,
            "i": compare_direct(direct_m, glass_m), "ii": compare_log(log_m, glass_m),
            "direct_vs_log": compare_log(log_m, direct_m),
            "norm_checked": checked, "norm_mismatches": bad,
            "timers": timer_rows(original, glass_json, timer_names) if "G2" in cfg["goal_config"] else None}


# ---------------------------------------------------------------- report
def fmt(x, nd=1):
    if x is None:
        return "–"
    if isinstance(x, float) and math.isinf(x):
        return "∞"
    return f"{x:.{nd}f}"


def pass_i(res: dict) -> bool:
    return "error" not in res and all(res["i"]["checks"].values())


def pass_ii(res: dict) -> bool:
    if "error" in res:
        return False
    timers_ok = res["timers"] is None or (bool(res["timers"]) and all(t["match"] for t in res["timers"]))
    return all(res["ii"]["checks"].values()) and not res["norm_mismatches"] and timers_ok


def verdict(res: dict) -> str:
    return "PASS" if pass_i(res) and pass_ii(res) else "FAIL"


def mark(ok: bool) -> str:
    return "" if ok else " ✗"


def report(results: dict, cases_of: dict, built: dict) -> str:
    L = ["# Stage 2 baselines: validation report", "",
         "Generated by `scripts/build_stage2_baselines.py`. Each baseline (`<config_id>/model.bpmn` + `sim_params.json`) "
         "was loaded through GLASS's own path (`accept_simod_raw_input` → `build_baseline_scenario` → "
         "`scenario_to_prosimos_json` → `run_prosimos_simulation`) and simulated once with Prosimos "
         f"({N_CASES} cases from {START}, seed {SEED}). PASS requires both (i) and (ii). ✗ marks a failed check.", "",
         "**(i) GLASS path vs direct run.** The same `sim_params.json` is simulated directly with Prosimos with the same "
         "seed. GLASS must not change what the converter produced.",
         f"- Per-activity duration medians: within ±max({DUR_FLOOR_MIN:g} min, {K_SE:g} SE). SE is the standard error "
         f"of the difference of the two medians, √(SE₁² + SE₂²), each from {N_BOOT} bootstrap resamples. "
         "The activity with the largest deviation relative to its tolerance is shown.",
         f"- Events per case: within ±{K_SE:g} SE of the difference of the two means.",
         f"- Mean cycle time: within ±{CYCLE_REL:.0%}.",
         f"- Median waiting before the KPI activity: within ±{WAIT_REL:.0%} or ±{WAIT_ABS_H * 60:g} min, "
         "whichever is larger.",
         "",
         "**(ii) GLASS path vs BIMP input log.** This compares different engines; end-of-day handling differs, as "
         "documented in Step 1.",
         f"- Mean cycle time and median waiting before the KPI activity: within a factor of {FACTOR:g}. "
         "Waiting medians below 1 min count as 1 min.",
         "- Cases and distinct resources per pool: exact.",
         "- G2-containing configurations: every timer is present on the GLASS side with its original distribution "
         "and mean.",
         "- All configurations: every normal distribution keeps its std.",
         "",
         "KPI activity: the activity of the process's \"<activity> Waiting Time\" KPI in `setup/stage1_config.json`. "
         "Waiting is the start of that activity minus the latest earlier end in the same case.",
         "", "## Summary", "", "| Configuration | Model | (i) vs direct run | (ii) vs BIMP log | Verdict |",
         "|---|---|---|---|---|"]
    for cid, res in results.items():
        if "error" in res:
            L.append(f"| {cid} | {cases_of[cid]} | – | – | FAIL ({res['error']}) |")
            continue
        L.append(f"| {cid} | {cases_of[cid]} | {'PASS' if pass_i(res) else 'FAIL'} | "
                 f"{'PASS' if pass_ii(res) else 'FAIL'} | **{verdict(res)}** |")

    L += ["", "## (i) GLASS path vs direct Prosimos run (same seed)", "",
          "| Configuration | Worst duration median: Δ / tolerance (min), activity | Events per case direct/GLASS "
          "(Δ / tol) | Mean cycle h direct/GLASS (Δ) | KPI wait median h direct/GLASS (Δ / tol, min) | (i) |",
          "|---|---|---|---|---|---|"]
    for cid, res in results.items():
        if "error" in res:
            continue
        d, g, c = res["direct"], res["glass"], res["i"]
        ch = c["checks"]
        L.append(
            f"| {cid} | {fmt(c['worst_dev_min'])} / {fmt(c['worst_tol_min'])} ({c['worst_activity']}){mark(ch['duration_medians'])} | "
            f"{fmt(d['events_per_case'], 2)}/{fmt(g['events_per_case'], 2)} ({fmt(c['events_dev'], 2)} / "
            f"{fmt(c['events_tol'], 2)}){mark(ch['events_per_case'])} | "
            f"{fmt(d['mean_cycle_h'])}/{fmt(g['mean_cycle_h'])} ({c['cycle_rel']:+.1%}){mark(ch['mean_cycle'])} | "
            f"{fmt(d['kpi_wait_median_h'], 2)}/{fmt(g['kpi_wait_median_h'], 2)} "
            f"({fmt(c['wait_dev_h'] * 60 if c['wait_dev_h'] is not None else None)} / "
            f"{fmt(c['wait_tol_h'] * 60 if c['wait_tol_h'] is not None else None)}){mark(ch['kpi_wait'])} | "
            f"{'PASS' if pass_i(res) else 'FAIL'} |")

    L += ["", "## (ii) GLASS path vs BIMP input log", "",
          "| Configuration | Cases log/GLASS | Pools | Mean cycle h log/GLASS (ratio) | KPI wait median h log/GLASS (ratio) | "
          "Timers | Norm std | (ii) |", "|---|---|---|---|---|---|---|---|"]
    for cid, res in results.items():
        if "error" in res:
            continue
        lg, g, c = res["log"], res["glass"], res["ii"]
        ch = c["checks"]
        pools = "equal" if ch["resources_per_pool"] else \
            "✗ " + ", ".join(f"{p} {lg['resources_per_pool'].get(p, 0)}/{g['resources_per_pool'].get(p, 0)}"
                             for p in sorted(set(lg["resources_per_pool"]) | set(g["resources_per_pool"]))
                             if lg["resources_per_pool"].get(p) != g["resources_per_pool"].get(p))
        timers = "n/a" if res["timers"] is None else \
            f"{sum(t['match'] for t in res['timers'])}/{len(res['timers'])} match" + \
            mark(bool(res["timers"]) and all(t["match"] for t in res["timers"]))
        norm = f"{res['norm_checked']} ok" if not res["norm_mismatches"] else f"{len(res['norm_mismatches'])} changed ✗"
        L.append(
            f"| {cid} | {lg['cases']}/{g['cases']}{mark(ch['cases'])} | {pools} | "
            f"{fmt(lg['mean_cycle_h'])}/{fmt(g['mean_cycle_h'])} ({fmt(c['cycle_ratio'], 2)}){mark(ch['mean_cycle'])} | "
            f"{fmt(lg['kpi_wait_median_h'], 2)}/{fmt(g['kpi_wait_median_h'], 2)} ({fmt(c['wait_ratio'], 2)}){mark(ch['kpi_wait'])} | "
            f"{timers} | {norm} | {'PASS' if pass_ii(res) else 'FAIL'} |")

    L += ["", "## Timers per G2 configuration", "",
          "Original: `sim_params.json` converted from the BIMP model. GLASS side: `scenario_to_prosimos_json` output.", "",
          "| Configuration | Timer id | GLASS display name | Original | Original mean (min) | GLASS side | "
          "GLASS-side mean (min) | Std orig/GLASS (min) | Match |", "|---|---|---|---|---|---|---|---|---|"]
    for cid, res in results.items():
        for t in (res.get("timers") or []):
            std = "–" if t["orig_std_s"] is None and t["glass_std_s"] is None else \
                f"{fmt(t['orig_std_s'] / 60 if t['orig_std_s'] is not None else None)}/" \
                f"{fmt(t['glass_std_s'] / 60 if t['glass_std_s'] is not None else None)}"
            L.append(f"| {cid} | {t['event_id']} | {t['glass_name']} | {t['orig']} | {fmt(t['orig_mean_s'] / 60)} | "
                     f"{t['glass']} | {fmt(t['glass_mean_s'] / 60 if t['glass_mean_s'] is not None else None)} | "
                     f"{std} | {'yes' if t['match'] else 'NO'} |")

    L += ["", "## Diagnostic: direct run vs BIMP input log (not part of the verdict)", "",
          "Criteria (ii) applied to the direct Prosimos run. A deviation that appears here too comes from the "
          "engine difference (Prosimos vs BIMP), not from GLASS.", "",
          "| Configuration | Mean cycle h log/direct (ratio) | KPI wait median h log/direct (ratio) |", "|---|---|---|"]
    for cid, res in results.items():
        if "error" in res:
            continue
        lg, d, c = res["log"], res["direct"], res["direct_vs_log"]
        L.append(f"| {cid} | {fmt(lg['mean_cycle_h'])}/{fmt(d['mean_cycle_h'])} ({fmt(c['cycle_ratio'], 2)})"
                 f"{mark(c['checks']['mean_cycle'])} | {fmt(lg['kpi_wait_median_h'], 2)}/{fmt(d['kpi_wait_median_h'], 2)} "
                 f"({fmt(c['wait_ratio'], 2)}){mark(c['checks']['kpi_wait'])} |")

    L += ["", "## Conversion log", "", "| Output | Source model | merge_ends.py | Tasks | Timers | Gateways | Pools |",
          "|---|---|---|---|---|---|---|"]
    for out, b in built.items():
        cv = b["converter"]
        L.append(f"| {out} | {b['model']} | {b['merge_ends']} | {cv['tasks']} | {cv['timers']} | {cv['gateways']} | "
                 + ", ".join(f"{k} {v}" for k, v in cv["pools"].items()) + " |")
    return "\n".join(L) + "\n"


def main() -> None:
    config = json.loads((EVAL / "setup" / "stage1_config.json").read_text(encoding="utf-8"))["configurations"]
    sheet = evaluation_cases()
    model_of_log = {row["Event log given as input to GLASS"].strip(): row["BPS model"].strip() for row in sheet}
    kpi_activity_of = {}
    for cfg in config:
        kpis = cfg["expected_kpis"] if isinstance(cfg["expected_kpis"], list) else re.findall(r"['\"](.+?)['\"](?:,|\])", cfg["expected_kpis"])
        for k in kpis:
            if k.endswith(" Waiting Time"):
                kpi_activity_of[cfg["process"]] = k[: -len(" Waiting Time")]

    built, results, cases_of = {}, {}, {}
    for cfg in config:
        model = model_of_log[cfg["event_log"]]
        dest = OUT / "baselines" / cfg["id"]
        built[f"baselines/{cfg['id']}"] = {"model": model, **build(EVAL / "bps_models" / model, dest)}
        cases_of[cfg["id"]] = model
        print(f"validating {cfg['id']} ({model}) ...", flush=True)
        results[cfg["id"]] = validate(cfg, dest, kpi_activity_of.get(cfg["process"]))
        print(f"  -> {verdict(results[cfg['id']])}", flush=True)
    for process in sorted({c["process"] for c in config}):
        healthy = next(r["BPS model"].strip() for r in sheet
                       if r["Process"].strip() == process and r["BPS model"].strip().endswith("_original.bpmn"))
        built[f"healthy/{process}"] = {"model": healthy, **build(EVAL / "bps_models" / healthy, OUT / "healthy" / process)}

    (OUT / "baselines" / "validation_report.md").write_text(report(results, cases_of, built), encoding="utf-8")
    (OUT / "baselines" / "validation_metrics.json").write_text(
        json.dumps({"start": START, "n_cases": N_CASES, "seed": SEED, "results": results}, indent=1, default=str),
        encoding="utf-8")
    print("FAIL:", [c for c, r in results.items() if verdict(r) == "FAIL"] or "none")


if __name__ == "__main__":
    main()

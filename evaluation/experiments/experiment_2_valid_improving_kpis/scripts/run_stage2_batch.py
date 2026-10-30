"""
Stage-2 batch evaluation (validated KPIs + baseline -> scenario patch -> simulation), as delivered by GLASS.

Every configuration in setup/stage2_config.json is run N times through GLASS's own Stage-2 code, called
as a library (nothing under goal_to_parameters/ is modified; nothing an LLM returns is altered):
  baseline   stage2/baselines/<id>/ (validated in bcbf24c), cost_per_hour set from business_context,
             loaded with accept_simod_raw_input -> build_baseline_scenario
  inputs     the KPI objects named in kpi_names_to_pass, taken from the Stage-1 record (first proposals:
             results/stage1_results_normalised/, so measurable_as is set); utilization KPIs get
             target_direction "minimize" and an overload sentence (lead author's interpretation, see README);
             per-KPI inputs from the configuration's kpi_inputs (if any): description_append is appended to
             the KPI description (so it reaches the Stage 2 prompt), target_threshold is set as
             KPITarget.threshold on the loop's and the multi-seed evaluation's targets;
             the three business-context answers as the clarification chat (user turns); the log profile
  loop       iterative_evaluator.run_iterative_evaluation (max_iterations from the config) with the UI's
             KPI targets and cost map; iteration 1 = initial, the loop's best iteration = final
  evaluation multi_seed_evaluation.evaluate_multi_seed (10 seeds) for initial and final against one cached
             10-seed baseline run set per configuration (same seeds for all three models)
Records go to <out>/raw/<id>/rep_<k>.json; reruns resume (final statuses are skipped, llm_error is redone).
Simulated logs are written next to the records but kept out of git (SHA-256 in the records, zipped per
configuration in <out>/log_archives/).

Variant (--variant, default V_full = the batch as before): V_no_kb switches the knowledge base off in generation
and feedback with the real-life ablation's switch (evaluation/experiments/experiment_4_real_life_ablation/scripts/run_ablation_batch.py,
disable_kb); the record gets variant, switches and kb_switch (stub call counts). GLASS v3.2 can keep the
baseline (no_improving_scenario): the record's loop gets no_improving_scenario and best_candidate_iteration.

Usage (repo root, Python environment with Prosimos, see stage2/README.md):
  python evaluation/experiments/experiment_2_valid_improving_kpis/scripts/run_stage2_batch.py [--smoke | --only ID[,ID]] [--reps N] [--workers N]
         [--variant V_full|V_no_kb] [--out DIR]

SCORING (analysis only; the recording is never changed by scoring)
Primary
  valid          the patch validator (validate_patch) passed for the accepted patch, the scenario simulated,
                 and the business-context limits hold on the simulated scenario:
                 headcount of each internal role <= baseline + 3; total hourly staff cost
                 (sum of headcount x costHour) <= baseline x 1.35; external roles (cost_per_hour 0 in
                 business_context: applicant, supplier) keep their headcount; working time of each role
                 <= baseline + 2 h on every weekday (per resource calendar); the immutable steps are still present with their
                 baseline role(s) assigned.
  KPI improvement  per KPI from evaluate_multi_seed (10 seeds, paired with the cached baseline): mean, 95 % CI,
                 paired t-test p-value. improved: GLASS's direction rule on the means for time KPIs; for
                 utilization KPIs the utilization rule below. all_kpis_improved = every KPI improved.
  utilization rule  utilization of the target pool (the role of the G3 problem) = busy time within working
                 time / available working time of its resources (each with its own calendar) over the simulated
                 span, per seed, averaged over the 10 seeds.
                 improved: proposed below 0.85 and lower than the baseline; worsened: higher than the baseline;
                 otherwise unchanged.
Secondary
  addressed      per injected problem: >= 1 modification with the expected parameter_type on an expected
                 element in the expected direction.
  precision      share of all modifications that are on target for any injected problem of the configuration.
  recall         share of the expected elements (of the injected problems) touched by an on-target modification.
  off_target     modifications whose element is not an expected element of any injected problem.
  distance       CTD, RED, CWD, NGD (log-distance-measures) between each of the 10 logs of a model and the
                 healthy model's log of the same seed, averaged; improvement_ratio =
                 1 - d(proposed, healthy) / d(baseline, healthy) per measure. Preprocessing (distances only,
                 all models): an end timestamp of exactly 00:00:00 directly after the end of the resource's
                 working day on the previous day is mapped back to that end (Prosimos logs a task that ends
                 exactly at the end of a working interval at the next midnight); counts per log are recorded.
                 CWD is also recorded on the unmapped logs (CWD_raw).
  Elements match by display name or id, case-insensitively.
END SCORING
"""

from __future__ import annotations

import argparse
import bisect
import copy
import csv
import json
import logging
import random
import subprocess
import sys
import time
import traceback
import warnings
import zipfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path
from statistics import mean

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "loanapp_p2p"))
import lp_paths as P  # noqa: E402  (experiment paths; puts the other experiments' scripts on sys.path)
BASE = P.SHARED
REPO_ROOT = P.REPO
CONFIG_PATH = P.SHARED / "setup" / "stage2_config.json"
sys.path.insert(0, str(REPO_ROOT / "goal_to_parameters"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_stage1_batch as s1   # provider, retry/back-off recording, JSON helpers, app.py log profile  # noqa: E402

BASE_SEED = 42            # GLASS's UI default; seeds 42..51
FINAL_STATUSES = {"ok", "no_initial_proposal", "app_error"}
DIST_BIN = timedelta(hours=1)
UTIL_LIMIT = 0.85
OVERLOAD_TEXT = ("The resource is currently overloaded; bring its utilization down to a sustainable level "
                 "without idling the team.")
# Steps the business context declares immutable (performed by AML Investigator, Senior Officer, Financial Manager).
IMMUTABLE_STEPS = {"LoanApp": ["AML check", "Approve loan offer"],
                   "Procure2Pay": ["Authorize Supplier's Invoice payment"]}
MAX_ADDED_PER_ROLE, MAX_COST_FACTOR, MAX_EXTRA_HOURS_PER_DAY = 3, 1.35, 2
MEASURES = ("CTD", "RED", "CWD", "NGD")
VARIANTS = {"V_full": {"use_kb": True}, "V_no_kb": {"use_kb": False}}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def seed_all(seed: int) -> None:
    """Prosimos draws from Python's random and NumPy's global RNG."""
    random.seed(seed)
    np.random.seed(seed)


def quiet() -> None:
    warnings.filterwarnings("ignore")
    logging.getLogger("streamlit").setLevel(logging.ERROR)
    for name in ("streamlit.runtime.scriptrunner_utils.script_run_context", "streamlit.runtime.state.session_state_proxy"):
        logging.getLogger(name).setLevel(logging.ERROR)
    if not isinstance(sys.stdout, s1._MuteCostTracker):
        sys.stdout = s1._MuteCostTracker(sys.stdout)


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()


def dump(obj):
    """JSON-able form of pydantic models / dataclasses / enums, without changing values."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "__dataclass_fields__"):
        return {k: dump(getattr(obj, k)) for k in obj.__dataclass_fields__}
    if hasattr(obj, "value") and hasattr(obj, "name"):          # enum
        return obj.value
    if isinstance(obj, dict):
        return {str(k): dump(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [dump(v) for v in obj]
    if isinstance(obj, pd.DataFrame):
        return f"<DataFrame {len(obj)} rows>"
    return str(obj)


def seeds_of(settings: dict) -> list[int]:
    return [BASE_SEED + i for i in range(settings["seeds"])]


# ------------------------------------------------------------------ inputs ---

def kpi_source(cfg: dict) -> str:
    """First proposals are read from the normalised Stage-1 records (measurable_as set)."""
    return cfg["kpi_source_record"].replace("results/stage1_results/", "results/stage1_results_normalised/")


def load_inputs(cfg: dict, config: dict) -> dict:
    """Baseline with business-context costs, kept KPIs and the Stage-1 result object for GLASS."""
    bdir = P.lp(cfg["baseline_dir"])
    bc = config["business_context"][cfg["process"]]
    sim_params = json.loads((bdir / "sim_params.json").read_text(encoding="utf-8"))
    for prof in sim_params["resource_profiles"]:
        for res in prof["resource_list"]:
            res["cost_per_hour"] = bc["cost_per_hour"][prof["name"]]
    rec = json.loads((P.lp(kpi_source(cfg))).read_text(encoding="utf-8"))
    by_name = {k["name"]: k for k in rec["result"]["kpis"]}
    missing = [n for n in cfg["kpi_names_to_pass"] if n not in by_name]
    if missing:
        raise SystemExit(f"{cfg['config_id']}: KPI(s) {missing} not found in {kpi_source(cfg)}")
    kept = [copy.deepcopy(by_name[n]) for n in cfg["kpi_names_to_pass"]]
    if null := [k["name"] for k in kept if not k.get("measurable_as")]:
        raise SystemExit(f"{cfg['config_id']}: measurable_as is null for {null} in {kpi_source(cfg)}")
    stage1_dirs = {k["name"]: k["target_direction"] for k in kept}
    kpi_inputs = cfg.get("kpi_inputs") or {}
    if unknown := [n for n in kpi_inputs if n not in cfg["kpi_names_to_pass"]]:
        raise SystemExit(f"{cfg['config_id']}: kpi_inputs for KPI(s) not passed: {unknown}")
    util_kpis = []
    for k in kept:
        if k.get("category") == "utilization":
            k["target_direction"] = "minimize"
            k["description"] = f"{k['description'].rstrip()} {OVERLOAD_TEXT}"
            util_kpis.append(k["name"])
        if extra := kpi_inputs.get(k["name"], {}).get("description_append"):
            k["description"] = f"{k['description'].rstrip()} {extra}"
    thresholds = {n: v["target_threshold"] for n, v in kpi_inputs.items() if v.get("target_threshold") is not None}
    stage1 = {"simulation_goal_structured": rec["result"]["simulation_goal_structured"],
              "kpis": kept, "reasoning": rec["result"]["reasoning"]}
    pool = config["expected_fix"][cfg["process"]]["G3"]["elements"][0] if util_kpis else None
    return {"sim_params": sim_params, "sim_params_text": json.dumps(sim_params, indent=1),
            "bpmn": (bdir / "model.bpmn").read_text(encoding="utf-8"), "stage1_result": stage1,
            "stage1_target_direction": stage1_dirs, "util_kpis": util_kpis, "util_pool": pool,
            "kpi_thresholds": thresholds,
            "kpi_source_used": kpi_source(cfg),
            "chat": [bc["staffing"], bc["budget"], bc["immutable"]], "business_context": bc}


def apply_thresholds(targets: list, thresholds: dict[str, float]) -> list:
    """Set KPITarget.threshold from the configuration (each threshold must match exactly one target)."""
    for name, value in thresholds.items():
        hits = [t for t in targets if t.name == name]
        if len(hits) != 1:
            raise RuntimeError(f"threshold for {name!r} matches {len(hits)} KPI target(s)")
        hits[0].threshold = value
    return targets


def load_scenario(sim_params_text: str, bpmn: str):
    from second_llm.simod_input import accept_simod_raw_input
    from second_llm.simod_to_simubridge import build_baseline_scenario
    raw = accept_simod_raw_input(sim_params_text, bpmn)
    built = build_baseline_scenario(json.loads(raw.simod_result.json_params_content),
                                    bpmn_xml=raw.simod_result.bpmn_content)
    if not built.ok:
        raise RuntimeError("build_baseline_scenario: " + "; ".join(built.errors))
    return built.scenario


def simulate_seeds(scenario, bpmn: str, settings: dict, paths: list[Path]) -> dict[str, str]:
    """GLASS's runner with the seed applied (as the shim does); logs to gzipped CSV, returns SHA-256s."""
    from second_llm.prosimos_runner import run_prosimos_simulation
    shas = {}
    for s, p in zip(seeds_of(settings), paths):
        seed_all(s)
        res = run_prosimos_simulation(scenario, bpmn, total_cases=settings["cases"], start_time=settings["start"], seed=s)
        if not res.ok:
            raise RuntimeError(f"seed {s}: {res.error}")
        p.parent.mkdir(parents=True, exist_ok=True)
        res.simulated_log.to_csv(p, index=False, compression="gzip")
        shas[p.name] = s1.sha256(p.read_bytes())
    return shas


def costed(sim_params_path: Path, process: str, config: dict) -> str:
    sp = json.loads(sim_params_path.read_text(encoding="utf-8"))
    for prof in sp["resource_profiles"]:
        for res in prof["resource_list"]:
            res["cost_per_hour"] = config["business_context"][process]["cost_per_hour"][prof["name"]]
    return json.dumps(sp, indent=1)


# --------------------------------------------------------- measurements ---

def prep_log(df: pd.DataFrame) -> pd.DataFrame:
    """Timestamps as local wall-clock time (the calendars' frame)."""
    df = df.copy()
    for c in ("start_time", "end_time", "enable_time"):
        if c in df:
            df[c] = pd.to_datetime(df[c], format="ISO8601").dt.tz_localize(None)
    return df


def weekday_hours(items: list[dict]) -> dict[str, float]:
    hours = {d: 0.0 for d in WEEKDAYS}
    for it in items:
        a, b = WEEKDAYS.index(it["startWeekday"]), WEEKDAYS.index(it["endWeekday"])
        for k in range(7):
            d = (a + k) % 7
            hours[WEEKDAYS[d]] += it["endTime"] - it["startTime"]
            if d == b:
                break
    return hours


def resource_timetables(scn: dict) -> dict[str, list[dict]]:
    """Resource id -> timetable items of its own calendar (or its role's schedule)."""
    tts = {t["id"]: t["timeTableItems"] for t in scn["resourceParameters"]["timeTables"]}
    return {res["id"]: tts[res.get("calendar") or role["schedule"]]
            for role in scn["resourceParameters"]["roles"] for res in role["resources"]}


def working_windows(items: list[dict], t0, t1) -> tuple[list, list]:
    """Sorted working intervals of a calendar between t0 and t1."""
    starts, ends = [], []
    day = t0.normalize()
    while day <= t1:
        wd = WEEKDAYS[day.weekday()]
        for it in items:
            if weekday_hours([it])[wd] > 0:
                s, e = max(day + timedelta(hours=it["startTime"]), t0), min(day + timedelta(hours=it["endTime"]), t1)
                if e > s:
                    starts.append(s)
                    ends.append(e)
        day += timedelta(days=1)
    order = np.argsort(starts)
    return [starts[i] for i in order], [ends[i] for i in order]


def pool_utilization(log: pd.DataFrame, scn: dict, role_id: str) -> dict:
    """Busy time within working time / available working time of the role's resources (each with its own
    calendar), over the log's span."""
    role = next(r for r in scn["resourceParameters"]["roles"] if r["id"] == role_id)
    res_ids = [r["id"] for r in role["resources"]]
    calendars = resource_timetables(scn)
    t0, t1 = log["start_time"].min(), log["end_time"].max()
    avail = busy = 0.0
    for rid in res_ids:
        starts, ends = working_windows(calendars[rid], t0, t1)
        avail += sum((e - s).total_seconds() for s, e in zip(starts, ends))
        for s, e in log.loc[log["resource"].astype(str) == rid, ["start_time", "end_time"]].itertuples(index=False):
            i = bisect.bisect_right(ends, s)
            while i < len(starts) and starts[i] < e:
                busy += max(0.0, (min(e, ends[i]) - max(s, starts[i])).total_seconds())
                i += 1
    avail_h, busy_h = avail / 3600, busy / 3600
    return {"utilization": round(busy_h / avail_h, 6) if avail_h else None, "busy_h": round(busy_h, 3),
            "available_h": round(avail_h, 3), "n_resources": len(res_ids)}


def utilization_verdict(base_u: list[float], prop_u: list[float]) -> dict:
    """improved: below UTIL_LIMIT and lower than the baseline; worsened: higher; otherwise unchanged."""
    from scipy import stats
    bu, pu = float(np.mean(base_u)), float(np.mean(prop_u))
    verdict = "improved" if pu < UTIL_LIMIT and pu < bu else "worsened" if pu > bu else "unchanged"
    p = float(stats.ttest_rel(base_u, prop_u).pvalue) if len(set(np.subtract(prop_u, base_u))) > 1 else None
    return {"limit": UTIL_LIMIT, "baseline_mean_utilization": round(bu, 4), "proposed_mean_utilization": round(pu, 4),
            "verdict": verdict, "improved": verdict == "improved", "p_value": p,
            "baseline_per_seed": base_u, "proposed_per_seed": prop_u}


def map_midnight_ends(log: pd.DataFrame, scn: dict) -> tuple[pd.DataFrame, int]:
    """Distance preprocessing only: an end of exactly 00:00:00 that directly follows the end of the resource's
    working day on the previous day is mapped back to that end. Returns (copy, number of mapped events)."""
    calendars = resource_timetables(scn)
    out = log.copy()
    mapped = 0
    midnight = out["end_time"] == out["end_time"].dt.normalize()
    for idx in out.index[midnight]:
        res, start, end = str(out.at[idx, "resource"]), out.at[idx, "start_time"], out.at[idx, "end_time"]
        items = calendars.get(res)
        if not items:
            continue
        prev = end - timedelta(days=1)
        day_end = max((it["endTime"] for it in items if weekday_hours([it])[WEEKDAYS[prev.weekday()]] > 0), default=None)
        if day_end is None or day_end >= 24:
            continue
        boundary = prev + timedelta(hours=day_end)
        if start <= boundary:
            out.at[idx, "end_time"] = boundary
            mapped += 1
    return out, mapped


def distances(model_logs: list[pd.DataFrame], healthy: list[pd.DataFrame],
              model_raw: list[pd.DataFrame] | None = None, healthy_raw: list[pd.DataFrame] | None = None) -> dict:
    from log_distance_measures.circadian_workforce_distribution import circadian_workforce_distribution_distance
    from log_distance_measures.config import EventLogIDs
    from log_distance_measures.cycle_time_distribution import cycle_time_distribution_distance
    from log_distance_measures.n_gram_distribution import n_gram_distribution_distance
    from log_distance_measures.relative_event_distribution import relative_event_distribution_distance
    ids = EventLogIDs(case="case_id", activity="activity", start_time="start_time", end_time="end_time",
                      resource="resource")
    fns = {"CTD": lambda a, b: cycle_time_distribution_distance(a, ids, b, ids, bin_size=DIST_BIN),
           "RED": lambda a, b: relative_event_distribution_distance(a, ids, b, ids),
           "CWD": lambda a, b: circadian_workforce_distribution_distance(a, ids, b, ids),
           "NGD": lambda a, b: n_gram_distribution_distance(a, ids, b, ids, n=3)}
    per_seed = {m: [float(f(lg, h)) for lg, h in zip(model_logs, healthy)] for m, f in fns.items()}
    if model_raw is not None:
        per_seed["CWD_raw"] = [float(fns["CWD"](lg, h)) for lg, h in zip(model_raw, healthy_raw)]
    return {m: {"mean": float(np.mean(v)), "per_seed": v} for m, v in per_seed.items()}


def corrected(logs: list[pd.DataFrame], scn: dict) -> tuple[list[pd.DataFrame], list[int]]:
    pairs = [map_midnight_ends(lg, scn) for lg in logs]
    return [p[0] for p in pairs], [p[1] for p in pairs]


# ------------------------------------------------------------- recording ---

class Stage2RecordingProvider(s1.RecordingProvider):
    """Step-1 recorder (verbatim answers, back-off) plus system prompt, iteration tag and failures."""

    def __init__(self, inner, tracker):
        super().__init__(inner)
        self.tracker, self.failed, self.fatal = tracker, [], None

    def __getattr__(self, name):          # e.g. supports_structured_output
        return getattr(self.inner, name)

    def generate(self, system_prompt, user_prompt, temperature=0.3, **kwargs) -> str:
        try:
            raw = super().generate(system_prompt, user_prompt, temperature=temperature, **kwargs)
        except s1.FATAL_API_ERRORS as exc:
            self.fatal = f"{type(exc).__name__}: {exc}"
            raise
        except s1.LLMCallFailed as exc:
            self.failed.append(str(exc))
            raise
        self.calls[-1].update({"system_prompt": system_prompt, "iteration": self.tracker["iteration"],
                               "kwargs": {k: (v if k != "json_schema" else bool(v)) for k, v in kwargs.items()}})
        return raw


class Patches:
    """Replaces module attributes for the duration of a run and restores them."""

    def __init__(self):
        self._saved = []

    def set(self, module, name, value):
        self._saved.append((module, name, getattr(module, name)))
        setattr(module, name, value)

    def restore(self):
        for module, name, original in reversed(self._saved):
            setattr(module, name, original)
        self._saved.clear()


def capture_generation(patches: Patches, tracker: dict, events: list) -> None:
    """Capture-only wrappers: same arguments, unchanged results; inputs/outputs recorded."""
    import second_llm.scenario_generator as sg

    def wrap(name, record):
        original = getattr(sg, name)

        def wrapper(*args, **kwargs):
            result = original(*args, **kwargs)
            events.append({"fn": name, "iteration": tracker["iteration"], **record(args, kwargs, result)})
            return result
        patches.set(sg, name, wrapper)

    wrap("validate_patch", lambda a, k, r: {
        "patch": a[0].model_dump(mode="json"), "result": dump(r), "has_errors": r.has_errors})
    wrap("apply_patch", lambda a, k, r: {
        "patch": a[1].model_dump(mode="json"),
        "merged_scenario": r.scenario.model_dump(mode="json") if r.scenario is not None else None,
        "diagnostics": dump(r.diagnostics), "applied": r.applied_modifications, "skipped": r.skipped_modifications})
    wrap("build_legacy_proposal", lambda a, k, r: {"proposal_id": id(r)})


def seed_shim(patches: Patches, module, baseline=None, cached: dict | None = None, logs: dict | None = None) -> None:
    """GLASS passes a seed to run_prosimos_simulation, which does not apply it; the shim seeds the global RNGs
    with that seed before the call. With ``cached``, baseline runs are served from the cached seed runs."""
    from second_llm.prosimos_runner import ProsimosResult
    original = module.run_prosimos_simulation

    def shim(scenario, bpmn_xml, *args, **kwargs):
        seed = kwargs.get("seed", BASE_SEED)
        if cached is not None and scenario is baseline:
            return ProsimosResult(simulated_log=cached[seed].copy())
        seed_all(seed)
        res = original(scenario, bpmn_xml, *args, **kwargs)
        if logs is not None and res is not None and res.ok:
            logs.setdefault(seed, res.simulated_log)
        return res
    patches.set(module, "run_prosimos_simulation", shim)


# ------------------------------------------------------------ evaluation ---

def kpi_table(res, util: dict | None, util_kpis: list[str]) -> list[dict]:
    rows = []
    for c in res.kpi_comparisons:
        b, p = c.baseline_stats, c.proposed_stats
        is_util = c.category == "utilization" and util is not None and any(
            c.kpi_name in n or n in c.kpi_name for n in util_kpis)
        rows.append({"kpi": c.kpi_name, "category": c.category, "direction": c.target_direction, "status": c.status,
                     "baseline_mean": c.mean_baseline, "baseline_ci": [b.ci_lower, b.ci_upper] if b else None,
                     "proposed_mean": c.mean_proposed, "proposed_ci": [p.ci_lower, p.ci_upper] if p else None,
                     "pct_change": c.mean_percentage_change, "delta_ci": [c.ci_lower_change, c.ci_upper_change],
                     "p_value": c.p_value, "significant": c.statistically_significant,
                     "glass_improved": c.improved, "glass_violated_safeguard": c.violated_safeguard,
                     "threshold": getattr(c, "threshold", None), "glass_achieved": getattr(c, "achieved", None),
                     "scored_by": "utilization_rule" if is_util else "glass",
                     "improved": util["improved"] if is_util else c.improved,
                     "baseline_values": b.values if b else [], "proposed_values": p.values if p else []})
    return rows


def check_validity(scn: dict, base: dict, process: str, bc: dict) -> list[str]:
    """Business-context limits on a scenario (JSON form; independent of GLASS's own checks)."""
    v = []
    roles = {r["id"]: r for r in scn["resourceParameters"]["roles"]}
    for b in base["resourceParameters"]["roles"]:
        r = roles.get(b["id"])
        if r is None:
            v.append(f"role {b['id']} removed")
            continue
        n0, n1 = len(b["resources"]), len(r["resources"])
        internal = bc["cost_per_hour"].get(b["id"], 0) > 0
        if internal and n1 > n0 + MAX_ADDED_PER_ROLE:
            v.append(f"{b['id']}: {n0} -> {n1} (> +{MAX_ADDED_PER_ROLE})")
        if not internal and n1 != n0:
            v.append(f"external role {b['id']}: {n0} -> {n1}")
        cal0, cal1 = resource_timetables(base), resource_timetables(scn)
        sched0 = next(t["timeTableItems"] for t in base["resourceParameters"]["timeTables"] if t["id"] == b["schedule"])
        for res in r["resources"]:
            h0, h1 = weekday_hours(cal0.get(res["id"], sched0)), weekday_hours(cal1[res["id"]])
            for d in WEEKDAYS:
                if h1[d] > h0[d] + MAX_EXTRA_HOURS_PER_DAY + 1e-9:
                    v.append(f"{b['id']}/{res['id']}: {d} {h0[d]:g} h -> {h1[d]:g} h (> +{MAX_EXTRA_HOURS_PER_DAY} h/day)")
    cost = lambda s: sum(len(r["resources"]) * r["costHour"] for r in s["resourceParameters"]["roles"])
    if cost(scn) > MAX_COST_FACTOR * cost(base) + 1e-9:
        v.append(f"hourly staff cost {cost(base):g} -> {cost(scn):g} (> x{MAX_COST_FACTOR})")
    acts = {a["name"]: a for m in scn["models"] for a in m["modelParameter"]["activities"]}
    base_acts = {a["name"]: a for m in base["models"] for a in m["modelParameter"]["activities"]}
    for step in IMMUTABLE_STEPS[process]:
        if step not in acts:
            v.append(f"immutable step '{step}' missing")
        elif not set(base_acts[step]["resources"]) <= set(acts[step]["resources"]):
            v.append(f"immutable step '{step}': roles {base_acts[step]['resources']} -> {acts[step]['resources']}")
    return v


def element_aliases(base: dict, names: list[str]) -> dict[str, set[str]]:
    """Expected element -> {display name, id} (lower case)."""
    pool = [(a["name"], a["id"]) for m in base["models"] for a in m["modelParameter"]["activities"]]
    pool += [(e["name"], e["id"]) for m in base["models"] for e in m["modelParameter"].get("intermediateEvents", [])]
    pool += [(r["id"], r["id"]) for r in base["resourceParameters"]["roles"]]
    out = {}
    for n in names:
        al = {n.lower()}
        for name, eid in pool:
            if n.lower() in (name.lower(), eid.lower()):
                al |= {name.lower(), eid.lower()}
        out[n] = al
    return out


def score_patch(mods: list[dict], cfg: dict, expected_fix: dict, base: dict) -> dict:
    problems = {g: expected_fix[g] for g in cfg["injected_problems"]}
    aliases = {g: element_aliases(base, fx["elements"]) for g, fx in problems.items()}

    def hits(mod, g):
        fx = problems[g]
        el = str(mod.get("target_element", "")).lower()
        return [e for e, al in aliases[g].items() if el in al] if (
            mod.get("parameter_type") == fx["parameter_type"] and mod.get("direction") == fx["direction"]) else []

    on_target = [m for m in mods if any(hits(m, g) for g in problems)]
    all_aliases = set().union(*(al for g in problems for al in aliases[g].values()))
    off = [m for m in mods if str(m.get("target_element", "")).lower() not in all_aliases]
    out = {"n_modifications": len(mods), "n_on_target": len(on_target), "n_off_target": len(off),
           "off_target_elements": [m.get("target_element") for m in off],
           "precision": round(len(on_target) / len(mods), 4) if mods else None}
    touched_all, expected_all = set(), set()
    for g, fx in problems.items():
        touched = {e for m in mods for e in hits(m, g)}
        out[f"addressed_{g}"] = bool(touched)
        out[f"recall_{g}"] = round(len(touched) / len(fx["elements"]), 4)
        touched_all |= {(g, e) for e in touched}
        expected_all |= {(g, e) for e in fx["elements"]}
    out["recall"] = round(len(touched_all) / len(expected_all), 4) if expected_all else None
    return out


# --------------------------------------------------------------- one run ---

def run_rep(job: dict) -> dict:
    """One (configuration, rep), executed in a worker process."""
    quiet()
    config, cfg, rep, out = job["config"], job["cfg"], job["rep"], Path(job["out"])
    settings = config["settings"]
    cid = cfg["config_id"]
    path = out / "raw" / cid / f"rep_{rep}.json"
    t_start = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t0 = time.time()

    import second_llm.iterative_evaluator as ie
    import second_llm.multi_seed_evaluation as mse
    from prompts.scenario_patch_prompt import _build_constraints_block
    from second_llm.models import ChatRole
    from second_llm.orchestrator import SecondLLMWorkspaceOrchestrator
    from second_llm.output_schema import SimuBridgeScenario
    from second_llm.prosimos_runner import scenario_to_prosimos_json
    from second_llm.state import reset_workspace
    from ui.evaluation_panel import _build_cost_map, _get_kpi_targets_from_workspace
    from ui.second_llm_panel import _get_evaluation_targets

    inputs = load_inputs(cfg, config)
    shared = job["shared"][cid]
    variant = job.get("variant", "V_full")
    tracker, events, patches = {"iteration": 0}, [], Patches()
    kb_counter = {"kb_stub_calls": 0, "feedback_kb_suppressed": 0}
    provider = Stage2RecordingProvider(s1.make_provider(settings["model"]), tracker)
    record = {"config_id": cid, "process": cfg["process"], "goal_config": cfg["goal_config"],
              "variant": variant, "switches": VARIANTS[variant],
              "rep": rep, "status": None, "error": None, "timestamp": t_start,
              "provider": type(provider.inner).__name__, "model": provider.get_model_name(),
              "glass_commit": job["glass_commit"], "config_sha256": job["config_sha"],
              "script_sha256": job["script_sha"], "baseline_files_sha256": shared["baseline_files_sha256"],
              "event_log_sha256": shared["event_log_sha256"], "settings": settings, "seeds": seeds_of(settings),
              "temperature_schedule": "GLASS run_iterative_evaluation: min(0.3 + 0.1 * (iteration - 1), 0.7)",
              "kpi_source_record": inputs["kpi_source_used"], "stage1_result_passed": inputs["stage1_result"],
              "stage1_target_direction": inputs["stage1_target_direction"], "util_kpis": inputs["util_kpis"],
              "util_pool": inputs["util_pool"], "kpi_thresholds": inputs["kpi_thresholds"],
              "chat_user_turns": inputs["chat"],
              "sim_params_with_costs": inputs["sim_params"],
              "baseline_logs_sha256": shared["baseline_logs_sha256"],
              "healthy_logs_sha256": job["healthy_sha"][cfg["process"]]}
    try:
        reset_workspace()
        orch = SecondLLMWorkspaceOrchestrator(provider=provider)
        first = orch.parse_first_llm_json(json.dumps(inputs["stage1_result"], ensure_ascii=False))
        if not first.is_valid:
            raise RuntimeError(f"Stage-1 result rejected by GLASS: {first.validation_error or first.parse_error}")
        orch.accept_simod_raw_input(inputs["sim_params_text"], bpmn_xml=inputs["bpmn"])
        for turn in inputs["chat"]:
            orch.chat_session.append(ChatRole.USER, turn)
        if not VARIANTS[variant]["use_kb"]:
            sys.path.insert(0, str(P.EXP4 / "scripts"))
            from run_ablation_batch import disable_kb
            disable_kb(patches, kb_counter)
        log_profile = job["log_profile"][cid]
        record["draft_payload"] = dump(orch.build_second_llm_request_draft(log_profile=log_profile))

        baseline = load_scenario(inputs["sim_params_text"], inputs["bpmn"])
        base_json = baseline.model_dump(mode="json")
        loop_targets = apply_thresholds(_get_evaluation_targets(), inputs["kpi_thresholds"])
        eval_targets = apply_thresholds(_get_kpi_targets_from_workspace(), inputs["kpi_thresholds"])
        cost_map = _build_cost_map(baseline)
        record.update({"loop_targets": dump(loop_targets), "eval_targets": dump(eval_targets), "cost_map": cost_map})

        capture_generation(patches, tracker, events)
        seed_shim(patches, ie)

        def on_iteration(i, msg):
            tracker["iteration"] = i

        loop = ie.run_iterative_evaluation(orch, baseline, inputs["bpmn"], loop_targets,
                                           max_iterations=settings["max_iterations"], total_cases=settings["cases"],
                                           start_time=settings["start"], seed=BASE_SEED, cost_per_hour=cost_map,
                                           on_iteration=on_iteration, log_profile=log_profile)
        patches.restore()
        record["kb_switch"] = dict(kb_counter)
        if provider.fatal:
            return {"fatal": provider.fatal}

        iterations = []
        for it in loop.iterations:
            g = it.gen_result
            evs = [e for e in events if e["iteration"] == it.iteration]
            legacy = [e for e in evs if e["fn"] == "build_legacy_proposal"]
            accepted = next((k for k, e in enumerate(legacy) if g and g.proposal is not None
                             and e["proposal_id"] == id(g.proposal)), None)
            merges = [e for e in evs if e["fn"] == "apply_patch"]
            merge = merges[accepted] if accepted is not None else None
            validation = next((v for v in reversed([e for e in evs if e["fn"] == "validate_patch"])
                               if merge and v["patch"] == merge["patch"]), None)
            scenario = g.proposal.scenario if g and g.proposal else None
            iterations.append({
                "iteration": it.iteration, "error": it.error, "status": dump(it.status), "score": it.score,
                "temperature": round(min(0.3 + 0.1 * (it.iteration - 1), 0.7), 2),
                "generation": None if g is None else {
                    "success": g.success, "error": g.error, "attempts": g.attempts, "decoding_mode": g.decoding_mode,
                    "raw_llm_output": g.raw_llm_output, "generation_notes": g.generation_notes,
                    "context_summary": dump(g.context_summary._data) if g.context_summary else None,
                    "context_block_in_prompt": _build_constraints_block(g.context_summary),
                    "post_merge_validation": dump(g.validation), "merge_stability": g.merge_stability,
                    "cost_report": dump(g.cost_report)},
                "patch": merge["patch"] if merge else None,
                "patch_validator": validation["result"] if validation else None,
                "patch_validator_passed": (not validation["has_errors"]) if validation else None,
                "merged_scenario": merge["merged_scenario"] if merge else None,
                "merge_diagnostics": merge["diagnostics"] if merge else None,
                "simulated_scenario": scenario.model_dump(mode="json") if scenario else None,
                "simulated_scenario_prosimos": scenario_to_prosimos_json(scenario, inputs["bpmn"]) if scenario else None,
                "all_patch_attempts": [{k: e[k] for k in ("patch", "has_errors", "result")}
                                       for e in evs if e["fn"] == "validate_patch"],
                "single_run_evaluation": dump(it.eval_result), "feedback_text": it.feedback_message,
            })
        record["loop"] = {"iterations": iterations, "best_iteration": (loop.best_iteration_idx + 1)
                          if loop.best_iteration_idx is not None else None,
                          "no_improving_scenario": bool(getattr(loop, "no_improving_scenario", False)),
                          "best_candidate_iteration": (loop.best_candidate_idx + 1)
                          if getattr(loop, "best_candidate_idx", None) is not None else None,
                          "final_status": dump(loop.final_status), "error": loop.error,
                          "baseline_kpis_single_run": dump(loop.baseline_kpis),
                          "total_time_seconds": round(loop.total_time_seconds, 1)}
        record["llm_calls"] = provider.calls
        if provider.failed:
            record.update(status="llm_error", error=" | ".join(provider.failed))
            return finish(record, path, t0)
        if not iterations or iterations[0]["simulated_scenario"] is None:
            record.update(status="no_initial_proposal", error=loop.error or (iterations[0]["error"] if iterations else None))
            return finish(record, path, t0)

        cached = {s: pd.read_csv(out / "baseline_logs" / cid / f"seed_{s}.csv.gz") for s in record["seeds"]}
        healthy_raw = [prep_log(pd.read_csv(out / "healthy_logs" / cfg["process"] / f"seed_{s}.csv.gz"))
                       for s in record["seeds"]]
        healthy, _ = corrected(healthy_raw, job["healthy_scenario"][cfg["process"]])
        logs_dir = out / "logs" / cid / f"rep_{rep}"
        evaluation, log_sha = {}, {}
        for phase, it_no in (("initial", 1), ("final", record["loop"]["best_iteration"])):
            it = iterations[it_no - 1] if it_no else None
            if it is None or it["simulated_scenario"] is None:
                evaluation[phase] = {"iteration": it_no, "error": "no scenario"}
                continue
            if phase == "final" and it_no == 1:
                evaluation["final"] = {**evaluation["initial"], "same_as_initial": True}
                continue
            proposed = SimuBridgeScenario.model_validate(it["simulated_scenario"])
            logs: dict[int, pd.DataFrame] = {}
            seed_shim(patches, mse, baseline=baseline, cached=cached, logs=logs)
            try:
                res = mse.evaluate_multi_seed(baseline, proposed, inputs["bpmn"], eval_targets,
                                              num_seeds=settings["seeds"], base_seed=BASE_SEED,
                                              total_cases=settings["cases"], start_time=settings["start"],
                                              cost_per_hour=cost_map)
            finally:
                patches.restore()
            util = None
            if inputs["util_kpis"] and res.ok:
                prop_u = [pool_utilization(prep_log(logs[s]), it["simulated_scenario"], inputs["util_pool"])
                          for s in record["seeds"]]
                util = utilization_verdict(shared["baseline_pool_utilization"], [u["utilization"] for u in prop_u])
                util["proposed_detail"] = prop_u
            for s in record["seeds"]:
                if s in logs:
                    p = logs_dir / f"{phase}_seed_{s}.csv.gz"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    logs[s].to_csv(p, index=False, compression="gzip")
                    log_sha[f"{phase}_seed_{s}.csv.gz"] = s1.sha256(p.read_bytes())
            dist = mapped = None
            if res.ok:
                raw_logs = [prep_log(logs[s]) for s in record["seeds"]]
                corr_logs, mapped = corrected(raw_logs, it["simulated_scenario"])
                dist = distances(corr_logs, healthy, raw_logs, healthy_raw)
            violations = check_validity(it["simulated_scenario"], base_json, cfg["process"], inputs["business_context"])
            evaluation[phase] = {
                "iteration": it_no, "same_as_initial": False, "multi_seed_ok": res.ok, "multi_seed_error": res.error,
                "kpis": kpi_table(res, util, inputs["util_kpis"]) if res.ok else [], "utilization_rule": util,
                "summary": dump(res.summary), "simulation_settings": res.simulation_settings, "n_logs": len(logs),
                "patch_validator_passed": it["patch_validator_passed"],
                "post_merge_validation_errors": [i["message"] for i in
                                                 ((it["generation"] or {}).get("post_merge_validation") or {}).get("issues", [])
                                                 if i.get("severity") == "error"],
                "violations": violations,
                "valid": bool(it["patch_validator_passed"]) and res.ok and not violations,
                "scores": score_patch((it["patch"] or {}).get("modifications", []), cfg,
                                      config["expected_fix"][cfg["process"]], base_json),
                "distance_to_healthy": {k: v["mean"] for k, v in dist.items()} if dist else None,
                "midnight_ends_mapped_per_seed": mapped,
                "distance_to_healthy_per_seed": {k: v["per_seed"] for k, v in dist.items()} if dist else None,
                "improvement_ratio": {k: (round(1 - dist[k]["mean"] / shared["baseline_distance"][k], 4)
                                          if shared["baseline_distance"][k] else None)
                                      for k in MEASURES + ("CWD_raw",)} if dist else None}
        record.update(evaluation=evaluation, baseline_distance_to_healthy=shared["baseline_distance"],
                      baseline_pool_utilization=shared["baseline_pool_utilization"],
                      logs_dir=logs_dir.relative_to(out).as_posix(), logs_sha256=log_sha, status="ok")
    except Exception:
        patches.restore()
        if provider.fatal:
            return {"fatal": provider.fatal}
        record.update(status="app_error", error=traceback.format_exc(), llm_calls=provider.calls)
    return finish(record, path, t0)


def finish(record: dict, path: Path, t0: float) -> dict:
    record["elapsed_s"] = round(time.time() - t0, 1)
    s1.write_json(path, record)
    return {"config_id": record["config_id"], "rep": record["rep"], "status": record["status"],
            "elapsed_s": record["elapsed_s"], "error": (record.get("error") or "").splitlines()[-1:]}


# --------------------------------------------------------- per-config cache ---

def prepare_config(cfg: dict, config: dict, out: Path, app) -> dict:
    """Cached per configuration: the 10-seed baseline runs, their pool utilization and distance to healthy."""
    settings, cid = config["settings"], cfg["config_id"]
    inputs = load_inputs(cfg, config)
    baseline = load_scenario(inputs["sim_params_text"], inputs["bpmn"])
    paths = [out / "baseline_logs" / cid / f"seed_{s}.csv.gz" for s in seeds_of(settings)]
    meta_path = out / "baseline_logs" / cid / "baseline.json"
    if meta_path.exists() and all(p.exists() for p in paths):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    else:
        shas = simulate_seeds(baseline, inputs["bpmn"], settings, paths)
        # determinism: a fresh seeded run of the first seed reproduces the cached log
        check = out / "baseline_logs" / cid / "check.csv.gz"
        simulate_seeds(baseline, inputs["bpmn"], {**settings, "seeds": 1}, [check])
        same = pd.read_csv(check).equals(pd.read_csv(paths[0]))
        check.unlink()
        if not same:
            raise SystemExit(f"{cid}: seeded baseline run is not reproducible")
        logs = [prep_log(pd.read_csv(p)) for p in paths]
        healthy_raw = [prep_log(pd.read_csv(out / "healthy_logs" / cfg["process"] / f"seed_{s}.csv.gz"))
                       for s in seeds_of(settings)]
        base_json = baseline.model_dump(mode="json")
        healthy, healthy_mapped = corrected(healthy_raw, healthy_scenario(cfg["process"], cfg["healthy_dir"], config))
        corr, mapped = corrected(logs, base_json)
        pool = inputs["util_pool"]
        meta = {"baseline_logs_sha256": shas,
                "baseline_pool_utilization": [pool_utilization(lg, base_json, pool)["utilization"] for lg in logs]
                if pool else None,
                "baseline_distance": {k: v["mean"] for k, v in distances(corr, healthy, logs, healthy_raw).items()},
                "baseline_midnight_ends_mapped_per_seed": mapped,
                "healthy_midnight_ends_mapped_per_seed": healthy_mapped}
        meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    with open(P.lp(cfg["event_log"]), "rb") as fh:
        log_profile = app["extract_log_artifacts"](fh)[0]
    return {**meta, "log_profile": log_profile,
            "event_log_sha256": s1.sha256((P.lp(cfg["event_log"])).read_bytes()),
            "baseline_files_sha256": {f: s1.sha256((P.lp(cfg["baseline_dir"]) / f).read_bytes())
                                      for f in ("model.bpmn", "sim_params.json")}}


def healthy_scenario(process: str, healthy_dir: str, config: dict) -> dict:
    hdir = P.lp(healthy_dir)
    return load_scenario(costed(hdir / "sim_params.json", process, config),
                         (hdir / "model.bpmn").read_text(encoding="utf-8")).model_dump(mode="json")


def prepare_healthy(process: str, healthy_dir: str, config: dict, out: Path) -> dict[str, str]:
    paths = [out / "healthy_logs" / process / f"seed_{s}.csv.gz" for s in seeds_of(config["settings"])]
    if all(p.exists() for p in paths):
        return {p.name: s1.sha256(p.read_bytes()) for p in paths}
    hdir = P.lp(healthy_dir)
    bpmn = (hdir / "model.bpmn").read_text(encoding="utf-8")
    scenario = load_scenario(costed(hdir / "sim_params.json", process, config), bpmn)
    return simulate_seeds(scenario, bpmn, config["settings"], paths)


def zip_logs(out: Path, config: dict) -> None:
    arch = out / "log_archives"
    arch.mkdir(exist_ok=True)
    for cfg in config["configurations"]:
        cid = cfg["config_id"]
        files = sorted((out / "baseline_logs" / cid).glob("*")) + sorted((out / "logs" / cid).rglob("*.csv.gz"))
        if files:
            with zipfile.ZipFile(arch / f"{cid}.zip", "w", zipfile.ZIP_STORED) as z:
                for f in files:
                    z.write(f, f.relative_to(out).as_posix())
    for process in sorted({c["process"] for c in config["configurations"]}):
        files = sorted((out / "healthy_logs" / process).glob("*.csv.gz"))
        if files:
            with zipfile.ZipFile(arch / f"healthy_{process}.zip", "w", zipfile.ZIP_STORED) as z:
                for f in files:
                    z.write(f, f.relative_to(out).as_posix())


# --------------------------------------------------------------- summaries ---

def summary_rows(rec: dict, cfg: dict) -> list[dict]:
    rows = []
    loop = rec.get("loop") or {}
    for phase in ("initial", "final"):
        ev = (rec.get("evaluation") or {}).get(phase) or {}
        sc = ev.get("scores") or {}
        util = ev.get("utilization_rule") or {}
        row = {"config_id": rec["config_id"], "process": rec["process"], "goal_config": rec["goal_config"],
               "rep": rec["rep"], "status": rec["status"], "phase": phase, "iteration": ev.get("iteration"),
               "iterations_used": len(loop.get("iterations") or []),
               "valid": ev.get("valid"), "patch_validator_passed": ev.get("patch_validator_passed"),
               "simulation_ran": ev.get("multi_seed_ok"), "violations": " | ".join(ev.get("violations") or []),
               "post_merge_validation_errors": len(ev.get("post_merge_validation_errors") or [])}
        kpis = ev.get("kpis") or []
        for i in range(3):
            k = kpis[i] if i < len(kpis) else {}
            row[f"kpi{i + 1}"] = k.get("kpi", "")
            row[f"kpi{i + 1}_scored_by"] = k.get("scored_by", "")
            row[f"kpi{i + 1}_improved"] = k.get("improved", "")
            row[f"kpi{i + 1}_p_value"] = k.get("p_value", "")
            row[f"kpi{i + 1}_pct_change"] = k.get("pct_change", "")
        row["all_kpis_improved"] = bool(kpis) and all(k.get("improved") is True for k in kpis)
        row["utilization_verdict"] = util.get("verdict", "")
        row["utilization_baseline"] = util.get("baseline_mean_utilization", "")
        row["utilization_proposed"] = util.get("proposed_mean_utilization", "")
        for g in ("G1", "G2", "G3"):
            row[f"addressed_{g}"] = sc.get(f"addressed_{g}", "") if g in cfg["injected_problems"] else ""
        row.update({"precision": sc.get("precision"), "recall": sc.get("recall"),
                    "n_modifications": sc.get("n_modifications"), "n_off_target": sc.get("n_off_target")})
        for m in MEASURES:
            row[f"improvement_ratio_{m}"] = (ev.get("improvement_ratio") or {}).get(m)
        rows.append(row)
    return rows


def write_summaries(out: Path, config: dict) -> None:
    summary, per_config = [], []
    for cfg in config["configurations"]:
        files = sorted((out / "raw" / cfg["config_id"]).glob("rep_*.json"), key=lambda p: int(p.stem.split("_")[1]))
        recs = [s1.load_json(p) for p in files]
        rows = [r for rec in recs for r in summary_rows(rec, cfg)]
        if not rows:
            continue
        summary += rows
        row = {"config_id": cfg["config_id"], "process": cfg["process"], "goal_config": cfg["goal_config"],
               "injected_problems": "|".join(cfg["injected_problems"]), "reps": len(recs),
               "reps_ok": sum(r["status"] == "ok" for r in recs),
               "mean_iterations_used": round(mean(len((r.get("loop") or {}).get("iterations") or []) for r in recs), 2)}
        for phase in ("initial", "final"):
            pr = [r for r in rows if r["phase"] == phase and r["status"] == "ok"]
            row[f"{phase}_valid"] = sum(r["valid"] is True for r in pr)
            row[f"{phase}_all_kpis_improved"] = sum(r["all_kpis_improved"] is True for r in pr)
            row[f"{phase}_valid_and_improved"] = sum(r["valid"] is True and r["all_kpis_improved"] is True for r in pr)
            for g in cfg["injected_problems"]:
                row[f"{phase}_addressed_{g}"] = sum(r[f"addressed_{g}"] is True for r in pr)
            for key in ["precision", "recall"] + [f"improvement_ratio_{m}" for m in MEASURES]:
                vals = [r[key] for r in pr if r[key] is not None]
                row[f"{phase}_mean_{key}"] = round(mean(vals), 3) if vals else ""
        per_config.append(row)
    s1.write_csv(out / "stage2_summary.csv", summary)
    keys = list(dict.fromkeys(k for r in per_config for k in r))
    with open(out / "stage2_per_config.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(per_config)


def write_readme(out: Path, args, config: dict, started: str, ended: str, commit: str, config_sha: str) -> None:
    stamps = [s1.load_json(p)["timestamp"] for p in (out / "raw").glob("*/rep_*.json")]
    rules = __doc__.split("SCORING", 1)[1].split("END SCORING")[0]
    s = config["settings"]
    lines = [
        "# GLASS Stage-2 batch evaluation: replication record", "",
        f"Generated by `{s1.rel(__file__)}` at the end of the run.", "",
        "## Run", "",
        f"- GLASS commit: `{commit}` (goal_to_parameters/ unmodified at run time)",
        f"- `setup/stage2_config.json` SHA-256: `{config_sha}`",
        f"- Model: `{s['model']}`; temperature: GLASS's loop schedule min(0.3 + 0.1 x (iteration - 1), 0.7), "
        f"i.e. 0.3 for the initial proposal (config: {s['temperature']}); GLASS's context-summary and "
        "compliance calls: 0.1",
        f"- Iterations: max {s['max_iterations']}; multi-seed: {s['seeds']} seeds ({BASE_SEED}..{BASE_SEED + s['seeds'] - 1}), "
        f"{s['cases']} cases, start {s['start']}",
        f"- Repetitions: {args.reps}; workers: {args.workers} (processes); variant: {args.variant}",
        f"- This invocation: start {started}, end {ended}; earliest / latest rep timestamp: "
        f"{min(stamps, default='')} / {max(stamps, default='')}",
        f"- Python {sys.version.split()[0]}; prosimos {version('prosimos')}; log-distance-measures "
        f"{version('log-distance-measures')}; numpy {np.__version__}; pandas {pd.__version__}", "",
        "## KPI validation (lead author's interpretation)", "",
        "Every KPI object of category utilization passed to Stage 2 gets `target_direction = \"minimize\"` and the "
        f"sentence \"{OVERLOAD_TEXT}\" appended to its description. The Stage-1 direction (maximize, in all but one "
        "case) is kept in each record as `stage1_target_direction`.",
        "",
        "Rationale: in the G3 configurations the target pool is overloaded. Maximizing its utilization would reward "
        "overloading it further and would score the injected change's recovery (more staff) as a degradation.",
        "",
        "GLASS evaluates the KPI with its own minimize rule on its utilization value, which is the mean of all "
        "resources' busy time over wall-clock time. The batch scores utilization KPIs with its own utilization rule "
        "on the target pool against its working time (see Scoring). GLASS's own verdicts are kept in each record "
        "(`glass_improved`).", "",
        "Per-KPI inputs from the configuration (`kpi_inputs`, if set): `description_append` is appended to the KPI "
        "description and `target_threshold` is passed as `KPITarget.threshold` to the loop and the multi-seed "
        "evaluation (recorded per run as `kpi_thresholds`; GLASS's verdict per KPI as `glass_achieved`).",
        "",
        "## Prosimos midnight end times (distance measures only)", "",
        "Prosimos logs a task that ends exactly at the end of a working interval (e.g. 17:00) with an end time of "
        "00:00 on the next day. Fixed durations proposed by patches hit that boundary regularly; the stochastic "
        "baseline durations rarely do.",
        "",
        "CWD then compares a weekday that appears in only one log (e.g. a Saturday 00:00) and charges its maximum "
        "penalty of 23 for that weekday.",
        "",
        "For the distance measures only, every log (baseline, initial, final, healthy) is preprocessed: an end of "
        "exactly 00:00:00 that directly follows the end of the resource's working day on the previous day is mapped "
        "back to that end. The number of mapped events is recorded per log. The raw logs are not changed.",
        "",
        "The records hold CWD on the raw logs (`CWD_raw`) and on the mapped logs; the summary columns use the mapped "
        "values.", "",
        "## How GLASS was driven (differences from the UI)", "",
        "- Clarification chat: the three business-context answers (staffing, budget, immutable) as three user "
        "turns. No greeting, no assistant replies; the chatbot's readiness signal and keyword gate are not applied. "
        "GLASS summarises the chat into the prompt itself (`build_context_summary`, as after the chat in the app).",
        "- KPI objects: first proposals come from `results/stage1_results_normalised/` (same names, measurable_as set); "
        "refinement-pilot records as configured. measurable_as is asserted non-null for every passed KPI.",
        "- The event-log profile (app.py `extract_log_artifacts`) is passed to the loop; the UI's Generate button passes none.",
        "- Start time from the config; the UI's loop uses 2024-01-01 09:00 UTC.",
        "- Seeds: GLASS passes a seed to `run_prosimos_simulation`, which does not apply it. A shim seeds Python's "
        "`random` and NumPy's global RNG with that seed before each call (loop and multi-seed evaluation), so the "
        "loop's baseline and proposals use seed 42 and every model's run k uses seed 42 + k.",
        "- Paired baseline: the baseline is simulated once per configuration for the 10 seeds "
        "(`baseline_logs/<id>/`, determinism checked by re-running the first seed); `evaluate_multi_seed` gets those "
        "runs for the baseline (served by the shim) for both the initial and the final comparison.",
        "- KPI targets: the loop gets the UI's `_get_evaluation_targets`, the multi-seed evaluation the evaluation "
        "panel's `_get_kpi_targets_from_workspace` (with measurable_as); cost map: `_build_cost_map`.",
        "- GLASS's Stage 2 has no process-description input; the description entered through the Stage-1 KPIs.",
        "- cost_per_hour in the baseline resource profiles is set from business_context (the BIMP files carry none).",
        "- Capture-only wrappers record what `validate_patch`, `apply_patch` and `build_legacy_proposal` receive and "
        "return inside GLASS (same arguments, unchanged results).",
        "- When the best iteration is iteration 1, the final evaluation is the initial one.",
        "- Simulated logs (`logs/`, `baseline_logs/`, `healthy_logs/`) are not in git; their SHA-256 values are in "
        "the records (and `baseline_logs/<id>/baseline.json`), and `log_archives/` holds one zip per configuration "
        "plus one per healthy model.", "",
        "## Scoring (copied from the script header)", "", "```" + rules.rstrip(), "```", "",
    ]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")


# -------------------------------------------------------------------- main ---

def smoke_report(rec: dict) -> str:
    L = [f"== {rec['config_id']} rep {rec['rep']}: status {rec['status']} ({rec.get('elapsed_s')} s)"]
    if rec.get("error"):
        L.append(rec["error"][-2000:])
    L.append(f"\n-- KPI objects passed (from {rec['kpi_source_record']})")
    for k in rec["stage1_result_passed"]["kpis"]:
        L.append(f"   {k['name']} | category {k['category']} | measurable_as {k['measurable_as']} | direction "
                 f"{rec['stage1_target_direction'][k['name']]} (Stage 1) -> {k['target_direction']} (passed)")
        L.append(f"   description: {k['description']}")
    its = (rec.get("loop") or {}).get("iterations", [])
    if its:
        L.append("\n-- Context summary (as rendered into the prompt, iteration 1)\n"
                 + ((its[0]["generation"] or {}).get("context_block_in_prompt") or "(empty)"))
    L.append("\n-- Iterations")
    for it in its:
        g = it["generation"] or {}
        ev = it["single_run_evaluation"] or {}
        kp = "; ".join(f"{c['kpi_name']}: {c['baseline_value']} -> {c['proposed_value']} ({c['percentage_change']}%, "
                       f"improved={c['improved']}, safeguard_violated={c.get('violated_safeguard')})"
                       for c in ev.get("kpi_comparisons") or [])
        L.append(f"   it {it['iteration']} | T {it['temperature']} | status {it['status']} | score {it['score']:.1f} | "
                 f"validator {it['patch_validator_passed']} | attempts {g.get('attempts')} | {kp}")
        for m in (it["patch"] or {}).get("modifications", []):
            L.append(f"       {m['parameter_type']:28s} {m['direction']:12s} {m['target_element']}: "
                     f"{m['baseline_value']} -> {m['proposed_value']}")
        if it["feedback_text"]:
            L.append("       feedback (first lines): " + " / ".join(it["feedback_text"].splitlines()[3:7]))
    L.append(f"\n-- Final = iteration {(rec.get('loop') or {}).get('best_iteration')}")
    for phase, ev in (rec.get("evaluation") or {}).items():
        L.append(f"\n-- {phase} (iteration {ev.get('iteration')}): valid={ev.get('valid')} violations={ev.get('violations')}")
        for k in ev.get("kpis", []):
            L.append(f"   {k['kpi']}: baseline {k['baseline_mean']} {k['baseline_ci']} | proposed {k['proposed_mean']} "
                     f"{k['proposed_ci']} | {k['pct_change']}% p={k['p_value']} | GLASS improved={k['glass_improved']} "
                     f"safeguard_violated={k['glass_violated_safeguard']} | scored_by={k['scored_by']} improved={k['improved']}")
        if ev.get("utilization_rule"):
            b = ev["utilization_rule"]
            L.append(f"   utilization rule: pool utilization baseline {b['baseline_mean_utilization']} -> proposed "
                     f"{b['proposed_mean_utilization']} verdict {b['verdict']} p={b['p_value']}")
        L.append(f"   midnight ends mapped per seed: {ev.get('midnight_ends_mapped_per_seed')}")
        L.append(f"   scores: {ev.get('scores')}")
        L.append(f"   distance to healthy: {ev.get('distance_to_healthy')}  improvement ratio: {ev.get('improvement_ratio')}")
    if rec.get("baseline_distance_to_healthy"):
        L.append(f"\n-- baseline distance to healthy: {rec['baseline_distance_to_healthy']}")
    return "\n".join(L)


def main() -> None:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    settings = config["settings"]
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reps", type=int, default=settings["repetitions"])
    ap.add_argument("--only", help="configuration id(s), comma-separated")
    ap.add_argument("--smoke", action="store_true", help="= --reps 1 --only LoanApp_G3")
    ap.add_argument("--out", type=Path, default=P.OLDER / "stage2_results")
    ap.add_argument("--workers", type=int, default=1, help="parallel worker processes")
    ap.add_argument("--variant", choices=sorted(VARIANTS), default="V_full", help="V_no_kb: knowledge base off")
    args = ap.parse_args()
    if args.smoke:
        args.reps, args.only = 1, "LoanApp_G3"
    only = args.only.split(",") if args.only else None
    selected = [c for c in config["configurations"] if only is None or c["config_id"] in only]
    if not selected or (only and len(selected) != len(only)):
        sys.exit(f"Unknown configuration id in: {args.only}")
    if git("status", "--porcelain", "--", "goal_to_parameters"):
        sys.exit("goal_to_parameters/ has local changes; Stage 2 must run on committed GLASS code")
    quiet()
    commit, config_sha = git("rev-parse", "HEAD"), s1.sha256(CONFIG_PATH.read_bytes())
    for cfg in selected:                              # assert the KPI objects before any LLM call
        load_inputs(cfg, config)
    app, _ = s1.load_app_stage1()
    healthy_sha = {}
    for process in sorted({c["process"] for c in selected}):
        s1.log(f"healthy logs for {process} ...")
        healthy_sha[process] = prepare_healthy(process, next(c["healthy_dir"] for c in selected
                                                             if c["process"] == process), config, args.out)
    shared = {}
    for cfg in selected:
        s1.log(f"baseline runs for {cfg['config_id']} ...")
        shared[cfg["config_id"]] = prepare_config(cfg, config, args.out, app)
    log_profile = {cid: sh.pop("log_profile") for cid, sh in shared.items()}
    healthy_scn = {p: healthy_scenario(p, next(c["healthy_dir"] for c in selected if c["process"] == p), config)
                   for p in healthy_sha}

    jobs = []
    for cfg in selected:
        for rep in range(1, args.reps + 1):
            path = args.out / "raw" / cfg["config_id"] / f"rep_{rep}.json"
            if (existing := s1.load_json(path)) and existing.get("status") in FINAL_STATUSES:
                s1.log(f"{cfg['config_id']} rep {rep} skipped (exists: {existing['status']})")
                continue
            jobs.append({"config": config, "cfg": cfg, "rep": rep, "out": str(args.out), "glass_commit": commit,
                         "variant": args.variant,
                         "config_sha": config_sha, "script_sha": s1.sha256(Path(__file__).read_bytes()),
                         "log_profile": log_profile, "shared": shared, "healthy_sha": healthy_sha,
                         "healthy_scenario": healthy_scn})
    s1.log(f"{len(jobs)} run(s) to do; variant {args.variant}; model {settings['model']}; workers {args.workers}; "
           f"GLASS {commit[:7]}")
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_rep, job) for job in jobs]
        for done, fut in enumerate(futures, 1):
            res = fut.result()
            if "fatal" in res:
                pool.shutdown(wait=True, cancel_futures=True)
                sys.exit(f"Fatal API error: {res['fatal']}\nAborted; runs written so far are kept, rerun to resume.")
            s1.log(f"[{done}/{len(jobs)}] {res['config_id']} rep {res['rep']}: {res['status']} ({res['elapsed_s']} s) "
                   f"{' '.join(res['error'])}")
    write_summaries(args.out, config)
    zip_logs(args.out, config)
    write_readme(args.out, args, config, started, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 commit, config_sha)
    if args.smoke:
        text = "\n\n".join(smoke_report(s1.load_json(args.out / "raw" / c["config_id"] / "rep_1.json")) for c in selected)
        (args.out / "smoke_report.txt").write_text(text, encoding="utf-8")
        s1.log(f"smoke report: {args.out / 'smoke_report.txt'}")


if __name__ == "__main__":
    main()

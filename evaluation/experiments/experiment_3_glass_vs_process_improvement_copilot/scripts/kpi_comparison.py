"""KPI computation and comparison helpers for compare_repeat_runs_with_glass.py.

KPI values per log (seeds 42-51) via GLASS's kpi_computation.py; GLASS reps that kept the baseline use baseline values.
"""

from __future__ import annotations

import glob
import importlib.util
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "loanapp_p2p"))
import lp_paths as P  # noqa: E402

COMP = Path(__file__).resolve().parents[1]
REPO = P.REPO
INPUTS = COMP / "inputs"
MINE = INPUTS / "manual_models"
V32_LOGS = INPUTS / "glass_event_logs" / "v3.2_rag_comparison"
RUNS = P.EXP2 / "results" / "stage2_results_v32_rag"
BASELINES = P.SHARED / "stage2" / "baselines"
KPI_MODULE = REPO / "goal_to_parameters" / "second_llm" / "kpi_computation.py"
OUT = COMP / "results"

ARMS = [
    dict(key="V_full", label="V_full v3.2", source="glass_v32_full", v32=True, suffix="_V_full_v3.2",
         sheet="Summary V_full v3.2", final_root=V32_LOGS / "V_full_system_simulated_eventlogs",
         records=RUNS / "V_full" / "raw"),
    dict(key="V_no_kb", label="V_no_kb v3.2", source="glass_v32_no_kb", v32=True, suffix="_V_no_kb_v3.2",
         sheet="Summary V_no_kb v3.2", final_root=V32_LOGS / "V_no_kb_simulated_eventlogs",
         records=RUNS / "V_no_kb" / "raw"),
]
SEEDS = list(range(42, 52))
SIM_START = "2023-02-09T10:00:00+02:00"
UTIL_LIMIT = 0.85
ALL_CONFIGS = [f"{p}_{g}" for p in ("LoanApp", "Procure2Pay")
               for g in ("G1", "G2", "G3", "G1G2", "G1G3", "G2G3", "G1G2G3")]

# Expected KPI per goal tag (shared/loanapp_p2p/setup/GLASS_expected_KPIs_and_evaluation_cases.xlsx)
WAIT_ACTIVITY = {"LoanApp": "Assess loan risk", "Procure2Pay": "Release Supplier's Invoice"}
UTIL_ROLE = {"LoanApp": "Loan Officer", "Procure2Pay": "Purchasing Agent"}
CYCLE = "Average Cycle Time"


def expected_kpis(config: str) -> list[tuple[str, str, str]]:
    """[(kpi label, kind, target)] for a config, from its goal tags."""
    process, tags = config.split("_", 1)[0], re.findall(r"G\d", config.split("_", 1)[1])
    out = []
    if "G1" in tags:
        out.append((CYCLE, "cycle", ""))
    if "G2" in tags:
        out.append((f"{WAIT_ACTIVITY[process]} Waiting Time", "wait", WAIT_ACTIVITY[process]))
    if "G3" in tags:
        out.append((f"{UTIL_ROLE[process]} Utilization", "util", UTIL_ROLE[process]))
    return out


def norm(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'").strip().lower()


def load_kpi_module():
    spec = importlib.util.spec_from_file_location("glass_kpi_computation", KPI_MODULE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["glass_kpi_computation"] = mod
    spec.loader.exec_module(mod)
    return mod


def compute_one(job: dict) -> list[dict]:
    km = load_kpi_module()
    df = pd.read_csv(job["log_path"])
    df = km._ensure_timestamps(df)
    rows = []
    base = {k: job[k] for k in ("source", "config", "variant", "model", "rep", "seed")}
    for label, kind, target in job["kpis"]:
        value, note = float("nan"), ""
        if kind == "cycle":
            k = km._compute_cycle_time(df)
            value = k.value if k else value
        elif kind == "wait":
            hit = [k for k in km._compute_activity_waiting_times(df) if norm(k.name) == norm(label)]
            if hit:
                value = hit[0].value
            else:
                note = "activity not found in log"
        elif kind == "util":
            k = km._compute_resource_utilization(df.copy(), job["params"], SIM_START)
            if k is None:
                note = "utilization not computed"
            else:
                per_role = k.details.get("per_role", {})
                roles = {norm(r): r for r in per_role}
                role = roles.get(norm(target))
                fb = k.details.get("fallback_span_based", [])
                if role is None:
                    note = f"role not found; roles={sorted(per_role)}"
                else:
                    members = [r for r, ro in k.details["resource_role"].items() if ro == role]
                    vals = [k.details["per_resource"][r] for r in members]
                    value = float(np.mean(vals))          # unrounded role mean
                    fb_role = [r for r in fb if r in members]
                    note = f"fallback_span_based={fb_role}" if fb_role else ""
                    if set(fb) - set(members):
                        note += f" other-role fallback={sorted(set(fb) - set(members))}"
        rows.append({**base, "kpi": label, "value": value, "note": note.strip()})
    return rows


def collect_jobs() -> tuple[list[dict], dict, dict]:
    """Logs to process, run info, and the reps per (arm, config) where GLASS kept the baseline (no final log)."""
    jobs: list[dict] = []
    info = {"mine_models": [], "skipped_configs": []}
    baseline_reps: dict[tuple[str, str], list[int]] = {}

    mine_models = []
    for d in sorted(glob.glob(str(MINE / "*" / "*"))):
        d = Path(d)
        if not d.is_dir() or d.parent.name.startswith("_") or d.parent.name == "comparison":
            continue
        js = list(d.glob("*.json"))
        if len(js) != 1:
            raise SystemExit(f"{d}: expected exactly one JSON, found {len(js)}")
        mine_models.append((d.parent.name, d.name, d.name.split("__")[0], json.loads(js[0].read_text(encoding="utf-8"))))
    info["mine_models"] = [(v, m, c) for v, m, c, _ in mine_models]
    configs = sorted({c for _, _, c, _ in mine_models}, key=ALL_CONFIGS.index)
    info["skipped_configs"] = [c for c in ALL_CONFIGS if c not in configs]

    for variant, model, config, params in mine_models:
        for s in SEEDS:
            p = MINE / variant / model / f"seed_{s}_log.csv"
            jobs.append(dict(source="mine", config=config, variant=variant, model=model, rep=0, seed=s,
                             log_path=str(p), params=params, kpis=expected_kpis(config)))

    for config in configs:
        bparams = json.loads((BASELINES / config / "sim_params.json").read_text(encoding="utf-8"))
        for s in SEEDS:
            jobs.append(dict(source="baseline", config=config, variant="", model="", rep=0, seed=s,
                             log_path=str(V32_LOGS / "baseline_logs" / config / f"seed_{s}.csv.gz"),
                             params=bparams, kpis=expected_kpis(config)))
        for arm in ARMS:
            baseline_reps[(arm["key"], config)] = []
            for rep in range(1, 6):
                rec = json.loads((arm["records"] / config / f"rep_{rep}.json").read_text(encoding="utf-8"))
                if arm["v32"]:
                    if rec["loop"].get("no_improving_scenario"):
                        baseline_reps[(arm["key"], config)].append(rep)
                        continue
                    final_iter = rec["evaluation"]["final"]["iteration"]
                else:
                    final_iter = rec["loop"]["best_iteration"]
                gparams = rec["loop"]["iterations"][final_iter - 1]["simulated_scenario_prosimos"]
                for s in SEEDS:
                    p = arm["final_root"] / config / f"rep_{rep}" / f"iter{final_iter}_seed{s}.csv.gz"
                    if not p.exists():
                        raise SystemExit(f"missing GLASS log {p} (final iteration in record = {final_iter})")
                    jobs.append(dict(source=arm["source"], config=config, variant="", model="", rep=rep, seed=s,
                                     log_path=str(p), params=gparams, kpis=expected_kpis(config)))
    return jobs, info, baseline_reps


def compare(a: np.ndarray, b: np.ndarray) -> dict:
    """a - b, paired by seed."""
    d = a - b
    n = len(d)
    mean_d = float(d.mean())
    sd = float(d.std(ddof=1)) if n > 1 else float("nan")
    se = sd / math.sqrt(n) if n > 1 else float("nan")
    tcrit = float(stats.t.ppf(0.975, n - 1)) if n > 1 else float("nan")
    lo, hi = mean_d - tcrit * se, mean_d + tcrit * se
    if n > 1 and sd > 0:
        p_paired = float(stats.ttest_rel(a, b).pvalue)
    else:
        p_paired = 0.0 if (n > 1 and mean_d != 0) else 1.0
    va, vb = a.std(ddof=1), b.std(ddof=1)
    p_welch = float(stats.ttest_ind(a, b, equal_var=False).pvalue) if (va > 0 or vb > 0) else (0.0 if mean_d != 0 else 1.0)
    return dict(a_mean=float(a.mean()), b_mean=float(b.mean()), mean_diff=mean_d, ci_low=float(lo), ci_high=float(hi),
                p_paired=p_paired, p_welch=p_welch)


def verdict(r: dict, a_name: str, b_name: str) -> str:
    if r["ci_low"] > 0:
        return f"{b_name} lower"
    if r["ci_high"] < 0:
        return f"{a_name} lower"
    return "no significant difference"


def pct_improvement(x: float, base: float) -> float:
    return float("nan") if base == 0 or math.isnan(base) else (base - x) / base * 100.0


def utilization_counts(base: float, manual: float, reps: list[float]) -> tuple[int, int]:
    """(GLASS better, manual better) for utilization, from the means.

    Both below the baseline: GLASS is better if the rep is above the manual value, else manual is better.
    Only one below the baseline: that one is better. Neither: tie.
    """
    better = worse = 0
    m_ok = manual < base
    for g in reps:
        g_ok = g < base
        if g_ok and m_ok:
            better, worse = (better + 1, worse) if g > manual else (better, worse + 1)
        elif g_ok:
            better += 1
        elif m_ok:
            worse += 1
    return better, worse

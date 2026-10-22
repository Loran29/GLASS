"""Compare my Prosimos results (my_prosimos_results_v2/) with the GLASS Stage 2 v3.1 final logs and the baseline.

Inputs
  my_prosimos_results_v2/{extended_hours,extra_resources}/<model>/   JSON, BPMN, seed_42..51_log.csv
  stage2_v3_1_final_logs/final_logs/<config>/rep_<k>/iter<n>_seed<s>.csv.gz   GLASS, best iteration of each rep
  stage2_v3_1_final_logs/baseline_logs/<config>/seed_<s>.csv.gz               unchanged baseline
  loanapp_p2p/results/stage2_results_v3_1/raw/<config>/rep_<k>.json           GLASS records (Prosimos parameters)
  loanapp_p2p/stage2/baselines/<config>/sim_params.json                       baseline Prosimos parameters
  EXPECTED_KPIS.md                                                            expected KPIs per goal (encoded below)

KPIs are computed with GLASS's own code (goal_to_parameters/second_llm/kpi_computation.py), the same for all sources:
  G1  Average Cycle Time                   first event start -> last event end per case, mean in hours
  G2  <activity> Waiting Time              gap between the previous event's end and this event's start in the
                                           same case (positive gaps only), mean in hours
        LoanApp: Assess loan risk, Procure2Pay: Release Supplier's Invoice
  G3  <role> Utilization                   busy time within working hours / available working time, per resource
                                           calendar, mean over the role's resources
        LoanApp: Loan Officer, Procure2Pay: Purchasing Agent

Usage:  python -I comparison/scripts/compar_results_with_glass.py [--workers N] [--reuse]
        --reuse reads comparison/kpi_values_per_log.csv instead of recomputing the KPIs from the logs (about 20 minutes)
Output (in evaluation/comparison/): comparison_generated.xlsx (Summary sheet only), comparison_summary.csv,
        comparison_all.csv, kpi_values_per_log.csv, comparison_notes.md
        comparison.xlsx is the hand-curated workbook and is never written by this script.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import math
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

EVAL = Path(__file__).resolve().parents[2]      # .../evaluation (this file: evaluation/comparison/scripts/)
REPO = EVAL.parent
MINE = EVAL / "my_prosimos_results_v2"
GLASS_LOGS = EVAL / "stage2_v3_1_final_logs"
RECORDS = EVAL / "loanapp_p2p" / "results" / "stage2_results_v3_1" / "raw"
BASELINES = EVAL / "loanapp_p2p" / "stage2" / "baselines"
KPI_MODULE = REPO / "goal_to_parameters" / "second_llm" / "kpi_computation.py"
OUT = EVAL / "comparison"

SEEDS = list(range(42, 52))
SIM_START = "2023-02-09T10:00:00+02:00"
UTIL_LIMIT = 0.85
ALL_CONFIGS = [f"{p}_{g}" for p in ("LoanApp", "Procure2Pay")
               for g in ("G1", "G2", "G3", "G1G2", "G1G3", "G2G3", "G1G2G3")]

# Expected KPI per goal tag (EXPECTED_KPIS.md)
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


# ---------------------------------------------------------------------------
# Per-log KPI computation (runs in worker processes)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Job collection
# ---------------------------------------------------------------------------

def collect_jobs() -> tuple[list[dict], dict]:
    jobs: list[dict] = []
    info = {"mine_models": [], "skipped_configs": []}

    # my models
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
                             log_path=str(GLASS_LOGS / "baseline_logs" / config / f"seed_{s}.csv.gz"),
                             params=bparams, kpis=expected_kpis(config)))
        for rep in range(1, 6):
            rec = json.loads((RECORDS / config / f"rep_{rep}.json").read_text(encoding="utf-8"))
            best = rec["loop"]["best_iteration"]
            gparams = rec["loop"]["iterations"][best - 1]["simulated_scenario_prosimos"]
            for s in SEEDS:
                p = GLASS_LOGS / "final_logs" / config / f"rep_{rep}" / f"iter{best}_seed{s}.csv.gz"
                if not p.exists():
                    raise SystemExit(f"missing GLASS log {p} (best_iteration in record = {best})")
                jobs.append(dict(source="glass", config=config, variant="", model="", rep=rep, seed=s,
                                 log_path=str(p), params=gparams, kpis=expected_kpis(config)))
    return jobs, info


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--reuse", action="store_true",
                    help="reuse comparison/kpi_values_per_log.csv instead of recomputing the KPIs from the logs")
    args = ap.parse_args()

    jobs, info = collect_jobs()
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / "kpi_values_per_log.csv"
    if args.reuse and cache.exists():
        vals = pd.read_csv(cache).fillna({"variant": "", "model": "", "note": ""})
        print(f"reusing {len(vals)} KPI values from {cache.name}", flush=True)
    else:
        print(f"{len(jobs)} logs to process with {args.workers} workers", flush=True)
        results: list[dict] = []
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for i, rows in enumerate(ex.map(compute_one, jobs, chunksize=4), 1):
                results.extend(rows)
                if i % 100 == 0:
                    print(f"  {i}/{len(jobs)}", flush=True)
        vals = pd.DataFrame(results)
        vals.to_csv(cache, index=False)

    problems = vals[(vals["value"].isna()) | (vals["note"] != "")]
    # fallback is only a problem for the resources we use; the note already limits it to the role
    bad = problems[problems["note"].str.contains("fallback_span_based|not found|not computed")
                   | problems["value"].isna()]

    def series(source, config, kpi, variant="", model="", rep=0):
        q = vals[(vals.source == source) & (vals.config == config) & (vals.kpi == kpi) & (vals.rep == rep)
                 & (vals.variant == variant) & (vals.model == model)].sort_values("seed")
        return q["value"].to_numpy(dtype=float)

    all_rows, summary_rows = [], []
    configs = sorted(vals.config.unique(), key=ALL_CONFIGS.index)

    def add(config, variant, model, kpi, comparison, a_name, b_name, a, b, extra=None):
        r = compare(a, b)
        row = dict(config=config, variant=variant, model=model, kpi=kpi, comparison=comparison,
                   a=a_name, b=b_name, **r, verdict=verdict(r, a_name, b_name))
        if extra:
            row.update(extra)
        all_rows.append(row)
        return row

    for config in configs:
        process = config.split("_")[0]
        my_models = [(v, m) for v, m, c in info["mine_models"] if c == config]
        for label, kind, _ in expected_kpis(config):
            base = series("baseline", config, label)
            rep_series = {k: series("glass", config, label, rep=k) for k in range(1, 6)}
            glass_mean = np.mean([rep_series[k] for k in range(1, 6)], axis=0)
            gl = {**{f"GLASS rep{k}": rep_series[k] for k in rep_series}, "GLASS mean of 5 reps": glass_mean}
            # GLASS vs baseline (once per config)
            for name, arr in gl.items():
                add(config, "", "", label, "GLASS vs baseline", name, "baseline", arr, base,
                    dict(improvement_pct_a=pct_improvement(arr.mean(), base.mean())))
            for variant, model in my_models:
                mine = series("mine", config, label, variant, model)
                add(config, variant, model, label, "mine vs baseline", "mine", "baseline", mine, base,
                    dict(improvement_pct_a=pct_improvement(mine.mean(), base.mean())))
                for name, arr in gl.items():
                    add(config, variant, model, label, "mine vs GLASS", "mine", name, mine, arr,
                        dict(improvement_pct_a=pct_improvement(mine.mean(), base.mean()),
                             improvement_pct_b=pct_improvement(arr.mean(), base.mean())))
                # summary row: mine vs the 5-rep GLASS mean, with context
                main_cmp = next(r for r in all_rows if r["config"] == config and r["model"] == model
                                and r["kpi"] == label and r["comparison"] == "mine vs GLASS"
                                and r["b"] == "GLASS mean of 5 reps")
                rep_means = [rep_series[k].mean() for k in range(1, 6)]
                srow = dict(config=config, variant=variant, model=model, kpi=label,
                            baseline_mean=base.mean(), mine_mean=mine.mean(), glass_mean_5reps=glass_mean.mean(),
                            glass_rep_min=min(rep_means), glass_rep_max=max(rep_means),
                            mine_vs_baseline_pct=pct_improvement(mine.mean(), base.mean()),
                            glass_vs_baseline_pct=pct_improvement(glass_mean.mean(), base.mean()),
                            diff_mine_minus_glass=main_cmp["mean_diff"], ci_low=main_cmp["ci_low"],
                            ci_high=main_cmp["ci_high"], p_paired=main_cmp["p_paired"], p_welch=main_cmp["p_welch"],
                            verdict=main_cmp["verdict"])
                if kind == "util":
                    srow.update(mine_target_met=bool(mine.mean() < UTIL_LIMIT and mine.mean() < base.mean()),
                                glass_target_met=bool(glass_mean.mean() < UTIL_LIMIT and glass_mean.mean() < base.mean()),
                                baseline_target_met=bool(base.mean() < UTIL_LIMIT))
                summary_rows.append(srow)

    summary = pd.DataFrame(summary_rows)
    allc = pd.DataFrame(all_rows)

    # --- cross-check of my KPI code against the GLASS records -----------------
    checks = []
    for config in configs:
        rec = json.loads((RECORDS / config / "rep_1.json").read_text(encoding="utf-8"))
        final = rec["evaluation"]["final"]
        mine_series = {lab: series("baseline", config, lab) for lab, _, _ in expected_kpis(config)}
        for kp in final.get("kpis", []):
            bv = np.array(kp.get("baseline_values") or [], dtype=float)
            if len(bv) != 10:
                continue
            match = [lab for lab, s in mine_series.items() if np.allclose(s, bv, rtol=0.002, atol=0.011)]
            checks.append(dict(config=config, glass_record_kpi=kp["kpi"], category=kp["category"],
                               matches_my_series=", ".join(match) if match else "no match",
                               max_abs_diff_to_closest=float(min(np.abs(s - bv).max() for s in mine_series.values()))))
        ur = final.get("utilization_rule") or {}
        per_seed = ur.get("baseline_per_seed")
        if per_seed and any(k == "util" for _, k, _ in expected_kpis(config)):
            lab = next(l for l, k, _ in expected_kpis(config) if k == "util")
            s = mine_series[lab]
            checks.append(dict(config=config, glass_record_kpi="utilization_rule.baseline_per_seed", category="utilization",
                               matches_my_series=lab if np.allclose(s, per_seed, atol=0.002) else "no match",
                               max_abs_diff_to_closest=float(np.abs(s - np.array(per_seed)).max())))
    checks = pd.DataFrame(checks)

    # --- write ----------------------------------------------------------------
    summary.to_csv(OUT / "comparison_summary.csv", index=False)
    allc.to_csv(OUT / "comparison_all.csv", index=False)
    notes = build_notes(info, bad, checks, summary)
    (OUT / "comparison_notes.md").write_text(notes, encoding="utf-8")
    # The workbook holds only the summary table; everything else is in the CSV files and the notes.
    excel_cols = ["config", "variant", "model", "kpi_expected", "baseline_mean", "mine_mean", "glass_mean_5reps",
                  "glass_rep_min", "glass_rep_max", "mine_vs_baseline_pct", "glass_vs_baseline_pct",
                  "diff_mine_minus_glass", "ci_low", "ci_high", "verdict"]
    with pd.ExcelWriter(OUT / "comparison_generated.xlsx", engine="openpyxl") as xw:
        summary.rename(columns={"kpi": "kpi_expected"})[excel_cols].to_excel(xw, sheet_name="Summary", index=False)
    print("written to", OUT)
    print(f"problems in KPI computation: {len(bad)}; cross-check rows without match: "
          f"{int((checks['matches_my_series'] == 'no match').sum()) if len(checks) else 0}")


def build_notes(info, bad, checks, summary) -> str:
    L = ["# Comparison notes", ""]
    L += ["## What the table shows",
          "`comparison.xlsx` (sheet `Summary`) has one row per config x variant x expected KPI. "
          "Cycle time and waiting time are in hours, utilization is a ratio. Lower is better for all three.", "",
          "| Column | Meaning |", "|---|---|",
          "| `config` | GLASS configuration: process and goals (G1 end-to-end time, G2 local waiting, G3 resource workload) |",
          "| `variant` | Which of my model sets: `extended_hours` or `extra_resources` |",
          "| `model` | Folder name of my model (Procure2Pay_G1G2 has `hours_1` and `hours_2`) |",
          "| `kpi_expected` | Expected KPI of the config (`EXPECTED_KPIS.md`) |",
          "| `baseline_mean` | Unchanged baseline, mean over the 10 seeds (42-51) |",
          "| `mine_mean` | My model, mean over the 10 seeds |",
          "| `glass_mean_5reps` | GLASS Stage 2 v3.1: mean over the 5 reps (each rep = its best iteration) and the 10 seeds |",
          "| `glass_rep_min`, `glass_rep_max` | Lowest and highest of the 5 reps' own 10-seed means |",
          "| `mine_vs_baseline_pct` | (baseline - mine) / baseline x 100. Positive = lower than the baseline |",
          "| `glass_vs_baseline_pct` | The same for `glass_mean_5reps` |",
          "| `diff_mine_minus_glass` | Mean over the seeds of (mine - GLASS 5-rep mean). Negative = mine is lower |",
          "| `ci_low`, `ci_high` | 95% confidence interval of that difference (t distribution, n = 10 seeds) |",
          "| `verdict` | `mine lower` if the interval is below zero, `GLASS mean of 5 reps lower` if above zero, "
          "`no significant difference` if it contains zero |", "",
          "More detail is in the CSV files: `comparison_summary.csv` (also p-values paired and Welch, and the utilization "
          "target flags `baseline/mine/glass_target_met`), `comparison_all.csv` (every comparison, each GLASS rep separately, "
          "mine vs baseline, GLASS vs baseline) and `kpi_values_per_log.csv` (the value of every KPI in every log).", ""]
    L += ["## Scope",
          f"- {len(info['mine_models'])} models of mine in {len({c for _, _, c in info['mine_models']})} configs "
          "(a config in both `extended_hours` and `extra_resources` gets two rows).",
          "- Each is compared with the same config in the GLASS Stage 2 v3.1 final logs "
          "(`stage2_v3_1_final_logs`, 5 reps) and with the unchanged baseline, on seeds 42-51."]
    for c in info["skipped_configs"]:
        L.append(f"- **{c} is not compared**: there is no model of it in `my_prosimos_results_v2/`.")
    L += ["", "## KPI definitions (GLASS `kpi_computation.py`, the same code for all sources)",
          "- Average Cycle Time: first event start to last event end per case, mean.",
          "- `<activity>` Waiting Time: gap between the previous event's end and this event's start in the same case "
          "(positive gaps only), mean. Activity: Assess loan risk (LoanApp), Release Supplier's Invoice (Procure2Pay).",
          "- `<role>` Utilization: busy time clipped to each resource's working calendar / available working time, "
          "from the simulation start to the last event of the log; mean over the role's resources. "
          "Role: Loan Officer (LoanApp), Purchasing Agent (Procure2Pay). GLASS target rule: below "
          f"{UTIL_LIMIT} and below the baseline.", "",
          "## How the KPIs are computed, step by step",
          "Every KPI is computed per log (one log = one model or one rep on one seed), which gives 10 values per source "
          "(seeds 42-51). The table reports the mean of these 10 values.", "",
          "**Utilization of a role** (`_compute_resource_utilization` in `kpi_computation.py`):",
          "1. Period: from the simulation start (2023-02-09 10:00) to the last `end_time` in the log. Times are taken as local "
          "wall-clock time (time zone removed), because the calendars are written that way. Each log uses its own period.",
          "2. Available time of a person: the person's calendar (e.g. Mon-Fri 09:00-17:00, or 09:00-19:00 for the roles of the "
          "`extended_hours` models) cut to the period; the lengths of all working windows inside the period are added up. "
          "The first day starts at 10:00 because the period does.",
          "3. Busy time of a person: every activity the person performed, cut to the working windows. The part of an "
          "activity outside working hours is not counted.",
          "4. Utilization of a person = busy time / available time.",
          "5. Utilization of the role = mean over the people of that role (Loan Officer for LoanApp, Purchasing Agent for "
          "Procure2Pay). The person-to-role link comes from `resource_profiles` of each log's own Prosimos JSON.",
          "6. Repeated for seeds 42-51.", "",
          "Example (made-up numbers): two Loan Officers. A: busy 700 h, available 800 h = 0.875. B: busy 500 h, "
          "available 800 h = 0.625. Role utilization = (0.875 + 0.625) / 2 = 0.75.", "",
          "- `extended_hours`: the affected role has more available time (10 h a day instead of 8) and the window for busy "
          "time is longer too, so utilization can drop just from the longer day.",
          "- `extra_resources`: the same work is shared by more people, so the mean per person drops.",
          "- A person without a calendar would fall back to busy time / log length. This never happened "
          "(see 'KPI computation problems').",
          "- The period ends with the log, so a model that finishes earlier is measured over a shorter period. "
          "A very overloaded baseline (e.g. 0.997) means the person is almost always busy until the end of the log.",
          "- Target rule used by GLASS: role utilization below 0.85 and below the baseline "
          "(`mine_target_met`, `glass_target_met`, `baseline_target_met` in `comparison_summary.csv`). The 0.85 limit comes from "
          "the GLASS Stage 2 configuration; its reason is not documented in the repository.", "",
          "**Average Cycle Time:** per case, last `end_time` minus first `start_time` in hours; mean over all 1000 cases "
          "of the log.", "",
          "**Waiting time of an activity:** in each case the events are sorted by `start_time`. For every occurrence of the "
          "activity, the wait is its `start_time` minus the previous event's `end_time`. Only positive gaps count, and "
          "the value is the mean over all such occurrences in the log (in hours).", "",
          "**Statistics of the table:** `diff_mine_minus_glass` is the mean of the 10 per-seed differences "
          "(my value for seed s minus the GLASS 5-rep mean for seed s, where that mean is taken over the 5 reps for the same "
          "seed). `ci_low`/`ci_high` are mean difference +- t(0.975, 9) x standard error. "
          "`mine_vs_baseline_pct` and `glass_vs_baseline_pct` use the baseline mean of the same config.", "",
          "## Reading the table",
          "- For utilization, a lower value can also mean idle capacity; read it together with the 0.85 target rule.",
          "- Where the baseline is overloaded (LoanApp G1G3 and G1G2G3: cycle time about 2250 hours) the queue grows during "
          "the simulation, so times and percentages there are dominated by the queue.",
          "- Seed pairing is weak between different models (their random streams drift after the first change), "
          "so the CI is computed on the per-seed differences but should be read with the Welch p-value in the CSV.", ""]
    L += ["## KPI computation problems",
          f"- {len(bad)} log/KPI values with a problem (NaN, utilization falling back to the span-based formula, "
          "role or activity not found)."]
    for _, r in bad.head(20).iterrows():
        L.append(f"  - {r['source']} {r['config']} {r['model']} rep{r['rep']} seed{r['seed']} {r['kpi']}: {r['note'] or 'NaN'}")
    L += ["", "## Cross-check against the GLASS records (baseline per-seed values)"]
    if len(checks):
        nm = checks[checks["matches_my_series"] == "no match"]
        L.append(f"- {len(checks) - len(nm)} of {len(checks)} record series match my baseline series.")
        for _, r in nm.iterrows():
            L.append(f"  - {r['config']}: {r['glass_record_kpi']} ({r['category']}) has no match "
                     f"(closest max abs diff {r['max_abs_diff_to_closest']:.3f}).")
            if r["config"] == "Procure2Pay_G1G2G3":
                L.append("    Reason: in this config GLASS's Stage 2 optimised the case-level KPI "
                         "'Average Wait Time Before Invoice Release' (measurable_as case_wait_time_hours, the summed "
                         "waiting per case), not the expected activity waiting time 'Release Supplier's Invoice Waiting Time'. "
                         "This comparison uses the expected KPI from EXPECTED_KPIS.md.")
    else:
        L.append("- no comparable series found.")
    return chr(10).join(L) + chr(10)


if __name__ == "__main__":
    main()

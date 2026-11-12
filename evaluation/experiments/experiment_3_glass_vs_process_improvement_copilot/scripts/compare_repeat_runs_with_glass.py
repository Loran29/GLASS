"""Compare the manual models of Copilot runs 1-5 with GLASS v3.2 (V_full, V_no_kb).

Per model and expected KPI: per-seed difference manual - mean of GLASS's 5 reps, 95% CI verdict
(utilization: means rule). GLASS/baseline values come from results/kpi_values_per_log.csv; manual values
are cached in results/repeat_runs/kpi_values_manual_runs.csv. Writes results/repeat_runs/comparison_repeat_runs.xlsx.

Usage: python -I scripts/compare_repeat_runs_with_glass.py [--workers N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kpi_comparison as C  # noqa: E402

RR = C.INPUTS / "repeat_runs"
OUT = C.OUT / "repeat_runs"
RUNS = [1, 2, 3, 4, 5]
LEVER = {"extended_hours": "More working hours", "extra_resources": "More people"}
GREEN, YELLOW, RED, GREY, BLUE = "C6EFCE", "FFEB9C", "FFC7CE", "EDEDED", "305496"
V_MANUAL, V_GLASS, V_SAME, V_NONE = "manual lower", "GLASS lower", "no significant difference", "no applicable idea"
# utilization rows: means only, no CI (glass = mean of GLASS's 5 reps)
U_MANUAL, U_GLASS, U_SAME = "manual better", "GLASS better", "no difference"
MAN, GLA, SAME = {V_MANUAL, U_MANUAL}, {V_GLASS, U_GLASS}, {V_SAME, U_SAME}
COLOR = {V_MANUAL: GREEN, V_SAME: YELLOW, V_GLASS: RED, V_NONE: GREY, U_MANUAL: GREEN, U_GLASS: RED, U_SAME: YELLOW}
UTIL_NOTE = ("Utilization: GLASS is better when manual < rep < baseline, or when only the rep is below the baseline; "
               "manual is better when the rep is at or below the manual mean, or when only the manual mean is below the baseline.")


def util_rule(base: float, manual: float, glass: float) -> str:
    """glass_ok = glass < baseline, manual_ok = manual < baseline (strict); see UTIL_NOTE."""
    glass_ok, manual_ok = glass < base, manual < base
    if glass_ok and manual_ok:
        return U_GLASS if glass > manual else U_MANUAL
    if glass_ok:
        return U_GLASS
    if manual_ok:
        return U_MANUAL
    return U_SAME


def short(v: str) -> str:
    return {"mine lower": V_MANUAL, "GLASS mean of 5 reps lower": V_GLASS}.get(v, v)


def manual_models() -> list[dict]:
    out = []
    for n in RUNS:
        for d in sorted((RR / f"run_{n}" / "prosimos_models").glob("*/*")):
            if not d.is_dir() or d.parent.name == "_stats":
                continue
            js = d / f"{d.name}.json"
            out.append(dict(run=n, variant=d.parent.name, model=d.name, config=d.name.split("__")[0], dir=d,
                            params=json.loads(js.read_text(encoding="utf-8")),
                            sha=hashlib.sha256(js.read_bytes() + (d / f"{d.name}.bpmn").read_bytes()).hexdigest()))
    return out


def manual_kpis(models: list[dict], workers: int) -> pd.DataFrame:
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / "kpi_values_manual_runs.csv"
    cached = pd.read_csv(cache).fillna({"note": ""}) if cache.exists() else pd.DataFrame()
    done = set(zip(cached.run, cached.variant, cached.model, cached.seed)) if len(cached) else set()
    jobs = [dict(source=f"run_{m['run']}", config=m["config"], variant=m["variant"], model=m["model"], rep=m["run"],
                 seed=s, log_path=str(m["dir"] / f"seed_{s}_log.csv"), params=m["params"],
                 kpis=C.expected_kpis(m["config"]))
            for m in models for s in C.SEEDS if (m["run"], m["variant"], m["model"], s) not in done]
    print(f"manual logs: {len(models) * len(C.SEEDS)}, to compute: {len(jobs)} ({workers} workers)", flush=True)
    rows = []
    if jobs:
        with ProcessPoolExecutor(workers) as ex:
            for i, r in enumerate(ex.map(C.compute_one, jobs, chunksize=4), 1):
                rows.extend(r)
                if i % 100 == 0:
                    print(f"  {i}/{len(jobs)}", flush=True)
    new = pd.DataFrame(rows)
    if len(new):
        new = new.rename(columns={"rep": "run"}).drop(columns="source")
    vals = pd.concat([cached, new], ignore_index=True).fillna({"note": ""})
    vals.to_csv(cache, index=False)
    return vals


def glass_side() -> tuple[pd.DataFrame, dict]:
    """GLASS + baseline KPI values (cached in results/kpi_values_per_log.csv; missing ones are computed)."""
    jobs, _, baseline_reps = C.collect_jobs()
    cache = C.OUT / "kpi_values_per_log.csv"
    vals = pd.read_csv(cache).fillna({"variant": "", "model": "", "note": ""})
    extra = []
    for config in C.ALL_CONFIGS:  # configs without a run-1 manual model (Procure2Pay_G1)
        if ((vals.source == "baseline") & (vals.config == config)).any():
            continue
        bparams = json.loads((C.BASELINES / config / "sim_params.json").read_text(encoding="utf-8"))
        for s in C.SEEDS:
            extra.append(dict(source="baseline", config=config, variant="", model="", rep=0, seed=s,
                              log_path=str(C.V32_LOGS / "baseline_logs" / config / f"seed_{s}.csv.gz"),
                              params=bparams, kpis=C.expected_kpis(config)))
        for arm in C.ARMS:
            baseline_reps[(arm["key"], config)] = []
            for rep in range(1, 6):
                rec = json.loads((arm["records"] / config / f"rep_{rep}.json").read_text(encoding="utf-8"))
                if rec["loop"].get("no_improving_scenario"):
                    baseline_reps[(arm["key"], config)].append(rep)
                    continue
                it = rec["evaluation"]["final"]["iteration"]
                gparams = rec["loop"]["iterations"][it - 1]["simulated_scenario_prosimos"]
                for s in C.SEEDS:
                    extra.append(dict(source=arm["source"], config=config, variant="", model="", rep=rep, seed=s,
                                      log_path=str(arm["final_root"] / config / f"rep_{rep}" / f"iter{it}_seed{s}.csv.gz"),
                                      params=gparams, kpis=C.expected_kpis(config)))
    extra_cache = OUT / "kpi_values_glass_extra_configs.csv"
    if extra and extra_cache.exists():  # computed before: reuse (same logs)
        add = pd.read_csv(extra_cache).fillna({"variant": "", "model": "", "note": ""})
        if len(add) == sum(len(j["kpis"]) for j in extra):
            print(f"GLASS/baseline logs not in the cache: {len(extra)} (cached)", flush=True)
            return pd.concat([vals, add], ignore_index=True).fillna({"variant": "", "model": "", "note": ""}), baseline_reps
    if extra:
        print(f"GLASS/baseline logs not in the cache: {len(extra)} (computing)", flush=True)
        rows = []
        with ProcessPoolExecutor(8) as ex:
            for r in ex.map(C.compute_one, extra, chunksize=4):
                rows.extend(r)
        add = pd.DataFrame(rows)
        add.to_csv(OUT / "kpi_values_glass_extra_configs.csv", index=False)
        vals = pd.concat([vals, add], ignore_index=True).fillna({"variant": "", "model": "", "note": ""})
    return vals, baseline_reps


def model_rows(models, mvals, gvals, baseline_reps) -> pd.DataFrame:
    """One row per arm x run x model x expected KPI: summary columns and rep counts."""
    def gs(source, config, kpi, rep=0):
        q = gvals[(gvals.source == source) & (gvals.config == config) & (gvals.kpi == kpi) & (gvals.rep == rep)
                  & (gvals.variant == "") & (gvals.model == "")].sort_values("seed")
        a = q.value.to_numpy(dtype=float)
        assert len(a) == 10, (source, config, kpi, rep, len(a))
        return a

    rows = []
    for arm in C.ARMS:
        for m in models:
            kept = baseline_reps[(arm["key"], m["config"])]
            for label, kind, _ in C.expected_kpis(m["config"]):
                base = gs("baseline", m["config"], label)
                reps = [base if k in kept else gs(arm["source"], m["config"], label, k) for k in range(1, 6)]
                glass = np.mean(reps, axis=0)
                q = mvals[(mvals.run == m["run"]) & (mvals.variant == m["variant"]) & (mvals.model == m["model"])
                          & (mvals.kpi == label)].sort_values("seed")
                mine = q.value.to_numpy(dtype=float)
                assert len(mine) == 10, (m["run"], m["model"], label)
                r = C.compare(mine, glass)
                if kind == "util":
                    g_better, m_better = C.utilization_counts(base.mean(), mine.mean(), [x.mean() for x in reps])
                else:
                    vs = [C.verdict(C.compare(mine, x), "mine", "GLASS") for x in reps]
                    g_better, m_better = sum(v.startswith("GLASS") for v in vs), sum(v.startswith("mine") for v in vs)
                rows.append(dict(glass_arm=arm["label"], run=m["run"], config=m["config"], variant=m["variant"],
                                 model=m["model"], kpi_expected=label, baseline_mean=base.mean(),
                                 manual_mean=mine.mean(), glass_mean_5reps=glass.mean(),
                                 manual_vs_baseline_pct=C.pct_improvement(mine.mean(), base.mean()),
                                 glass_vs_baseline_pct=C.pct_improvement(glass.mean(), base.mean()),
                                 ci_low=None if kind == "util" else r["ci_low"],
                                 ci_high=None if kind == "util" else r["ci_high"],
                                 verdict=util_rule(base.mean(), mine.mean(), glass.mean()) if kind == "util"
                                 else short(C.verdict(r, "mine", "GLASS mean of 5 reps")),
                                 reps_glass_better=g_better, reps_no_difference=5 - g_better - m_better,
                                 reps_manual_better=m_better, glass_kept_baseline_reps=", ".join(map(str, kept)),
                                 **{f"glass_rep{k}_mean": reps[k - 1].mean() for k in range(1, 6)}))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- workbook
HEAD_FILL, ORANGE = "305496", "FCE4D6"
SHEET_COLS = [("config", "config", 18), ("variant", "variant", 15), ("model", "model", 28),
              ("kpi_expected", "kpi_expected", 30), ("baseline_mean", "baseline_mean", 13),
              ("manual_mean", "manual_mean", 13), ("glass_mean_5reps", "glass_mean_5reps", 15),
              ("manual_vs_baseline_pct", "manual_vs_baseline_%", 14), ("glass_vs_baseline_pct", "glass_vs_baseline_%", 14),
              ("ci_low", "ci_low", 11), ("ci_high", "ci_high", 11), ("verdict", "verdict", 24),
              ("reps_glass_better", "reps: GLASS better", 11), ("reps_no_difference", "reps: no difference", 11),
              ("reps_manual_better", "reps: manual better", 11)]


def header(ws, row, names):
    for i, n in enumerate(names, 1):
        c = ws.cell(row, i, n)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=HEAD_FILL)
        c.alignment = Alignment(wrap_text=True, vertical="center")


def paint(cell, color):
    cell.fill = PatternFill("solid", fgColor=color)


def counts(d):
    return [int(d.verdict.isin(MAN).sum()), int(d.verdict.isin(GLA).sum()), int(d.verdict.isin(SAME).sum()),
            int(d.reps_glass_better.sum()), int(d.reps_no_difference.sum()), int(d.reps_manual_better.sum())]


def write_workbook(df: pd.DataFrame, path: Path):
    wb = Workbook()
    # ---------------- Overview
    ws = wb.active
    ws.title = "Overview"
    ws["A1"] = "Copilot runs 1-5 vs GLASS v3.2"
    ws["A1"].font = Font(bold=True, size=13)
    notes = [
        "Each run: the Copilot's directly applicable ideas applied to the BPS models, simulated with Prosimos "
        "(1000 cases, seeds 42-51).",
        "Verdict, cycle and waiting time: 95% confidence interval of the per-seed difference manual - mean of GLASS's "
        "5 reps. Verdict, utilization: rule below on the means (manual vs mean of GLASS's 5 reps), no confidence "
        "interval. Reps: each GLASS rep against the manual model, same rules.",
        "No significant difference: the 95% confidence interval of the per-seed difference (manual - mean of GLASS's 5 "
        "reps, 10 seeds) contains 0, i.e. the difference can be zero. 'manual lower' = whole interval below 0, "
        "'GLASS lower' = whole interval above 0.",
        UTIL_NOTE + " Neither below the baseline: no difference. A GLASS rep that kept the baseline counts as "
        "not below the baseline.",
        "The runs have different numbers of models, so the totals are not on the same base; table 2 compares per "
        "configuration and KPI.",
    ]
    for i, t in enumerate(notes, 2):
        ws.cell(i, 1, t)
    r = len(notes) + 3
    ws.cell(r, 1, "1. Totals per run").font = Font(bold=True)
    r += 1
    header(ws, r, ["GLASS arm", "Run", "Models", "Rows (model x KPI)", "manual lower / better", "GLASS lower / better",
                   "no (significant) difference", "Reps: GLASS better", "Reps: no difference", "Reps: manual better"])
    for arm in C.ARMS:
        for n in RUNS:
            d = df[(df.glass_arm == arm["label"]) & (df.run == n)]
            ws.append([arm["label"], f"Run {n}", d.model.nunique(), len(d)] + counts(d))
            rr = ws.max_row
            for i, col in ((5, GREEN), (6, RED), (7, YELLOW), (8, RED), (9, YELLOW), (10, GREEN)):
                paint(ws.cell(rr, i), col)
    r = ws.max_row + 2
    ws.cell(r, 1, "2. Verdict per configuration and KPI").font = Font(bold=True)
    ws.cell(r + 1, 1, "M = manual lower/better, G = GLASS lower/better, = = no (significant) difference, - = no directly applicable "
                      "idea in that run. Two models in one run: both shown (hours / resources).")
    r += 2
    header(ws, r, ["Configuration", "Expected KPI"] + [f"V_full run {n}" for n in RUNS] + [""] +
           [f"V_no_kb run {n}" for n in RUNS])
    code = {**{x: "M" for x in MAN}, **{x: "G" for x in GLA}, **{x: "=" for x in SAME}}
    for c in C.ALL_CONFIGS:
        for k, _, _ in C.expected_kpis(c):
            row, colors = [c, k], []
            for arm in C.ARMS:
                for n in RUNS:
                    h = df[(df.glass_arm == arm["label"]) & (df.run == n) & (df.config == c) & (df.kpi_expected == k)]
                    vs = [code[v] for v in h.verdict]
                    row.append(" / ".join(vs) if vs else "-")
                    colors.append(GREY if not vs else GREEN if set(vs) == {"M"} else RED if set(vs) == {"G"} else YELLOW)
                if arm is C.ARMS[0]:
                    row.append("")
                    colors.append(None)
            ws.append(row)
            for i, col in enumerate(colors):
                if col:
                    paint(ws.cell(ws.max_row, 3 + i), col)
                ws.cell(ws.max_row, 3 + i).alignment = Alignment(horizontal="center")
    for col, w in zip("ABCDEFGHIJKLM", [18, 34] + [11] * 5 + [3] + [11] * 5):
        ws.column_dimensions[col].width = w
    ws.column_dimensions["D"].width = 13

    # ---------------- one sheet per run
    for n in RUNS:
        ws = wb.create_sheet(f"Run {n}")
        r = 1
        for arm in C.ARMS:
            d = df[(df.glass_arm == arm["label"]) & (df.run == n)]
            d = d.assign(_o=d.config.map(C.ALL_CONFIGS.index)).sort_values(["_o", "variant", "model"], kind="stable")
            ws.cell(r, 1, f"Run {n} - {arm['label']}").font = Font(bold=True, size=12)
            header(ws, r + 1, [h for _, h, _ in SHEET_COLS])
            for row in d.itertuples():
                vals = [getattr(row, k) for k, _, _ in SHEET_COLS]
                ws.append([float(v) if isinstance(v, (float, np.floating)) else
                           int(v) if isinstance(v, np.integer) else v for v in vals])
                rr = ws.max_row
                paint(ws.cell(rr, 12), COLOR[row.verdict])
                if row.glass_kept_baseline_reps:
                    paint(ws.cell(rr, 7), ORANGE)
                for i in range(5, 12):
                    ws.cell(rr, i).number_format = "0.000" if i not in (8, 9) else "0.0"
            v = counts(d)
            ws.append([])
            ws.append([f"{len(d)} rows: {v[0]} manual lower/better, {v[1]} GLASS lower/better, {v[2]} no (significant) difference. "
                       f"Reps ({len(d) * 5}): {v[3]} GLASS better, {v[4]} no difference, {v[5]} manual better."])
            ws.cell(ws.max_row, 1).font = Font(bold=True)
            ws.append(["Cycle and waiting time: no significant difference = the 95% confidence interval [ci_low, "
                       "ci_high] of the per-seed difference manual - GLASS contains 0."])
            ws.append([UTIL_NOTE + " (Verdict uses the mean of GLASS's 5 reps; no confidence interval.)"])
            kept =d[d.glass_kept_baseline_reps != ""].drop_duplicates("config")
            for x in kept.itertuples():
                ws.append([f"Orange: {x.config}: GLASS kept the baseline in rep(s) {x.glass_kept_baseline_reps}; "
                           f"baseline values used."])
            r = ws.max_row + 3
        for i, (_, _, w) in enumerate(SHEET_COLS, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "E3"

    # ---------------- GLASS 5 reps next to the manual model of each run
    for arm in C.ARMS:
        ws = wb.create_sheet(f"Runs vs reps {arm['key']}")
        ws["A1"] = f"Manual model of each Copilot run next to GLASS's 5 reps ({arm['label']})"
        ws["A1"].font = Font(bold=True, size=12)
        ws["A2"] = ("One row per configuration x change x KPI. Manual run cell colour = that run's verdict against the "
                    "mean of GLASS's 5 reps: green = manual lower/better, red = GLASS lower/better, yellow = no (significant) "
                    "difference, grey '-' = no directly applicable idea of this kind in that run.")
        ws["A3"] = ("The 5 manual values are 5 Copilot runs, not 5 independent repetitions: many runs give the identical "
                    "model (same value). Orange GLASS cell: GLASS kept the baseline in that rep.")
        cols = (["config", "change", "kpi_expected", "baseline_mean"] + [f"GLASS rep{k}" for k in range(1, 6)] +
                ["GLASS mean"] + [f"Manual run {n}" for n in RUNS] + ["runs with a model"])
        header(ws, 5, cols)
        d = df[df.glass_arm == arm["label"]].copy()
        # same change twice in one run (only Procure2Pay_G1G2 run 1): hours_1 is the model of runs 2-5 -> same row
        d["row_key"] = d.model.str.replace(r"__hours_1$", "__hours", regex=True)
        d["_o"] = d.config.map(C.ALL_CONFIGS.index)
        for key, g in d.sort_values(["_o", "variant", "row_key"], kind="stable").groupby(
                ["_o", "variant", "row_key", "kpi_expected"], sort=False):
            f = g.iloc[0]
            label = LEVER[f.variant] + (" (2nd idea)" if f.row_key.endswith("_2") else "")
            reps = [f[f"glass_rep{k}_mean"] for k in range(1, 6)]
            vals, colors = [], []
            for n in RUNS:
                h = g[g.run == n]
                vals.append(float(h.manual_mean.iloc[0]) if len(h) else "-")
                colors.append(COLOR[h.verdict.iloc[0]] if len(h) else GREY)
            ws.append([f.config, label, f.kpi_expected, float(f.baseline_mean)] + [float(x) for x in reps] +
                      [float(f.glass_mean_5reps)] + vals + [f"{len(g)} of 5"])
            rr = ws.max_row
            kept = [int(x) for x in str(f.glass_kept_baseline_reps).split(", ") if x]
            for k in kept:
                paint(ws.cell(rr, 4 + k), ORANGE)
            for i, col in enumerate(colors):
                paint(ws.cell(rr, 11 + i), col)
                ws.cell(rr, 11 + i).alignment = Alignment(horizontal="center")
            for i in range(4, 16):
                ws.cell(rr, i).number_format = "0.000"
        for i, w in enumerate([18, 26, 34, 12] + [11] * 6 + [12] * 5 + [11], 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.append([])
        ws.append([UTIL_NOTE])
        ws.append(["Cycle and waiting time: 95% confidence interval of the per-seed difference manual - mean of GLASS's "
                   "5 reps (no significant difference = interval contains 0)."])
        ws.freeze_panes = "D6"
    wb.save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    models = manual_models()
    mvals = manual_kpis(models, a.workers)
    gvals, baseline_reps = glass_side()
    df = model_rows(models, mvals, gvals, baseline_reps).fillna({"glass_kept_baseline_reps": ""})
    df.to_csv(OUT / "comparison_all_runs.csv", index=False)
    write_workbook(df, OUT / "comparison_repeat_runs.xlsx")

    for arm in C.ARMS:
        for n in RUNS:
            d = df[(df.glass_arm == arm["label"]) & (df.run == n)]
            print(f"{arm['label']} run {n}: {d.model.nunique()} models, {len(d)} rows, {counts(d)}")
    print("written to", OUT)


if __name__ == "__main__":
    main()

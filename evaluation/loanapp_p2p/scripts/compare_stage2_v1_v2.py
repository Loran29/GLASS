"""
Compare the Stage-2 batch v1 (GLASS 8039058) with v2 (GLASS 5112fbc: calendar-based, role-aware Resource
Utilization) per configuration, from the verified per-configuration CSVs of both result folders.

Writes results/stage2_v1_vs_v2.csv and results/stage2_v1_vs_v2.md, and results/stage2_v1_vs_v2_runs.csv (one row
per run and version, final phase, with the flag unit_defect for the runs listed in results/unit_defect_runs_v1_v2.csv:
hour values applied as seconds; their scores are kept as recorded). Configurations with a utilization KPI
(expected_kpi_kinds contains "UT") are the ones the GLASS change targets; the others are the control.

Control check: two-sided sign test over the non-utilization configurations on "valid and all KPIs improved
(final)" (ties dropped), and a pooled run-level Fisher exact test on the same outcome.

Usage (repo root): python evaluation/loanapp_p2p/scripts/compare_stage2_v1_v2.py
"""
from __future__ import annotations

import csv
import json
from math import comb
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
RES = BASE / "results"
FOLDERS = {"v1": RES / "stage2_results", "v2": RES / "stage2_results_v2"}
COUNTS = ["initial_valid", "final_valid", "initial_all_kpis_improved", "final_all_kpis_improved",
          "initial_all_kpis_improved_glass_rule", "final_all_kpis_improved_glass_rule",
          "final_util_improved_glass", "final_util_improved_batch",
          "final_valid_and_improved", "reps_loop_discarded_better_initial"]
MEANS = ["final_mean_recall", "final_mean_improvement_ratio_CTD", "final_median_improvement_ratio_CTD"]
LABELS = {"initial_valid": "valid initial", "final_valid": "valid final",
          "initial_all_kpis_improved": "all KPIs improved initial", "final_all_kpis_improved": "all KPIs improved final",
          "initial_all_kpis_improved_glass_rule": "all KPIs improved initial (GLASS rule)",
          "final_all_kpis_improved_glass_rule": "all KPIs improved final (GLASS rule)",
          "final_util_improved_glass": "utilization improved final (GLASS rule)",
          "final_util_improved_batch": "utilization improved final (batch rule)",
          "final_valid_and_improved": "valid and improved final",
          "reps_loop_discarded_better_initial": "loop discarded better initial",
          "final_mean_recall": "recall final", "final_mean_improvement_ratio_CTD": "CTD ratio final",
          "final_median_improvement_ratio_CTD": "CTD ratio final (median)"}


def sign_test(diffs: list[int]) -> tuple[int, int, float]:
    nonzero = [d for d in diffs if d]
    n, k = len(nonzero), sum(d > 0 for d in nonzero)
    if n == 0:
        return 0, 0, 1.0
    tail = sum(comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n
    return n, k, min(1.0, 2 * tail)


def main() -> None:
    config = json.loads((BASE / "setup" / "stage2_config.json").read_text(encoding="utf-8"))
    util = {c["config_id"]: "UT" in c["expected_kpi_kinds"] for c in config["configurations"]}
    per = {v: {r["config_id"]: r for r in csv.DictReader(open(f / "stage2_per_config.csv", encoding="utf-8"))}
           for v, f in FOLDERS.items()}
    order = sorted(util, key=lambda c: (c.split("_")[0], len(c.split("_")[1]), c))
    from statistics import median
    ctd_runs = {v: {} for v in FOLDERS}            # per-run final CTD ratios (median is robust to one outlier)
    for v, f in FOLDERS.items():
        for r in csv.DictReader(open(f / "stage2_summary.csv", encoding="utf-8")):
            if r["phase"] == "final" and r["status"] == "ok" and r["improvement_ratio_CTD"] not in ("", None):
                ctd_runs[v].setdefault(r["config_id"], []).append(float(r["improvement_ratio_CTD"]))
    for v in FOLDERS:
        for cid, row in per[v].items():
            vals = ctd_runs[v].get(cid, [])
            row["final_median_improvement_ratio_CTD"] = str(round(median(vals), 4)) if vals else ""
    num = lambda x: float(x) if x not in ("", None) else None
    defect = {(r["version"], r["config_id"], r["rep"])
              for r in csv.DictReader(open(RES / "unit_defect_runs_v1_v2.csv", encoding="utf-8"))}

    run_rows = []                                   # per run: final phase, as recorded, plus the unit_defect flag
    for v, f in FOLDERS.items():
        for r in csv.DictReader(open(f / "stage2_summary.csv", encoding="utf-8")):
            if r["phase"] == "final":
                run_rows.append({"version": v, "config_id": r["config_id"], "rep": r["rep"], "status": r["status"],
                                 "valid_final": r["valid"], "all_kpis_improved_final": r["all_kpis_improved"],
                                 "recall_final": r["recall"], "improvement_ratio_CTD_final": r["improvement_ratio_CTD"],
                                 "unit_defect": (v, r["config_id"], r["rep"]) in defect})
    run_rows.sort(key=lambda x: (order.index(x["config_id"]), int(x["rep"]), x["version"]))
    with open(RES / "stage2_v1_vs_v2_runs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(run_rows[0]))
        w.writeheader()
        w.writerows(run_rows)

    rows = []
    for cid in order:
        row = {"config_id": cid, "has_utilization_kpi": util[cid]}
        for m in COUNTS + MEANS:
            for v in FOLDERS:
                row[f"{m}_{v}"] = num(per[v][cid][m])
            if row[f"{m}_v1"] is not None and row[f"{m}_v2"] is not None:
                row[f"{m}_delta"] = round(row[f"{m}_v2"] - row[f"{m}_v1"], 3)
            else:
                row[f"{m}_delta"] = None
        for v in FOLDERS:
            row[f"unit_defect_runs_{v}"] = sum(1 for d in defect if d[0] == v and d[1] == cid)
        rows.append(row)

    def group(rs, label):
        out = {"config_id": label, "has_utilization_kpi": ""}
        for m in COUNTS:
            for v in ("v1", "v2", "delta"):
                out[f"{m}_{v}"] = round(sum(r[f"{m}_{v}"] or 0 for r in rs), 3)
        for m in MEANS:
            for v in ("v1", "v2", "delta"):
                vals = [r[f"{m}_{v}"] for r in rs if r[f"{m}_{v}"] is not None]
                out[f"{m}_{v}"] = round(sum(vals) / len(vals), 3) if vals else None
        for v in FOLDERS:                         # group median over the runs, not over configuration medians
            vals = [x for r in rs for x in ctd_runs[v].get(r["config_id"], [])]
            out[f"final_median_improvement_ratio_CTD_{v}"] = round(median(vals), 3) if vals else None
        a, b = out["final_median_improvement_ratio_CTD_v1"], out["final_median_improvement_ratio_CTD_v2"]
        out["final_median_improvement_ratio_CTD_delta"] = round(b - a, 3) if a is not None and b is not None else None
        for v in FOLDERS:
            out[f"unit_defect_runs_{v}"] = sum(r[f"unit_defect_runs_{v}"] for r in rs)
        return out

    with_u = [r for r in rows if r["has_utilization_kpi"]]
    without_u = [r for r in rows if not r["has_utilization_kpi"]]
    totals = [group(rows, "TOTAL (70 runs per version)"), group(with_u, "configurations with a utilization KPI"),
              group(without_u, "configurations without a utilization KPI (control)")]
    fields = list(rows[0])
    with open(RES / "stage2_v1_vs_v2.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows + totals)

    from scipy.stats import fisher_exact
    n, k, p = sign_test([int(r["final_valid_and_improved_delta"]) for r in without_u])
    runs = 5 * len(without_u)
    a = int(sum(r["final_valid_and_improved_v1"] for r in without_u))
    b = int(sum(r["final_valid_and_improved_v2"] for r in without_u))
    fisher_p = fisher_exact([[a, runs - a], [b, runs - b]])[1]

    def cell(r, m, nd=0):
        if m.startswith("final_util_") and r["has_utilization_kpi"] is False:
            return "–"
        f = (lambda x: "–" if x is None else (f"{x:.{nd}f}" if nd else f"{int(x)}"))
        return f"{f(r[f'{m}_v1'])} / {f(r[f'{m}_v2'])}"

    L = ["# Stage 2: batch v1 vs v2", "",
         "- **v1:** GLASS `8039058`; Resource Utilization = busy time ÷ simulated span, averaged over all resources.",
         "- **v2:** GLASS `5112fbc`; Resource Utilization = busy time ÷ available working time, evaluated for the "
         "role the KPI names.",
         "- **Same in both:** batch script, config, seeds and baseline logic (baselines recomputed for v2).",
         "- **Cells:** v1 / v2. Counts are out of 5 runs; recall and CTD ratio are means over the evaluated runs.",
         "- **Groups:** configurations with a utilization KPI are those the GLASS change targets; the others are "
         "the control.",
         "- **Hour values applied as seconds:** in the runs flagged `unit_defect` (`stage2_v1_vs_v2_runs.csv`; list "
         "in `unit_defect_runs_v1_v2.csv`, see `VERSIONS.md`) a proposed duration meant in hours was applied in "
         "seconds; their scores are kept as recorded and are included in all counts below.", "",
         "- **Two rules for utilization KPIs:** \"batch rule\" = target pool below 0.85 and lower than the "
         "baseline (the scored rule); \"GLASS rule\" = GLASS's own verdict (any decrease of GLASS's utilization "
         "value: all-resource mean of busy time ÷ span in v1, role-resolved working-time utilization in v2). "
         "\"All KPIs improved (GLASS rule)\" applies GLASS's verdict to every KPI.", "",
         "| Configuration | Utilization KPI | Valid initial | Valid final | All KPIs improved initial | "
         "All KPIs improved final | All KPIs improved initial (GLASS rule) | All KPIs improved final (GLASS rule) | "
         "Utilization improved final (GLASS rule) | Utilization improved final (batch rule) | "
         "Valid and improved final | Loop discarded better initial | Recall final | CTD ratio final (mean) | "
         "CTD ratio final (median) | Runs affected by the unit defect (v1 / v2) |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows + totals:
        L.append(f"| {r['config_id']} | {'yes' if r['has_utilization_kpi'] is True else ('no' if r['has_utilization_kpi'] is False else '')} | "
                 + " | ".join(cell(r, m) for m in COUNTS) + " | "
                 + " | ".join(cell(r, m, 2) for m in MEANS)
                 + f" | {r['unit_defect_runs_v1']} / {r['unit_defect_runs_v2']} |")
    flagged = sorted({d for d in defect}, key=lambda d: (d[0], order.index(d[1]), int(d[2])))
    L += ["", "Runs flagged `unit_defect`: " + "; ".join(f"{v} {c} rep {k}" for v, c, k in flagged) + "."]
    d_u, d_c = totals[1], totals[2]
    L += ["", "**Per-group delta (v2 − v1)**", "",
          "| Group | Valid and improved final | Loop discarded better initial | Recall final | CTD ratio final (mean) | "
          "CTD ratio final (median) |",
          "|---|---|---|---|---|---|",
          f"| With a utilization KPI | {d_u['final_valid_and_improved_delta']:+g} | "
          f"{d_u['reps_loop_discarded_better_initial_delta']:+g} | {d_u['final_mean_recall_delta']:+.3f} | "
          f"{d_u['final_mean_improvement_ratio_CTD_delta']:+.3f} | {d_u['final_median_improvement_ratio_CTD_delta']:+.3f} |",
          f"| Without (control) | {d_c['final_valid_and_improved_delta']:+g} | "
          f"{d_c['reps_loop_discarded_better_initial_delta']:+g} | {d_c['final_mean_recall_delta']:+.3f} | "
          f"{d_c['final_mean_improvement_ratio_CTD_delta']:+.3f} | {d_c['final_median_improvement_ratio_CTD_delta']:+.3f} |", "",
          "**Control check** (configurations without a utilization KPI, outcome: valid and all KPIs improved, "
          "final):", "",
          f"- **Sign test** over the {len(without_u)} configurations: {n} differ, {k} of them higher in v2; "
          f"two-sided p = {p:.3f}.",
          f"- **Pooled run-level Fisher exact test:** {a}/{runs} in v1 vs {b}/{runs} in v2; p = {fisher_p:.3f}.",
          f"- **Reading:** the control configurations are "
          f"{'statistically indistinguishable' if min(p, fisher_p) >= 0.05 else 'NOT statistically indistinguishable'}"
          " between v1 and v2 at the 5 % level. With 6 configurations, the sign test can detect only large "
          "systematic shifts. The LLM is not seeded, so run-to-run variation alone also differs between versions.", ""]
    (RES / "stage2_v1_vs_v2.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()

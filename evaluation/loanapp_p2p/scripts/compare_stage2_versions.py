"""
Compare the Stage-2 batches v1, v2 and v3 per configuration, from the verified per-configuration and summary CSVs
of the three result folders (see results/VERSIONS.md for what differs between the versions).

Writes results/stage2_v1_v2_v3.csv and results/stage2_v1_v2_v3.md, and results/stage2_v1_v2_v3_runs.csv (one row
per run and version, final phase, with the flag unit_defect: hour values applied as seconds, from
results/unit_defect_runs_v1_v2.csv and results/unit_defect_runs_v3.csv; their scores are kept as recorded).
Configurations with a utilization KPI
(expected_kpi_kinds contains "UT") are the ones the utilization changes target; the others are the control group.

Control check v2 -> v3: two-sided sign test over the configurations without a utilization KPI on "valid and
all KPIs improved (final)" (ties dropped), and a pooled run-level Fisher exact test on the same outcome. Unlike
v1 -> v2, the v3 GLASS changes (baseline-mismatch rejection, id-stem mapping, best-iteration order) can also
act on these configurations, so the check tests whether the robustness fixes changed them, not a pure null.

Usage (repo root): python evaluation/loanapp_p2p/scripts/compare_stage2_versions.py
"""
from __future__ import annotations

import csv
import json
from math import comb
from pathlib import Path
from statistics import median

BASE = Path(__file__).resolve().parent.parent
RES = BASE / "results"
FOLDERS = {"v1": RES / "stage2_results", "v2": RES / "stage2_results_v2", "v3": RES / "stage2_results_v3"}
COUNTS = ["final_valid", "final_all_kpis_improved", "final_all_kpis_improved_glass_rule",
          "final_util_target_achieved_glass", "final_util_target_achieved_batch",
          "final_valid_and_improved", "reps_loop_discarded_better_initial"]
MEANS = ["final_mean_recall", "final_median_improvement_ratio_CTD"]
V3_ONLY = {"final_util_target_achieved_glass", "final_util_target_achieved_batch"}
LABELS = {"final_valid": "Valid final",
          "final_all_kpis_improved": "All KPIs improved final (batch rule)",
          "final_all_kpis_improved_glass_rule": "All KPIs improved final (GLASS rule)",
          "final_util_target_achieved_glass": "Utilization target achieved final, GLASS (v3)",
          "final_util_target_achieved_batch": "Utilization target achieved final, batch (v3)",
          "final_valid_and_improved": "Valid and improved final",
          "reps_loop_discarded_better_initial": "Loop discarded better initial",
          "final_mean_recall": "Recall final (mean)",
          "final_median_improvement_ratio_CTD": "CTD ratio final (median)"}


def sign_test(diffs: list[float]) -> tuple[int, int, float]:
    nonzero = [d for d in diffs if d]
    n, k = len(nonzero), sum(d > 0 for d in nonzero)
    if n == 0:
        return 0, 0, 1.0
    tail = sum(comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n
    return n, k, min(1.0, 2 * tail)


def main() -> None:
    config = json.loads((BASE / "setup" / "stage2_config.json").read_text(encoding="utf-8"))
    util = {c["config_id"]: "UT" in c["expected_kpi_kinds"] for c in config["configurations"]}
    order = sorted(util, key=lambda c: (c.split("_")[0], len(c.split("_")[1]), c))
    per = {v: {r["config_id"]: r for r in csv.DictReader(open(f / "stage2_per_config.csv", encoding="utf-8"))}
           for v, f in FOLDERS.items()}
    ctd_runs = {v: {} for v in FOLDERS}            # per-run final CTD ratios (the median is robust to outliers)
    for v, f in FOLDERS.items():
        for r in csv.DictReader(open(f / "stage2_summary.csv", encoding="utf-8")):
            if r["phase"] == "final" and r["status"] == "ok" and r["improvement_ratio_CTD"] not in ("", None):
                ctd_runs[v].setdefault(r["config_id"], []).append(float(r["improvement_ratio_CTD"]))
    num = lambda x: float(x) if x not in ("", None) else None
    defect = set()
    for name in ("unit_defect_runs_v1_v2.csv", "unit_defect_runs_v3.csv"):
        if (RES / name).exists():
            defect |= {(r["version"], r["config_id"], r["rep"])
                       for r in csv.DictReader(open(RES / name, encoding="utf-8"))}
    run_rows = []                                   # per run: final phase, as recorded, plus the unit_defect flag
    for v, f in FOLDERS.items():
        for r in csv.DictReader(open(f / "stage2_summary.csv", encoding="utf-8")):
            if r["phase"] == "final":
                run_rows.append({"version": v, "config_id": r["config_id"], "rep": r["rep"], "status": r["status"],
                                 "valid_final": r["valid"], "all_kpis_improved_final": r["all_kpis_improved"],
                                 "util_target_achieved_glass_final": r.get("util_target_achieved_glass", ""),
                                 "util_target_achieved_batch_final": r.get("util_target_achieved_batch", ""),
                                 "recall_final": r["recall"], "improvement_ratio_CTD_final": r["improvement_ratio_CTD"],
                                 "unit_defect": (v, r["config_id"], r["rep"]) in defect})
    run_rows.sort(key=lambda x: (order.index(x["config_id"]), int(x["rep"]), x["version"]))
    with open(RES / "stage2_v1_v2_v3_runs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(run_rows[0]))
        w.writeheader()
        w.writerows(run_rows)

    from statistics import mean
    base_pool = {}                                  # baseline pool utilization (cached 10-seed baseline, v3)
    for cid in order:
        p1 = FOLDERS["v3"] / "raw" / cid / "rep_1.json"
        rec = json.loads(p1.read_text(encoding="utf-8")) if p1.exists() else {}
        base_pool[cid] = round(mean(rec["baseline_pool_utilization"]), 4) if rec.get("baseline_pool_utilization") else None

    rows = []
    for cid in order:
        row = {"config_id": cid, "has_utilization_kpi": util[cid], "baseline_pool_utilization": base_pool[cid]}
        for m in COUNTS + MEANS:
            for v in FOLDERS:
                if m == "final_median_improvement_ratio_CTD":
                    vals = ctd_runs[v].get(cid, [])
                    row[f"{m}_{v}"] = round(median(vals), 4) if vals else None
                else:
                    row[f"{m}_{v}"] = num(per[v][cid].get(m))
            for a, b in (("v2", "v3"), ("v1", "v3")):
                x, y = row[f"{m}_{a}"], row[f"{m}_{b}"]
                row[f"{m}_delta_{b}_{a}"] = round(y - x, 3) if x is not None and y is not None else None
        for v in FOLDERS:
            row[f"unit_defect_runs_{v}"] = sum(1 for d in defect if d[0] == v and d[1] == cid)
        rows.append(row)

    def group(rs, label):
        out = {"config_id": label, "has_utilization_kpi": "", "baseline_pool_utilization": None}
        keys = [f"{m}_{v}" for m in COUNTS + MEANS for v in list(FOLDERS) + ["delta_v3_v2", "delta_v3_v1"]]
        for key in keys:
            vals = [r[key] for r in rs if r[key] is not None]
            if any(key.startswith(m) for m in COUNTS):
                out[key] = round(sum(vals), 3) if vals else None
            else:
                out[key] = round(sum(vals) / len(vals), 3) if vals else None
        for v in FOLDERS:                         # group median over the runs, not over configuration medians
            vals = [x for r in rs for x in ctd_runs[v].get(r["config_id"], [])]
            out[f"final_median_improvement_ratio_CTD_{v}"] = round(median(vals), 3) if vals else None
        for a, b in (("v2", "v3"), ("v1", "v3")):
            x, y = (out[f"final_median_improvement_ratio_CTD_{a}"], out[f"final_median_improvement_ratio_CTD_{b}"])
            out[f"final_median_improvement_ratio_CTD_delta_{b}_{a}"] = round(y - x, 3) if None not in (x, y) else None
        for v in FOLDERS:
            out[f"unit_defect_runs_{v}"] = sum(r[f"unit_defect_runs_{v}"] for r in rs)
        return out

    with_u = [r for r in rows if r["has_utilization_kpi"]]
    without_u = [r for r in rows if not r["has_utilization_kpi"]]
    totals = [group(rows, "TOTAL (70 runs per version)"), group(with_u, "configurations with a utilization KPI"),
              group(without_u, "configurations without a utilization KPI (control)")]
    with open(RES / "stage2_v1_v2_v3.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows + totals)

    from scipy.stats import fisher_exact
    n, k, p = sign_test([r["final_valid_and_improved_delta_v3_v2"] for r in without_u])
    runs = 5 * len(without_u)
    a = int(sum(r["final_valid_and_improved_v2"] for r in without_u))
    b = int(sum(r["final_valid_and_improved_v3"] for r in without_u))
    fisher_p = fisher_exact([[a, runs - a], [b, runs - b]])[1]

    def cell(r, m):
        nd = 2 if m in MEANS else 0
        f = (lambda x: "–" if x is None else (f"{x:.{nd}f}" if nd else f"{int(x)}"))
        if m in V3_ONLY:
            return "–" if r["has_utilization_kpi"] is False else f(r[f"{m}_v3"])
        return " / ".join(f(r[f"{m}_{v}"]) for v in FOLDERS)

    head = (["Configuration", "Utilization KPI"] + [LABELS[m] for m in COUNTS + MEANS]
            + ["Runs affected by the unit defect (v1 / v2)"])
    L = ["# Stage 2: batches v1, v2 and v3", "",
         "- **Versions:** see `VERSIONS.md` (GLASS commits, code, input and scoring changes, affected runs).",
         "- **Cells:** v1 / v2 / v3. Counts are out of 5 runs per configuration; recall is the mean over the "
         "evaluated runs, the CTD ratio the median over the runs (one outlier run in v2 LoanApp_G2G3, see the v2 "
         "README).",
         "- **Utilization target achieved (v3 only):** GLASS = its role-resolved utilization (mean over the 10 "
         "seeds) <= 0.85; batch = the target pool's utilization of working time < 0.85. v1 and v2 had no threshold.",
         "- **Rules:** \"batch rule\" = the scored rule (utilization: target pool below 0.85 and lower than the "
         "baseline); \"GLASS rule\" = GLASS's improved verdict for every KPI (any decrease for utilization).",
         "- **Groups:** configurations with a utilization KPI are those the utilization changes (v2 metric, v3 "
         "target) address; the others are the control group.",
         "- **Hour values applied as seconds (v1, v2):** in the runs flagged `unit_defect` "
         "(`stage2_v1_v2_v3_runs.csv`; see `VERSIONS.md`) a proposed duration meant in hours was applied in "
         "seconds; their scores are kept as recorded and are included in all counts below. v3 rejects such "
         "modifications.", "",
         "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows + totals:
        flag = {True: "yes", False: "no"}.get(r["has_utilization_kpi"], "")
        L.append(f"| {r['config_id']} | {flag} | " + " | ".join(cell(r, m) for m in COUNTS + MEANS)
                 + f" | {r['unit_defect_runs_v1']} / {r['unit_defect_runs_v2']} |")
    below = [f"{c} ({base_pool[c]:.3f})" for c in order if base_pool[c] is not None and base_pool[c] < 0.85]
    L += ["", "**Target already met by the baseline:** the target pool's baseline utilization (mean over the 10 "
          "seeds) is below 0.85 in " + ", ".join(below) + ". There, \"utilization target achieved\" holds without "
          "any change; the scored rule also requires the utilization to be lower than the baseline (column "
          "`baseline_pool_utilization` in the CSV)."]
    flagged = sorted(defect, key=lambda d: (d[0], order.index(d[1]), int(d[2])))
    n_v3 = sum(1 for d in defect if d[0] == "v3")
    L += ["", "Runs flagged `unit_defect`: " + "; ".join(f"{v} {c} rep {k}" for v, c, k in flagged)
          + f". v3: {n_v3} run(s) (same search over the v3 records)."]
    d_u, d_c = totals[1], totals[2]
    dm = ["final_valid", "final_all_kpis_improved", "final_valid_and_improved", "reps_loop_discarded_better_initial",
          "final_mean_recall", "final_median_improvement_ratio_CTD"]

    def dcell(g, m, b, a):
        x = g[f"{m}_delta_{b}_{a}"]
        return "–" if x is None else (f"{x:+.3f}" if m in MEANS else f"{x:+g}")

    L += ["", "**Per-group change (v3 − v2, and v3 − v1 in brackets)**", "",
          "| Group | " + " | ".join(LABELS[m] for m in dm) + " |", "|" + "---|" * (len(dm) + 1)]
    for label, g in (("With a utilization KPI", d_u), ("Without (control)", d_c)):
        L.append(f"| {label} | " + " | ".join(f"{dcell(g, m, 'v3', 'v2')} ({dcell(g, m, 'v3', 'v1')})"
                                              for m in dm) + " |")
    L += ["", "**Control check v2 → v3** (configurations without a utilization KPI, outcome: valid and all KPIs "
          "improved, final):", "",
          f"- **Sign test** over the {len(without_u)} configurations: {n} differ, {k} of them higher in v3; "
          f"two-sided p = {p:.3f}.",
          f"- **Pooled run-level Fisher exact test:** {a}/{runs} in v2 vs {b}/{runs} in v3; p = {fisher_p:.3f}.",
          f"- **Reading:** the control configurations are "
          f"{'statistically indistinguishable' if min(p, fisher_p) >= 0.05 else 'NOT statistically indistinguishable'}"
          " between v2 and v3 at the 5 % level. Unlike v1 → v2, the v3 changes can act on these configurations "
          "too (rejected mismatched modifications, id-stem mapping, best-iteration order), so this is a check that "
          "they did not shift them, not a pure null comparison. With 6 configurations the sign test detects only "
          "large systematic shifts; the LLM is not seeded, so run-to-run variation also differs between batches.",
          ""]
    (RES / "stage2_v1_v2_v3.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()

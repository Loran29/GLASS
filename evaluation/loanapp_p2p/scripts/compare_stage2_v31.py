"""
Compare the Stage-2 batches v1, v2, v3 and v3.1 per configuration, from the verified per-configuration and
summary CSVs and the records of the four result folders (see results/VERSIONS.md).

Writes results/stage2_v1_v2_v3_v31.csv and .md, and results/stage2_v1_v2_v3_v31_runs.csv (one row per run
and version, final phase, with unit_defect and the v3.1 fix flags).

Runs affected by each v3.1 fix (counted per configuration, from the records; v3 shown for comparison):
  budget     runs in which a patch was rejected for exceeding the relative budget (budget_exceeded naming
             "Total staff cost would rise")
  cal_parsed runs in which a calendar change given as text or in Prosimos period form was read
             (merge diagnostic calendar_parsed, or a Prosimos-form item applied)
  cal_rej    runs in which a calendar change could not be read and was rejected with feedback
             (calendar_unparsed)
  cal_drop   runs in which a calendar change was skipped without feedback (v3: invalid_value on
             resource_calendar; expected 0 in v3.1)
  util_raise runs whose refinement feedback showed a strategy raising utilization while the utilization
             KPI (minimize) was a problem KPI

Control check v3 -> v3.1: two-sided sign test over the configurations without a utilization KPI on "valid
and all KPIs improved (final)" (ties dropped), and a pooled run-level Fisher exact test.

Usage (repo root): python evaluation/loanapp_p2p/scripts/compare_stage2_v31.py
"""
from __future__ import annotations

import csv
import json
import re
from math import comb
from pathlib import Path
from statistics import mean, median

BASE = Path(__file__).resolve().parent.parent
RES = BASE / "results"
FOLDERS = {"v1": RES / "stage2_results", "v2": RES / "stage2_results_v2", "v3": RES / "stage2_results_v3",
           "v3.1": RES / "stage2_results_v3_1"}
COUNTS = ["final_valid", "final_all_kpis_improved", "final_all_kpis_improved_glass_rule",
          "final_util_target_achieved_glass", "final_util_target_achieved_batch",
          "final_valid_and_improved", "reps_loop_discarded_better_initial"]
MEANS = ["final_mean_recall", "final_median_improvement_ratio_CTD"]
TARGET = {"final_util_target_achieved_glass", "final_util_target_achieved_batch"}       # v3 and v3.1 only
FIXES = ["budget", "cal_parsed", "cal_rej", "cal_drop", "util_raise"]
LABELS = {"final_valid": "Valid final",
          "final_all_kpis_improved": "All KPIs improved final (batch rule)",
          "final_all_kpis_improved_glass_rule": "All KPIs improved final (GLASS rule)",
          "final_util_target_achieved_glass": "Utilization target achieved final, GLASS (v3 / v3.1)",
          "final_util_target_achieved_batch": "Utilization target achieved final, batch (v3 / v3.1)",
          "final_valid_and_improved": "Valid and improved final",
          "reps_loop_discarded_better_initial": "Loop discarded better initial",
          "final_mean_recall": "Recall final (mean)",
          "final_median_improvement_ratio_CTD": "CTD ratio final (median)",
          "budget": "Runs with a relative-budget rejection (v3 / v3.1)",
          "cal_parsed": "Runs with a calendar change read from text or Prosimos form (v3 / v3.1)",
          "cal_rej": "Runs with a calendar change rejected with feedback (v3 / v3.1)",
          "cal_drop": "Runs with a calendar change skipped without feedback (v3 / v3.1)",
          "util_raise": "Runs shown utilization-raising strategies for a utilization problem KPI (v3 / v3.1)"}
RAISES_UTIL = re.compile(
    r"(utili[sz]ation (rose|improved|improves|increased)|(raised|increase[sd]?|improve[sd]?|drives) (overall |"
    r"average |the )*(staff |operator |server |counter |resource )*utili[sz]ation|utili[sz]ation upward|"
    r"utili[sz]ation rate of)", re.I)


def sign_test(diffs: list[float]) -> tuple[int, int, float]:
    nonzero = [d for d in diffs if d]
    n, k = len(nonzero), sum(d > 0 for d in nonzero)
    if n == 0:
        return 0, 0, 1.0
    tail = sum(comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n
    return n, k, min(1.0, 2 * tail)


def fix_flags(rec: dict) -> dict[str, bool]:
    """Which of the v3.1 fixes acted in this run (see the module docstring)."""
    f = dict.fromkeys(FIXES, False)
    for it in (rec.get("loop") or {}).get("iterations") or []:
        notes = (it.get("generation") or {}).get("generation_notes") or []
        f["budget"] |= any("budget_exceeded" in n and "Total staff cost would rise" in n for n in notes)
        f["cal_rej"] |= any("calendar_unparsed" in n for n in notes)
        diags = it.get("merge_diagnostics") or []
        f["cal_parsed"] |= any(d.get("category") == "calendar_parsed" for d in diags)
        f["cal_rej"] |= any(d.get("category") == "calendar_unparsed" for d in diags)
        f["cal_drop"] |= any(d.get("category") == "invalid_value" and "resource_calendar modifications require"
                             in d.get("message", "") or (d.get("category") == "invalid_value"
                                                         and "invalid timeTableItems" in d.get("message", ""))
                             for d in diags)
        for m in (it.get("patch") or {}).get("modifications") or []:      # Prosimos form applied
            items = ((m.get("proposed_structured") or {}).get("timeTableItems") or []) if \
                m.get("parameter_type") == "resource_calendar" else []
            if any(isinstance(x, dict) and "beginTime" in x for x in items) and not any(
                    d.get("modification_index") and d.get("severity") == "error" for d in diags):
                f["cal_parsed"] = True
        fb = it.get("feedback_text") or ""
        comps = (it.get("single_run_evaluation") or {}).get("kpi_comparisons") or []
        util = [c for c in comps if c.get("category") == "utilization"]
        problem = bool(util) and (util[0].get("improved") is False
                                  or (util[0].get("achieved") is False and util[0].get("improved") is True))
        if problem and "Literature-Backed" in fb:
            section = fb.split("## Literature-Backed Alternative Interventions:")[1].split("## What You Must Do")[0]
            f["util_raise"] |= bool(RAISES_UTIL.search(section))
    return f


def main() -> None:
    config = json.loads((BASE / "setup" / "stage2_config.json").read_text(encoding="utf-8"))
    util = {c["config_id"]: "UT" in c["expected_kpi_kinds"] for c in config["configurations"]}
    order = sorted(util, key=lambda c: (c.split("_")[0], len(c.split("_")[1]), c))
    per = {v: {r["config_id"]: r for r in csv.DictReader(open(f / "stage2_per_config.csv", encoding="utf-8"))}
           for v, f in FOLDERS.items()}
    summ = {v: list(csv.DictReader(open(f / "stage2_summary.csv", encoding="utf-8"))) for v, f in FOLDERS.items()}
    defect = set()
    for name in ("unit_defect_runs_v1_v2.csv", "unit_defect_runs_v3.csv", "unit_defect_runs_v3_1.csv"):
        if (RES / name).exists():
            defect |= {(r["version"], r["config_id"], r["rep"]) for r in csv.DictReader(open(RES / name, encoding="utf-8"))}
    flags = {}
    for v in ("v3", "v3.1"):
        for p in (FOLDERS[v] / "raw").glob("*/rep_*.json"):
            rec = json.loads(p.read_text(encoding="utf-8"))
            flags[(v, rec["config_id"], str(rec["rep"]))] = fix_flags(rec)
    base_pool = {}
    for cid in order:
        p1 = FOLDERS["v3.1"] / "raw" / cid / "rep_1.json"
        rec = json.loads(p1.read_text(encoding="utf-8")) if p1.exists() else {}
        base_pool[cid] = round(mean(rec["baseline_pool_utilization"]), 4) if rec.get("baseline_pool_utilization") else None
    num = lambda x: float(x) if x not in ("", None) else None

    run_rows = []
    for v in FOLDERS:
        for r in summ[v]:
            if r["phase"] != "final":
                continue
            fl = flags.get((v, r["config_id"], r["rep"]), {})
            run_rows.append({"version": v, "config_id": r["config_id"], "rep": r["rep"], "status": r["status"],
                             "valid_final": r["valid"], "all_kpis_improved_final": r["all_kpis_improved"],
                             "util_target_achieved_glass_final": r.get("util_target_achieved_glass", ""),
                             "util_target_achieved_batch_final": r.get("util_target_achieved_batch", ""),
                             "recall_final": r["recall"], "improvement_ratio_CTD_final": r["improvement_ratio_CTD"],
                             "unit_defect": (v, r["config_id"], r["rep"]) in defect,
                             **{f"fix_{k}": fl.get(k, "") for k in FIXES}})
    run_rows.sort(key=lambda x: (order.index(x["config_id"]), int(x["rep"]), list(FOLDERS).index(x["version"])))
    with open(RES / "stage2_v1_v2_v3_v31_runs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(run_rows[0]))
        w.writeheader()
        w.writerows(run_rows)

    rows = []
    for cid in order:
        row = {"config_id": cid, "has_utilization_kpi": util[cid], "baseline_pool_utilization": base_pool[cid]}
        for m in COUNTS + MEANS:
            for v in FOLDERS:
                if m == "final_median_improvement_ratio_CTD":
                    vals = [float(r["improvement_ratio_CTD"]) for r in summ[v] if r["config_id"] == cid
                            and r["phase"] == "final" and r["status"] == "ok" and r["improvement_ratio_CTD"] not in ("", None)]
                    row[f"{m}_{v}"] = round(median(vals), 4) if vals else None
                else:
                    row[f"{m}_{v}"] = num(per[v][cid].get(m))
            for a in ("v3", "v2"):
                x, y = row[f"{m}_{a}"], row[f"{m}_v3.1"]
                row[f"{m}_delta_v31_{a}"] = round(y - x, 3) if x is not None and y is not None else None
        for v in ("v1", "v2"):
            row[f"unit_defect_runs_{v}"] = sum(1 for d in defect if d[0] == v and d[1] == cid)
        for k in FIXES:
            for v in ("v3", "v3.1"):
                row[f"{k}_{v}"] = sum(1 for (vv, c, _), fl in flags.items() if vv == v and c == cid and fl[k])
        rows.append(row)

    def group(rs, label):
        out = {"config_id": label, "has_utilization_kpi": "", "baseline_pool_utilization": None}
        for key in rows[0]:
            if key in out:
                continue
            vals = [r[key] for r in rs if r[key] is not None]
            if any(key.startswith(m) for m in MEANS):
                out[key] = round(sum(vals) / len(vals), 3) if vals else None
            else:
                out[key] = round(sum(vals), 3) if vals else None
        for v in FOLDERS:                         # group median over the runs
            vals = [float(r["improvement_ratio_CTD"]) for r in summ[v] if r["config_id"] in {x["config_id"] for x in rs}
                    and r["phase"] == "final" and r["status"] == "ok" and r["improvement_ratio_CTD"] not in ("", None)]
            out[f"final_median_improvement_ratio_CTD_{v}"] = round(median(vals), 3) if vals else None
        for a in ("v3", "v2"):
            x, y = out[f"final_median_improvement_ratio_CTD_{a}"], out["final_median_improvement_ratio_CTD_v3.1"]
            out[f"final_median_improvement_ratio_CTD_delta_v31_{a}"] = round(y - x, 3) if None not in (x, y) else None
        return out

    with_u = [r for r in rows if r["has_utilization_kpi"]]
    without_u = [r for r in rows if not r["has_utilization_kpi"]]
    totals = [group(rows, "TOTAL (70 runs per version)"), group(with_u, "configurations with a utilization KPI"),
              group(without_u, "configurations without a utilization KPI (control)")]
    with open(RES / "stage2_v1_v2_v3_v31.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows + totals)

    from scipy.stats import fisher_exact
    n, k, p = sign_test([r["final_valid_and_improved_delta_v31_v3"] for r in without_u])
    runs = 5 * len(without_u)
    a = int(sum(r["final_valid_and_improved_v3"] for r in without_u))
    b = int(sum(r["final_valid_and_improved_v3.1"] for r in without_u))
    fisher_p = fisher_exact([[a, runs - a], [b, runs - b]])[1]
    nu, ku, pu = sign_test([r["final_valid_and_improved_delta_v31_v3"] for r in with_u])

    def cell(r, m):
        nd = 2 if m in MEANS else 0
        f = (lambda x: "–" if x is None else (f"{x:.{nd}f}" if nd else f"{int(x)}"))
        if m in TARGET:
            return "–" if r["has_utilization_kpi"] is False else f"{f(r[f'{m}_v3'])} / {f(r[f'{m}_v3.1'])}"
        return " / ".join(f(r[f"{m}_{v}"]) for v in FOLDERS)

    head = (["Configuration", "Utilization KPI"] + [LABELS[m] for m in COUNTS + MEANS]
            + ["Runs affected by the unit defect (v1 / v2)"] + [LABELS[k] for k in FIXES])
    L = ["# Stage 2: batches v1, v2, v3 and v3.1", "",
         "- **Versions:** see `VERSIONS.md`. v3.1 = v3 plus four implementation fixes (direction filter by KPI "
         "category, relative budget enforced, calendar changes given as text parsed or reported, calendar items "
         "in Prosimos period form accepted); same script, configuration and seeds.",
         "- **Cells:** v1 / v2 / v3 / v3.1 (target and fix columns: v3 / v3.1). Counts are out of 5 runs per "
         "configuration; recall is the mean over the evaluated runs, the CTD ratio the median over the runs.",
         "- **Rules:** \"batch rule\" = the scored rule (utilization: target pool below 0.85 and lower than the "
         "baseline); \"GLASS rule\" = GLASS's improved verdict for every KPI.",
         "- **Hour values applied as seconds (v1, v2):** runs flagged `unit_defect` keep their recorded scores.",
         "- **Fix columns:** runs in which each v3.1 fix acted (see the script's docstring; per run in "
         "`stage2_v1_v2_v3_v31_runs.csv`).", "",
         "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows + totals:
        flag = {True: "yes", False: "no"}.get(r["has_utilization_kpi"], "")
        L.append(f"| {r['config_id']} | {flag} | " + " | ".join(cell(r, m) for m in COUNTS + MEANS)
                 + f" | {r['unit_defect_runs_v1']} / {r['unit_defect_runs_v2']} | "
                 + " | ".join(f"{r[f'{k}_v3']} / {r[f'{k}_v3.1']}" for k in FIXES) + " |")
    over = [r for r in with_u if (r["baseline_pool_utilization"] or 0) > 0.85]
    below = [r for r in with_u if r["baseline_pool_utilization"] is not None and r["baseline_pool_utilization"] <= 0.85]
    for v in ("v3", "v3.1"):
        ach = lambda rs: int(sum(r[f"final_util_target_achieved_glass_{v}"] or 0 for r in rs))
        L += ["", f"**Utilization target achieved ({v}, after refinement):** target achieved where the baseline was "
              f"overloaded (>0.85): {ach(over)} of {5 * len(over)}; all {5 * len(with_u)} utilization runs: "
              f"{ach(with_u)} of {5 * len(with_u)}, of which {5 * len(below)} already below 0.85 at baseline."]
    dm = ["final_valid", "final_all_kpis_improved", "final_valid_and_improved", "reps_loop_discarded_better_initial",
          "final_mean_recall", "final_median_improvement_ratio_CTD"]

    def dcell(g, m, a):
        x = g[f"{m}_delta_v31_{a}"]
        return "–" if x is None else (f"{x:+.3f}" if m in MEANS else f"{x:+g}")

    L += ["", "**Per-group change (v3.1 − v3, and v3.1 − v2 in brackets)**", "",
          "| Group | " + " | ".join(LABELS[m] for m in dm) + " |", "|" + "---|" * (len(dm) + 1)]
    for label, g in (("With a utilization KPI", totals[1]), ("Without (control)", totals[2])):
        L.append(f"| {label} | " + " | ".join(f"{dcell(g, m, 'v3')} ({dcell(g, m, 'v2')})" for m in dm) + " |")
    L += ["", "**Control check v3 → v3.1** (configurations without a utilization KPI, outcome: valid and all KPIs "
          "improved, final):", "",
          f"- **Sign test** over the {len(without_u)} configurations: {n} differ, {k} of them higher in v3.1; "
          f"two-sided p = {p:.3f}.",
          f"- **Pooled run-level Fisher exact test:** {a}/{runs} in v3 vs {b}/{runs} in v3.1; p = {fisher_p:.3f}.",
          f"- **Reading:** the control configurations are "
          f"{'statistically indistinguishable' if min(p, fisher_p) >= 0.05 else 'NOT statistically indistinguishable'}"
          " between v3 and v3.1 at the 5 % level. The v3.1 fixes can act on them too (relative budget, calendar "
          "changes), so this checks that they did not shift them. With 6 configurations the sign test detects only "
          "large systematic shifts; the LLM is not seeded.",
          f"- **For reference, configurations with a utilization KPI:** sign test {nu} differ, {ku} higher in v3.1, "
          f"p = {pu:.3f}.", ""]
    (RES / "stage2_v1_v2_v3_v31.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()

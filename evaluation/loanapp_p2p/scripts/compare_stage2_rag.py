"""
Compare the two arms of the Stage-2 RAG comparison on GLASS v3.2 (V_full: knowledge base on; V_no_kb: knowledge
base off in generation and feedback, the real-life ablation's switch) per configuration, with batch v3.1 for
reference (see results/VERSIONS.md).

Sources: the verified stage2_per_config.csv and stage2_summary.csv and the records of
results/stage2_results_v32_rag/V_full, results/stage2_results_v32_rag/V_no_kb and results/stage2_results_v3_1.
Writes results/stage2_rag_comparison.csv and .md, and results/stage2_rag_comparison_runs.csv (one row per run
and arm, final phase).

Measures per configuration (counts out of 5 runs; cells V_full v3.2 / V_no_kb v3.2 / v3.1):
  valid final, all KPIs improved final (batch rule), valid and improved final, utilization target achieved final
  (GLASS: at most 0.85; batch: target pool below 0.85), recall final (mean over the evaluated runs), CTD ratio
  to healthy final (median over the runs), loop discarded a better initial, loop kept the baseline (v3.2:
  no_improving_scenario), runs with at least one direction_inconsistent rejection and rejections per run
  (validator diagnostics over all patch attempts of all iterations; v3.1: the same diagnostic was a warning).
Tests V_full vs V_no_kb: for counts a pooled run-level Fisher exact test (all 70 runs, and per group) and a
two-sided sign test over the configurations (ties dropped); for recall, CTD ratio and rejections per run a sign
test over the configurations.

Usage (repo root): python evaluation/loanapp_p2p/scripts/compare_stage2_rag.py
"""
from __future__ import annotations

import csv
import json
from math import comb
from pathlib import Path
from statistics import mean, median

BASE = Path(__file__).resolve().parent.parent
RES = BASE / "results"
FOLDERS = {"V_full": RES / "stage2_results_v32_rag" / "V_full", "V_no_kb": RES / "stage2_results_v32_rag" / "V_no_kb",
           "v3.1": RES / "stage2_results_v3_1"}
ARMS = ("V_full", "V_no_kb")
COUNTS = ["final_valid", "final_all_kpis_improved", "final_valid_and_improved",
          "final_util_target_achieved_glass", "final_util_target_achieved_batch",
          "reps_loop_discarded_better_initial", "reps_loop_kept_baseline", "reps_direction_rejection"]
MEANS = ["final_mean_recall", "final_median_improvement_ratio_CTD", "direction_rejections_per_run"]
TARGET = {"final_util_target_achieved_glass", "final_util_target_achieved_batch"}
LABELS = {"final_valid": "Valid final",
          "final_all_kpis_improved": "All KPIs improved final",
          "final_valid_and_improved": "Valid and improved final",
          "final_util_target_achieved_glass": "Utilization target achieved final, GLASS",
          "final_util_target_achieved_batch": "Utilization target achieved final, batch",
          "reps_loop_discarded_better_initial": "Loop discarded better initial",
          "reps_loop_kept_baseline": "Loop kept the baseline",
          "reps_direction_rejection": "Runs with a direction_inconsistent rejection",
          "final_mean_recall": "Recall final (mean)",
          "final_median_improvement_ratio_CTD": "CTD ratio to healthy final (median)",
          "direction_rejections_per_run": "direction_inconsistent rejections per run"}


def sign_test(diffs: list[float]) -> tuple[int, int, float]:
    nonzero = [d for d in diffs if d]
    n, k = len(nonzero), sum(d > 0 for d in nonzero)
    if n == 0:
        return 0, 0, 1.0
    tail = sum(comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n
    return n, k, min(1.0, 2 * tail)


def direction_rejections(rec: dict) -> int:
    return sum(d.get("category") == "direction_inconsistent"
               for it in (rec.get("loop") or {}).get("iterations") or [] for a in it.get("all_patch_attempts") or []
               for d in ((a.get("result") or {}).get("diagnostics") or []))


def main() -> None:
    from scipy.stats import fisher_exact
    config = json.loads((BASE / "setup" / "stage2_config.json").read_text(encoding="utf-8"))
    util = {c["config_id"]: "UT" in c["expected_kpi_kinds"] for c in config["configurations"]}
    order = sorted(util, key=lambda c: (c.split("_")[0], len(c.split("_")[1]), c))
    per = {v: {r["config_id"]: r for r in csv.DictReader(open(f / "stage2_per_config.csv", encoding="utf-8"))}
           for v, f in FOLDERS.items()}
    summ = {v: [r for r in csv.DictReader(open(f / "stage2_summary.csv", encoding="utf-8")) if r["phase"] == "final"]
            for v, f in FOLDERS.items()}
    recs = {v: {(r["config_id"], str(r["rep"])): r for r in
                (json.loads(p.read_text(encoding="utf-8")) for p in (f / "raw").glob("*/rep_*.json"))}
            for v, f in FOLDERS.items()}
    base_pool = {}
    for cid in order:
        rec = recs["v3.1"].get((cid, "1"), {})
        base_pool[cid] = round(mean(rec["baseline_pool_utilization"]), 4) if rec.get("baseline_pool_utilization") else None
    num = lambda x: float(x) if x not in ("", None) else None

    run_rows = []
    for v in FOLDERS:
        for r in summ[v]:
            rec = recs[v][(r["config_id"], r["rep"])]
            run_rows.append({"arm": v, "glass_commit": rec.get("glass_commit", "")[:7], "config_id": r["config_id"],
                             "rep": r["rep"], "status": r["status"], "valid_final": r["valid"],
                             "all_kpis_improved_final": r["all_kpis_improved"],
                             "util_target_achieved_glass_final": r.get("util_target_achieved_glass", ""),
                             "util_target_achieved_batch_final": r.get("util_target_achieved_batch", ""),
                             "recall_final": r["recall"], "improvement_ratio_CTD_final": r["improvement_ratio_CTD"],
                             "loop_discarded_better_initial": r.get("loop_discarded_better_initial", ""),
                             "loop_kept_baseline": bool((rec.get("loop") or {}).get("no_improving_scenario")),
                             "direction_rejections": direction_rejections(rec),
                             "kb_stub_calls": (rec.get("kb_switch") or {}).get("kb_stub_calls", "")})
    run_rows.sort(key=lambda x: (order.index(x["config_id"]), int(x["rep"]), list(FOLDERS).index(x["arm"])))
    with open(RES / "stage2_rag_comparison_runs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(run_rows[0]))
        w.writeheader()
        w.writerows(run_rows)

    def runs_of(v, cids):
        return [x for x in run_rows if x["arm"] == v and x["config_id"] in cids]

    def ctd_values(v, cids):
        return [float(x["improvement_ratio_CTD_final"]) for x in runs_of(v, cids)
                if x["status"] == "ok" and x["improvement_ratio_CTD_final"] not in ("", None)]

    rows = []
    for cid in order:
        row = {"config_id": cid, "has_utilization_kpi": util[cid], "baseline_pool_utilization": base_pool[cid]}
        for m in COUNTS + MEANS:
            for v in FOLDERS:
                rr = runs_of(v, {cid})
                if m == "final_median_improvement_ratio_CTD":
                    vals = ctd_values(v, {cid})
                    row[f"{m}_{v}"] = round(median(vals), 4) if vals else None
                elif m == "reps_loop_kept_baseline":
                    row[f"{m}_{v}"] = sum(x["loop_kept_baseline"] for x in rr)
                elif m == "reps_direction_rejection":
                    row[f"{m}_{v}"] = sum(x["direction_rejections"] > 0 for x in rr)
                elif m == "direction_rejections_per_run":
                    row[f"{m}_{v}"] = round(mean(x["direction_rejections"] for x in rr), 3) if rr else None
                else:
                    row[f"{m}_{v}"] = num(per[v][cid].get(m))
            x, y = row[f"{m}_V_full"], row[f"{m}_V_no_kb"]
            row[f"{m}_delta_full_minus_no_kb"] = round(x - y, 3) if x is not None and y is not None else None
        rows.append(row)

    def group(rs, label):
        cids = {r["config_id"] for r in rs}
        out = {"config_id": label, "has_utilization_kpi": "", "baseline_pool_utilization": None}
        for key in rows[0]:
            if key in out:
                continue
            vals = [r[key] for r in rs if r[key] is not None]
            if any(key.startswith(m) for m in MEANS):
                out[key] = round(sum(vals) / len(vals), 3) if vals else None
            else:
                out[key] = round(sum(vals), 3) if vals else None
        for v in FOLDERS:                         # group median over the runs; rejections per run over the runs
            vals = ctd_values(v, cids)
            out[f"final_median_improvement_ratio_CTD_{v}"] = round(median(vals), 3) if vals else None
            rr = runs_of(v, cids)
            out[f"direction_rejections_per_run_{v}"] = round(mean(x["direction_rejections"] for x in rr), 3) if rr else None
        for m in ("final_median_improvement_ratio_CTD", "direction_rejections_per_run"):
            x, y = out[f"{m}_V_full"], out[f"{m}_V_no_kb"]
            out[f"{m}_delta_full_minus_no_kb"] = round(x - y, 3) if None not in (x, y) else None
        return out

    with_u = [r for r in rows if r["has_utilization_kpi"]]
    without_u = [r for r in rows if not r["has_utilization_kpi"]]
    totals = [group(rows, "TOTAL (70 runs per arm)"), group(with_u, "configurations with a utilization KPI"),
              group(without_u, "configurations without a utilization KPI (control)")]
    with open(RES / "stage2_rag_comparison.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows + totals)

    tests = []                                # (group label, measure, Fisher / sign test texts)
    for label, rs in (("all 14 configurations", rows), ("with a utilization KPI", with_u),
                      ("without a utilization KPI (control)", without_u)):
        for m in COUNTS + MEANS:
            if m in TARGET and not any(r["has_utilization_kpi"] for r in rs):
                continue
            diffs = [r[f"{m}_delta_full_minus_no_kb"] for r in rs if r[f"{m}_delta_full_minus_no_kb"] is not None
                     and not (m in TARGET and not r["has_utilization_kpi"])]
            n, k, p = sign_test(diffs)
            fisher = ""
            if m in COUNTS:
                runs = 5 * len([r for r in rs if not (m in TARGET and not r["has_utilization_kpi"])])
                a = int(sum(r[f"{m}_V_full"] or 0 for r in rs))
                b = int(sum(r[f"{m}_V_no_kb"] or 0 for r in rs))
                fisher = (a, b, runs, fisher_exact([[a, runs - a], [b, runs - b]])[1])
            tests.append({"group": label, "measure": m, "sign_n": n, "sign_k": k, "sign_p": p, "fisher": fisher})

    def cell(r, m):
        nd = 2 if m in MEANS else 0
        f = (lambda x: "–" if x is None else (f"{x:.{nd}f}" if nd else f"{int(x)}"))
        if m in TARGET and r["has_utilization_kpi"] is False:
            return "–"
        return " / ".join(f(r[f"{m}_{v}"]) for v in FOLDERS)

    commits = {v: sorted({x["glass_commit"] for x in run_rows if x["arm"] == v}) for v in FOLDERS}
    shas = {v: sorted({rec.get("script_sha256", "")[:8] for rec in recs[v].values()}) for v in FOLDERS}
    head = ["Configuration", "Utilization KPI"] + [LABELS[m] for m in COUNTS + MEANS]
    L = ["# Stage 2: RAG comparison on GLASS v3.2 (V_full vs V_no_kb), v3.1 for reference", "",
         f"- **Arms:** V_full = the Stage-2 batch on v3.2, knowledge base on; V_no_kb = the same batch with the "
         f"knowledge base off in generation and feedback (the real-life ablation's switch). Records: GLASS "
         f"{', '.join(commits['V_full'])} / {', '.join(commits['V_no_kb'])}; batch script {', '.join(shas['V_full'])} / "
         f"{', '.join(shas['V_no_kb'])}. v3.1: GLASS {', '.join(commits['v3.1'])}, script {', '.join(shas['v3.1'])}. "
         "Same configuration, KPI objects, 5 repetitions and seeds 42–51 in all three.",
         "- **Cells:** V_full v3.2 / V_no_kb v3.2 / v3.1. Counts are out of 5 runs per configuration; recall is the "
         "mean over the evaluated runs, the CTD ratio the median over the runs, rejections per run the mean.",
         "- **Kept baseline (v3.2):** when no iteration reaches a positive score, GLASS keeps the baseline; the run has "
         "no final scenario and is scored not valid. It counts as \"loop discarded better initial\" only if the "
         "initial was valid and improved at least one KPI on 10 seeds.",
         "- **direction_inconsistent:** v3.2 rejects a modification whose stated direction contradicts its values; in "
         "v3.1 the same diagnostic was a warning (none recorded in v3.1).", "",
         "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows + totals:
        flag = {True: "yes", False: "no"}.get(r["has_utilization_kpi"], "")
        L.append(f"| {r['config_id']} | {flag} | " + " | ".join(cell(r, m) for m in COUNTS + MEANS) + " |")
    over = [r for r in with_u if (r["baseline_pool_utilization"] or 0) > 0.85]
    below = [r for r in with_u if r["baseline_pool_utilization"] is not None and r["baseline_pool_utilization"] <= 0.85]
    L += ["", "**Utilization target achieved (GLASS, after refinement):**"]
    for v in FOLDERS:
        ach = lambda rs: int(sum(r[f"final_util_target_achieved_glass_{v}"] or 0 for r in rs))
        L.append(f"- {v}: where the baseline was overloaded (>0.85) {ach(over)} of {5 * len(over)}; all "
                 f"{5 * len(with_u)} utilization runs {ach(with_u)} of {5 * len(with_u)}, of which {5 * len(below)} "
                 "already below 0.85 at baseline.")
    L += ["", "**V_full vs V_no_kb (v3.2).** Counts: pooled run-level Fisher exact test (V_full / V_no_kb of the "
          "runs). All measures: two-sided sign test over the configurations (configurations that differ, of them "
          "higher in V_full).", "",
          "| Group | Measure | V_full | V_no_kb | Fisher p | Sign test | Sign p |", "|---|---|---|---|---|---|---|"]
    gtot = {"all 14 configurations": totals[0], "with a utilization KPI": totals[1],
            "without a utilization KPI (control)": totals[2]}
    for t in tests:
        g, m = gtot[t["group"]], t["measure"]
        if t["fisher"]:
            a, b, runs, fp = t["fisher"]
            fa, fb, fps = f"{a}/{runs}", f"{b}/{runs}", f"{fp:.3f}"
        else:
            two = lambda x: "–" if x is None else f"{x:.2f}"
            fa, fb, fps = two(g[f"{m}_V_full"]), two(g[f"{m}_V_no_kb"]), "–"
        L.append(f"| {t['group']} | {LABELS[m]} | {fa} | {fb} | {fps} | {t['sign_k']} of {t['sign_n']} | "
                 f"{t['sign_p']:.3f} |")
    sig = [t for t in tests if (t["fisher"] and t["fisher"][3] < 0.05) or t["sign_p"] < 0.05]
    L += ["", "- **Significant at 5 %:** " + ("; ".join(f"{LABELS[t['measure']]} ({t['group']})" for t in sig)
                                             if sig else "none") + ".",
          "- The LLM is not seeded; each arm is one draw of 5 repetitions per configuration. With 14 configurations "
          "the sign test detects only large systematic shifts.", ""]
    (RES / "stage2_rag_comparison.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()

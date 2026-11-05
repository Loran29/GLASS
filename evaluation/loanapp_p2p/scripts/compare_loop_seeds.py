"""
Compare the two arms of the loop replications experiment (LoanApp, GLASS with loop_seeds): seeds1 = the
refinement loop judges each iteration on one simulation run (the default), seeds5 = on the mean of five paired
runs (seeds 42-46). Everything else is the Stage-2 batch as in v3.2: same configuration, KPI objects,
5 repetitions and the same final evaluation on 10 seeds (42-51).

Sources: the verified stage2_summary.csv / stage2_per_config.csv and the records of
results/stage2_loop_seeds/seeds1 and results/stage2_loop_seeds/seeds5.
Writes results/stage2_loop_seeds/loop_seeds_comparison.csv and .md, and loop_seeds_runs.csv (one row per run and arm).

Measures per configuration (counts out of 5 runs; cells seeds1 / seeds5):
  valid final, all KPIs improved final (batch rule), valid and improved final, utilization target achieved final
  (GLASS: at most 0.85; batch: target pool below 0.85), loop discarded a better initial (reporting rule),
  early stops after iteration 1 (the loop stopped because its check found all targets met) and, of those, the
  ones whose 10-seed final has not all KPIs improved; iterations used (mean), run time (median, minutes),
  LLM calls per run (mean).
Tests seeds1 vs seeds5: for counts a pooled run-level Fisher exact test (35 runs per arm, or 20 for the
utilization target) and a two-sided sign test over the configurations; for the other measures a sign test.
Ratios: success (valid and all KPIs improved final) with 1 loop seed as a percentage of success with 5;
run time: median over all runs of seeds5 / seeds1.

Usage (repo root): python evaluation/loanapp_p2p/scripts/compare_loop_seeds.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_stage2_rag import sign_test  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "results" / "stage2_loop_seeds"
ARMS = {"seeds1": OUT / "seeds1", "seeds5": OUT / "seeds5"}
COUNTS = ["final_valid", "final_all_kpis_improved", "final_valid_and_improved",
          "final_util_target_achieved_glass", "final_util_target_achieved_batch",
          "reps_loop_discarded_better_initial", "early_stops", "early_stops_disagreeing"]
MEANS = ["mean_iterations_used", "median_run_time_min", "median_loop_time_min", "mean_llm_calls"]
TARGET = {"final_util_target_achieved_glass", "final_util_target_achieved_batch"}
LABELS = {"final_valid": "Valid final",
          "final_all_kpis_improved": "All KPIs improved final",
          "final_valid_and_improved": "Valid and improved final",
          "final_util_target_achieved_glass": "Utilization target achieved final, GLASS",
          "final_util_target_achieved_batch": "Utilization target achieved final, batch",
          "reps_loop_discarded_better_initial": "Loop discarded better initial",
          "early_stops": "Early stops after iteration 1",
          "early_stops_disagreeing": "Early stops whose 10-seed final has not all KPIs improved",
          "mean_iterations_used": "Iterations used (mean)",
          "median_run_time_min": "Run time, minutes (median)",
          "median_loop_time_min": "Refinement loop time, minutes (median)",
          "mean_llm_calls": "LLM calls per run (mean)"}
truth = lambda x: str(x) == "True"


def main() -> None:
    from scipy.stats import fisher_exact
    config = json.loads((BASE / "setup" / "stage2_config.json").read_text(encoding="utf-8"))
    cfgs = [c for c in config["configurations"] if c["process"] == "LoanApp"]
    util = {c["config_id"]: "UT" in c["expected_kpi_kinds"] for c in cfgs}
    order = sorted(util, key=lambda c: (len(c.split("_")[1]), c))
    per = {a: {r["config_id"]: r for r in csv.DictReader(open(f / "stage2_per_config.csv", encoding="utf-8"))}
           for a, f in ARMS.items()}
    summ = {a: {(r["config_id"], r["rep"]): r for r in csv.DictReader(open(f / "stage2_summary.csv", encoding="utf-8"))
                if r["phase"] == "final"} for a, f in ARMS.items()}
    recs = {a: {(r["config_id"], str(r["rep"])): r for r in
                (json.loads(p.read_text(encoding="utf-8")) for p in (f / "raw").glob("*/rep_*.json"))}
            for a, f in ARMS.items()}

    run_rows = []
    for a in ARMS:
        for (cid, rep), rec in recs[a].items():
            r = summ[a].get((cid, rep), {})
            loop = rec.get("loop") or {}
            its = loop.get("iterations") or []
            early = len(its) == 1 and loop.get("best_iteration") == 1 and not loop.get("no_improving_scenario")
            run_rows.append({
                "arm": a, "loop_seeds": rec.get("loop_seeds"), "glass_commit": rec.get("glass_commit", "")[:7],
                "config_id": cid, "rep": rep, "status": rec["status"], "iterations_used": len(its),
                "final_iteration": loop.get("best_iteration"),
                "loop_kept_baseline": bool(loop.get("no_improving_scenario")),
                "valid_final": r.get("valid", ""), "all_kpis_improved_final": r.get("all_kpis_improved", ""),
                "util_target_achieved_glass_final": r.get("util_target_achieved_glass", ""),
                "util_target_achieved_batch_final": r.get("util_target_achieved_batch", ""),
                "loop_discarded_better_initial": r.get("loop_discarded_better_initial", ""),
                "early_stop": early,
                "early_stop_disagreeing": early and not truth(r.get("all_kpis_improved")),
                "run_time_min": round(rec["elapsed_s"] / 60, 2), "llm_calls": len(rec.get("llm_calls") or []),
                "loop_time_s": loop.get("total_time_seconds")})
    run_rows.sort(key=lambda x: (order.index(x["config_id"]), int(x["rep"]), list(ARMS).index(x["arm"])))
    with open(OUT / "loop_seeds_runs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(run_rows[0]))
        w.writeheader()
        w.writerows(run_rows)

    def runs_of(a, cids):
        return [x for x in run_rows if x["arm"] == a and x["config_id"] in cids]

    def measure(a, cids, m):
        rr = runs_of(a, cids)
        if m == "early_stops":
            return sum(x["early_stop"] for x in rr)
        if m == "early_stops_disagreeing":
            return sum(x["early_stop_disagreeing"] for x in rr)
        if m == "mean_iterations_used":
            return round(mean(x["iterations_used"] for x in rr), 2)
        if m == "median_run_time_min":
            return round(median(x["run_time_min"] for x in rr), 2)
        if m == "median_loop_time_min":
            return round(median((x["loop_time_s"] or 0) / 60 for x in rr), 2)
        if m == "mean_llm_calls":
            return round(mean(x["llm_calls"] for x in rr), 2)
        if m in TARGET and not any(util[c] for c in cids):
            return None
        return sum(float(per[a][c].get(m) or 0) for c in cids if not (m in TARGET and not util[c]))

    rows = []
    for label, cids in [(c, {c}) for c in order] + [
            ("TOTAL (35 runs per arm)", set(order)),
            ("configurations with a utilization KPI", {c for c in order if util[c]}),
            ("configurations without a utilization KPI (control)", {c for c in order if not util[c]})]:
        row = {"config_id": label, "has_utilization_kpi": util.get(label, "")}
        for m in COUNTS + MEANS:
            for a in ARMS:
                row[f"{m}_{a}"] = measure(a, cids, m)
            x, y = row[f"{m}_seeds1"], row[f"{m}_seeds5"]
            row[f"{m}_delta_1_minus_5"] = round(x - y, 3) if None not in (x, y) else None
        rows.append(row)
    with open(OUT / "loop_seeds_comparison.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    per_cfg = rows[:len(order)]
    tests = []
    GROUP_ROW = {"all 7 configurations": "TOTAL (35 runs per arm)",
                 "with a utilization KPI": "configurations with a utilization KPI",
                 "without a utilization KPI (control)": "configurations without a utilization KPI (control)"}
    for glabel, rs in (("all 7 configurations", per_cfg), ("with a utilization KPI", [r for r in per_cfg if util[r["config_id"]]]),
                       ("without a utilization KPI (control)", [r for r in per_cfg if not util[r["config_id"]]])):
        for m in COUNTS + MEANS:
            if m in TARGET and not any(util[r["config_id"]] for r in rs):
                continue
            diffs = [r[f"{m}_delta_1_minus_5"] for r in rs if r[f"{m}_delta_1_minus_5"] is not None]
            n, k, p = sign_test(diffs)
            fisher = ""
            if m == "early_stops_disagreeing":       # out of each arm's early stops
                e1 = int(sum(r["early_stops_seeds1"] for r in rs))
                e5 = int(sum(r["early_stops_seeds5"] for r in rs))
                a1 = int(sum(r[f"{m}_seeds1"] for r in rs))
                a5 = int(sum(r[f"{m}_seeds5"] for r in rs))
                if e1 and e5:
                    fisher = (f"{a1}/{e1}", f"{a5}/{e5}", "", fisher_exact([[a1, e1 - a1], [a5, e5 - a5]])[1])
            elif m in COUNTS:
                runs = 5 * len([r for r in rs if not (m in TARGET and not util[r["config_id"]])])
                a1 = int(sum(r[f"{m}_seeds1"] or 0 for r in rs))
                a5 = int(sum(r[f"{m}_seeds5"] or 0 for r in rs))
                fisher = (f"{a1}/{runs}", f"{a5}/{runs}", runs, fisher_exact([[a1, runs - a1], [a5, runs - a5]])[1])
            else:
                g = next(r for r in rows if r["config_id"] == GROUP_ROW[glabel])
                fisher = (f"{g[f'{m}_seeds1']:g}", f"{g[f'{m}_seeds5']:g}", "", None)
            tests.append({"group": glabel, "measure": m, "sign_n": n, "sign_k": k, "sign_p": p, "fisher": fisher})

    tot = rows[len(order)]
    s1, s5 = tot["final_valid_and_improved_seeds1"], tot["final_valid_and_improved_seeds5"]
    a1, a5 = tot["final_all_kpis_improved_seeds1"], tot["final_all_kpis_improved_seeds5"]
    rt1 = median(x["run_time_min"] for x in runs_of("seeds1", set(order)))
    rt5 = median(x["run_time_min"] for x in runs_of("seeds5", set(order)))
    ratios = {"success_1_as_pct_of_5": round(100 * s1 / s5, 1) if s5 else None,
              "all_improved_1_as_pct_of_5": round(100 * a1 / a5, 1) if a5 else None,
              "runtime_ratio_5_over_1": round(rt5 / rt1, 2) if rt1 else None,
              "median_run_time_min": (rt1, rt5),
              "loop_time_ratio_5_over_1": round(tot["median_loop_time_min_seeds5"] / tot["median_loop_time_min_seeds1"], 2)
              if tot["median_loop_time_min_seeds1"] else None,
              "median_loop_time_min": (tot["median_loop_time_min_seeds1"], tot["median_loop_time_min_seeds5"])}
    write_md(rows, order, util, tests, ratios, run_rows, recs)
    print(json.dumps(ratios))


def write_md(rows, order, util, tests, ratios, run_rows, recs) -> None:
    def cell(r, m):
        x, y = r[f"{m}_seeds1"], r[f"{m}_seeds5"]
        if x is None and y is None:
            return "–"
        f = (lambda v: f"{v:g}") if m in MEANS else (lambda v: f"{int(v)}")
        return f"{f(x)} / {f(y)}"

    commits = {a: sorted({x["glass_commit"] for x in run_rows if x["arm"] == a}) for a in ARMS}
    shas = {a: sorted({r.get("script_sha256", "")[:8] for r in recs[a].values()}) for a in ARMS}
    L = ["# Loop replications experiment: 1 vs 5 seeds per refinement iteration (LoanApp)", "",
         "- **Arms:** seeds1 = the refinement loop judges each iteration on one simulation run (seed 42, the "
         "default); seeds5 = on the mean KPIs of five paired runs (seeds 42–46) of the baseline and the proposal "
         "(`run_iterative_evaluation(..., loop_seeds=5)`). Everything else as in the Stage-2 batch on v3.2: the "
         "seven LoanApp configurations of `setup/stage2_config.json`, 5 repetitions, the same KPI objects, up to 4 "
         "iterations, the same final evaluation on 10 seeds (42–51).",
         f"- **Code:** GLASS {', '.join(commits['seeds1'])} / {', '.join(commits['seeds5'])}; batch script "
         f"{', '.join(shas['seeds1'])} / {', '.join(shas['seeds5'])}.",
         "- **Cells:** seeds1 / seeds5. Counts are out of 5 runs per configuration (utilization target: only the "
         "configurations with a utilization KPI).",
         "- **Early stop:** the loop stopped after iteration 1 because its own check (one run, or the mean of five) "
         "found every target met. *Disagreeing:* the 10-seed final evaluation of that scenario does not have all KPIs "
         "improved (batch rule).", "",
         "| Configuration | Utilization KPI | " + " | ".join(LABELS[m] for m in COUNTS + MEANS) + " |",
         "|---|---|" + "---|" * len(COUNTS + MEANS)]
    for r in rows:
        name = r["config_id"] if r["config_id"] in order else f"**{r['config_id']}**"
        u = ("yes" if r["has_utilization_kpi"] else "no") if r["config_id"] in order else ""
        L.append(f"| {name} | {u} | " + " | ".join(cell(r, m) for m in COUNTS + MEANS) + " |")
    L += ["", "## Success and run time", "",
          f"- **Success with 1 loop seed as a percentage of success with 5:** {ratios['success_1_as_pct_of_5']} % "
          f"(valid and all KPIs improved final: {rows[len(order)]['final_valid_and_improved_seeds1']:.0f} vs "
          f"{rows[len(order)]['final_valid_and_improved_seeds5']:.0f} of 35); all KPIs improved final: "
          f"{ratios['all_improved_1_as_pct_of_5']} %.",
          f"- **Run time:** median {ratios['median_run_time_min'][0]} min (seeds1) vs {ratios['median_run_time_min'][1]} "
          f"min (seeds5), ratio {ratios['runtime_ratio_5_over_1']} (seeds5 / seeds1; 4 workers in both arms). The "
          f"refinement loop alone: median {ratios['median_loop_time_min'][0]} vs {ratios['median_loop_time_min'][1]} min, "
          f"ratio {ratios['loop_time_ratio_5_over_1']}; the rest of a run is mostly the 10-seed final evaluation, the "
          "same in both arms. Five runs per iteration cost more simulation, but seeds5 used fewer iterations and LLM "
          "calls.", "",
          "## Tests (seeds1 vs seeds5)", "",
          "| Group | Measure | seeds1 | seeds5 | Fisher p | Sign test: configurations seeds1 > seeds5 of non-tied | Sign test p |",
          "|---|---|---|---|---|---|---|"]
    for t in tests:
        f = t["fisher"]
        a1, a5, fp = (f[0], f[1], f"{f[3]:.3f}" if f[3] is not None else "–") if f else ("", "", "–")
        L.append(f"| {t['group']} | {LABELS[t['measure']]} | {a1} | {a5} | {fp} | {t['sign_k']} of {t['sign_n']} | "
                 f"{t['sign_p']:.3f} |")
    (OUT / "loop_seeds_comparison.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

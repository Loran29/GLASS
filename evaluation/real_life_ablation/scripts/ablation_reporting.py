"""
Reporting for the real-life ablation, Stage 2 (reads only the run records; scores are not changed).

Writes to results/stage2/:
  ablation_summary.csv      one row per run (configuration x variant x repetition): the final scenario's
                            (the loop's best iteration) primary and secondary measures, plus the initial's
  ablation_per_cell.csv     per dataset x goal x variant: counts and means over the repetitions
  ablation_per_variant.csv  per variant, over all configurations and repetitions, in the scoring order
                            scenario produced -> valid -> executable -> merge stability > 0 -> all KPIs improved
                            -> DHR -> NIS -> secondary; and V_full minus each other variant:
                            rates: difference in percentage points, Fisher's exact test (two-sided);
                            DHR, NIS and the other means: mean difference over the pairs (same configuration
                            and repetition, both values present) and a two-sided sign test (binomial, ties
                            dropped)
Runs that ended without a scenario (status no_initial_proposal, app_error, llm_error) count as not produced
(denominator = all runs of the cell / variant); their DHR, NIS and distances are missing and left out of means.

Usage (repo root): python evaluation/real_life_ablation/scripts/ablation_reporting.py [--results DIR]
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

from scipy import stats

BASE = Path(__file__).resolve().parent.parent
VARIANTS = ["V_full", "V_no_log", "V_no_context", "V_no_kb", "V_no_refine", "V_old_refine"]
RATES = [("scenario_produced", "Scenario produced"), ("valid", "Valid"), ("executable", "Executable"),
         ("merge_stability_positive", "Merge stability > 0"), ("all_kpis_improved", "All KPIs improved")]
MEANS = [("DHR", "DHR"), ("NIS", "NIS")]
SECONDARY_RATES = [("admissible", "Admissible (valid, executable, merge stability > 0)"),
                   ("admissible_and_all_improved", "Admissible and all KPIs improved"),
                   ("ct_improved", "Average Cycle Time improved (G1, G1G2)"),
                   ("x_improved", "X waiting time improved (G2, G1G2)"),
                   ("any_kpi_significant_improvement", "At least one KPI improved with p < 0.05"),
                   ("loop_discarded_better_initial", "Loop discarded a better initial"),
                   ("no_improving_scenario", "Loop kept the baseline (no_improving_scenario)"),
                   ("any_direction_rejection", "At least one direction_inconsistent rejection"),
                   ("demand_proposed", "inter_arrival_time proposed (skipped by the merger)"),
                   ("rate_changed", "Hourly rate changed")]
SECONDARY_MEANS = [("merge_stability", "Merge stability (applied / proposed)"),
                   ("glass_merge_stability", "Merge stability, GLASS's value"), ("share_targeting_x", "Share of modifications on X"),
                   ("n_modifications", "Modifications"), ("iterations_used", "Iterations used"),
                   ("direction_inconsistent_rejections", "direction_inconsistent rejections per run"),
                   ("CTD", "CTD to the test log"), ("RED", "RED to the test log"), ("CWD", "CWD to the test log")]


def phase_row(ev: dict, x_kpi: str | None, prefix: str) -> dict:
    kpis = ev.get("kpis") or []
    by = {k["kpi"]: k for k in kpis}
    stab = ev.get("merge_stability")
    produced = bool(ev.get("scenario_produced"))
    row = {"scenario_produced": produced, "valid": bool(ev.get("valid")), "executable": bool(ev.get("executable")),
           "merge_stability": stab, "merge_stability_positive": produced and (stab or 0) > 0,
           "glass_merge_stability": ev.get("glass_merge_stability"),
           "admissible": bool(ev.get("admissible")), "all_kpis_improved": bool(ev.get("all_kpis_improved")),
           "DHR": ev.get("DHR"), "NIS": ev.get("NIS"),
           "violations": " | ".join(ev.get("violations") or []),
           "demand_proposed": bool(ev.get("demand_proposed")), "rate_changed": bool(ev.get("rate_changed"))}
    row["admissible_and_all_improved"] = row["admissible"] and row["all_kpis_improved"]
    row["any_kpi_significant_improvement"] = any(k.get("glass_improved") is True and k.get("significant") is True
                                                 for k in kpis)
    cp = ev.get("change_profile") or {}
    row["n_modifications"] = cp.get("n_modifications")
    row["share_targeting_x"] = cp.get("share_targeting_x")
    row["parameter_types"] = json.dumps(cp.get("per_parameter_type") or {}, sort_keys=True)
    for m in ("CTD", "RED", "CWD"):
        row[m] = (ev.get("distance_to_test_log") or {}).get(m)
    for i, k in enumerate(kpis[:2], 1):
        row[f"kpi{i}"] = k["kpi"]
        row[f"kpi{i}_pct_change"] = k.get("pct_change")
        row[f"kpi{i}_p_value"] = k.get("p_value")
        row[f"kpi{i}_significant"] = k.get("significant")
        row[f"kpi{i}_improved"] = k.get("glass_improved")
    return {f"{prefix}{k}": v for k, v in row.items()}


def run_rows(results: Path, config: dict) -> list[dict]:
    cfgs = {c["config_id"]: c for c in config["configurations"]}
    rows = []
    for path in sorted((results / "raw").glob("*/rep_*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        cfg = cfgs[rec["config_id"]]
        ev = rec.get("evaluation") or {}
        exp = cfg["expected_measurable_as"]
        row = {"config_id": rec["config_id"], "dataset": rec["dataset"], "goal": rec["goal"], "variant": rec["variant"],
               "rep": rec["rep"], "status": rec["status"],
               **{f"switch_{k}": v for k, v in rec["switches"].items()},
               "iterations_used": (rec.get("loop") or {}).get("iterations_used"),
               "final_iteration": (rec.get("loop") or {}).get("best_iteration"),
               "loop_discarded_better_initial": bool(rec.get("loop_discarded_better_initial")),
               "loop_discarded_condition": rec.get("loop_discarded_condition") or "",
               "no_improving_scenario": bool((rec.get("loop") or {}).get("no_improving_scenario")),
               "candidate_iteration": (ev.get("final") or {}).get("candidate_iteration") or "",
               "direction_inconsistent_rejections": rec.get("direction_inconsistent_rejections", 0),
               "any_direction_rejection": (rec.get("direction_inconsistent_rejections") or 0) > 0}
        row.update(phase_row(ev.get("final") or {}, None, ""))
        # the KPI table rows carry the KPI names passed; match the expected KPIs through the passed objects
        passed = {k["name"]: k["measurable_as"] for k in rec["stage1_result_passed"]["kpis"]}
        kpis = {passed.get(k["kpi"], k["kpi"]): k for k in (ev.get("final") or {}).get("kpis") or []}
        ct, x = "Average Cycle Time", next((e for e in exp if e != "Average Cycle Time"), None)
        row["ct_improved"] = (kpis.get(ct, {}).get("glass_improved") is True) if ct in exp else ""
        row["x_improved"] = (kpis.get(x, {}).get("glass_improved") is True) if x else ""
        row.update(phase_row(ev.get("initial") or {}, None, "initial_"))
        rows.append(row)
    return rows


def rate(rows: list[dict], key: str) -> tuple[int, int]:
    vals = [r[key] for r in rows if r[key] != ""]
    return sum(v is True for v in vals), len(vals)


def avg(rows: list[dict], key: str):
    vals = [r[key] for r in rows if r[key] not in ("", None)]
    return round(mean(vals), 4) if vals else ""


def per_cell(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        groups[(r["dataset"], r["goal"], r["variant"])].append(r)
    out = []
    for (ds, goal, variant), rr in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], VARIANTS.index(kv[0][2]))):
        row = {"dataset": ds, "goal": goal, "variant": variant, "runs": len(rr),
               "status_ok": sum(r["status"] == "ok" for r in rr)}
        for key, _ in RATES + SECONDARY_RATES:
            k, n = rate(rr, key)
            row[key] = f"{k}/{n}" if n else ""
        for key, _ in MEANS + SECONDARY_MEANS:
            row[f"mean_{key}"] = avg(rr, key)
        out.append(row)
    return out


def per_variant(rows: list[dict]) -> list[dict]:
    by_var = {v: [r for r in rows if r["variant"] == v] for v in VARIANTS}
    full = {(r["config_id"], r["rep"]): r for r in by_var["V_full"]}
    out = []
    for v in VARIANTS:
        rr = by_var[v]
        if not rr:
            continue
        row = {"variant": v, "runs": len(rr), "status_ok": sum(r["status"] == "ok" for r in rr)}
        for key, label in RATES + MEANS + SECONDARY_RATES + SECONDARY_MEANS:
            is_rate = (key, label) in RATES + SECONDARY_RATES
            if is_rate:
                k, n = rate(rr, key)
                row[label] = f"{k}/{n} ({k / n:.1%})" if n else ""
            else:
                row[label] = avg(rr, key)
            if v == "V_full":
                row[f"{label}: V_full minus variant"] = row[f"{label}: p"] = ""
                continue
            if is_rate:
                kf, nf = rate(by_var["V_full"], key)
                k, n = rate(rr, key)
                if n and nf:
                    row[f"{label}: V_full minus variant"] = f"{(kf / nf - k / n) * 100:+.1f} pp"
                    row[f"{label}: p"] = round(float(stats.fisher_exact([[kf, nf - kf], [k, n - k]])[1]), 4)
            else:
                pairs = [(full[(r["config_id"], r["rep"])][key], r[key]) for r in rr
                         if (r["config_id"], r["rep"]) in full and r[key] not in ("", None)
                         and full[(r["config_id"], r["rep"])][key] not in ("", None)]
                diffs = [a - b for a, b in pairs]
                nz = [d for d in diffs if d != 0]
                row[f"{label}: V_full minus variant"] = round(mean(diffs), 4) if diffs else ""
                row[f"{label}: p"] = round(float(stats.binomtest(sum(d > 0 for d in nz), len(nz)).pvalue), 4) \
                    if nz else ""
                row[f"{label}: pairs"] = len(pairs)
        out.append(row)
    return out


def write(path: Path, rows: list[dict]) -> None:
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=BASE / "results" / "stage2")
    args = ap.parse_args()
    config = json.loads((BASE / "setup" / "ablation_config.json").read_text(encoding="utf-8"))
    rows = run_rows(args.results, config)
    write(args.results / "ablation_summary.csv", rows)
    write(args.results / "ablation_per_cell.csv", per_cell(rows))
    pv = per_variant(rows)
    write(args.results / "ablation_per_variant.csv", pv)
    for r in pv:
        print(r["variant"], {k: r[k] for k in ["runs"] + [lbl for _, lbl in RATES + MEANS]})


if __name__ == "__main__":
    main()

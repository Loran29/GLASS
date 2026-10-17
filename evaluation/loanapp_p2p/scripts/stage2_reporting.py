"""
Reporting additions for a Stage-2 result folder (run after run_stage2_batch.py; reads only the records).

Adds to stage2_summary.csv (one value per run, repeated on its initial and final rows):
  glass_utilization_baseline / glass_utilization_proposed   GLASS's utilization KPI (mean over 10 seeds;
                      busy time / whole simulated span, all resources), next to the batch's
                      utilization_baseline / utilization_proposed (target pool / its working time)
  loop_discarded_better_initial   the loop's final (best iteration) differs from iteration 1 and the initial
                      scenario scores better than the final under the primary rules (initial valid, and the
                      final invalid or with fewer KPIs improved) or the secondary ones (higher recall or higher
                      CTD improvement ratio)
  loop_discarded_reason  which of those criteria hold, plus "GLASS utilization metric rose as backlog cleared"
                      when GLASS's utilization KPI rose for the initial scenario while the pool utilization fell
  util_improved_glass / util_improved_batch / all_kpis_improved_glass_rule   both utilization verdicts
  kpi<i>_target_achieved   GLASS's verdict whether KPI i met its numeric threshold (records with
                      kpi_thresholds, i.e. batch v3; empty without a threshold), next to kpi<i>_improved
  util_target_achieved_glass / util_target_achieved_batch   the utilization KPI's target achieved: GLASS's
                      verdict (its utilization value <= threshold) / the target pool's utilization of working
                      time below the batch limit (0.85); empty without a threshold
and to stage2_per_config.csv: reps_loop_discarded_better_initial and the per-phase counts of the verdicts.
Appends to README.md the finding "GLASS utilization metric" and a sensitivity line for the utilization limit
(0.85 as scored vs 0.90, reported only). Scores and records are not changed.

Usage (repo root): python evaluation/loanapp_p2p/scripts/stage2_reporting.py [--results <folder>]
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SENSITIVITY_LIMIT = 0.90
REASON_GLASS_UTIL = "GLASS utilization metric rose as backlog cleared"


def n_improved(ev: dict) -> int:
    return sum(k.get("improved") is True for k in ev.get("kpis") or [])


def glass_util(ev: dict) -> tuple:
    k = next((k for k in ev.get("kpis") or [] if k.get("category") == "utilization"), None)
    return (k["baseline_mean"], k["proposed_mean"]) if k else ("", "")


def discarded(rec: dict) -> tuple[bool, str]:
    ev = rec.get("evaluation") or {}
    ini, fin = ev.get("initial") or {}, ev.get("final") or {}
    if rec.get("status") != "ok" or not ini.get("kpis") or fin.get("same_as_initial") or fin.get("iteration") == 1:
        return False, ""
    reasons = []
    if ini.get("valid") is True and (fin.get("valid") is not True or n_improved(ini) > n_improved(fin)):
        reasons.append(f"primary: valid {ini.get('valid')}/{fin.get('valid')}, KPIs improved "
                       f"{n_improved(ini)}/{n_improved(fin)}")
    ri, rf = (ini.get("scores") or {}).get("recall"), (fin.get("scores") or {}).get("recall")
    if ri is not None and rf is not None and ri > rf:
        reasons.append(f"recall {ri:g} > {rf:g}")
    ci, cf = (ini.get("improvement_ratio") or {}).get("CTD"), (fin.get("improvement_ratio") or {}).get("CTD")
    if ci is not None and cf is not None and ci > cf:
        reasons.append(f"CTD ratio {ci:g} > {cf:g}")
    if not reasons:
        return False, ""
    gb, gp = glass_util(ini)
    pool = ini.get("utilization_rule") or {}
    if gb != "" and gp > gb and pool and pool["proposed_mean_utilization"] < pool["baseline_mean_utilization"]:
        reasons.append(f"{REASON_GLASS_UTIL} (GLASS {gb:g} -> {gp:g}; pool {pool['baseline_mean_utilization']:g} -> "
                       f"{pool['proposed_mean_utilization']:g})")
    return True, "; ".join(reasons)


def target_verdicts(ev: dict) -> tuple:
    """(GLASS's achieved, batch achieved) for the phase's utilization KPI when it has a threshold, else ("", "")."""
    util = next((k for k in ev.get("kpis") or [] if k.get("category") == "utilization"), None)
    if util is None or util.get("threshold") is None:
        return "", ""
    rule = ev.get("utilization_rule") or {}
    batch = rule["proposed_mean_utilization"] < rule["limit"] if rule else ""
    return util.get("glass_achieved") is True, batch


def util_verdicts(ev: dict) -> tuple:
    """(GLASS's verdict, batch verdict) for the phase's utilization KPI, and all KPIs improved under GLASS's rules."""
    kpis = ev.get("kpis") or []
    util = next((k for k in kpis if k.get("category") == "utilization"), None)
    glass_all = bool(kpis) and all(k.get("glass_improved") is True for k in kpis)
    if util is None:
        return "", "", glass_all
    return util.get("glass_improved") is True, util.get("improved") is True, glass_all


def sensitivity(records: list[dict]) -> dict:
    """Utilization verdict counts with the scored limit (0.85) and with SENSITIVITY_LIMIT, from stored per-seed values."""
    out = {}
    for phase in ("initial", "final"):
        n = a = b = 0
        for rec in records:
            u = ((rec.get("evaluation") or {}).get(phase) or {}).get("utilization_rule")
            if not u:
                continue
            bu = sum(u["baseline_per_seed"]) / len(u["baseline_per_seed"])
            pu = sum(u["proposed_per_seed"]) / len(u["proposed_per_seed"])
            n += 1
            a += pu < u["limit"] and pu < bu
            b += pu < SENSITIVITY_LIMIT and pu < bu
        out[phase] = (n, a, b)
    return out


def target_lines(counts: dict) -> list[str]:
    """README section for a batch whose utilization KPIs carry a numeric target (kpi_thresholds)."""
    return [
        "## Finding: utilization KPI with numeric target", "",
        "In this batch every utilization KPI carries a numeric target. Its description ends with \"Target: below "
        "85 % of the available working time.\", and GLASS receives the threshold 0.85 as `KPITarget.threshold` "
        "(records: `kpi_thresholds`). GLASS's loop stops only when that target is achieved and every other KPI "
        "improved, and its feedback says \"improved but target not yet reached\" otherwise.",
        "",
        "Columns:",
        "- `util_improved_glass`, `util_improved_batch`: the improved verdicts as before.",
        "- `util_target_achieved_glass`: GLASS's achieved verdict (its role-resolved utilization, mean over the 10 "
        "seeds, <= 0.85).",
        "- `util_target_achieved_batch`: the target pool's utilization of working time below 0.85.",
        "- `kpi<i>_target_achieved`: GLASS's achieved verdict per KPI.",
        "",
        "Runs with a utilization KPI (status ok):", "",
        "| Phase | Runs | Improved, GLASS's rule | Target achieved, GLASS | Improved, batch rule (< 0.85) | "
        "Target achieved, batch |",
        "|---|---|---|---|---|---|",
        *[f"| {ph} | {n} | {g} | {ta} | {b_} | {tb} |" for ph, (n, g, ta, b_, tb) in counts.items()],
        "",
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=BASE / "results" / "stage2_results")
    out = ap.parse_args().results
    records = {}
    for p in sorted((out / "raw").glob("*/rep_*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        records[(r["config_id"], str(r["rep"]))] = r

    with open(out / "stage2_summary.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    added = ("glass_utilization_", "loop_discarded_", "util_improved_", "util_target_achieved_",
             "all_kpis_improved_glass_rule")
    base_fields = [k for k in rows[0] if not k.startswith(added) and not k.endswith("_target_achieved")]
    for row in rows:
        rec = records[(row["config_id"], row["rep"])]
        ev = (rec.get("evaluation") or {}).get(row["phase"]) or {}
        gb, gp = glass_util(ev)
        flag, reason = discarded(rec)
        ug, ub, ga = util_verdicts(ev)
        tg, tb = target_verdicts(ev)
        kpis = ev.get("kpis") or []
        for i in range(3):
            k = kpis[i] if i < len(kpis) else {}
            row[f"kpi{i + 1}_target_achieved"] = (k.get("glass_achieved") is True) if k.get("threshold") is not None \
                else ""
        row.update({"glass_utilization_baseline": gb, "glass_utilization_proposed": gp,
                    "util_improved_glass": ug, "util_improved_batch": ub,
                    "util_target_achieved_glass": tg, "util_target_achieved_batch": tb,
                    "all_kpis_improved_glass_rule": ga,
                    "loop_discarded_better_initial": flag, "loop_discarded_reason": reason})
    fields = []
    for k in base_fields:                       # kpi<i>_target_achieved next to kpi<i>_improved
        fields.append(k)
        if k.endswith("_improved") and k.startswith("kpi") and k[3:-9].isdigit():
            fields.append(k.replace("_improved", "_target_achieved"))
    fields += ["glass_utilization_baseline", "glass_utilization_proposed", "util_improved_glass",
               "util_improved_batch", "util_target_achieved_glass", "util_target_achieved_batch",
               "all_kpis_improved_glass_rule", "loop_discarded_better_initial", "loop_discarded_reason"]
    with open(out / "stage2_summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    with open(out / "stage2_per_config.csv", newline="", encoding="utf-8") as f:
        per = list(csv.DictReader(f))
    extra = ["reps_loop_discarded_better_initial"] + [f"{ph}_{m}" for ph in ("initial", "final") for m in
                                                       ("util_improved_glass", "util_improved_batch",
                                                        "util_target_achieved_glass", "util_target_achieved_batch",
                                                        "all_kpis_improved_glass_rule")]
    pfields = [k for k in per[0] if k not in extra]
    for row in per:
        recs = [r for (cid, _), r in records.items() if cid == row["config_id"]]
        row["reps_loop_discarded_better_initial"] = sum(discarded(r)[0] for r in recs)
        has_util = any(k.get("category") == "utilization" for r in recs
                       for k in (r.get("stage1_result_passed") or {}).get("kpis", []))
        for ph in ("initial", "final"):
            v = [util_verdicts((r.get("evaluation") or {}).get(ph) or {}) for r in recs if r.get("status") == "ok"]
            row[f"{ph}_util_improved_glass"] = sum(x[0] is True for x in v) if has_util else ""
            row[f"{ph}_util_improved_batch"] = sum(x[1] is True for x in v) if has_util else ""
            row[f"{ph}_all_kpis_improved_glass_rule"] = sum(x[2] for x in v)
            t = [target_verdicts((r.get("evaluation") or {}).get(ph) or {}) for r in recs if r.get("status") == "ok"]
            has_target = any(r.get("kpi_thresholds") for r in recs)
            row[f"{ph}_util_target_achieved_glass"] = sum(x[0] is True for x in t) if has_target else ""
            row[f"{ph}_util_target_achieved_batch"] = sum(x[1] is True for x in t) if has_target else ""
    with open(out / "stage2_per_config.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=pfields + extra, extrasaction="ignore")
        w.writeheader()
        w.writerows(per)

    sens = sensitivity(list(records.values()))
    n_disc = sum(discarded(r)[0] for r in records.values())
    n_glass = sum(REASON_GLASS_UTIL in discarded(r)[1] for r in records.values())
    marker = "\n## Finding: GLASS utilization metric\n"
    readme = (out / "README.md").read_text(encoding="utf-8").split(marker)[0].rstrip("\n")
    completed = sorted(f"{r['config_id']} rep {r['rep']}" for r in records.values() if r.get("completed_by"))
    blocks = [[((it.get("generation") or {}).get("context_block_in_prompt") or "") for it in (r.get("loop") or {}).get("iterations", [])]
              for r in records.values()]
    n_later = sum(len(bs) - 1 for bs in blocks if bs)
    n_changed = sum(b != bs[0] for bs in blocks if bs for b in bs[1:])
    n_blocks = sum(len(bs) for bs in blocks)
    n_cap = sum("total additional resources across all roles" in b.lower() for bs in blocks for b in bs)
    n_cap_first = sum(bool(bs) and "total additional resources across all roles" in bs[0].lower() for bs in blocks)
    old_metric = all(str(r.get("glass_commit", "")).startswith("8039058") for r in records.values())
    with_target = any(r.get("kpi_thresholds") for r in records.values())
    target_counts = {}
    for ph in ("initial", "final"):
        evs = [(r.get("evaluation") or {}).get(ph) or {} for r in records.values() if r.get("status") == "ok"]
        t = [target_verdicts(e) for e in evs]
        u = [util_verdicts(e) for e in evs]
        pairs = [(a, b_, g, bb) for (a, b_), (g, bb, _) in zip(t, u) if a != ""]
        target_counts[ph] = (len(pairs), sum(x[2] is True for x in pairs), sum(x[0] is True for x in pairs),
                             sum(x[3] is True for x in pairs), sum(x[1] is True for x in pairs))
    util_counts = {}
    for ph in ("initial", "final"):
        v = [util_verdicts((r.get("evaluation") or {}).get(ph) or {}) for r in records.values() if r.get("status") == "ok"]
        v = [x for x in v if x[0] != ""]
        util_counts[ph] = (len(v), sum(x[0] is True for x in v), sum(x[1] is True for x in v),
                           sum(x[0] is True and x[1] is not True for x in v))
    metric_text = (
        "GLASS's utilization KPI (`Resource Utilization`, kpi_computation.py) is the mean over all resources of busy "
        "time divided by the whole simulated span (first start to last end, wall-clock time). When a fix removes a "
        "backlog, the span shrinks and this value rises, although the overloaded pool works less of its available "
        "time. Under `minimize` the iterative loop therefore reads the correct fix as a degradation, and GLASS's "
        "feedback marks it as having \"LIKELY CAUSED DEGRADATION\"."
        if old_metric else
        "This batch ran on a GLASS version whose `Resource Utilization` already uses each resource's available "
        "working time and is evaluated for the role the KPI names (commit 5112fbc). The earlier definition, busy "
        "time divided by the whole simulated span averaged over all resources, rose when a backlog was cleared; "
        "that made the loop discard correct fixes in batch v1. Discarded-better-initial counts below have other "
        "causes here.")
    lines = [
        "", "## Finding: GLASS utilization metric", "",
        metric_text,
        "",
        "The records keep both values: GLASS's in `evaluation.<phase>.kpis[*].baseline_mean / proposed_mean`, the "
        "batch's pool utilization of working time in `evaluation.<phase>.utilization_rule`. The summary CSV shows "
        "them side by side (`glass_utilization_*`, `utilization_*`).",
        "",
        f"`loop_discarded_better_initial` (summary columns; per configuration: "
        f"`reps_loop_discarded_better_initial`) marks runs whose loop kept a final worse than iteration 1. "
        f"Across all runs: {n_disc} run(s); in {n_glass} of them GLASS's utilization metric rose while the pool's "
        f"utilization fell.",
        "",
        *(target_lines(target_counts) if with_target else [
        "## Finding: utilization KPI without numeric target", "",
        "The validated utilization KPI tells GLASS to *minimize* utilization (\"bring its utilization down to a "
        "sustainable level without idling the team\") but carries no target value. GLASS's rule for minimize "
        "counts any decrease as improved, and its loop stops at the first iteration in which all target KPIs move "
        "the right way.",
        "",
        "The batch's limit (below 0.85 of available working time, and lower than the baseline) is an evaluation "
        "rule that GLASS never received. Both verdicts are reported side by side:",
        "- `util_improved_glass`: GLASS's own verdict for the utilization KPI, computed with GLASS's utilization "
        "value in this batch.",
        "- `util_improved_batch`: the batch's verdict.",
        "- `all_kpis_improved_glass_rule`: next to `all_kpis_improved`, using GLASS's rules for every KPI.",
        "",
        "Runs with a utilization KPI (status ok):", "",
        "| Phase | Runs | Improved, GLASS's rule | Improved, batch rule (< 0.85) | GLASS improved, batch not |",
        "|---|---|---|---|---|",
        *[f"| {ph} | {n} | {g} | {b_} | {gb} |" for ph, (n, g, b_, gb) in util_counts.items()],
        "",
        "A numeric target in the KPI text (e.g. below 85 % of available working time) is a protocol variant for a "
        "possible later batch; it is not applied here.", ""]),
        "## Sensitivity: utilization limit (reported, not scored)", "",
        "Utilization verdicts \"improved\" (below the limit and lower than the baseline), from the stored per-seed "
        f"pool utilizations, with the scored limit 0.85 and with {SENSITIVITY_LIMIT:.2f}:",
        "",
        f"| Phase | Runs with a utilization KPI | Improved at 0.85 (scored) | Improved at {SENSITIVITY_LIMIT:.2f} |",
        "|---|---|---|---|",
        *[f"| {ph} | {n} | {a} | {b} |" for ph, (n, a, b) in sens.items()], "",
        "## Finding: constraints re-derived in every iteration", "",
        "GLASS rebuilds the hard-constraints block of the patch prompt in every iteration: its summariser "
        "(`build_context_summary`, temperature 0.1) runs on the whole chat, and `inject_feedback_message` adds "
        "each iteration's simulation feedback to that chat as a user message. The feedback therefore also reaches "
        f"the summariser. In {n_changed} of {n_later} later iterations the block differs from iteration 1, mostly "
        "in wording.",
        "",
        f"In {n_cap} of {n_blocks} blocks ({n_cap_first} of them in iteration 1), the summariser still read \"+3 per "
        "internal role\" as a global cap of 3 additional people across all roles, despite the rule added in "
        "`bb6cd17`.", "",
        "## Post-processing", "",
        "1. `scripts/complete_stage2_records.py`: for runs whose iteration 1 produced no scenario, the batch had "
        "stopped before evaluating the final (the loop's best iteration). This step evaluates that final with the "
        "batch's own phase evaluation (same functions, seeding and cached baseline; no LLM calls), scores the "
        "initial as not valid, and marks the record (`status_at_run`, `initial_proposal: false`, `completed_by`). "
        f"Completed runs: {', '.join(completed) or 'none'}.",
        "2. `scripts/stage2_reporting.py`: the reporting columns and the two sections above.",
        "3. `scripts/verify_stage2_results.py`: independent verification (`verification_report.md`).", "",
    ]
    (out / "README.md").write_text(readme + "\n" + "\n".join(lines), encoding="utf-8")
    print(f"reporting columns and README sections written; discarded-better-initial runs: {n_disc}; sensitivity: {sens}")


if __name__ == "__main__":
    main()

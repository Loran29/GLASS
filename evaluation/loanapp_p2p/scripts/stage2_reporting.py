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
and to stage2_per_config.csv: reps_loop_discarded_better_initial.
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
    base_fields = [k for k in rows[0] if not k.startswith(("glass_utilization_", "loop_discarded_"))]
    for row in rows:
        rec = records[(row["config_id"], row["rep"])]
        ev = (rec.get("evaluation") or {}).get(row["phase"]) or {}
        gb, gp = glass_util(ev)
        flag, reason = discarded(rec)
        row.update({"glass_utilization_baseline": gb, "glass_utilization_proposed": gp,
                    "loop_discarded_better_initial": flag, "loop_discarded_reason": reason})
    fields = base_fields + ["glass_utilization_baseline", "glass_utilization_proposed",
                            "loop_discarded_better_initial", "loop_discarded_reason"]
    with open(out / "stage2_summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    with open(out / "stage2_per_config.csv", newline="", encoding="utf-8") as f:
        per = list(csv.DictReader(f))
    pfields = [k for k in per[0] if k != "reps_loop_discarded_better_initial"]
    for row in per:
        row["reps_loop_discarded_better_initial"] = sum(
            discarded(r)[0] for (cid, _), r in records.items() if cid == row["config_id"])
    with open(out / "stage2_per_config.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=pfields + ["reps_loop_discarded_better_initial"], extrasaction="ignore")
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
    lines = [
        "", "## Finding: GLASS utilization metric", "",
        "GLASS's utilization KPI (`Resource Utilization`, kpi_computation.py) is the mean over all resources of busy "
        "time divided by the whole simulated span (first start to last end, wall-clock time). When a fix removes a "
        "backlog, the span shrinks and this value rises, although the overloaded pool works less of its available "
        "time. Under `minimize` the iterative loop therefore reads the correct fix as a degradation, and GLASS's "
        "feedback marks it as having \"LIKELY CAUSED DEGRADATION\".",
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

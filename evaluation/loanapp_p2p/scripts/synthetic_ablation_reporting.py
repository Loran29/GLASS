"""
Reporting for the Stage-2 ablation on the synthetic processes (reads only the run records of
results/stage2_synthetic_ablation/<variant>/raw/; scores are not changed).

Measures, per run, for the final scenario (the loop's best iteration) and the initial (iteration 1), with the
real-life ablation's definitions (evaluation/real_life_ablation/scripts/run_ablation_batch.py, SCORING; its
dhr_nis, merge_counts and discarded functions are used as they are) on the v3.2 records:
  scenario_produced   the phase has a merged scenario (a run without a proposal, an app or LLM error, or a loop
                      that kept the baseline (no_improving_scenario, final only) has none)
  valid               GLASS's patch validator passed and the v3.2 business-context check found no violation
                      (staffing, budget, immutable steps; run_stage2_batch.check_validity). Unlike the v3.2
                      batch's `valid`, this does not include whether the simulation ran (that is `executable`)
  executable          the 10-seed evaluation simulated on all seeds
  merge_stability     applied / proposed modifications of the accepted attempt (merge_counts); > 0 required
  admissible          valid, executable and merge stability > 0
  all_kpis_improved   every evaluated KPI improved (GLASS's direction rule, glass_improved); the v3.2 batch rule
                      (`improved`) is reported next to it as all_kpis_improved_batch_rule
  DHR, NIS            share of the KPIs improved; mean |% change| signed + if improved, - otherwise
  util_target         the utilization KPI's 0.85 target achieved (GLASS's verdict), where a KPI has a threshold;
                      none of the six configurations has one, so it is not applicable here
  recall              share of the expected elements of the injected problems touched by an on-target
                      modification (v3.2 scoring)
  CTD_ratio           1 - CTD(proposed, healthy) / CTD(baseline, healthy), mean over the 10 seeds (v3.2)
  loop_discarded_better_initial   real-life definition (admissible initial and a: final not admissible,
                      b: lower DHR, c: equal DHR and NIS lower by > 1 pp; a kept baseline counts only if the
                      initial was better than the baseline)
  iterations_used, run time (elapsed_s of the run)
Aggregation as in the real-life ablation (ablation_reporting.py): per cell (configuration x variant) and per
variant over all runs; V_main minus each variant: rates in percentage points with Fisher's exact test
(two-sided, all runs as denominator), means as the mean paired difference (same configuration and repetition,
both values present) with a two-sided sign test (ties dropped).

Writes results/stage2_synthetic_ablation/synthetic_ablation_{summary,per_cell,per_variant}.csv, README.md and
results/GLASS_synthetic_ablation.xlsx.
Usage (repo root): python evaluation/loanapp_p2p/scripts/synthetic_ablation_reporting.py [--results DIR]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

from scipy import stats

HERE = Path(__file__).resolve().parent
BASE = HERE.parent
REPO = BASE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "evaluation" / "real_life_ablation" / "scripts"))
import run_ablation_batch as rl  # noqa: E402  (dhr_nis, merge_counts, discarded: the real-life definitions)
import run_stage2_synthetic_ablation as syn  # noqa: E402

VARIANTS = list(syn.VARIANTS)
REF = "V_main"
RATES = [("scenario_produced", "Scenario produced"), ("valid", "Valid"), ("executable", "Executable"),
         ("merge_stability_positive", "Merge stability > 0"), ("all_kpis_improved", "All KPIs improved")]
MEANS = [("DHR", "DHR"), ("NIS", "NIS")]
SECONDARY_RATES = [("util_target_achieved", "Utilization target achieved (where applicable)"),
                   ("admissible", "Admissible (valid, executable, merge stability > 0)"),
                   ("admissible_and_all_improved", "Admissible and all KPIs improved"),
                   ("any_kpi_significant_improvement", "At least one KPI improved with p < 0.05"),
                   ("loop_discarded_better_initial", "Loop discarded a better initial"),
                   ("no_improving_scenario", "Loop kept the baseline (no_improving_scenario)")]
SECONDARY_MEANS = [("recall", "Recall"), ("CTD_ratio", "CTD ratio to healthy"),
                   ("merge_stability", "Merge stability (applied / proposed)"),
                   ("glass_merge_stability", "Merge stability, GLASS's value"),
                   ("n_modifications", "Modifications"), ("iterations_used", "Iterations used"),
                   ("elapsed_s", "Run time (s)")]


def phase_eval(rec: dict, phase: str) -> dict:
    """The phase's measures; {} when the phase has no scenario."""
    loop = rec.get("loop") or {}
    ev = (rec.get("evaluation") or {}).get(phase) or {}
    if rec["status"] != "ok" or ev.get("iteration") is None or ev.get("error") == "no scenario":
        return {}
    it = loop["iterations"][ev["iteration"] - 1]
    counts = rl.merge_counts((it.get("generation") or {}).get("generation_notes") or [], rec.get("llm_calls") or [],
                             ev["iteration"]) or {}
    kpis = ev.get("kpis") or []
    dhr, nis = rl.dhr_nis(kpis)
    valid = bool(ev.get("patch_validator_passed")) and not ev.get("violations")
    executable = bool(ev.get("multi_seed_ok")) and ev.get("n_logs") == len(rec["seeds"])
    stab = counts.get("merge_stability")
    targets = [k for k in kpis if k.get("threshold") is not None]
    return {"iteration": ev["iteration"], "same_as_initial": bool(ev.get("same_as_initial")),
            "scenario_produced": True, "valid": valid, "executable": executable, "merge_stability": stab,
            "glass_merge_stability": counts.get("glass_merge_stability"),
            "admissible": valid and executable and (stab or 0) > 0,
            "all_kpis_improved": bool(kpis) and all(k.get("glass_improved") is True for k in kpis),
            "all_kpis_improved_batch_rule": bool(kpis) and all(k.get("improved") is True for k in kpis),
            "DHR": dhr, "NIS": nis,
            "util_target_achieved": (all(k.get("glass_achieved") is True for k in targets) if targets else ""),
            "any_kpi_significant_improvement": any(k.get("glass_improved") is True and k.get("significant") is True
                                                   for k in kpis),
            "recall": (ev.get("scores") or {}).get("recall"),
            "CTD_ratio": (ev.get("improvement_ratio") or {}).get("CTD"),
            "n_modifications": (ev.get("scores") or {}).get("n_modifications"),
            "violations": " | ".join(ev.get("violations") or []), "kpis": kpis}


def phase_row(p: dict, prefix: str) -> dict:
    produced = bool(p)
    row = {"scenario_produced": produced, "valid": bool(p.get("valid")), "executable": bool(p.get("executable")),
           "merge_stability": p.get("merge_stability"),
           "merge_stability_positive": produced and (p.get("merge_stability") or 0) > 0,
           "glass_merge_stability": p.get("glass_merge_stability"), "admissible": bool(p.get("admissible")),
           "all_kpis_improved": bool(p.get("all_kpis_improved")),
           "all_kpis_improved_batch_rule": bool(p.get("all_kpis_improved_batch_rule")),
           "DHR": p.get("DHR"), "NIS": p.get("NIS"), "util_target_achieved": p.get("util_target_achieved", ""),
           "any_kpi_significant_improvement": bool(p.get("any_kpi_significant_improvement")),
           "recall": p.get("recall"), "CTD_ratio": p.get("CTD_ratio"), "n_modifications": p.get("n_modifications"),
           "violations": p.get("violations", "")}
    row["admissible_and_all_improved"] = row["admissible"] and row["all_kpis_improved"]
    for i, k in enumerate((p.get("kpis") or [])[:2], 1):
        row.update({f"kpi{i}": k["kpi"], f"kpi{i}_pct_change": k.get("pct_change"),
                    f"kpi{i}_p_value": k.get("p_value"), f"kpi{i}_improved": k.get("glass_improved")})
    return {f"{prefix}{k}": v for k, v in row.items()}


def run_rows(results: Path) -> list[dict]:
    rows = []
    for v in VARIANTS:
        for path in sorted((results / v / "raw").glob("*/rep_*.json")):
            rec = json.loads(path.read_text(encoding="utf-8"))
            loop = rec.get("loop") or {}
            kept = bool(loop.get("no_improving_scenario"))
            ini, fin = phase_eval(rec, "initial"), phase_eval(rec, "final")
            flag, cond = rl.discarded({"initial": ini, "final": fin}, kept_baseline=kept) if rec["status"] == "ok" \
                else (False, "")
            row = {"config_id": rec["config_id"], "process": rec["process"], "goal_config": rec["goal_config"],
                   "variant": rec["variant"], "rep": rec["rep"], "status": rec["status"],
                   **{f"switch_{k}": val for k, val in rec["switches"].items()},
                   "iterations_used": len(loop.get("iterations") or []), "final_iteration": loop.get("best_iteration"),
                   "no_improving_scenario": kept, "loop_discarded_better_initial": flag,
                   "loop_discarded_condition": cond, "elapsed_s": rec.get("elapsed_s")}
            row.update(phase_row(fin, ""))
            row.update(phase_row(ini, "initial_"))
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
        groups[(r["config_id"], r["variant"])].append(r)
    out = []
    for (cid, v), rr in sorted(groups.items(), key=lambda kv: (syn.CONFIGS.index(kv[0][0]), VARIANTS.index(kv[0][1]))):
        row = {"config_id": cid, "variant": v, "runs": len(rr), "status_ok": sum(r["status"] == "ok" for r in rr)}
        for key, _ in RATES + SECONDARY_RATES:
            k, n = rate(rr, key)
            row[key] = f"{k}/{n}" if n else ""
        for key, _ in MEANS + SECONDARY_MEANS:
            row[f"mean_{key}"] = avg(rr, key)
        out.append(row)
    return out


def per_variant(rows: list[dict]) -> list[dict]:
    by_var = {v: [r for r in rows if r["variant"] == v] for v in VARIANTS}
    ref = {(r["config_id"], r["rep"]): r for r in by_var[REF]}
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
            if v == REF:
                row[f"{label}: {REF} minus variant"] = row[f"{label}: p"] = ""
                if not is_rate:
                    row[f"{label}: pairs"] = ""
                continue
            if is_rate:
                kf, nf = rate(by_var[REF], key)
                k, n = rate(rr, key)
                row[f"{label}: {REF} minus variant"] = f"{(kf / nf - k / n) * 100:+.1f} pp" if n and nf else ""
                row[f"{label}: p"] = round(float(stats.fisher_exact([[kf, nf - kf], [k, n - k]])[1]), 4) \
                    if n and nf else ""
            else:
                pairs = [(ref[(r["config_id"], r["rep"])][key], r[key]) for r in rr
                         if (r["config_id"], r["rep"]) in ref and r[key] not in ("", None)
                         and ref[(r["config_id"], r["rep"])][key] not in ("", None)]
                diffs = [a - b for a, b in pairs]
                nz = [d for d in diffs if d != 0]
                row[f"{label}: {REF} minus variant"] = round(mean(diffs), 4) if diffs else ""
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


def variant_table(pv: list[dict]) -> tuple[list[str], list[list]]:
    labels = [lbl for _, lbl in RATES + MEANS + SECONDARY_RATES + SECONDARY_MEANS]
    header = ["Measure"] + [r["variant"] for r in pv]
    body = [["Runs"] + [r["runs"] for r in pv], ["Runs that ended ok"] + [r["status_ok"] for r in pv]]
    body += [[lbl] + [r[lbl] for r in pv] for lbl in labels]
    return header, body


def diff_table(pv: list[dict]) -> tuple[list[str], list[list]]:
    others = [r for r in pv if r["variant"] != REF]
    labels = [lbl for _, lbl in RATES + MEANS + SECONDARY_RATES + SECONDARY_MEANS]
    header = ["Measure"] + [x for r in others for x in (f"{REF} − {r['variant']}", "p")]
    body = [[lbl] + [x for r in others for x in (r.get(f"{lbl}: {REF} minus variant", ""), r.get(f"{lbl}: p", ""))]
            for lbl in labels]
    return header, body


def md(header: list[str], body: list[list]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + \
           ["| " + " | ".join("" if c is None else str(c) for c in r) + " |" for r in body]


def _order(r: dict) -> tuple:
    return VARIANTS.index(r["variant"]), syn.CONFIGS.index(r["config_id"]), r["rep"]


def failure_cause(error: str) -> tuple[str, str]:
    """(cause, GLASS's message) of a no_initial_proposal run, from the record's error text."""
    text = (error or "").strip()
    m = re.search(r"\[(\w+)\]\s*(.*)", text)
    if m:
        return m.group(1), m.group(2).split(" Use one of:")[0].strip()
    if text.startswith("Patch schema validation failed"):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        field = lines[2] if len(lines) > 2 else ""
        return "schema_validation", f"{lines[1] if len(lines) > 1 else ''}: {field.split(' [type=')[0]}".strip(": ")
    return "other", text.splitlines()[0][:200] if text else ""


def empty_patch_iterations(recs: list[dict]) -> list[tuple]:
    """(record, iteration number, iterations, evaluated phases) for every iteration whose patch carries neither a
    modification nor an unresolved KPI."""
    out = []
    for r in sorted(recs, key=_order):
        its = (r.get("loop") or {}).get("iterations") or []
        for i, it in enumerate(its, 1):
            p = it.get("patch")
            if isinstance(p, dict) and not p.get("modifications") and not p.get("unresolved_kpis"):
                used = [ph for ph, ev in (r.get("evaluation") or {}).items()
                        if "error" not in ev and ev.get("iteration") == i]
                out.append((r, i, len(its), used, bool(it.get("patch_validator_passed"))))
    return out


def notes_sections(recs: list[dict]) -> list[str]:
    L = ["## What each switch removes", "",
         "- **V_no_log** passes no log profile to GLASS: the generation prompt has no event-log evidence section, "
         "and the context evidence is missing too, because GLASS takes it from the log profile's context profile "
         "(statistically screened relationships between context factors and metrics; `knowledge/retrieval.py`) "
         "(GLASS notes \"Log evidence: not available\" and \"Context evidence: not available\").",
         "- **V_no_context** removes only the chat-derived constraints: the clarification chat is empty, so the "
         "prompt has no constraints section from the chat. The log-derived context relationships stay in the "
         "prompt (GLASS notes e.g. \"Context: 6 significant relationships\"); they are removed only in V_no_log.",
         "- **V_no_refine** sets `max_iterations` to 1: the first proposal is evaluated once; if it does not improve, "
         "the loop keeps the baseline (`no_improving_scenario`).",
         "- **V_old_refine** runs the loop with `legacy_refinement=True` (v2 stop and selection rules).", ""]
    failed = sorted((r for r in recs if r["status"] == "no_initial_proposal"), key=_order)
    causes = [failure_cause(r.get("error") or "") for r in failed]
    L += [f"## Runs without an initial proposal ({len(failed)})", "",
          "GLASS's patch validator rejected the first proposal, so the loop had nothing to evaluate. These runs "
          "are scored as no scenario produced, not valid and not improved (as in the v3.2 batch).", ""]
    L += md(["Variant", "Configuration", "Rep", "Cause", "GLASS's message"],
            [[r["variant"], r["config_id"], r["rep"], c, msg.replace("|", "\\|")] for r, (c, msg) in zip(failed, causes)])
    by_cause = {}
    for c, _ in causes:
        by_cause[c] = by_cause.get(c, 0) + 1
    L += ["", "Causes: " + "; ".join(f"`{c}` {n}" for c, n in sorted(by_cause.items(), key=lambda x: -x[1])) + ".",
          "`missing_element`: the proposal names a timer event by its activity (\"Release Supplier's Invoice\") "
          "instead of the baseline's event name (\"Timer before Release Supplier's Invoice\"). "
          "`conflicting_modifications`: two modifications target the same element and field. "
          "`schema_validation`: a field of the patch has the wrong type (`context_condition` returned as an object "
          "instead of text).", ""]
    empty = empty_patch_iterations(recs)
    L += [f"## Finding: empty patches accepted by GLASS ({len(empty)})", "",
          "In these iterations GLASS returned a patch with no modification and no unresolved KPI, its patch "
          "validator passed it, and the merged scenario was simulated. GLASS's own patch schema "
          "(`ScenarioPatch`) rejects such a patch when it is validated again, so `verify_stage2_results.py` cannot "
          "re-merge it; `verify_synthetic_ablation.py` skips the re-merge for these patches only (the file "
          "`verify_stage2_results.py` is not changed) and lists them as a finding.", ""]
    L += md(["Variant", "Configuration", "Rep", "Iteration", "Patch validator", "Evaluated as"],
            [[r["variant"], r["config_id"], r["rep"], f"{i} of {n}", "passed" if ok else "failed",
              ", ".join(used) or "not evaluated (not initial or final)"] for r, i, n, used, ok in empty])
    return L


def write_readme(results: Path, rows: list[dict], cells: list[dict], pv: list[dict]) -> None:
    recs = [json.loads(p.read_text(encoding="utf-8")) for v in VARIANTS for p in (results / v / "raw").glob("*/rep_*.json")]
    commits = sorted({r["glass_commit"] for r in recs})
    stamps = sorted(r["timestamp"] for r in recs)
    rel = lambda p: Path(p).resolve().relative_to(REPO).as_posix()
    L = ["# Stage 2 ablation on the synthetic processes, without the knowledge base", "",
         f"Generated by `{rel(__file__)}` from the records of `{rel(syn.__file__)}`.", "",
         "**Question.** Which of GLASS's Stage-2 components matter on LoanApp / Procure2Pay when the knowledge base "
         "is off: the log evidence, the clarification context, the refinement loop, and the loop's current stop and "
         "selection rules? Each run is the v3.2 batch's run (`run_stage2_batch.run_rep`: same configuration file, "
         "baselines, Stage-1 KPIs, business context, loop and 10-seed evaluation), with the variant's switches "
         "applied where the real-life ablation applies them.", "",
         "| Variant | Log evidence | Clarification context | Refinement | Stop / selection rule |",
         "|---|---|---|---|---|",
         "| V_main | yes | yes | up to 4 iterations | current (v3.2) |",
         "| V_no_log | no | yes | up to 4 iterations | current |",
         "| V_no_context | yes | no | up to 4 iterations | current |",
         "| V_no_refine | yes | yes | 1 iteration | – |",
         "| V_old_refine | yes | yes | up to 4 iterations | v2 (first improved; best by score) |", "",
         "Knowledge base off in every variant. Configurations: " + ", ".join(syn.CONFIGS) + " (lead author's "
         "selection); 5 repetitions; evaluation seeds 42..51. None of these configurations has a utilization KPI, "
         "so the utilization target does not apply.", "",
         "## Final scenario, per variant", ""]
    L += md(*variant_table(pv))
    L += ["", f"## {REF} minus each variant", "",
          "Rates: difference in percentage points, Fisher's exact test (two-sided, all runs). Means: mean paired "
          "difference (same configuration and repetition), two-sided sign test (ties dropped).", ""]
    L += md(*diff_table(pv))
    L += [""] + notes_sections(recs)
    L += ["", "## Per configuration (final scenario)", ""]
    keys = ["scenario_produced", "valid", "executable", "merge_stability_positive", "all_kpis_improved",
            "mean_DHR", "mean_NIS", "mean_recall", "mean_CTD_ratio", "loop_discarded_better_initial",
            "mean_iterations_used", "mean_elapsed_s"]
    L += md(["Configuration", "Variant"] + keys, [[c["config_id"], c["variant"]] + [c[k] for k in keys] for c in cells])
    L += ["", "## Run", "",
          f"- GLASS: commit {', '.join(f'`{c}`' for c in commits)} (v3.3), no local changes under `goal_to_parameters/`",
          f"- Runs: {len(recs)}; first / last run started {stamps[0] if stamps else ''} / {stamps[-1] if stamps else ''}",
          f"- Command: `python {rel(syn.__file__)} --workers 4`",
          f"- Check: `python {rel(HERE / 'verify_synthetic_ablation.py')}` (rebuilds these CSVs independently and "
          "runs `verify_stage2_results.py` on every variant folder)", "",
          "## Files", "",
          "- `<variant>/`: a complete v3.2 batch folder per variant (records, summaries, README; simulated logs "
          "are not in git, their SHA-256 is in the records)",
          "- `synthetic_ablation_summary.csv`: one row per run, final and initial (`initial_` columns)",
          "- `synthetic_ablation_per_cell.csv`: per configuration and variant",
          "- `synthetic_ablation_per_variant.csv`: per variant, with the differences to V_main and the tests",
          f"- `{rel(BASE / 'results' / 'GLASS_synthetic_ablation.xlsx')}`: the same tables as a workbook", "",
          "## Scoring (copied from the reporting script)", "", "```",
          __doc__.split("Measures, per run,")[1].split("Writes results/")[0].strip("\n"), "```", ""]
    (results / "README.md").write_text("\n".join(L), encoding="utf-8", newline="\n")


def write_workbook(rows: list[dict], cells: list[dict], pv: list[dict]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    first = True

    def sheet(title, header, body, widths=None):
        nonlocal first
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = title
        ws.append(header)
        for c in ws[1]:
            c.font, c.fill = Font(bold=True), PatternFill("solid", fgColor="DDE7F0")
            c.alignment = Alignment(wrap_text=True, vertical="top")
        for r in body:
            ws.append(["" if x is None else x for x in r])
        for i, w in enumerate(widths or [], 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = w
        ws.freeze_panes = "B2"

    h, b = variant_table(pv)
    sheet("Per variant", h, b, [48] + [18] * len(pv))
    h, b = diff_table(pv)
    sheet(f"{REF} minus variant", h, b, [48] + [14] * (len(h) - 1))
    sheet("Per configuration", list(cells[0].keys()), [list(c.values()) for c in cells], [18] * 30)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    sheet("All runs", keys, [[r.get(k, "") for k in keys] for r in rows], [16] * len(keys))
    sheet("Scoring", ["Scoring rules"], [[line] for line in __doc__.splitlines()], [140])
    wb.save(BASE / "results" / "GLASS_synthetic_ablation.xlsx")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=syn.OUT_DEFAULT)
    results = ap.parse_args().results
    rows = run_rows(results)
    cells, pv = per_cell(rows), per_variant(rows)
    write(results / "synthetic_ablation_summary.csv", rows)
    write(results / "synthetic_ablation_per_cell.csv", cells)
    write(results / "synthetic_ablation_per_variant.csv", pv)
    write_readme(results, rows, cells, pv)
    if results.resolve() == syn.OUT_DEFAULT.resolve():
        write_workbook(rows, cells, pv)
    for r in pv:
        print(r["variant"], {lbl: r[lbl] for _, lbl in RATES + MEANS})


if __name__ == "__main__":
    main()

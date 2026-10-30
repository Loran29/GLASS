"""
Build results/GLASS_Stage2_results_v1_v2_v3_v31.xlsx from the verified Stage-2 files (values, no formulas).

Sources: results/stage2_v1_v2_v3_v31.csv and .md, results/stage2_v1_vs_v2.md and stage2_v1_v2_v3.md (control
checks v1 -> v2, v2 -> v3), results/stage2_results_v3_1/ (records and summary of batch v3.1),
results/VERSIONS.md (version table, copied verbatim), setup/stage2_config.json. Same style as the v1/v2/v3
workbook; wording follows the v1 workbook ("first proposal" / "after refinement").

Usage (repo root, environment with GLASS's dependencies and openpyxl):
  python evaluation/experiments/shared/loanapp_p2p/scripts/make_stage2_v1_v2_v3_v31_workbook.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from openpyxl import Workbook

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import lp_paths as P  # noqa: E402  (experiment paths; puts the other experiments' scripts on sys.path)
import make_stage2_workbook as w1  # noqa: E402  (names_for, readable, header, style_sheet, BOX, BOLD, TITLE)
import run_stage2_batch as b  # noqa: E402

RES = P.OLDER
R3 = RES / "stage2_results_v3_1"
OUT = RES / "GLASS_Stage2_results_v1_v2_v3_v31.xlsx"
VERSIONS = ("v1", "v2", "v3", "v3.1")
FIX_COLS = [("budget", "Runs with a relative-budget rejection (v3 / v3.1)"),
            ("cal_parsed", "Runs with a calendar change read from text or Prosimos form (v3 / v3.1)"),
            ("cal_rej", "Runs with a calendar change rejected with feedback (v3 / v3.1)"),
            ("cal_drop", "Runs with a calendar change skipped without feedback (v3 / v3.1)"),
            ("util_raise", "Runs shown utilization-raising strategies for a utilization problem KPI (v3 / v3.1)")]

CMP_COLS = [  # (csv metric, header, kind) kind: all = v1 / v2 / v3 / v3.1; v3 = v3 / v3.1; defect = v1 / v2
    ("final_valid", "Valid scenario (after refinement)", "all"),
    ("final_all_kpis_improved", "All target KPIs improved, our rule (after refinement)", "all"),
    ("final_all_kpis_improved_glass_rule", "All target KPIs improved, GLASS's rule (after refinement)", "all"),
    ("final_util_target_achieved_glass", "Utilization target achieved, GLASS: at most 0.85 (after refinement, v3 / v3.1)", "v3"),
    ("final_util_target_achieved_batch", "Utilization target achieved, our rule: below 0.85 (after refinement, v3 / v3.1)", "v3"),
    ("final_valid_and_improved", "Valid and all KPIs improved, our rule (after refinement)", "all"),
    ("reps_loop_discarded_better_initial", "Runs where the refinement made the result worse than the first proposal",
     "all"),
    ("final_mean_recall", "Share of injected changes recovered, mean (after refinement)", "all"),
    ("final_median_improvement_ratio_CTD", "Closeness to the healthy model, median (after refinement)", "all"),
    ("unit_defect_runs", "Runs affected by the unit defect: hour values applied as seconds (v1 / v2)", "defect"),
] + [(k, h, "fix") for k, h in FIX_COLS]
DELTA_COLS = [("final_valid", "Valid scenario (after refinement)"),
              ("final_all_kpis_improved", "All target KPIs improved, our rule (after refinement)"),
              ("final_valid_and_improved", "Valid and all KPIs improved, our rule (after refinement)"),
              ("reps_loop_discarded_better_initial", "Runs where the refinement made the result worse"),
              ("final_mean_recall", "Share of injected changes recovered (after refinement)"),
              ("final_median_improvement_ratio_CTD", "Closeness to the healthy model, median (after refinement)")]
RUN_COLS = ["Configuration", "Process", "Problems", "Rep", "Refinement iterations used", "Final iteration",
            "Valid (first proposal)", "Valid (after refinement)", "Violations (first proposal)",
            "Violations (after refinement)", "KPI change % (first proposal)", "KPI change % (after refinement)",
            "All KPIs improved (first proposal)", "All KPIs improved (after refinement)",
            "Utilization target achieved, GLASS / our rule (first proposal)",
            "Utilization target achieved, GLASS / our rule (after refinement)",
            "Problems addressed (first proposal)", "Problems addressed (after refinement)",
            "Injected changes recovered (first proposal)", "Injected changes recovered (after refinement)",
            "Closeness to healthy (first proposal)", "Closeness to healthy (after refinement)",
            "Refinement made it worse", "Changes in the scenario after refinement"]
MEANS = {"final_mean_recall", "final_median_improvement_ratio_CTD"}


def fmt(v, nd):
    if v in ("", None):
        return "–"
    x = float(v)
    return f"{x:.{nd}f}" if nd else f"{int(round(x))}"


def put(ws, row, values):
    for j, v in enumerate(values, 1):
        ws.cell(row=row, column=j, value=v).border = w1.BOX


def md_bullets(md: str, marker: str) -> list[str]:
    part = md.split(marker, 1)[1].split("\n")[1:]
    out = []
    for line in part:
        if line.strip().startswith("- "):
            out.append(line.strip()[2:].replace("**", ""))
        elif out and not line.strip():
            break
    return out


def versions_table(md: str) -> list[list[str]]:
    rows = [l for l in md.splitlines() if l.startswith("| ")]
    return [[c.strip() for c in l.strip().strip("|").split(" | ")] for l in rows if not set(l) <= set("|- ")]


def main() -> None:
    b.quiet()
    config = json.loads(b.CONFIG_PATH.read_text(encoding="utf-8"))
    cmp_rows = list(csv.DictReader(open(RES / "stage2_v1_v2_v3_v31.csv", encoding="utf-8")))
    md31 = (RES / "stage2_v1_v2_v3_v31.md").read_text(encoding="utf-8")
    md3 = (RES / "stage2_v1_v2_v3.md").read_text(encoding="utf-8")
    md12 = (RES / "stage2_v1_vs_v2.md").read_text(encoding="utf-8")
    vmd = (RES / "VERSIONS.md").read_text(encoding="utf-8")
    wb = Workbook()

    # ---------------------------------------------------------------- sheet 1: comparison
    ws = wb.active
    ws.title = "v1 v2 v3 v3.1 per configuration"
    w1.header(ws, 1, ["Configuration", "Has a utilization KPI"] + [h for _, h, _ in CMP_COLS])
    util_rows = [x for x in cmp_rows if x["has_utilization_kpi"] == "True"]
    over = [x for x in util_rows if float(x["baseline_pool_utilization"]) > 0.85]

    def target_figures(m: str, v: str = "v3.1") -> str:
        """Target achieved split by baseline: overloaded (> 0.85) vs already below 0.85."""
        n = lambda rs: int(sum(float(x[f"{m}_{v}"]) for x in rs))
        return (f"{v}: target achieved where the baseline was overloaded (>0.85): {n(over)} of {5 * len(over)}; "
                f"all {5 * len(util_rows)} utilization runs: {n(util_rows)} of {5 * len(util_rows)}, of which "
                f"{5 * (len(util_rows) - len(over))} already below 0.85 at baseline")

    r = 2
    for row in cmp_rows:
        util = row["has_utilization_kpi"]
        cells = [row["config_id"], {"True": "yes", "False": "no"}.get(util, "")]
        for m, _, kind in CMP_COLS:
            nd = 2 if m in MEANS else 0
            if kind == "defect":
                cells.append(f"{fmt(row['unit_defect_runs_v1'], 0)} / {fmt(row['unit_defect_runs_v2'], 0)}")
            elif kind == "fix":
                cells.append(f"{fmt(row[f'{m}_v3'], 0)} / {fmt(row[f'{m}_v3.1'], 0)}")
            elif kind == "v3":
                if util == "" and "without" not in row["config_id"]:      # total row and utilization group
                    cells.append(target_figures(m, "v3") + "; " + target_figures(m, "v3.1"))
                else:
                    cells.append("–" if util == "False" else f"{fmt(row[f'{m}_v3'], 0)} / {fmt(row[f'{m}_v3.1'], 0)}")
            else:
                cells.append(" / ".join(fmt(row[f"{m}_{v}"], nd) for v in VERSIONS))
        put(ws, r, cells)
        if util == "":
            for j in range(1, len(cells) + 1):
                ws.cell(row=r, column=j).font = w1.BOLD
        r += 1
    notes = [
        "Cells: v1 / v2 / v3 / v3.1 (target and fix columns: v3 / v3.1). Counts are out of 5 runs per configuration "
        "(70 per batch in the total row). Recall is the mean over the evaluated runs; closeness to the healthy model "
        "is the median over the runs.",
        "v3.1 = v3 plus four implementation fixes: the direction filter uses the KPI category; the relative budget "
        "limit is enforced; calendar changes given as text are parsed or rejected with feedback; calendar items in "
        "Prosimos period form are accepted. Same script, configuration and seeds as v3.",
        "Utilization target achieved exists only in v3 and v3.1 (v1 and v2 had no threshold). GLASS: its role-resolved "
        "utilization, mean over 10 seeds, at most 0.85. Our rule: the target pool's utilization of working time "
        "below 0.85. The two differ at exactly 0.85 only, and use different (normally close) utilization values.",
        "Target already met by the baseline: " + md3.split("**Target already met by the baseline:** ", 1)[1].split("\n")[0]
        .replace("`", ""),
        "Utilization target achieved (after refinement): " + target_figures("final_util_target_achieved_glass", "v3")
        + "; " + target_figures("final_util_target_achieved_glass", "v3.1") + ".",
        "Known defect of v3, fixed in v3.1: the direction filter read the KPI name instead of the category (in v3 it "
        "affected Procure2Pay_G3 only). The column \"shown utilization-raising strategies\" counts the runs in which "
        "it acted (v3 / v3.1).",
        "Runs affected by the unit defect (v1 / v2): a proposed duration meant in hours was applied in seconds. "
        "Their scores are kept as recorded and are included in all counts (see VERSIONS.md). v3 rejects such "
        "modifications.",
        "Source: stage2_v1_v2_v3_v31.csv (from the verified per-configuration and summary CSVs and the records of "
        "the four batches).",
    ]
    for n in notes:
        ws.cell(row=r, column=1, value=n)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
        r += 1
    r += 1
    groups = [x for x in cmp_rows if x["has_utilization_kpi"] == "" and not x["config_id"].startswith("TOTAL")]
    for a, label in (("v3", "Change from v3 to v3.1 per group (v3.1 − v3)"), ("v2", "Change from v2 to v3.1 per group (v3.1 − v2)")):
        ws.cell(row=r, column=1, value=label).font = w1.TITLE
        w1.header(ws, r + 1, ["Group"] + [h for _, h in DELTA_COLS])
        for k, g in enumerate(groups, r + 2):
            name = "With a utilization KPI" if "with a" in g["config_id"] else "Without a utilization KPI (control)"
            vals = []
            for m, _ in DELTA_COLS:
                x = g[f"{m}_delta_v31_{a}"]
                vals.append("–" if x in ("", None) else (f"{float(x):+.3f}" if m in MEANS else f"{float(x):+g}"))
            put(ws, k, [name] + vals)
        r += 2 + len(groups) + 1
    for title, md, marker in (("Control check v1 → v2", md12, "**Control check**"),
                              ("Control check v2 → v3", md3, "**Control check v2 → v3**"),
                              ("Control check v3 → v3.1", md31, "**Control check v3 → v3.1**")):
        ws.cell(row=r, column=1, value=title).font = w1.TITLE
        for line in md_bullets(md, marker):
            r += 1
            ws.cell(row=r, column=1, value=line)
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
        r += 2
    w1.style_sheet(ws, [22, 11] + [15] * len(CMP_COLS))

    # ---------------------------------------------------------------- sheet 2: all 70 v3 runs
    ws2 = wb.create_sheet("All 70 runs (v3.1)")
    w1.header(ws2, 1, RUN_COLS)
    summ = list(csv.DictReader(open(R3 / "stage2_summary.csv", encoding="utf-8")))
    cfgs = {c["config_id"]: c for c in config["configurations"]}
    order = sorted(cfgs, key=lambda c: (c.split("_")[0], len(c.split("_")[1]), c))
    names = {cid: w1.names_for(cfgs[cid], config) for cid in order}
    yes = {"True": "yes", "False": "no"}
    row = 2
    for cid in order:
        gs = cfgs[cid]["injected_problems"]
        for rep in range(1, 6):
            rec = json.loads((R3 / "raw" / cid / f"rep_{rep}.json").read_text(encoding="utf-8"))
            ev = rec.get("evaluation") or {}
            ph = {p: ev.get(p) or {"error": "no scenario"} for p in ("initial", "final")}
            srow = {x["phase"]: x for x in summ if x["config_id"] == cid and x["rep"] == str(rep)}

            def yn(e):
                return "no scenario" if "error" in e else ("yes" if e.get("valid") else "no")

            def allimp(e):
                if "error" in e:
                    return "no scenario"
                return "yes" if e.get("kpis") and all(k.get("improved") is True for k in e["kpis"]) else "no"

            def kpis(e):
                if "error" in e:
                    return "no scenario"
                return "\n".join(f"{k['kpi']}: {k['pct_change']:+.1f} % ({'improved' if k.get('improved') else 'not improved'})"
                                 if k.get("pct_change") is not None else f"{k['kpi']}: n/a" for k in e.get("kpis") or [])

            def target(phase):
                e, s = ph[phase], srow.get(phase, {})
                if "error" in e:
                    return "no scenario"
                g, t = s.get("util_target_achieved_glass", ""), s.get("util_target_achieved_batch", "")
                return "–" if g == "" else f"{yes.get(g, g)} / {yes.get(t, t)}"

            def addressed(e):
                if "error" in e:
                    return "no scenario"
                sc = e.get("scores") or {}
                return ", ".join(f"{g} {'yes' if sc.get(f'addressed_{g}') else 'no'}" for g in gs)

            def num(e, f):
                v = f(e) if "error" not in e else None
                return round(v, 4) if isinstance(v, (int, float)) else "–"

            it_no = ph["final"].get("iteration")
            fin_it = rec["loop"]["iterations"][it_no - 1] if it_no else {}
            mods = "\n".join(
                f"{m['parameter_type']} {m['direction']}: {w1.readable(m['target_element'], names[cid])}: "
                f"{w1.readable(str(m['baseline_value']), names[cid])} → {w1.readable(str(m['proposed_value']), names[cid])}"
                for m in ((fin_it or {}).get("patch") or {}).get("modifications", [])) or "–"
            put(ws2, row, [cid, rec["process"], "+".join(gs), rep, len(rec["loop"]["iterations"]), it_no or "–",
                           yn(ph["initial"]), yn(ph["final"]),
                           "–" if "error" in ph["initial"] else ("; ".join(ph["initial"].get("violations") or []) or "none"),
                           "–" if "error" in ph["final"] else ("; ".join(ph["final"].get("violations") or []) or "none"),
                           kpis(ph["initial"]), kpis(ph["final"]), allimp(ph["initial"]), allimp(ph["final"]),
                           target("initial"), target("final"),
                           addressed(ph["initial"]), addressed(ph["final"]),
                           num(ph["initial"], lambda e: (e.get("scores") or {}).get("recall")),
                           num(ph["final"], lambda e: (e.get("scores") or {}).get("recall")),
                           num(ph["initial"], lambda e: (e.get("improvement_ratio") or {}).get("CTD")),
                           num(ph["final"], lambda e: (e.get("improvement_ratio") or {}).get("CTD")),
                           "yes" if srow.get("final", {}).get("loop_discarded_better_initial") == "True" else "no",
                           mods])
            row += 1
    w1.style_sheet(ws2, [18, 12, 10, 5, 10, 9, 10, 10, 26, 26, 34, 34, 11, 11, 13, 13, 16, 16, 10, 10, 10, 10, 11, 60])

    # ---------------------------------------------------------------- sheet 3: settings
    ws3 = wb.create_sheet("Settings and scoring")
    w1.header(ws3, 1, ["Item", "Value"])
    s = config["settings"]
    rules = b.__doc__.split("SCORING", 1)[1].split("END SCORING")[0].strip()
    items = [
        ("How to read", "\"First proposal\" = the scenario GLASS proposes first; \"after refinement\" = the scenario "
         "GLASS selects after its simulation-based refinement (up to 4 iterations). On the first sheet each cell is "
         "v1 / v2 / v3 / v3.1; counts are out of 5 runs per configuration."),
        ("GLASS commit, batch v1", "8039058"),
        ("GLASS commit, batch v2", "5112fbc"),
        ("GLASS commits, batch v3", "33cf3cb, 9c6df0c, 9aa1dd4, 26617b7, 3f722a5 (records: 3f722a5; f6f1d30 in "
         "between changes only the config and the batch scripts)"),
        ("GLASS commits, batch v3.1", "v3 plus 1cf24c2 (direction filter by KPI category), 009f26e (relative budget "
         "enforced), 01f7df1 (calendar text parsed or reported), 01f76d6 (Prosimos period format accepted); "
         "records: 01f76d6. Same script, configuration and seeds as v3."),
        ("Model", s["model"]),
        ("Temperature schedule", "0.3 for the initial proposal; GLASS raises it by 0.1 per iteration "
         "(min(0.3 + 0.1 × (iteration − 1), 0.7)): 0.3 / 0.4 / 0.5 / 0.6. GLASS's context-summary and compliance "
         "calls run at 0.1."),
        ("Maximum iterations", s["max_iterations"]), ("Repetitions per configuration", s["repetitions"]),
        ("Seeds", f"{s['seeds']} (42–51), paired: baseline, first proposal and after refinement use the same seeds"),
        ("Cases per simulation", s["cases"]), ("Simulation start", s["start"]),
        ("Utilization target (v3)", "Every utilization KPI carries the threshold 0.85 (KPITarget.threshold, loop and "
         "multi-seed evaluation) and the description sentence \"Target: below 85 % of the available working time.\" "
         "(setup/stage2_config.json, kpi_inputs). GLASS's loop stops only when that target is achieved and every "
         "other KPI improved."),
        ("Target achieved: at most vs below 0.85", "GLASS's achieved flag is value ≤ 0.85; our rule is pool "
         "utilization < 0.85. They differ only for a value of exactly 0.85, and are computed on GLASS's role-resolved "
         "utilization and on the target pool's utilization of working time respectively."),
        ("Hour values applied as seconds (v1, v2)", "Before v3 the merger applied a proposed duration as a number in "
         "the element's own unit (seconds), so a proposal meant in hours became seconds (e.g. \"0.5 hours\" → 0.5 s). "
         "10 runs are affected (3 in v1, 7 in v2; unit_defect_runs_v1_v2.csv). They are not re-scored; their scores "
         "are kept as recorded."),
        ("Scoring rules (primary and secondary)", rules),
        ("Re-verification", "Each batch must be re-verified with GLASS at its own commit (v1 8039058, v2 5112fbc, "
         "v3 3f722a5, v3.1 01f76d6): the verifier re-merges the stored patches with the GLASS code checked out."),
    ]
    for i, (k, v) in enumerate(items, 2):
        put(ws3, i, [k, v])
    r = len(items) + 3
    ws3.cell(row=r, column=1, value="Version table (VERSIONS.md, verbatim)").font = w1.TITLE
    table = versions_table(vmd.split("**Full SHA-256 values.**")[0])
    w1.header(ws3, r + 1, table[0])
    for k, cells in enumerate(table[1:], r + 2):
        put(ws3, k, cells)
    w1.style_sheet(ws3, [38, 60, 60, 45, 45, 60, 22])

    wb.save(OUT)
    print(f"written {OUT.relative_to(P.REPO).as_posix()}")


if __name__ == "__main__":
    main()

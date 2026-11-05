"""
Build results/GLASS_loop_seeds.xlsx from the verified files of the loop replications experiment (values, no
formulas), in the style of the Stage-2 workbooks.

Sources: results/stage2_loop_seeds/loop_seeds_comparison.csv and .md, loop_seeds_runs.csv, the records and
summaries of results/stage2_loop_seeds/{seeds1,seeds5}/, setup/stage2_config.json.
Sheets: Loop seeds per configuration (seeds1 / seeds5, groups, ratios, tests), All 35 runs (seeds1),
All 35 runs (seeds5), Loop, run time and LLM calls per run, Settings and scoring.

Usage (repo root, environment with GLASS's dependencies and openpyxl):
  python evaluation/loanapp_p2p/scripts/make_loop_seeds_workbook.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from openpyxl import Workbook

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_stage2_workbook as w1  # noqa: E402  (header, style_sheet, BOLD, TITLE)
import make_stage2_v1_v2_v3_v31_workbook as w31  # noqa: E402  (fmt, put, versions_table)
import run_stage2_batch as b  # noqa: E402
from compare_loop_seeds import COUNTS, LABELS, MEANS, TARGET  # noqa: E402
from make_stage2_rag_workbook import runs_sheet  # noqa: E402

RES = HERE.parent / "results" / "stage2_loop_seeds"
ARMS = {"seeds1": RES / "seeds1", "seeds5": RES / "seeds5"}
OUT = HERE.parent / "results" / "GLASS_loop_seeds.xlsx"


def main() -> None:
    b.quiet()
    config = json.loads(b.CONFIG_PATH.read_text(encoding="utf-8"))
    loanapp = {**config, "configurations": [c for c in config["configurations"] if c["process"] == "LoanApp"]}
    rows = list(csv.DictReader(open(RES / "loop_seeds_comparison.csv", encoding="utf-8")))
    md = (RES / "loop_seeds_comparison.md").read_text(encoding="utf-8")
    wb = Workbook()

    ws = wb.active
    ws.title = "Loop seeds per configuration"
    w1.header(ws, 1, ["Configuration", "Has a utilization KPI"] + [LABELS[m] + " (seeds1 / seeds5)" for m in COUNTS + MEANS])
    r = 2
    for row in rows:
        util = row["has_utilization_kpi"]
        cells = [row["config_id"], {"True": "yes", "False": "no"}.get(util, "")]
        for m in COUNTS + MEANS:
            x, y = row[f"{m}_seeds1"], row[f"{m}_seeds5"]
            nd = 2 if m in MEANS else 0
            cells.append("–" if (m in TARGET and util == "False") or (x == "" and y == "")
                         else f"{w31.fmt(x, nd)} / {w31.fmt(y, nd)}")
        w31.put(ws, r, cells)
        if util == "":
            for j in range(1, len(cells) + 1):
                ws.cell(row=r, column=j).font = w1.BOLD
        r += 1
    r += 1
    for line in md.split("\n\n| Configuration")[0].splitlines()[2:] + \
            md.split("## Success and run time")[1].split("## Tests")[0].strip().splitlines():
        if line.startswith("- "):
            ws.cell(row=r, column=1, value=line[2:].replace("**", "").replace("`", ""))
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=10)
            r += 1
    r += 1
    ws.cell(row=r, column=1, value="seeds1 vs seeds5").font = w1.TITLE
    table = w31.versions_table(md.split("## Tests (seeds1 vs seeds5)")[1])
    w1.header(ws, r + 1, table[0])
    for k, cells in enumerate(table[1:], r + 2):
        w31.put(ws, k, cells)
    w1.style_sheet(ws, [26, 11] + [15] * len(COUNTS + MEANS))

    for arm, folder in ARMS.items():
        runs_sheet(wb, f"All 35 runs ({arm})", folder, loanapp)

    ws4 = wb.create_sheet("Loop, run time, LLM calls")
    runs = list(csv.DictReader(open(RES / "loop_seeds_runs.csv", encoding="utf-8")))
    cols = [("arm", "Arm"), ("config_id", "Configuration"), ("rep", "Rep"), ("iterations_used", "Iterations used"),
            ("final_iteration", "Final iteration"), ("early_stop", "Early stop after iteration 1"),
            ("early_stop_disagreeing", "Early stop, 10-seed final not all KPIs improved"),
            ("all_kpis_improved_final", "All KPIs improved (after refinement)"), ("valid_final", "Valid (after refinement)"),
            ("run_time_min", "Run time (min)"), ("loop_time_s", "Loop time (s)"), ("llm_calls", "LLM calls")]
    w1.header(ws4, 1, [h for _, h in cols])
    yes = {"True": "yes", "False": "no"}
    for i, x in enumerate(runs, 2):
        w31.put(ws4, i, [yes.get(x[k], x[k]) if x[k] != "" else "–" for k, _ in cols])
    w1.style_sheet(ws4, [9, 18, 5, 10, 9, 11, 14, 12, 10, 9, 9, 8])

    ws5 = wb.create_sheet("Settings and scoring")
    w1.header(ws5, 1, ["Item", "Value"])
    s = config["settings"]
    rec = {a: json.loads(next((f / "raw").glob("*/rep_1.json")).read_text(encoding="utf-8")) for a, f in ARMS.items()}
    rules = b.__doc__.split("SCORING", 1)[1].split("END SCORING")[0].strip()
    items = [
        ("How to read", "\"First proposal\" = the scenario GLASS proposes first; \"after refinement\" = the scenario "
         "GLASS selects after its simulation-based refinement (up to 4 iterations), or no scenario when the loop kept "
         "the baseline. On the first sheet each cell is seeds1 / seeds5; counts are out of 5 runs per configuration."),
        ("Arms", "seeds1: GLASS's refinement loop judges each iteration on one simulation run of the baseline and of "
         "the proposal (seed 42; the default). seeds5: on the mean KPIs of five paired runs (seeds 42-46); "
         "improved / worsened / unchanged, achieved targets, score, stop rule, best iteration and feedback use the "
         "means with the same thresholds (run_iterative_evaluation(..., loop_seeds=5))."),
        ("Configurations", "The seven LoanApp configurations of setup/stage2_config.json (G1, G2, G3, G1G2, G1G3, "
         "G2G3, G1G2G3)."),
        ("GLASS commit (records)", f"{rec['seeds1']['glass_commit']} / {rec['seeds5']['glass_commit']}"),
        ("Batch script SHA-256", f"{rec['seeds1']['script_sha256']} / {rec['seeds5']['script_sha256']}"),
        ("Configuration SHA-256", rec["seeds1"]["config_sha256"]),
        ("Model", s["model"]),
        ("Temperature schedule", "0.3 for the initial proposal; GLASS raises it by 0.1 per iteration "
         "(min(0.3 + 0.1 × (iteration − 1), 0.7))."),
        ("Maximum iterations", s["max_iterations"]), ("Repetitions per configuration", s["repetitions"]),
        ("Final evaluation", f"{s['seeds']} seeds (42–51), paired: baseline, first proposal and after refinement "
         "use the same seeds; identical in both arms"),
        ("Cases per simulation", s["cases"]), ("Simulation start", s["start"]), ("Workers", "4 in both arms"),
        ("Early stop", "The loop stopped after iteration 1 because its own check found every target met (one run in "
         "seeds1, the mean of five in seeds5). Disagreeing: the 10-seed final evaluation of that scenario does not "
         "have all KPIs improved (batch rule)."),
        ("Scoring rules (primary and secondary)", rules),
    ]
    for i, (k, v) in enumerate(items, 2):
        w31.put(ws5, i, [k, v])
    w1.style_sheet(ws5, [38, 100])

    wb.save(OUT)
    print(f"written {OUT.relative_to(HERE.parents[2]).as_posix()}")


if __name__ == "__main__":
    main()

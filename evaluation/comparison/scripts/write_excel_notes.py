"""Rewrite the `Notes` sheet of the curated workbook comparison/comparison.xlsx.

The sheet describes only what the workbook contains: the `Summary` sheet (the columns kept there) and the
`All comparisons` sheet. The last two sections (KPI computation problems, cross-check against the GLASS records)
are copied from comparison_notes.md, so the checks stay in sync with the last run of the comparison script.
Other sheets are not touched.

Usage:  python -I comparison/scripts/write_excel_notes.py [path to comparison.xlsx]
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font

COMPARISON = Path(__file__).resolve().parents[1]
WORKBOOK = Path(sys.argv[1]) if len(sys.argv) > 1 else COMPARISON / "comparison.xlsx"
NOTES_MD = COMPARISON / "comparison_notes.md"

COLUMNS = [
    ("config", "GLASS configuration: process and goals (G1 end-to-end time, G2 local waiting, G3 resource workload)"),
    ("variant", "Which of my model sets: extended_hours (working hours extended to 09:00-19:00) or extra_resources (more people in a role)"),
    ("model", "Folder name of my model (Procure2Pay_G1G2 has hours_1 and hours_2)"),
    ("kpi_expected", "Expected KPI of the config (EXPECTED_KPIS.md)"),
    ("baseline_mean", "Unchanged baseline, mean over the 10 seeds (42-51)"),
    ("mine_mean", "My model, mean over the 10 seeds"),
    ("glass_mean_5reps", "GLASS Stage 2 v3.1: mean over the 5 reps (each rep = its best iteration) and the 10 seeds"),
    ("glass_rep_min, glass_rep_max", "Lowest and highest of the 5 reps' own 10-seed means"),
    ("mine_vs_baseline_pct", "(baseline - mine) / baseline x 100. Positive = lower than the baseline"),
    ("glass_vs_baseline_pct", "The same for glass_mean_5reps"),
    ("diff_mine_minus_glass", "Mean over the seeds of (mine - GLASS 5-rep mean). Negative = mine is lower"),
    ("ci_low, ci_high", "95% confidence interval of that difference (t distribution, n = 10 seeds)"),
    ("verdict", "'mine lower' if the interval is below zero, 'GLASS mean of 5 reps lower' if above zero, "
                "'no significant difference' if it contains zero"),
]

# (kind, text) with kind in: h (heading), p (paragraph), b (bullet)
BODY = [
    ("h", "What the workbook shows"),
    ("p", "Sheet Summary has one row per config x variant x expected KPI. Cycle time and waiting time are in hours, "
          "utilization is a ratio. Lower is better for all three. Sheet All comparisons lists every comparison "
          "(mine vs baseline, GLASS vs baseline, mine vs GLASS; each GLASS rep separately and the mean of the 5 reps) "
          "with the paired and Welch p-values."),
    ("table", ""),
    ("h", "Scope"),
    ("b", "16 models of mine in 13 configs (a config in both extended_hours and extra_resources gets two rows)."),
    ("b", "Each is compared with the same config in the GLASS Stage 2 v3.1 final logs (5 reps) and with the unchanged "
          "baseline, on seeds 42-51."),
    ("b", "Procure2Pay_G1 is not compared: there is no model of it in my results."),
    ("h", "KPI definitions (GLASS kpi_computation.py, the same code for all sources)"),
    ("p", "Every KPI is computed per log (one log = one model or one rep on one seed), which gives 10 values per source. "
          "The table reports the mean of these 10 values."),
    ("p", "Average Cycle Time: per case, last end_time minus first start_time in hours; mean over the 1000 cases of the log."),
    ("p", "Waiting time of an activity (Assess loan risk for LoanApp, Release Supplier's Invoice for Procure2Pay): in each "
          "case the events are sorted by start_time; for every occurrence of the activity the wait is its start_time minus "
          "the previous event's end_time. Only positive gaps count; the value is the mean over all such occurrences, in hours."),
    ("p", "Utilization of a role (Loan Officer for LoanApp, Purchasing Agent for Procure2Pay):"),
    ("b", "1. Period: from the simulation start (2023-02-09 10:00) to the last end_time in the log, as local wall-clock "
          "time. Each log uses its own period."),
    ("b", "2. Available time of a person: the person's calendar (Mon-Fri 09:00-17:00, or 09:00-19:00 for the roles of the "
          "extended_hours models) cut to the period; all working windows inside the period are added up."),
    ("b", "3. Busy time of a person: every activity the person performed, cut to the working windows (the part outside "
          "working hours is not counted)."),
    ("b", "4. Utilization of a person = busy time / available time."),
    ("b", "5. Utilization of the role = mean over the people of that role. The person-to-role link comes from "
          "resource_profiles of each log's own Prosimos JSON."),
    ("b", "6. Repeated for seeds 42-51."),
    ("p", "Example (made-up numbers): two Loan Officers. A: busy 700 h, available 800 h = 0.875. B: busy 500 h, available "
          "800 h = 0.625. Role utilization = (0.875 + 0.625) / 2 = 0.75."),
    ("b", "extended_hours: the affected role has more available time (10 h a day instead of 8) and the window for busy "
          "time is longer too, so utilization can drop just from the longer day."),
    ("b", "extra_resources: the same work is shared by more people, so the mean per person drops."),
    ("b", "The period ends with the log, so a model that finishes earlier is measured over a shorter period. An "
          "overloaded baseline (e.g. 0.997) means the person is almost always busy until the end of the log."),
    ("b", "GLASS target rule for utilization: role utilization below 0.85 and below the baseline. The reason for the "
          "0.85 limit is not documented in the repository."),
    ("h", "Statistics"),
    ("p", "diff_mine_minus_glass is the mean of the 10 per-seed differences (my value for seed s minus the GLASS 5-rep mean "
          "for seed s; that mean is taken over the 5 reps for the same seed). ci_low and ci_high are the mean difference "
          "+- t(0.975, 9) x standard error. mine_vs_baseline_pct and glass_vs_baseline_pct use the baseline mean of the "
          "same config."),
    ("h", "Reading the table"),
    ("b", "For utilization, a lower value can also mean idle capacity: look at the value, not only at the verdict."),
    ("b", "Where the baseline is overloaded (LoanApp G1G3 and G1G2G3: cycle time about 2250 hours) the queue grows during "
          "the simulation, so times and percentages there are dominated by the queue."),
    ("b", "Seed pairing is weak between different models (their random streams drift after the first change); read the "
          "confidence interval with that in mind."),
]


def md_sections(*titles: str) -> list[str]:
    """Lines of the given '## ' sections of comparison_notes.md (heading excluded)."""
    out, keep = [], False
    for line in NOTES_MD.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            keep = line[3:].strip() in titles
            if keep:
                out.append("\0" + line[3:].strip())
            continue
        if keep and line.strip():
            out.append(line)
    return out


def main() -> None:
    wb = load_workbook(WORKBOOK)
    if "Notes" in wb.sheetnames:
        del wb["Notes"]
    ws = wb.create_sheet("Notes", 0)
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 120
    wrap = Alignment(wrap_text=True, vertical="top")
    row = 1

    def line(text, bold=False, height_per=118):
        nonlocal row
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        c = ws.cell(row=row, column=1, value=text)
        c.alignment = wrap
        c.font = Font(bold=bold)
        ws.row_dimensions[row].height = 15 * max(1, math.ceil(len(text) / height_per))
        row += 1

    line("Comparison notes", bold=True)
    row += 1
    for kind, text in BODY:
        if kind == "h":
            row += 1
            line(text, bold=True)
        elif kind == "table":
            for name, meaning in COLUMNS:
                a = ws.cell(row=row, column=1, value=name)
                b = ws.cell(row=row, column=2, value=meaning)
                a.alignment, b.alignment = wrap, wrap
                a.font = Font(bold=True)
                ws.row_dimensions[row].height = 15 * max(1, math.ceil(len(meaning) / 115))
                row += 1
        elif kind == "b":
            line("- " + text)
        else:
            line(text)
    for item in md_sections("KPI computation problems", "Cross-check against the GLASS records (baseline per-seed values)"):
        if item.startswith("\0"):
            row += 1
            line(item[1:], bold=True)
        else:
            line(item.replace("`", "").strip())
    wb.save(WORKBOOK)
    print("Notes sheet rewritten in", WORKBOOK, "- sheets:", wb.sheetnames)


if __name__ == "__main__":
    main()

"""Apply 'directly applicable' GLASS ideas (LoanApp, Procure2Pay) to the BPS models.

Two levers only: extended working hours (+2h for one role, via a new timetable)
and extra resources (+EXTRA people on named roles). Originals are not touched;
output goes to bps_models_improved/ plus a change log CSV.
"""
import csv, re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "bps_models"
OUT = SRC.parent / "bps_models_improved"
EXTRA = 2  # workbook allows at most +3 people per role; +2 chosen by the analyst

# (config, source model, lever, roles, idea title from applicability.xlsx)
# Roles follow "Roles to change" in the "How to apply in the simulation" column of
# applicability_updated.xlsx. G2 / G2G3 hours name no role ("analyst has to choose"):
# Loan Officer chosen (performs "Assess loan risk", the G2 target step).
JOBS = [
    ("LoanApp_G1",     "LoanApp_durations",       "hours",     ["Clerk", "Loan Officer"], "Extended working hours for critical roles"),
    ("LoanApp_G1G2",   "LoanApp_G1G2",            "hours",     ["Clerk", "Loan Officer"], "Extended working hours for critical roles"),
    ("LoanApp_G1G2G3", "LoanApp_G1G2G3",          "hours",     ["Clerk", "Loan Officer"], "Empower resources by extending working hours for critical roles"),
    ("LoanApp_G1G2G3", "LoanApp_G1G2G3",          "resources", ["AML Investigator", "Appraiser", "Loan Officer"], "Deploy Extra Resources for AML Checks and Appraisals"),
    ("LoanApp_G1G3",   "LoanApp_G1G3",            "resources", ["AML Investigator", "Appraiser"], "Deploy extra resources for the appraisal and AML check"),
    ("LoanApp_G2",     "LoanApp_extraneous",      "hours",     ["Loan Officer"], "Empower resources by extending working hours for loan assessment roles"),
    ("LoanApp_G2G3",   "LoanApp_G2G3",            "hours",     ["Loan Officer"], "Empower resources by extending working hours"),
    ("LoanApp_G2G3",   "LoanApp_G2G3",            "resources", ["Clerk", "Loan Officer"], "Deploy extra resources for high-demand periods"),
    ("LoanApp_G3",     "LoanApp_resource_target", "resources", ["Loan Officer"], "Deploy Extra Resources for High Workload Periods"),
    # --- Procure2Pay (roles from "Roles to change" in applicability_updated.xlsx) ---
    # G1G2 has two separate extended-hours ideas (different inefficiencies) -> two files, tags hours_1 / hours_2
    ("Procure2Pay_G1G2",   "Procure2Pay_G1G2",   "hours",     ["Financial Manager"], "Extended working hours for financial managers during peak times", "hours_1"),
    ("Procure2Pay_G1G2",   "Procure2Pay_G1G2",   "hours",     ["Financial Manager", "Purchasing Agent"], "Empower resources by extending working hours for key roles", "hours_2"),
    ("Procure2Pay_G1G2G3", "Procure2Pay_G1G2G3", "resources", ["Purchasing Agent"], "Deploy extra resources in the procure-to-pay process"),
    ("Procure2Pay_G1G3",   "Procure2Pay_G1G3",   "resources", ["Purchasing Agent"], "Deploy extra resources in the procure-to-pay process"),
    # G2 hours: workbook says "analyst has to choose" -> Supplier performs "Release Supplier's Invoice" (the G2 target step)
    ("Procure2Pay_G2",     "Procure2Pay_extraneous", "hours", ["Supplier"], "Empower Resources by Extending Working Hours"),
    ("Procure2Pay_G2G3",   "Procure2Pay_G2G3",   "resources", ["Purchasing Agent"], "Deploy extra resources for invoice processing"),
    ("Procure2Pay_G3",     "Procure2Pay_resource_target", "resources", ["Purchasing Agent"], "Deploy extra resources in the procure-to-pay process"),
]

EXT_TT_ID = "qbp_ext_hours_09_19"
EXT_TT = (f'<qbp:timetable id="{EXT_TT_ID}" name="Business Hours - Extended (09-19)">'
          '<qbp:rules id="qbp_ext_hours_rules"><qbp:rule id="qbp_ext_hours_rule" name="Timeslot" '
          'fromTime="09:00:00.000+00:00" toTime="19:00:00.000+00:00" '
          'fromWeekDay="MONDAY" toWeekDay="FRIDAY" /></qbp:rules></qbp:timetable>')


def res_re(role):
    return re.compile(r'(<qbp:resource id="[^"]+" name="%s" totalAmount=")(\d+)("[^>]*?)timetableId="[^"]+"' % re.escape(role))


def apply(xml, lever, roles, log):
    for role in roles:
        m = res_re(role).search(xml)
        assert m, f"resource {role} not found"
        if lever == "resources":
            old = int(m.group(2)); new = old + EXTRA
            xml = xml[:m.start(2)] + str(new) + xml[m.end(2):]
            log.append((role, "totalAmount", old, new))
        else:
            xml = xml[:m.end()-len(re.search(r'timetableId="[^"]+"$', m.group(0)).group(0))] + f'timetableId="{EXT_TT_ID}"' + xml[m.end():]
            log.append((role, "timetable/toTime", "17:00", "19:00"))
    if lever == "hours":
        assert "</qbp:timetables>" in xml
        xml = xml.replace("</qbp:timetables>", EXT_TT + "</qbp:timetables>", 1)
    return xml


def main():
    OUT.mkdir(exist_ok=True)
    rows = []
    for cfg, model, lever, roles, idea, *tag in JOBS:
        tag = tag[0] if tag else lever
        xml = (SRC / f"{model}.bpmn").read_text(encoding="utf-8")
        log = []
        xml = apply(xml, lever, roles, log)
        # lever is in both the folder name and the file-name tag
        name = f"{'extended_hours' if lever == 'hours' else 'extra_resources'}/{cfg}__{tag}.bpmn"
        (OUT / name).parent.mkdir(exist_ok=True)
        (OUT / name).write_text(xml, encoding="utf-8")
        for role, attr, old, new in log:
            rows.append([cfg, model + ".bpmn", name, idea, lever, role, attr, old, new])
    with open(OUT / "change_log.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["config", "source_model", "output_model", "idea", "lever", "role", "attribute", "before", "after"])
        w.writerows(rows)
    print(f"wrote {len(JOBS)} models, {len(rows)} changes -> {OUT}")


if __name__ == "__main__":
    main()

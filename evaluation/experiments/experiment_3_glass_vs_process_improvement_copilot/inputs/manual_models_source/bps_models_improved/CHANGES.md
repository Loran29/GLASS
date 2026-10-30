# LoanApp and Procure2Pay BPMN changes (directly applicable ideas)

Source: `evaluation/claude_sess/applicability_updated.xlsx` (sheet "All ideas", columns "Roles / resources named in the idea" and "How to apply in the simulation") → the LoanApp (9) and Procure2Pay (7) ideas classified "Directly applicable". The LoanApp sections come first; Procure2Pay is described in its own section at the end.
Original models are in `bps_models/`, modified models in `bps_models_improved/`. Files are named `<config>__hours.bpmn` or `<config>__resources.bpmn` and sit in the matching folder `extended_hours/` or `extra_resources/`. The originals were not touched.
Script: `scripts/apply_directly_applicable_loanapp.py`. Per-change log: `change_log.csv`.

There are only two kinds of change, both in the QBP simulation block (`<qbp:resources>` and `<qbp:timetables>`).

---

## Type 1: Extended working hours (+2 hours per day)

Idea: extend one role's daily working hours from 09:00-17:00 to 09:00-19:00.

### Why a new timetable?
All roles (Clerk, Loan Officer, AML Investigator, Appraiser, Senior Officer, Applicant) share the same `QBP_DEFAULT_TIMETABLE`. Changing it directly to 19:00 would have extended everyone's hours. So a new timetable was added and only the roles named in the workbook were pointed at it.

### Before (original)
Timetable (unchanged, still present):
```xml
<qbp:timetable id="QBP_DEFAULT_TIMETABLE" default="true" name="Business Hours">
  <qbp:rules id="qbp_7eea8786-027e-ab6d-7785-e84f8cd37493">
    <qbp:rule id="b9da42af-0b3e-4fd7-86c6-94b351ca1994" name="Timeslot"
      fromTime="09:00:00.000+00:00" toTime="17:00:00.000+00:00"
      fromWeekDay="MONDAY" toWeekDay="FRIDAY" />
  </qbp:rules>
</qbp:timetable>
```
Loan Officer (totalAmount varies by model, e.g. 1 in G2G3, 4 in G1):
```xml
<qbp:resource id="qbp_e6c371a1-c8f5-49da-9e5c-50e0c4def74f" name="Loan Officer" totalAmount="1" timetableId="QBP_DEFAULT_TIMETABLE" />
```

### After (modified)
A new timetable was inserted just before `</qbp:timetables>`:
```xml
<qbp:timetable id="qbp_ext_hours_09_19" name="Business Hours - Extended (09-19)">
  <qbp:rules id="qbp_ext_hours_rules">
    <qbp:rule id="qbp_ext_hours_rule" name="Timeslot"
      fromTime="09:00:00.000+00:00" toTime="19:00:00.000+00:00"
      fromWeekDay="MONDAY" toWeekDay="FRIDAY" />
  </qbp:rules>
</qbp:timetable>
```
Only the named roles' `timetableId` changed (Loan Officer shown; Clerk changes the same way):
```xml
<qbp:resource id="qbp_e6c371a1-c8f5-49da-9e5c-50e0c4def74f" name="Loan Officer" totalAmount="1" timetableId="qbp_ext_hours_09_19" />
```
Roles not named in the idea stay on `QBP_DEFAULT_TIMETABLE`.

### Models
| Config | Source model | Output model | Roles (from workbook) |
|---|---|---|---|
| G1 | `LoanApp_durations` | `extended_hours/LoanApp_G1__hours.bpmn` | Clerk, Loan Officer |
| G1G2 | `LoanApp_G1G2` | `extended_hours/LoanApp_G1G2__hours.bpmn` | Clerk, Loan Officer |
| G1G2G3 | `LoanApp_G1G2G3` | `extended_hours/LoanApp_G1G2G3__hours.bpmn` | Clerk, Loan Officer |
| G2 | `LoanApp_extraneous` | `extended_hours/LoanApp_G2__hours.bpmn` | Loan Officer (analyst choice, see below) |
| G2G3 | `LoanApp_G2G3` | `extended_hours/LoanApp_G2G3__hours.bpmn` | Loan Officer (analyst choice, see below) |

Role choice: for G1, G1G2 and G1G2G3 the workbook names "Clerk, Loan officer". For G2 and G2G3 it says "Not specified in the idea; the analyst has to choose", so the Loan Officer was chosen. This is an assumption; see "Ambiguities and assumptions" below.

---

## Type 2: Deploy extra resources (+2 people per role)

Idea: increase a role's headcount (`totalAmount`). The workbook says "at most +3 people per role" without giving an exact number, so +2 was chosen (`EXTRA = 2` in the script). See "Ambiguities and assumptions".

### Before → After
Only `totalAmount` changed; nothing else did.

| Config | Output model | Role | Before | After |
|---|---|---|---|---|
| G1G2G3 | `extra_resources/LoanApp_G1G2G3__resources.bpmn` | AML Investigator | 2 | 4 |
| G1G2G3 | `extra_resources/LoanApp_G1G2G3__resources.bpmn` | Appraiser | 2 | 4 |
| G1G2G3 | `extra_resources/LoanApp_G1G2G3__resources.bpmn` | Loan Officer | 1 | 3 |
| G1G3 | `extra_resources/LoanApp_G1G3__resources.bpmn` | AML Investigator | 2 | 4 |
| G1G3 | `extra_resources/LoanApp_G1G3__resources.bpmn` | Appraiser | 2 | 4 |
| G2G3 | `extra_resources/LoanApp_G2G3__resources.bpmn` | Clerk | 8 | 10 |
| G2G3 | `extra_resources/LoanApp_G2G3__resources.bpmn` | Loan Officer | 1 | 3 |
| G3 | `extra_resources/LoanApp_G3__resources.bpmn` (source: `LoanApp_resource_target`) | Loan Officer | 1 | 3 |

Example (G1G3, AML Investigator):
```xml
<!-- before -->
<qbp:resource id="qbp_71f9ab6f-0283-4a62-bd00-a1256716dd47" name="AML Investigator" totalAmount="2" timetableId="QBP_DEFAULT_TIMETABLE" />
<!-- after -->
<qbp:resource id="qbp_71f9ab6f-0283-4a62-bd00-a1256716dd47" name="AML Investigator" totalAmount="4" timetableId="QBP_DEFAULT_TIMETABLE" />
```
Roles for every resource idea are taken from "Roles to change" in the workbook.

---

## Procure2Pay

Same two levers, same mechanism as above (new `qbp_ext_hours_09_19` timetable for hours; `totalAmount` +2 for resources). Roles come from "Roles to change" in the workbook. The Procure2Pay default timetable is also Mon-Fri 09:00-17:00, shared by all roles.

### Extended working hours (`extended_hours/`)
| Config | Source model | Output model | Roles |
|---|---|---|---|
| G1G2 (idea 1) | `Procure2Pay_G1G2` | `Procure2Pay_G1G2__hours_1.bpmn` | Financial Manager |
| G1G2 (idea 2) | `Procure2Pay_G1G2` | `Procure2Pay_G1G2__hours_2.bpmn` | Financial Manager, Purchasing Agent |
| G2 | `Procure2Pay_extraneous` | `Procure2Pay_G2__hours.bpmn` | Supplier (analyst choice, see below) |

G1G2 has two separate extended-hours ideas (from two different inefficiencies, with different role lists), so it gets two files.

Role choice for G2: the workbook says "Not specified in the idea; the analyst has to choose". The G2 target KPI is the waiting time of "Release Supplier's Invoice", which the model assigns to the Supplier role, so Supplier was chosen. Supplier is an external party, so this is an assumption; the alternative would be Financial Manager as in G1G2. To change it, edit the roles list in the script and re-run.

### Extra resources (`extra_resources/`)
| Config | Output model | Role | Before | After |
|---|---|---|---|---|
| G1G2G3 | `Procure2Pay_G1G2G3__resources.bpmn` | Purchasing Agent | 3 | 5 |
| G1G3 | `Procure2Pay_G1G3__resources.bpmn` | Purchasing Agent | 3 | 5 |
| G2G3 | `Procure2Pay_G2G3__resources.bpmn` | Purchasing Agent | 3 | 5 |
| G3 | `Procure2Pay_G3__resources.bpmn` (source: `Procure2Pay_resource_target`) | Purchasing Agent | 3 | 5 |

The workbook's role text mentions "Supplier (external)" in the role column, but "Roles to change" lists only Purchasing Agent, so only Purchasing Agent was changed.

---

## Ambiguities and assumptions

The idea PDFs name no resource for some ideas, and the workbook marks them "Not specified in the idea; the analyst has to choose". For these the role was derived from the configuration's Expected KPI (and the idea title where it helps). These choices are assumptions, not facts from the source.

| Config | Idea | What the source says | Role chosen | Why | Alternative |
|---|---|---|---|---|---|
| LoanApp_G2 | Empower resources by extending working hours for loan assessment roles | No role named. Title says "loan assessment roles" | Loan Officer | Title points to the roles assessing the loan; Expected KPI is "Assess loan risk" waiting time; the Loan Officer performs "Assess loan risk" | "Roles" is plural and may cover more roles (e.g. Clerk, Senior Officer) |
| LoanApp_G2G3 | Empower resources by extending working hours | No role named, title is generic | Loan Officer | Expected KPIs: "Assess loan risk" waiting time and Loan Officer utilization, both Loan Officer | Same as above |
| Procure2Pay_G2 | Empower Resources by Extending Working Hours | No role named, only "staff involved in the procure-to-pay process" | Supplier | Expected KPI is "Release Supplier's Invoice" waiting time; the model assigns that task to Supplier | Supplier is an external party, so Financial Manager (internal, Authorize payment / Pay invoice) or all roles are plausible |

Other points to keep in mind:
- **Roles inferred by the idea generator.** Where the workbook says "(from motivation/impact)", the roles were read from the idea's motivation/impact text, not stated in the idea itself (e.g. Clerk for LoanApp G1/G1G2/G1G2G3 hours; Loan Officer for LoanApp_G1G2G3 resources; Clerk for LoanApp_G2G3 resources).
- **Procure2Pay "Supplier (external)".** The role column lists it for the resource ideas, but "Roles to change" lists only Purchasing Agent. Only Purchasing Agent was changed.
- **Procure2Pay_G1G2.** It has two separate extended-hours ideas with different role lists, so two files (`hours_1`, `hours_2`) were produced and not merged.
- **Magnitude.** The workbook gives an upper bound for each lever: "up to 2 hours per day" for working hours and "at most +3 people per role" for resources. For working hours the bound is exact, so +2 h (09:00-19:00) was applied. For resources no exact number is given, so +2 people per role was chosen (below the +3 maximum). The +2 is the analyst's choice (`EXTRA = 2` in the script).
- **Cause of waiting time not verified.** Linking a waiting-time KPI to the role performing the task is a reasoning step; it has not been confirmed by simulation.

---

## Not changed
- BPMN process structure (tasks, gateways, flows), task durations, case arrival distribution, probabilities.
- "Applicable with assumption" and "Not applicable" ideas. These need an assumed effect size, which would have to be invented.
- Procure2Pay_G1 (no directly applicable idea in the workbook).

## Validation
1. `git diff --no-index --word-diff=porcelain bps_models/<source>.bpmn bps_models_improved/<folder>/<output>.bpmn`: only the differences above should appear.
2. Load the file in BIMP and check role counts and timetables in the Resources tab.
3. Simulate the original and modified model with the same settings and seed, then compare KPIs. Not done yet.

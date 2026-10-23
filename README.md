# GLASS — Goal-based LLM-Assisted Simulation Studio

**LLM-based generation of business process simulation parameters from goals and event logs.**

Bachelor thesis project — TU Munich, School of Computation, Information and Technology (Heilbronn).

---

## What this project does

GLASS is a Streamlit web application that turns a natural-language simulation goal plus an event log into a valid, goal-oriented set of BPM simulation parameters. It bridges the gap between what a stakeholder wants (a goal) and what a simulator like Prosimos needs (concrete parameter changes on top of a discovered baseline).

The full pipeline runs in a single Streamlit app split into three workspaces: **Goal to KPI**, **Scenario Studio**, and **Scenario Evaluation**.

---

## Getting started (no programming experience needed)

The fastest path is Docker.

1. **Install Docker Desktop** — [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop). Wait until the whale icon shows "Docker Desktop is running".
2. **Get an OpenRouter API key** — [openrouter.ai](https://openrouter.ai) → sign up → **Keys** → create key. OpenRouter gives access to many models through one key.
3. **Add your key** — copy `goal_to_parameters/.env.example` to `goal_to_parameters/.env` and paste your key next to `OPENROUTER_API_KEY=`.
4. **Start** — from the repo root:
   ```bash
   docker compose up --build
   ```
   Open [http://localhost:8501](http://localhost:8501).

To stop: `Ctrl+C` then `docker compose down`.

### Common issues

| Problem | Fix |
|---|---|
| Docker Desktop not running | Start it from the Start menu / Applications; wait for "Running". |
| Port 8501 in use | Change `8501:8501` to `8502:8501` in `docker-compose.yml`. |
| LLM calls fail | Check your API key has no trailing spaces and has credits. |
| `.env` changes ignored | Run `docker compose down` then `docker compose up --build`. |

---

## Developer setup

Requires Python 3.11+.

```bash
make setup          # creates .venv, installs deps, copies .env template
make run            # starts Streamlit
make test           # runs the test suite
```

Or manually:
```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
cp goal_to_parameters/.env.example goal_to_parameters/.env
streamlit run goal_to_parameters/app.py
```

---

## Repository layout

```
goal_to_parameters/       # main application
  app.py                  # Streamlit entry point
  config.yaml             # provider and model defaults
  llm/                    # provider adapters (Ollama, OpenAI, Anthropic, OpenRouter, HuggingFace)
  prompts/                # SMART-KPI and ScenarioPatch prompt templates + few-shot examples
  models/                 # Pydantic schemas for KPIs
  knowledge/              # hybrid RAG (BM25 + dense embeddings + cross-encoder + PDF chunks)
  second_llm/             # scenario generation, patch validation, merger, Prosimos runner, evaluation
  ui/                     # Streamlit panels for the three workspaces
  utils/                  # log profiling, context-factor screening, parsing, semantic validation
  examples/               # ready-to-use process descriptions, event logs, and full second-LLM data sets
tests/                    # unit and integration tests
evaluation/               # thesis evaluation harness — stage 1 & 2 pipelines, SIMOD baselines, results
Papers/CaseStudy/         # RAG corpus (case-study PDFs for evidence retrieval)
```

---

## Supported LLM providers

Configured in [`goal_to_parameters/config.yaml`](./goal_to_parameters/config.yaml); secrets in [`goal_to_parameters/.env`](./goal_to_parameters/.env.example).

| Provider | Key | Notes |
|---|---|---|
| **OpenRouter** (default) | `OPENROUTER_API_KEY` | Recommended; routes to many hosted models. |
| **OpenAI** | `OPENAI_API_KEY` | GPT-4o / GPT-4o-mini. |
| **Anthropic** | `ANTHROPIC_API_KEY` | Claude family. |
| **HuggingFace** | `HUGGINGFACE_API_TOKEN` | Uses `InferenceClient`. |
| **Ollama** | `OLLAMA_BASE_URL` (default `http://localhost:11434`) | Local, zero-cost inference. |

---

## Design choices worth flagging

- **Delta-only patches over full-scenario regeneration.** The second LLM emits a `ScenarioPatch` describing what to change, not a full SimuBridge scenario. A deterministic Python merger applies it onto the SIMOD baseline. This eliminates the token cost and hallucination risk of asking an LLM to re-emit the baseline.
- **Grounded RAG evidence.** Second-stage prompts are populated with retrieved case-study passages (hybrid BM25 + dense + cross-encoder over the `Papers/CaseStudy/` corpus) so proposed modifications cite prior literature.
- **Statistical hardening in context analysis.** Context-factor screening uses Benjamini-Hochberg FDR correction on top of Spearman / Kruskal-Wallis tests with practical effect-size thresholds — statistical significance alone is not enough to make it into the KPI prompt.
- **Post-generation feasibility enforcement.** After patch generation, dedicated validators check the patch against operational constraints captured in the clarification chat (max headcount, budget ceiling `Δ × costHour × weekly_hours × 4.33`, immutable elements, overtime and shift-extension restrictions). Violations trigger targeted retries with the computed overshoot.
- **Human-in-the-loop review.** Stage 1 requires the user to accept or reject each KPI individually before stage 2 can consume the output.

---

## Timer / extraneous-delay events

**Previous behaviour.** Stage 2 lost every timer delay discovered by SIMOD. `second_llm/simod_to_simubridge.py` ignored the baseline's `event_distribution`, and `second_llm/prosimos_runner.py::scenario_to_prosimos_json` exported every BPMN intermediate catch event with duration 0 (`expon`, mean 0). Baseline and proposed simulations therefore ran without extraneous waiting time, and the second LLM could neither see nor change it.

**What changed** (applies from the commit that adds this section, the first commit after `bddc958`):
- **Import.** Each `event_distribution` entry becomes an `IntermediateEvent` on `modelParameter.intermediateEvents`. Its display name is `Timer before <successor activity>`, falling back to the event id; predecessor and successor are resolved from the BPMN; the distribution is parsed by the existing activity-duration code.
- **Export.** Stored events are written back with their (possibly patched) distribution. BPMN intermediate events not held on the scenario keep the zero default.
- **Patch type.** New `event_duration` patch parameter type, targeting an intermediate event by display name or id. It is applied by `scenario_merger._apply_event_duration`. The validator rejects unknown events and negative durations, and cost estimation treats it as cost-neutral.
- **LLM visibility.** When the baseline has timers, the patch prompt lists them (name, id, predecessor → successor, current mean) and adds `event_duration` to the allowed types and to the constrained-decoding enum. For baselines without timers, the prompt, schemas and Prosimos export are byte-identical to before; `tests/test_event_duration.py` checks this against fingerprints taken at `bddc958`.

**Example modification** (remove a timer's waiting time):

```json
{"parameter_type": "event_duration", "target_element": "Timer before <activity>",
 "baseline_value": "<current mean>", "proposed_value": 0}
```

**Known limitations.** Timer distributions share the existing activity-duration conversion. `fix`, the `expon` mean, and the `norm` mean and std round-trip exactly. The `expon` and `norm` min/max bounds are regenerated on export (0 and 10× or 3× the mean). The `norm` std was imported as if it were the variance until the commit "Fix normal-distribution std being treated as variance in scenario conversion"; it is now squared on import into SimuBridge's (mean, variance) form and square-rooted on export. Likewise, a standard deviation proposed by the LLM (`std`, `sigma`, `std_dev`, `stdev`, `standard_deviation`, `std_hours`, `std_minutes`) was stored unsquared as the variance until the commit "Fix LLM-proposed normal std being treated as variance"; it is now squared, and an explicit `variance`/`var` keeps its value. The legacy full-scenario flow (`generate_scenario_proposal`, not used by the orchestrator) and hand-uploaded scenario JSON written before this change carry no `intermediateEvents`, so their timers still export as 0.

---

## Calendars

**Previous behaviour.** `second_llm/prosimos_runner.py::scenario_to_prosimos_json` replaced the baseline's calendars on export:
- The arrival calendar was hard-coded to 24/7 (the baseline's `arrival_time_calendar` was never imported). For a baseline whose cases arrive Mon–Fri 09–17, cases then arrived around the clock, which roughly tripled the daily load.
- Every weekday missing from a resource calendar was filled with that calendar's hours, a workaround for a Prosimos `CalendarIterator` crash. A Mon–Fri 09–17 calendar therefore became Mon–Sun 09–17.

Together these made simulated cycle and waiting times differ from the baseline by up to 40×.

**What changed** (applies from the commit "Fix arrival and resource calendars being replaced by 24/7 on export", the first commit after `cb9cb17`):
- **Arrival calendar.** It is imported into `StartEvent.arrivalCalendar` and exported as `arrival_time_calendar`. A scenario without one (for example a baseline without `arrival_time_calendar`, or a legacy full-scenario proposal) still exports 24/7.
- **Resource calendars.** They are exported as they are. Prosimos 2.x handles calendars without weekend days. Only a calendar with no periods at all falls back to Mon–Sun 09–17.
- **Unchanged outputs.** The new field is hidden from the LLM schemas and left out of the serialised output when empty. Baselines whose calendars are already 24/7 export byte-identically to before; `tests/test_calendars.py` checks this against fingerprints taken at `cb9cb17`.

**Known limitation.** Calendar times are whole hours (`TimetableItem` stores integer hours), as before for resource calendars.

**Role calendars** (from the commit "Fix role calendar import/patching applying to one resource only"). Previously, a baseline with one calendar per resource (as converted from BIMP) had each role bound to its *last* resource's calendar, and every resource of the role was exported with that calendar. The other resources' calendars stayed in the scenario unused, so a `resource_calendar` modification that named one of them was accepted but changed nothing. Now:
- **Import:** each resource keeps its own calendar (`Resource.calendar`, set only when it differs from the role's schedule, hidden from the LLM schemas and omitted when unset), and the export uses it.
- **Patching:** a `resource_calendar` modification that names a role, or any timetable a role uses, applies to every resource of that role.

Scenarios whose resources share their role's calendar are unchanged. `tests/test_role_calendars.py` covers the import and the patch.

**Calendar changes given as text** (from the commit "Calendar modifications given as text are parsed or reported").

*Previously,* a `resource_calendar` modification needed `proposed_structured.timeTableItems`. When the LLM gave only text ("09:00-17:00 → 09:00-19:00"), the merger skipped it in tolerant mode, and nobody was told. In the Stage 2 batch v3 the same change was therefore proposed again in 22 iterations.

*Now* `scenario_merger.parse_calendar_text` reads an unambiguous text into timetable items:
- **One working-time window in whole hours, with a clock marker** ("09:00-19:00", "8h-18h"):
  - with one weekday range ("Mon–Fri", "Monday to Saturday"), "weekdays" or "daily";
  - without one, each current period of the role keeps its weekdays.
- **An extension by whole hours** ("extend by 2 hours", "+2 h", "2 hours longer"), which moves the end of every current period.
- **Anything else is not parsed:** a bare "8 to 10" (which may mean hours per day), several windows, half hours, or an extension beyond midnight.

What happens to the modification:
- **Parsed:** it is applied to the role, with a `calendar_parsed` note.
- **Not parsed:** it is an error with category `calendar_unparsed`. The message says the change was not applied and gives the expected formats.
  - `validate_patch` reports it, so the retry prompt carries it.
  - On the generator's last attempt only that modification is dropped (noted as `[patch-reject]`), as for baseline mismatches.

**Prosimos period format** (from the commit "Accept calendar modifications given in Prosimos period format").

Structured items may also be given in Prosimos's form, e.g. `{"from": "MONDAY", "to": "FRIDAY", "beginTime": "09:00:00", "endTime": "19:00:00"}`. `scenario_merger.normalise_calendar_items` maps them to GLASS's form (`startWeekday`, `endWeekday`, `startTime`, `endTime`); "23:59:59" is read as the end of the day.
- **Genuinely ambiguous items** are not mapped: times that are not whole hours, weekdays that are not day names, unknown keys, or an end not after the start.
- **Rejection:** such items are errors with category `calendar_unparsed`. The message gives the reason and both accepted forms. Previously they appeared only as a skipped `invalid_value`.

`tests/test_calendar_prosimos_format.py` covers a Prosimos item (applied and exported unchanged in Prosimos form), the GLASS form, and an ambiguous item (rejected with feedback).

**Nothing skipped silently.** Every modification the merger skips in tolerant mode is now noted as `[merge-skip]`. The refinement feedback lists all `[patch-reject]` and `[merge-skip]` notes under "MODIFICATIONS NOT APPLIED", so the LLM learns in the next iteration that they are not in the simulated scenario.

`tests/test_calendar_text.py` covers:
- the two text patterns;
- texts that are not parsed;
- applying a parsed text, and rejecting an unparsable one with its message;
- the generator's retry and last attempt;
- the feedback section.

---

## Clarification-context summary

**Previous behaviour.** `second_llm/context_summary.py` asks an LLM to turn the clarification chat into structured constraints, and `prompts/scenario_patch_prompt.py::_build_constraints_block` renders them as hard rules in the Stage 2 prompt. The schema had no "not stated" value for `staffing_flexible` / `overtime_available`, no rule for statements about a group of roles, and no place for hours per day, external parties, a relative budget or hourly costs. An answer such as "for each internal role we could add up to three people, and working hours could be extended by up to two hours per day", followed by a sentence naming each role with its hourly cost, therefore became "staffing is FIXED, NO overtime" for every named role. In addition, summary role names ("loan_officer") were compared verbatim with model role names ("Loan Officer"), so per-role limits were never applied.

**What changed** (applies from the commit "Fix clarification-context summary misreading staffing and overtime flexibility"):
- **Schema:** `staffing_flexible` and `overtime_available` may be `null` (not stated). New fields: `max_overtime_hours_per_day`, `external_party`, `cost_per_hour` per role, and `budget.max_increase_pct`.
- **Summariser rules:**
  - `false` only when the user said so.
  - A group statement is recorded for every role of the group.
  - An external party gets its own entry.
  - A per-role limit is not a total.
- **Prompt rendering:** the constraints block also shows overtime availability with hours per day, external parties and the relative budget limit. Summaries without these fields render as before.
- **Role-name matching:** role names are matched case-insensitively, with `_` and `-` read as spaces and a plural "s" ignored.
- **Tests:** `tests/test_context_summary_flexibility.py` holds deterministic tests and a live test of the summariser on the three LoanApp business-context answers. The live test runs only with `GLASS_LIVE_LLM_TESTS=1` and an API key.

**Relative budget limit enforced** (from the commit "Enforce relative budget limits from the business context").

*Previously,* `budget.max_increase_pct` was shown to the LLM, but GLASS's validators checked only `additional_monthly`. In the Stage 2 batches, finals raised the staff cost by up to 57 % against a stated limit of 35 %.

*Now* `validation.validate_proposal`, when given the baseline (the patch generator passes it), compares the merged scenario's total staff cost per week with the baseline's.
- **Cost:** each resource's hourly cost times its weekly working hours, from its own calendar or its role's schedule. Added resources and extended calendars both count.
- **Above the limit:** the proposal gets an error with category `budget_exceeded`, as for absolute budgets. Its message states the limit, the baseline cost, the proposed cost and the allowed maximum.
- **Retry:** the generator's budget retry ("YOUR PREVIOUS PATCH EXCEEDS THE STATED BUDGET") forwards the message to the LLM.
- **Last attempt:** as with absolute budgets, if the last attempt still exceeds the limit, the generator returns it as the best available proposal, with the error recorded in its validation.
- **Unchanged:** absolute budgets (`additional_monthly`), and calls without a baseline.

`tests/test_relative_budget.py` covers the cost (headcount and hours), a +39 % proposal (rejected, with the figures in the message), a +30 % proposal (accepted) and the unchanged absolute check.

---

## Utilization and throughput metrics

**Previous definition.** `Resource Utilization` (`second_llm/kpi_computation.py`) was, per resource, busy time (sum of activity end − start) divided by the simulated span (first start to last end, wall-clock time). The KPI value was the mean over all resources.

**Why it moved the wrong way.** When a fix clears a bottleneck, the backlog drains sooner and the span shrinks. The resources that do not form the bottleneck (clerks, for example) then do the same work in fewer working days: they genuinely get busier, and they dominate the all-resource mean. The mean therefore rises even though the overloaded role is relieved. Under `minimize`, GLASS's iterative loop read such a correct fix as a degradation.

**New definition** (from the commit "Resource Utilization: calendar-based availability and role-aware evaluation"):
- **Per resource:** utilization = busy time ÷ available working time.
  - The evaluated period runs from the simulation start to the last end in the log.
  - Available time is the resource's calendar periods within that period.
  - Busy time counts only the in-calendar part of each activity, so an activity paused over night counts only its working hours.
- **Role resolution:** when a target KPI is matched to `Resource Utilization`, it is evaluated for the role(s) it names. The roles come from the resource profiles and are looked up case-insensitively, tolerating a plural "s", first in the KPI name, then in its description and suggested formula. The value is the mean utilization of those roles' resources. The all-resource mean, the per-role and the per-resource values stay in `details`. This applies in the iterative loop, the multi-seed evaluation and `evaluate_scenarios`.
- **Resource matching:** log resource names are matched to calendars by indexing `resource_profiles` by both id and name, as `simod_to_simubridge` and `patch_validator` already do.

**Fallbacks.**
- A resource without a calendar, or every resource when no Prosimos parameters are passed (for example `evaluate_from_logs` on uploaded logs), keeps the span-based value and is listed in `details["fallback_span_based"]`.
- A KPI that names no role keeps the all-resource mean, with `details["role_resolved"] = false`.

**Throughput** is unchanged (completed cases per day over the span of case ends). Its direction is already right. Only the Stage 1 wording describing both metrics was updated.

**Worked example: LoanApp_G1G3 rep 1, seed 42.** The baseline has one overloaded Loan Officer; the fix adds two more.

| | Baseline | After the fix |
|---|---|---|
| Old all-resource mean | 0.243 | 0.571 (rises) |
| New all-resource mean | 0.257 | 0.593 (still rises) |
| Clerks | 0.22 | 0.60 |
| **New, role-resolved (Loan Officer)** | **0.997** | **0.850 (falls)** |

`tests/test_utilization_role_aware.py` covers these cases.

---

## Patch modifications: quoted baseline values

**Previous behaviour.** Each modification quotes the element's current value (`baseline_value`). When the quote differed from the model by more than 5 %, `second_llm/scenario_merger.py` only warned and applied the proposed value anyway. Two kinds of error went through this way:
- **Wrong element.** In a Stage 2 run the LLM quoted 7200 s for an activity whose actual mean is 1200 s and proposed 5400 s. The activity became 4.5× longer instead of shorter.
- **Wrong unit.** A quote in hours for a duration stored in seconds ("1.1667 hours" for 4200 s), with the proposal "0.5 hours", set the activity to a constant 0.5 seconds.

**What changed** (from the commit "Reject modifications whose baseline value does not match the model"):
- **Numeric quotes** (resource count, hourly cost, and duration and inter-arrival means in the element's own time unit) must lie within 25 % of the actual value (`BASELINE_MISMATCH_TOLERANCE`).
- **Categorical quotes.** For `resource_activity_assignment`, the quoted roles must be the activity's roles. A role may also be named by one of its resources.
- **A mismatch is an error** (category `baseline_mismatch`). The merger skips the modification in both modes, and `validate_patch` reports it. The retry prompt therefore states the actual value and its unit, so the LLM can correct the target.
- **Last attempt.** On the generator's last attempt, when baseline mismatches are the only errors, just those modifications are dropped (noted as `[patch-reject]`) and the rest of the patch is applied.
- **Unchanged:** quotes within 25 %, empty quotes, and modifications of elements that do not exist (handled by the existence checks).

`tests/test_baseline_mismatch.py` covers the rejection, a close match, the unit case, the feedback text, the assignment check and the generator's retry. `goal_to_parameters/tests/test_patch_flow.py` now expects the rejection in tolerant mode.

**Resource-id stems as role targets** (from the commit "Map resource-id stems to roles in patch targets").

*Previous behaviour.* SIMOD gives each resource profile an id and a name. In the Procure2Pay baseline, for example, the profile id is `QBP_DEFAULT_RESOURCE`, the name is "Purchasing Agent", and the resources are `QBP_DEFAULT_RESOURCE_1` … `_n`. GLASS keys the role by the name, but the prompt showed the ids. A modification targeting `QBP_DEFAULT_RESOURCE` was therefore rejected as an unknown role. When the LLM repeated it on every retry, the run produced no scenario.

*Now:*
- **Repair step.** `patch_validator.repair_patch_against_baseline` redirects a `resource_count`, `resource_cost` or `resource_calendar` target that is not a role (nor, for a calendar, a timetable) to the role it names, through `scenario_merger._role_for_name`. A target can name the role in three ways:
  - one of the role's resources, by id or name;
  - the stem the role's resource ids share;
  - a resource name with an index ("Purchasing Agent-000001").

  A stem or index match must point to exactly one role. `add_new` on a role found this way becomes `increase`, as for a role named directly. Unknown names are still rejected.
- **Prompt.** `payload_builder.simod_for_llm` gives the prompt a copy of the SIMOD parameters in which profile and resource ids are replaced by their names, wherever both exist and the name is unique. This includes references such as `task_resource_distribution[].resources[].resource_id`. Calendar ids are kept. The copy is used by the draft payload and by the patch generator. The baseline is still built from the original parameters. The legacy full-scenario generator is unchanged.

`tests/test_resource_id_stems.py` covers stem, id and indexed-name resolution, the calendar target, `add_new`, an unknown name, the prompt copy, and the prompt the patch generator sends.

**Direction must match the values.** The validator compares a modification's stated direction (`increase` / `decrease`) with its numeric baseline and proposed values; a contradiction (`direction_inconsistent`) is now an error, not a warning. Before, a "decrease" whose proposed value lay far above the baseline was merged and simulated as a large increase. This happened in the real-life ablation study: after its baseline quote was corrected, the LLM kept a target value meant for a timer, so a task of 825.5 s became 180,000 s. The retry feedback now states the direction, the baseline value and the proposed value, and asks for a value consistent with the direction. On the generator's last attempt only that modification is dropped, as for a baseline mismatch. `tests/test_direction_consistency.py` covers this.

---

## Refinement: KPI improved vs target achieved

**Previous behaviour.** `second_llm/iterative_evaluator.py` stopped refining as soon as every target KPI moved in its direction (overall status `improved`), and chose the final iteration by score alone. A KPI with a numeric target (`KPITarget.threshold`, until now used only for safeguards) counted as done after any improvement. For example, utilization falling from 0.99 to 0.98 under a target of "below 0.85" stopped the loop, and the feedback never told the LLM that the target was still far off.

**What changed** (from the commit "Refinement: distinguish KPI improved from target achieved"):
- **`achieved`.** For a target with a threshold, `achieved` says whether the value meets it in the KPI's direction: minimize, value ≤ threshold; maximize, value ≥ threshold. Maintain targets and targets without a threshold get `None`. The value is reported next to `improved` per KPI:
  - in `compare_kpis` / `evaluate_scenarios` (`KPIComparisonEntry.threshold`, `.achieved`);
  - in `evaluate_multi_seed`, on the mean over the seeds (`MultiSeedKPIComparison.threshold`, `.achieved`).

  `EvaluationSummary.targets_achieved` is true when every target with a threshold is achieved, and `None` when no target has one.
- **Stop rule.** The loop stops when every KPI with a threshold is achieved and every KPI without one is improved, with no safeguard violated (`scenario_evaluation.targets_met`). KPIs that cannot be computed are not counted, as before. Without thresholds the rule is the same as before.
- **Best iteration.** The best iteration is the one with the most achieved targets, then the most improved KPIs, then the existing score; ties keep the earlier iteration. This also applies without thresholds: an iteration that improves more KPIs now ranks above one with a higher score.
- **Feedback.** For a KPI with a threshold, the feedback says either "improved but target not yet reached (current X, target Y)" or "target achieved", and gives the count ("Targets reached: k of n"). KPIs that improved but have not reached their target are listed under "TARGETS NOT YET REACHED", and alternative interventions are retrieved for them as for worsened KPIs.

**Remaining gap and allowed range** (from the commit "Refinement feedback states the remaining gap and the allowed range"). Telling the LLM only that a target was "not yet reached" did not make it propose a larger change. A typical case: it added one resource to an overloaded role, utilization stayed far above the target, and the next iterations repeated the same change.

For each KPI with a threshold that improved but did not reach it, the feedback now also states:
- **The remaining gap.** It is given in absolute terms and relative to the current value, with the sentence "a larger change than in this proposal is needed to reach the target".
- **The allowed range of each lever targeting the KPI** (modifications whose `kpi_reference` names it), taken from the operational-constraint summary that is also rendered into the prompt:
  - capacity: how far staffing may be increased, what the proposal adds, and how many additions remain for the role;
  - the global cap on additional resources and the relative budget limit, if they were stated;
  - calendars: the allowed working-time extension per day.
- **When no modification targets the KPI**, this is said, and the staffing limits are listed.

KPIs that achieved their target, and KPIs without a threshold, get nothing extra. `tests/test_feedback_gap_range.py` covers these cases.

**Direction-aware strategy retrieval** (from the commit "Direction-aware knowledge-base retrieval").

*Previously,* the "Literature-Backed Alternative Interventions" in the refinement feedback were chosen by text relevance only. A KPI to *minimize* utilization therefore received strategies whose stated effect is raising utilization ("Improve operator utilization rates …", "raised overall average utilization by 8.5 %").

*Now* `knowledge/direction.py` derives each entry's stated effect on the KPI's metric from the entry's own text: the goal and parameter-change rationale and evidence of a mapping, the finding of a literature entry, or a PDF excerpt.
- **How the effect is read.** An effect word near a word for the metric, in the same clause, gives the direction:
  - "increase": raise, rise, higher, boost, maximize, upward, grow, "keep … high";
  - "decrease": reduce, lower, minimize, cut, shorten, drop, fall, below, less, relieve, eliminate, and "without / avoid overloading" for utilization;
  - "improve" means increase for metrics where higher is better (utilization, throughput) and decrease for time and cost metrics;
  - a word for the inverse of the metric flips the direction ("reduce idle time" raises utilization).
- **The rule for each problem KPI.** `minimize` wants a stated decrease, `maximize` a stated increase, and `maintain` is not filtered:
  - an entry that states the opposite effect, or a mixed one, is dropped;
  - an entry that states no effect on the metric is kept and ranked after the matching entries.
- **Retrieval size.** More entries are retrieved than shown (10 mappings, 6 literature entries and 4 excerpts, of which at most 4, 3 and 2 are shown), so enough remain after filtering.

**Metric from the category** (from the commit "Direction-aware retrieval: use the KPI category, not the name"). The first version read a problem KPI's metric from its name, looking for "utiliz". A utilization KPI called e.g. "Purchasing Agent Workload Distribution" was therefore read as a capacity KPI, and nothing was filtered for it. In the Stage 2 batch v3 this showed in Procure2Pay_G3.

`knowledge.direction.kpi_metric(category, measurable_as, name)` now decides:
- **Category known:** the category gives the metric (utilization, cost, throughput). For `time`, `measurable_as` (or, without it, the name) tells waiting time from processing time.
- **Category unknown** (e.g. quality): no metric is derived, and nothing is filtered.
- **No category:** `measurable_as` decides. The name is used only when both are missing.

The refinement feedback passes each KPI's category and `measurable_as` to the retrieval (`KPIComparisonEntry.measurable_as` is new). The knowledge-base query uses the same metric. `tests/test_direction_by_category.py` checks that a utilization KPI with such a name gets no strategy raising utilization, directly and through the feedback.

No direction field is added to the knowledge-base entries; the rule is applied to their existing wording. It applies only to retrieval during refinement. The evidence retrieved for the initial proposal is unchanged. `tests/test_direction_aware_retrieval.py` covers the phrase rules and checks, on the bundled knowledge base, that a minimize-utilization KPI gets no strategy describing raising utilization and that a maximize one still does.

**Where thresholds come from.** The UI's target builders do not set `threshold`, because the Stage 1 KPI schema has no numeric target. The behaviour above therefore applies when the caller sets it, as the Stage 2 batch evaluation does from its configuration.

`tests/test_target_achieved.py` covers `achieved` in single-run and multi-seed comparisons, the stop rule with and without thresholds, the best-iteration order and the feedback text, using synthetic KPI series.

**The loop measures the KPI it was given.** The loop's targets (`ui/second_llm_panel._get_evaluation_targets`) now carry each KPI's `measurable_as`, as the multi-seed evaluation's targets (`ui/evaluation_panel._get_kpi_targets_from_workspace`) already did. Before, the loop matched a KPI by its name: "Average Waiting Time Before Review" resolved to the process-wide "Average Waiting Time" instead of "Review Waiting Time". The loop's stop rule, its choice of the best iteration and its feedback could then rest on a different metric than the final evaluation. `tests/test_loop_targets_measurable_as.py` covers this.

**Earlier rules, for ablation.** `run_iterative_evaluation(..., legacy_refinement=True)` restores the previous behaviour: stop at the first iteration whose overall status is `improved`, and choose the best iteration by score alone. It is off by default. The UI never sets it; it exists so evaluations can compare the two rules. `tests/test_legacy_refinement.py` covers it.

---

## Try it with the bundled examples

**Stage 1 (Goal → KPI)** — paste JSON contents from any of:
- [`goal_to_parameters/examples/order_fulfillment.json`](./goal_to_parameters/examples/order_fulfillment.json)
- [`goal_to_parameters/examples/loan_application.json`](./goal_to_parameters/examples/loan_application.json)
- [`goal_to_parameters/examples/hospital_discharge.json`](./goal_to_parameters/examples/hospital_discharge.json)
- [`goal_to_parameters/examples/context_aware_insurance_claim.json`](./goal_to_parameters/examples/context_aware_insurance_claim.json)

Matching event log CSVs sit next to each JSON.

**Stage 2 (Scenario Studio)** — pre-built end-to-end data sets:
- [`goal_to_parameters/examples/data/purchasing/`](./goal_to_parameters/examples/data/purchasing/)
- [`goal_to_parameters/examples/data/purchasing_time_focus/`](./goal_to_parameters/examples/data/purchasing_time_focus/)

Each contains a validated `first_llm.json`, a BPMN model, and SIMOD simulation parameters — plug them into the Scenario Studio workspace directly.

---

## Evaluation

The [`evaluation/`](./evaluation/) folder contains the thesis evaluation harness for three public event logs (BPIC 2012, BPIC 2017, Sepsis). Raw logs are not committed — regenerate them with `evaluation/download_logs.py`. Results (KPI sets, patches, comparison CSVs, figures) are checked in. See [`evaluation/README.md`](./evaluation/README.md) for reproduction instructions.

---

## Testing

```bash
make test
# or
.venv/Scripts/pytest tests/ goal_to_parameters/tests/
```

`tests/` holds integration tests across pipeline stages; `goal_to_parameters/tests/` holds unit tests for the patch flow and scenario evaluation.

---

## Thesis

**"From Goals to Simulation Configuration: LLM-Based Generation of Process Simulation Parameters from Event Logs"** — Loran Pllana, TU Munich (2026).

For a deeper walk-through of the pipeline stages, prompting strategy, patch merger design, and evaluation methodology, see the thesis document itself.

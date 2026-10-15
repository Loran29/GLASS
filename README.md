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

**Known limitation.** `budget.max_increase_pct` is shown to the LLM but not enforced by GLASS's validators, which check `additional_monthly` only.

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

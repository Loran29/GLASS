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

**Known limitations.** Timer distributions share the existing activity-duration conversion. `fix`, the `expon` mean, and the `norm` mean and std round-trip exactly. The `expon` and `norm` min/max bounds are regenerated on export (0 and 10× or 3× the mean). The `norm` std was imported as if it were the variance until the commit "Fix normal-distribution std being treated as variance in scenario conversion"; it is now squared on import into SimuBridge's (mean, variance) form and square-rooted on export. The legacy full-scenario flow (`generate_scenario_proposal`, not used by the orchestrator) and hand-uploaded scenario JSON written before this change carry no `intermediateEvents`, so their timers still export as 0.

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

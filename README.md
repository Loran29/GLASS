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

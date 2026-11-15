# GLASS — Goal-based LLM-Assisted Simulation Studio

GLASS supports business process improvement by translating natural-language improvement goals into measurable KPIs and executable what-if scenarios. It combines large language models (LLMs), event-log evidence, business context, and business process simulation (BPS) to construct, evaluate, and iteratively refine potential process improvements before implementation.

## Framework

As described in the paper, GLASS has **two stages**:

1. **Stage 1 — Goal-to-KPI Translation.** GLASS takes a natural-language improvement goal, a process description, and an event log as input. It profiles the event log and uses an LLM to propose SMART KPIs that capture the intended improvements. Through human-in-the-loop validation, users can accept or refine the proposed KPIs. The validated KPI set guides Stage 2.
2. **Stage 2 — What-if Scenario Generation and Refinement.** GLASS gathers business context and operational constraints, then uses the validated KPIs, event-log evidence, and an As-Is BPS model to propose parameter modifications. The LLM generates a structured `ScenarioPatch`, which is validated and deterministically merged into a copy of the As-Is model to construct an executable what-if scenario. GLASS simulates the scenario using Prosimos, evaluates its KPIs against the As-Is baseline, and uses simulation feedback to refine unsuccessful proposals. The selected scenario and baseline are evaluated across multiple simulation replications, producing a KPI Performance Report. If no candidate satisfies the selection criterion, GLASS retains the As-Is model.

The two-stage framework addresses five design requirements: **goal-level interaction (R1)**, **context-grounded intervention generation (R2)**, **executable and valid scenario construction (R3)**, **simulation-based scenario evaluation (R4)**, and **iterative scenario refinement (R5)**.

The software interface separates some Stage 2 activities into *Scenario Studio* and *Scenario Evaluation* pages; these are implementation components, not additional framework stages.

Optional knowledge-base retrieval (RAG) is supported but **disabled by default**. The main Stage 2 results reported in the paper were obtained without RAG.


## Requirements

- Python 3.11 or 3.12 and the packages in `requirements.txt`
- An OpenRouter or OpenAI API key (the reported experiments used `gpt-4o-mini` through OpenRouter)
- Java only if discovering a new baseline simulation model with SIMOD

## Installation

Run from the repository root:

```bash
python3 -m venv .venv        # (Python 3.11 or 3.12)
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -c "import shutil; shutil.copy('.env.example', 'goal_to_parameters/.env')"
```

Add your API key to `goal_to_parameters/.env`.

## Running GLASS

### Web interface

```bash
streamlit run goal_to_parameters/app.py
```

Open `http://localhost:8501`. The interface supports Stage 1 KPI translation and Stage 2 scenario generation, validation, simulation, evaluation, and refinement. To discover a new baseline through SIMOD, Java is required.
The interface checks that the goal contains a performance term (for example cycle time, waiting time, utilization) and rejects goals without one, including the example goal in `examples/loanapp/goal.txt` as written; the command-line example and the experiment scripts do not apply this check.

### Command-line example

The [`examples/loanapp/`](examples/loanapp/) directory provides a LoanApp example with the required inputs and baseline model.

```bash
# Stage 1: Generate KPIs
python examples/loanapp/run_example.py stage1

# Stage 2: Generate, validate, simulate, evaluate, and refine scenarios
python examples/loanapp/run_example.py stage23 --seeds 2

# Optional: Enable knowledge-base retrieval
python examples/loanapp/run_example.py stage23 --seeds 2 --use-knowledge-base
```

Run records and reports are written under `examples/loanapp/output/`. LLM outputs may vary across runs.

### Docker (optional)

```bash
docker compose up --build
```

The interface is available at `http://localhost:8501`. The Docker image does not include Java, so SIMOD discovery is unavailable inside the container. In the container you can run Stage 1 (Goal-to-KPI Translation) in the interface; Stage 2 needs an As-Is model, which the interface obtains only through SIMOD, so run Stage 2 with the command-line example or the experiment scripts outside the container.

## Experiments and results

The `evaluation/experiments/` directory contains the experiment configurations, scripts, input data, and recorded results. See its own `README.md` for detailed instructions.

| Experiment | Purpose | Folder |
|---|---|---|
| **Synthetic variants** | Documents the modifications introduced into LoanApp and Procure2Pay, together with expected KPI mappings. | `experiment_0_synthetic_variants_vs_original/` |
| **Goal-to-KPI generation (RQ1)** | Evaluates expected KPI identification and human-guided refinement. | `experiment_1_expected_kpi_generation/` |
| **Scenario generation and refinement (RQ2)** | Evaluates validity and executability of what-if scenarios and improvements to target KPIs. | `experiment_2_valid_improving_kpis/` |
| **PI Copilot comparison (RQ3)** | Compares simulated KPI outcomes of GLASS scenarios with scenarios manually constructed from directly applicable Process Improvement Copilot recommendations. | `experiment_3_glass_vs_process_improvement_copilot/` |
| **Ablation study (RQ4)** | Examines event-log evidence, business context, and iterative refinement. | `Experiment_3_real_life_ablation/`, `Experiment_3_synthetic_ablation/` |
| **Sensitivity analysis** | Examines the number of simulation replications used during refinement. | `Refinement_replications_Sensitvity_Analysis/` |
| **Additional RAG experiments** | Investigate optional knowledge-base retrieval; not part of the main reported results. | `Experiment 4 Adding RAG to Stage 2 (not reported in the paper)/` |

## Reproducibility

The repository provides the source code, prompts, input data, process descriptions, experimental configurations, baseline BPS models, evaluation scripts, and recorded results used to support the paper.

To inspect or rerun an experiment:

1. Open [`evaluation/experiments/`](evaluation/experiments/) and consult its `README.md` for the relevant experiment, configuration, and run instructions.
2. Install the pinned dependencies from `requirements.txt` and configure an LLM API key as described above.
3. Use the experiment-specific scripts and supplied baseline models rather than the interactive UI when reproducing the paper's evaluation setup.
4. Compare the newly generated outputs against the stored run records and aggregated results.

**Reproduction scope:** The stored records document the reported runs, but rerunning an LLM-based pipeline may not yield identical KPIs, patches, or KPI outcomes because the LLM is not seeded. Some simulated event logs are excluded from the repository; their hashes are retained in the run records. Case-study PDFs used only for optional knowledge-base functionality are also excluded. The UI is designed for interactive use and may discover a different As-Is baseline with SIMOD.

The main Stage 2 evaluation used the knowledge base **off**. Optional RAG-enabled evaluations are separate from those main results.

## Tests

Run the suites separately:

```bash
python -m pytest goal_to_parameters/tests
python -m pytest tests
```

Some tests depend on optional services or experimental artifacts; in `tests/`, two tests are known to fail (`test_hybrid_retrieval.py::TestHybridRetriever::test_loan_query_finds_loan_paper` and `test_second_llm_chatbot.py::SecondLLMChatbotTests::test_is_ready_true_after_minimum_turns_and_signal`), and a third (`test_knowledge_base_option.py::Off::test_only_the_kb_section_differs_from_on`) fails on the first run in a fresh clone, while the knowledge-base cache is created, and passes on later runs.

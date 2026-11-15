# GLASS — Goal-based LLM-Assisted Simulation Studio

GLASS supports business process improvement by translating natural-language goals into measurable KPIs and executable what-if simulation scenarios. It combines LLMs, event-log evidence, business context, and simulation-based evaluation to generate and iteratively refine process improvement scenarios.

## Framework Overview

As described in the paper, GLASS has **two stages**:

1. **Stage 1 — Goal-to-KPI Translation.** GLASS takes a natural-language improvement goal, a process description, and an event log as input. It profiles the event log and uses an LLM to propose SMART KPIs that capture the intended improvements. Through human-in-the-loop validation, users can accept or refine the proposed KPIs. The validated KPI set guides Stage 2.
2. **Stage 2 — What-if Scenario Generation and Refinement.** GLASS gathers business context and operational constraints, then uses the validated KPIs, event-log evidence, and an As-Is BPS model to propose parameter modifications. The LLM generates a structured `ScenarioPatch`, which is validated and deterministically merged into a copy of the As-Is model to construct an executable what-if scenario. GLASS simulates the scenario using Prosimos, evaluates its KPIs against the As-Is baseline, and uses simulation feedback to refine unsuccessful proposals. The selected scenario and baseline are evaluated across multiple simulation replications, producing a KPI Performance Report. If no candidate satisfies the selection criterion, GLASS retains the As-Is model.

GLASS supports optional knowledge-base retrieval (RAG), which is disabled by default.

## Requirements

- Python 3.11 or 3.12
- Dependencies listed in `requirements.txt`
- An OpenRouter or OpenAI API key
- Java (optional, for discovering new simulation models with SIMOD)

The reported experiments used `gpt-4o-mini` through OpenRouter.

## Installation

From the repository root:

```bash
python3 -m venv .venv        # (Python 3.11 or 3.12)
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate the environment using `.venv\Scripts\activate`.

Copy `.env.example` to `goal_to_parameters/.env` and add your API key.

## Running GLASS

### Web Interface

```bash
streamlit run goal_to_parameters/app.py
```

Open `http://localhost:8501`.

The interface supports KPI generation, scenario construction, simulation, evaluation, and refinement.
The interface checks that the goal contains a performance term (for example cycle time, waiting time, utilization) and rejects goals without one, including the example goal in `examples/loanapp/goal.txt` as written; the command-line example and the experiment scripts do not apply this check.

### Command-Line Example

The `examples/loanapp/` directory contains an example based on the LoanApp process.

```bash
# Stage 1: KPI generation
python examples/loanapp/run_example.py stage1

# Stage 2: Scenario generation and evaluation
python examples/loanapp/run_example.py stage23 --seeds 2
```

To enable optional knowledge-base retrieval:

```bash
python examples/loanapp/run_example.py stage23 --seeds 2 --use-knowledge-base
```

The commands generate structured run records and evaluation reports.

### Docker (Optional)

```bash
docker compose up --build
```

The web interface is then available at `http://localhost:8501`. The image has no Java, so SIMOD discovery is unavailable: in the container you can run Stage 1 (KPI generation) in the interface, and Stage 2 runs with the command-line example or the experiment scripts outside the container.

## Experiments and Results

The `evaluation/experiments/` directory contains the datasets, configurations, experimental records, and results supporting the evaluation.

| Experiment | Description | Folder |
|---|---|---|
| **Experiment 0 — Synthetic variants** | Construction of modified LoanApp and Procure2Pay models, including injected changes and expected KPI mappings. | `experiment_0_synthetic_variants_vs_original/` |
| **Experiment 1 — Goal-to-KPI generation (RQ1)** | Evaluation of KPI identification and human-guided refinement across 14 goal configurations. | `experiment_1_expected_kpi_generation/` |
| **Experiment 2 — Scenario generation (RQ2)** | Evaluation of scenario validity and KPI improvement on LoanApp and Procure2Pay. | `experiment_2_valid_improving_kpis/` |
| **PI Copilot comparison (RQ3)** | Comparison of GLASS scenarios with manually implemented Process Improvement Copilot recommendations. | `experiment_3_glass_vs_process_improvement_copilot/` |
| **Experiment 3 — Ablation (RQ4)** | Evaluation of the contributions of event-log evidence, business context, and iterative refinement. | `Experiment_3_real_life_ablation/`, `Experiment_3_synthetic_ablation/` |
| **Sensitivity analysis** | Comparison of different numbers of simulation replications during refinement. | `Refinement_replications_Sensitvity_Analysis/` |
| **Additional RAG experiment** | Evaluation of optional knowledge-base retrieval; not reported in the paper. | `Experiment 4 Adding RAG to Stage 2 (not reported in the paper)/` |

The main Stage 2 experiments were conducted without RAG.

## Reproducibility

The repository provides the GLASS implementation, prompts, experimental configurations, evaluation scripts, and recorded results.

SIMOD and Prosimos are used for simulation model discovery and execution, respectively. The experimental configurations include baseline simulation models where required.

LLM outputs may vary between executions. The stored experimental records document the reported results.

Some simulated event logs and case-study PDFs are not included in the repository.

## Tests

```bash
python -m pytest goal_to_parameters/tests
python -m pytest tests
```

The test suites should be run separately. Some tests require optional dependencies, API access, or experimental artifacts.

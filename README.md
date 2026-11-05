# GLASS — Goal-based LLM-Assisted Simulation Studio

GLASS turns a natural-language simulation goal and an event log into a goal-oriented set of business process
simulation parameters. It asks an LLM for KPIs that measure the goal, then for parameter changes on top of a
discovered baseline simulation model, and checks each proposal by simulating it with Prosimos and comparing its KPIs
with the baseline's. The code runs as a Streamlit application and from the command line.

## The three stages

| Stage | What it does | Main modules |
|---|---|---|
| **1. Goal → KPI identification** | From a process description, a simulation goal and an event log, the LLM proposes SMART KPIs (name, category, formula, `measurable_as`, target direction), grounded in a profile of the log. The user accepts, rejects or refines them. | `prompts/smart_kpi_prompt.py`, `models/smart_kpi.py`, `utils/log_processing.py`, `utils/semantic_validation.py` |
| **2. Scenario generation** | From the accepted KPIs, a baseline model (BPMN + SIMOD parameters), evidence from the event log and its context attributes, and a clarification chat on operational constraints, the LLM proposes a delta patch of parameter changes. Validators check it, and a deterministic merger applies it to the baseline. | `second_llm/scenario_generator.py`, `payload_builder.py`, `context_summary.py`, `patch_validator.py`, `scenario_merger.py`; optionally `knowledge/` |
| **3. Simulation-based evaluation and refinement** | Prosimos simulates baseline and scenario. KPIs are computed from the simulated logs, and a refinement loop feeds the comparison back to Stage 2 until the targets are met. The final scenario is evaluated on several seeds with 95 % confidence intervals. | `second_llm/prosimos_runner.py`, `kpi_computation.py`, `iterative_evaluator.py`, `multi_seed_evaluation.py`, `scenario_evaluation.py` |

GLASS does not use a knowledge base by default. Literature-backed retrieval (RAG) can be added to Stage 2; see
[Optional: knowledge-base retrieval](#optional-knowledge-base-retrieval).

Every module under `goal_to_parameters/` names its stage in its first line. [`STAGES.md`](STAGES.md) lists all
modules and evaluation scripts by stage, and [`docs/DESIGN_NOTES.md`](docs/DESIGN_NOTES.md) describes the
behaviour in detail.

## Requirements

- **Python 3.11 or 3.12.** SIMOD, Prosimos and log-distance-measures require Python ≥ 3.11 and < 3.13.
- **Packages:** `requirements.txt`, with every direct dependency pinned to the version used for the results,
  including Prosimos 2.1.0. Optional packages, not used for the results, are listed at its end.
- **An LLM API key** in `goal_to_parameters/.env`: `OPENROUTER_API_KEY` or `OPENAI_API_KEY`. The results used
  `gpt-4o-mini` through OpenRouter. The UI also supports Anthropic, Hugging Face and a local Ollama server
  (`goal_to_parameters/config.yaml`).
- **Java (optional)** only to discover a new baseline with SIMOD (the UI's "Run SIMOD"). The example and the
  experiments ship their baselines.

## Setup

From the repository root:

```bash
python3.11 -m venv .venv                       # Windows: py -3.11 -m venv .venv (or 3.12)
source .venv/bin/activate                      # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -c "import shutil; shutil.copy('.env.example', 'goal_to_parameters/.env')"
python examples/loanapp/run_example.py stage1  # after putting your key into goal_to_parameters/.env
```

## Running the example

[`examples/loanapp/`](examples/loanapp/) holds the inputs of one configuration of the evaluation (LoanApp_G3: an
overloaded loan officer role). They are copies of the evaluation's files:

| File | Content | Copied from |
|---|---|---|
| `process_description.txt` | process description | `evaluation/loanapp_p2p/setup/stage1_config.json` |
| `goal.txt` | simulation goal | same |
| `LoanApp_resource_target_bimp.csv` | event log | `evaluation/loanapp_p2p/input_logs/` |
| `baseline/model.bpmn`, `baseline/sim_params.json` | baseline simulation model | `evaluation/loanapp_p2p/stage2/baselines/LoanApp_G3/` |
| `healthy/model.bpmn`, `healthy/sim_params.json` | reference model for the log-distance measures | `evaluation/loanapp_p2p/stage2/healthy/LoanApp/` |
| `business_context.json` | answers to the clarification chat (staffing, budget, immutable steps, hourly costs) | `evaluation/loanapp_p2p/setup/stage2_config.json` |
| `stage1_record_LoanApp_G3_rep1.json` | the Stage 1 result the evaluation passed to Stage 2 | `evaluation/loanapp_p2p/results/stage1_results_normalised/raw/LoanApp_G3/rep_1.json` |
| `example_config.json` | settings and the configuration entry, paths relative to the folder | both configuration files |

### Command line

```bash
python examples/loanapp/run_example.py stage1                                    # Stage 1
python examples/loanapp/run_example.py stage23 --seeds 2                         # Stages 2 + 3
python examples/loanapp/run_example.py stage23 --seeds 2 --use-knowledge-base    # Stages 2 + 3 with the knowledge base
```

- `stage1` runs GLASS's Stage-1 path on the example (one LLM call) and writes
  `examples/loanapp/output/stage1/raw/LoanApp_G3/rep_1.json`.
- `stage23` passes the evaluation's Stage 1 KPI ("Loan Officer Workload Distribution", target below 85 %) to
  Stage 2. It then runs the refinement loop (up to 4 iterations) and evaluates the initial and the final scenario
  against the baseline on `--seeds` seeds (the experiments used 10). It writes the run record to
  `examples/loanapp/output/stage23/raw/LoanApp_G3/rep_1.json`, the simulated logs next to it, and a readable report
  to `examples/loanapp/output/stage23/report.txt`.
- **Continuing from your own Stage 1 run:** add
  `--stage1-record examples/loanapp/output/stage1/raw/LoanApp_G3/rep_1.json`. Its utilization KPIs are then
  passed, or the KPIs named with `--kpis`.
- **Knowledge base:** off by default. `--use-knowledge-base` switches it on for the run; `--no-knowledge-base`
  is the default. The record stores the setting (`use_knowledge_base`).

Both commands use the evaluation batch scripts' own per-run functions, so a run is recorded like a run of the
LoanApp/P2P evaluation.

### UI

```bash
streamlit run goal_to_parameters/app.py
```

Open http://localhost:8501.

1. **Goal to KPI (Stage 1).** Paste `process_description.txt` into "Process Description" and the goal into
   "Simulation Goal". Upload `LoanApp_resource_target_bimp.csv` as "Event Log". Click **Generate SMART KPIs**,
   then accept or reject the KPIs.
   - The UI's keyword check rejects the example goal as written (see Known limitations). Add a performance word,
     for example: "We want to make better use of our loan officers without overloading them (utilization)."
2. **Scenario Studio (Stages 2 and 3).**
   - Click **Load Output From Current KPI Session**.
   - Under the SIMOD inputs, upload `LoanApp_resource_target_bimp.csv` and click **Run SIMOD**. This needs Java
     and discovers a new baseline from the log; the UI cannot load the shipped `baseline/` model (see Known
     limitations).
   - Click **Open Chat Workspace** and answer the clarification questions with the texts in
     `business_context.json`.
   - Optionally switch on **Use the knowledge base** (off by default, from `config.yaml`).
   - Click **Generate Scenario**. This generates, simulates, evaluates and refines.
3. **Scenario Evaluation (Stage 3).** The page takes the baseline, the generated scenario and the target KPIs
   from the Scenario Studio. Set the number of seeds under "Simulation Settings" and, on the tab "Run Simulation
   (Prosimos)", click **Run Comparison** for the multi-seed comparison with 95 % confidence intervals.

## Expected output

Recorded in a fresh clone of this tree after the setup above. Environment: Windows, Python 3.11.13, the packages
of `requirements.txt`, OpenRouter with `gpt-4o-mini`. The LLM is not seeded, so KPI names, patches and values
change from run to run; the structure of the output stays the same.

```text
$ python examples/loanapp/run_example.py stage1
Stage 1, LoanApp_G3: ok (4 KPIs, 47.1 s)

record: examples/loanapp/output/stage1/raw/LoanApp_G3/rep_1.json
status: ok   scope gate passed: False   LLM attempts: 1
  KPI: Average Loan Assessment Cycle Time | category time | measurable_as Average Cycle Time | direction minimize
  KPI: Loan Officer Workload Distribution | category utilization | measurable_as Resource Utilization | direction maintain
  KPI: Average Activity Duration for Assess Loan Risk | category time | measurable_as None | direction minimize
  KPI: Average Wait Time Before Assess Loan Risk | category time | measurable_as None | direction minimize
```

- **Scope gate.** `scope gate passed: False` is the UI keyword check (see Known limitations). The command line
  records it but does not enforce it.
- **What to expect in other runs:** typically 3–5 KPIs, among them a utilization or workload KPI for the loan
  officers.

Stages 2 + 3 with the default setting (knowledge base off):

```text
$ python examples/loanapp/run_example.py stage23 --seeds 2
healthy reference logs for LoanApp (2 seeds) ...
baseline runs for LoanApp_G3 (2 seeds) ...
Stages 2+3, LoanApp_G3, KPIs ['Loan Officer Workload Distribution'], knowledge base off ...
2026-11-05 13:17:45.393 WARNING streamlit.runtime.caching.cache_data_api: No runtime found, using MemoryCacheStorageManager
2026-11-05 13:17:45.398 WARNING streamlit.runtime.caching.cache_data_api: No runtime found, using MemoryCacheStorageManager

record: examples/loanapp/output/stage23/raw/LoanApp_G3/rep_1.json
full report: examples/loanapp/output/stage23/report.txt
status: ok   elapsed: 196.2 s   iterations: 1   final iteration: 1
  iteration 1: status improved, score 50.0 | resource_count Loan Officer: 1 -> 3; resource_calendar qbp_e6c371a1-c8f5-49da-9e5c-50e0c4def74f_1_cal: 09:00 - 17:00 -> 09:00 - 19:00
  initial (iteration 1, 2 seeds): valid True
    Loan Officer Workload Distribution: baseline 0.8595 -> proposed 0.292 (-66.03 %), improved True
    target-role utilization: 0.8598 -> 0.2918 (improved)
  final (iteration 1, 2 seeds): valid True
    Loan Officer Workload Distribution: baseline 0.8595 -> proposed 0.292 (-66.03 %), improved True
    target-role utilization: 0.8598 -> 0.2918 (improved)
```

The same with the knowledge base on (`--out` keeps the first run's output):

```text
$ python examples/loanapp/run_example.py stage23 --seeds 2 --use-knowledge-base --out examples/loanapp/output/stage23_kb
healthy reference logs for LoanApp (2 seeds) ...
baseline runs for LoanApp_G3 (2 seeds) ...
Stages 2+3, LoanApp_G3, KPIs ['Loan Officer Workload Distribution'], knowledge base on ...
2026-11-05 13:22:18.661 WARNING streamlit.runtime.caching.cache_data_api: No runtime found, using MemoryCacheStorageManager
2026-11-05 13:22:18.665 WARNING streamlit.runtime.caching.cache_data_api: No runtime found, using MemoryCacheStorageManager
pypdf not installed — PDF indexing disabled. Run: pip install pypdf
Cross-encoder unavailable (No module named 'sentence_transformers') — falling back to pure RRF.

record: examples/loanapp/output/stage23_kb/raw/LoanApp_G3/rep_1.json
full report: examples/loanapp/output/stage23_kb/report.txt
status: ok   elapsed: 135.0 s   iterations: 1   final iteration: 1
  iteration 1: status improved, score 50.0 | resource_count Loan Officer: 1 -> 3; activity_duration Assess loan risk: 1200.0 -> 960.0
  initial (iteration 1, 2 seeds): valid True
    Loan Officer Workload Distribution: baseline 0.8595 -> proposed 0.242 (-71.84 %), improved True
    target-role utilization: 0.8598 -> 0.242 (improved)
  final (iteration 1, 2 seeds): valid True
    Loan Officer Workload Distribution: baseline 0.8595 -> proposed 0.242 (-71.84 %), improved True
    target-role utilization: 0.8598 -> 0.242 (improved)
```

- **Run time.** Each command took 3–4 minutes, most of it waiting for the LLM.
- **Warnings.** The two `streamlit` warnings come from cached helpers called outside the UI; they are harmless.
  With the knowledge base on, the two further warnings are about the retriever's optional packages, absent as
  in the experiments.
- **Knowledge base in the record.** The record stores `use_knowledge_base` (`false` / `true`). With the
  knowledge base off, the scenario prompt has no "Literature-Backed Parameter Recommendations" section; with
  it on, the section is there.
- **Baseline.** The baseline is identical in every run: its Loan Officer utilization is 0.86 on seeds 42 and 43.
- **What varies between runs:**
  - **Patch.** In other fresh runs, the LLM added one Loan Officer and reassigned an activity, or added one and
    shortened "Assess loan risk". Utilization fell to 0.46 and 0.36.
  - **Refinement.** When the first proposal does not improve the KPI, the loop continues for up to 4 iterations.
- **Report.** `report.txt` shows the prompts' constraint block, every iteration's modifications and feedback,
  confidence intervals, p-values and the log distances to the reference model.

## Optional: knowledge-base retrieval

GLASS has a knowledge base of goal-to-parameter recommendations and findings from simulation case studies
(`goal_to_parameters/knowledge/kb_data.py`, references in `Papers/CaseStudy/README.md`). With it on, Stage 2
retrieves the entries that match the goal and its KPIs (hybrid BM25 + dense retrieval) and adds them to the
scenario prompt as "Literature-Backed Parameter Recommendations". The refinement feedback then also lists
literature-backed alternative interventions for KPIs that did not improve.

**Switching it on.** It is off by default. Each of these switches it on:
- `use_knowledge_base: true` in `goal_to_parameters/config.yaml` (the default for every entry point);
- the toggle **Use the knowledge base** in the Scenario Studio, for the session;
- `--use-knowledge-base` on `examples/loanapp/run_example.py stage23` and
  `evaluation/loanapp_p2p/scripts/run_stage2_batch.py`, for the run (`--no-knowledge-base` is the default).

```bash
python examples/loanapp/run_example.py stage23 --seeds 2 --use-knowledge-base
python evaluation/loanapp_p2p/scripts/run_stage2_batch.py --use-knowledge-base --workers 4 --out <folder>
```

**What the option changes.** It decides only whether knowledge-base retrieval happens. With it off, there is
no call to `knowledge.retrieval.retrieve_for_second_llm` in generation or feedback, and the prompt and the
feedback have no knowledge-base section. Everything else is the same either way: the prompts' wording, the
log, SIMOD and context evidence, the clarification answers, the validators, the merger, the loop, the scoring
and the metrics. Full-text PDF excerpts and cross-encoder re-ranking are further options of the retriever; no
reported run used them.

**Which reported results used which setting.** The main Stage 2 results are the v3.2 runs without the
knowledge base. The runs with it are additional experiments. Stage 1 and the refinement pilot do not use
the knowledge base.

| Results | Knowledge base |
|---|---|
| `evaluation/loanapp_p2p/results/stage2_results_v32_rag/V_no_kb/` (LoanApp/P2P, v3.2) | off (main results) |
| `evaluation/real_life_ablation/results/stage2/`, variant V_no_kb | off (main results) |
| `evaluation/loanapp_p2p/results/stage2_results_v32_rag/V_full/` (comparison with the knowledge base) | on |
| `evaluation/real_life_ablation/results/stage2/`, variants V_full, V_no_log, V_no_context, V_no_refine, V_old_refine | on |
| `evaluation/loanapp_p2p/results/stage2_results*/` v1 to v3.1 | on |
| `evaluation/loanapp_p2p/results/stage2_loop_seeds/` (loop replications) | on |

**Consistency with the reported runs.** The runs without the knowledge base switched it off at the same three
points as the option does (`evaluation/loanapp_p2p/versions/README.md`, v3.3). Replaying the recorded LLM
answers of all 70 + 45 runs without it (option off) and of 10 runs with it (option on) reproduces every system
prompt, temperature and first refinement feedback byte for byte. Every user prompt is reproduced up to the
order of entries in a few JSON lists that GLASS builds from Python sets, which varies between processes
(`evaluation/loanapp_p2p/results/kb_option_consistency.md`).

## Experiments and results

All experiment files are in `evaluation/`; their READMEs describe each run, its settings and its scoring.

- `evaluation/loanapp_p2p/`: LoanApp and Procure2Pay evaluation (14 configurations, 5 repetitions each).
  - `setup/`: configurations.
  - `input_logs/`: event logs.
  - `stage2/`: baseline and reference models.
  - `scripts/`: batch, reporting and verification scripts.
  - `versions/`: patches between the GLASS code versions, with the original commit IDs.
  - `results/`:
    - `stage1_results/`, `stage1_results_normalised/`: Stage 1.
    - `refinement_pilot/`: KPI refinement pilot.
    - `stage2_results/`, `stage2_results_v2/`, `stage2_results_v3/`, `stage2_results_v3_1/`: Stage 2 batches
      v1–v3.1.
    - `stage2_results_v32_rag/`: v3.2 without the knowledge base (`V_no_kb/`, main results) and with it
      (`V_full/`).
    - `stage2_loop_seeds/`: the refinement loop with 1 and with 5 paired seeds per iteration (LoanApp).
    - `seed_analysis.*`: how the results depend on the number of evaluation seeds.
    - `kb_option_consistency.*`: replay of recorded runs with the knowledge-base option.
    - Comparisons `stage2_*.md`/`.csv` and workbooks `GLASS_*.xlsx`.
    - `runtimes.md`/`.csv`: run times.
    - `VERSIONS.md`: what changed between the versions.
- `evaluation/real_life_ablation/`: ablation on three real-life logs (AcademicCredentials, BPIC 2012 W,
  BPIC 2017 W).
  - `data/`, `baselines/`, `setup/`: inputs.
  - `scripts/`: batch and reporting scripts.
  - `results/stage1/`, `results/stage2/`: run records and summaries.
  - `results/GLASS_ablation_results.xlsx`: workbook.
- BPIC 2012, BPIC 2017 and Sepsis evaluation (`evaluation/README.md`):
  - `evaluation/*.py`: scripts.
  - `stage1_kpis/`, `stage2_results/`: results.
  - `simod_outputs/`: discovered baselines.
  - `figures/`.
- `Papers/CaseStudy/README.md`: references of the case-study literature in the (optional) knowledge base.

**Not in the repository:**
- **Simulated logs of the Stage 2 runs.** Their SHA-256 values are in the run records.
- **The case-study PDFs.** The reported runs did not use them (`Papers/CaseStudy/README.md`).

**Records left unchanged:**
- **Upstream absolute path.** The real-life ablation's baseline files from the upstream SIMOD package
  (`evaluation/real_life_ablation/data/*/*_enhanced.json`), and the 270 Stage 2 run records that embed them,
  contain the absolute Windows path under which that package was built. They are kept byte-identical so that the
  SHA-256 values recorded for them still hold.
- **Recorded hashes.** The run records name the GLASS code by its original commit ID; `evaluation/loanapp_p2p/versions/`
  rebuilds each version. The stage tags and some comment wording in this tree differ from those bytes, so a few
  recorded file hashes refer to the original files (`evaluation/loanapp_p2p/versions/README.md`).

## Known limitations

- **UI keyword check.** Before a Stage 1 call, the UI checks that the process description and the goal contain
  BPM keywords (`validate_generation_scope` in `app.py`). A goal without a performance word ("cycle time",
  "waiting time", "throughput", "utilization", "cost", …) is rejected, even when it is clear, as the example's
  goal is. The command line and the evaluation scripts record the result of this check but do not enforce it.
- **Calendars in whole hours.** Calendar periods are stored in whole hours (`TimetableItem`). Calendar changes
  given as text or in Prosimos's period format are accepted only in whole hours; other changes are rejected with
  feedback (`docs/DESIGN_NOTES.md`, "Calendars").
- **Context summary fields.** An LLM condenses the clarification chat into a fixed JSON schema
  (`second_llm/context_summary.py`): per-role resource constraints, budget, calendar constraints, SLAs, immutable
  parameters, operational policies and process context.
  - **Enforced:** the validators enforce only headcount, absolute and relative budget, immutable elements, and
    overtime and shift-extension limits.
  - **Prompt only:** the other fields reach the LLM as prompt text. A statement that fits no field is only in the
    chat history.
  - **Misreadings:** the summary is itself an LLM output and can misread an answer (`docs/DESIGN_NOTES.md`,
    "Clarification-context summary").
- **Baselines in the UI.** The Scenario Studio simulates only baselines it holds as BPMN plus parameters: from
  "Run SIMOD" or from its bundled examples. A pasted or uploaded parameter file carries no BPMN, so a given
  baseline such as `examples/loanapp/baseline/` runs from the command line only.
- **Prompt wording with the knowledge base off.** The scenario system prompt still mentions knowledge-base
  recommendations and a `literature_support` field when the knowledge base is off. This is the wording of the
  reported runs without it, kept so that the option reproduces them.
- **Order of some prompt entries.** A few JSON lists in the Stage 2 prompt (for example the evidence sections
  needed for a goal) are built from Python sets. Their order changes between processes with Python's hash
  randomization. Set `PYTHONHASHSEED` to a fixed value for byte-identical prompts.
- **Dense retrieval without `sentence-transformers`.** With the knowledge base on and without the optional
  `sentence-transformers` (as in the experiments), dense retrieval falls back to TF-IDF. When the corpus
  vectors come from the disk cache, the TF-IDF vocabulary is fitted on the first query the process encodes, so
  the dense ranking depends on that query.
- **LLM nondeterminism.** The LLM is not seeded. A rerun produces new samples; the stored run records are the
  results.
- **Verification scripts that need the original history.** `reprocess_stage1_normalised.py`,
  `verify_stage1_results.py` and `verify_refinement_pilot.py` (in `evaluation/loanapp_p2p/scripts/`) locate the
  Stage 1 base commit in git by its original ID. The batch scripts record the git commit of their working copy, if
  there is one.

## Tests

Run the two suites separately (one test in `tests/` replaces the `streamlit` module with a stub, which breaks the
other suite when both run in one process):

```bash
python -m pytest goal_to_parameters/tests
python -m pytest tests
```

In a fresh setup, `goal_to_parameters/tests` passes (37 tests). In `tests/`, 363 tests pass and 2 are skipped:
- a live LLM test, which runs with `GLASS_LIVE_LLM_TESTS=1` and an API key;
- a test that needs the stored Stage 2 simulated logs, which are not in the repository.

Two tests fail, as they did before the evaluation's GLASS changes (`evaluation/loanapp_p2p/README.md`,
"Regression check"):
- `test_hybrid_retrieval.py::test_loan_query_finds_loan_paper` (a ranking expectation);
- `test_second_llm_chatbot.py::test_is_ready_true_after_minimum_turns_and_signal` (chat readiness).

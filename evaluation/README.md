# Evaluation

Reproducibility harness for the thesis evaluation on three public event logs: **BPIC 2017**, **BPIC 2012**, and **Sepsis Cases**.

The pipeline is split into two evaluated stages, matching the two LLM stages of the main application:

- **Stage 1** measures KPI generation quality (Goal → SMART KPIs) with four metrics: coverage against frozen reference categories, computability, SMART completeness, and set stability across runs.
- **Stage 2** measures scenario proposal quality (KPIs → ScenarioPatch → simulation) with three metrics — directional hit rate, mean KPI improvement, net improvement score — computed across an ablation of four pipeline variants.

---

## Order of operations

Run from the repository root.

### 1. Download the event logs

```bash
python evaluation/download_logs.py
```

Downloads BPIC 2017, BPIC 2012, and Sepsis into `evaluation/logs/raw/`. These files are large (~300MB) and are excluded from git — regenerate them here rather than committing.

### 2. Convert XES to CSV

```bash
python evaluation/convert_xes_to_csv.py --all
```

### 3. Run SIMOD to get baseline parameters (needs Docker Desktop running)

```bash
python evaluation/run_simod_baselines.py
```

Outputs land in `evaluation/simod_outputs/<log>/simod_raw.json` — these are the SIMOD-discovered baselines that Stage 2 modifies.

### 4. Freeze Stage 1 reference categories

Open `run_stage1_evaluation.py` and fill in `REFERENCE_CATEGORIES` **before** running Stage 1. Do not change these after seeing results — they define the ground truth for the M1 coverage metric.

### 5. Run Stage 1

```bash
python evaluation/run_stage1_evaluation.py
```

Produces:
- `evaluation/stage1_results/stage1_results.csv` — M1–M4 per log per run
- `evaluation/stage1_kpis/<log>.json` — validated Stage 1 KPI set (fed into Stage 2)

### 6. Run Stage 2 ablation (needs Docker Desktop running for Prosimos)

```bash
python evaluation/run_stage2_ablation.py
```

Executes all four pipeline variants against all three logs across 10 independent seeds. The script auto-resumes on interruption — completed rows are skipped, results are written incrementally.

Produces:
- `evaluation/stage2_results/stage2_results.csv` — DHR, MKI, NIS per variant per log
- `evaluation/stage2_results/patches/<variant>_<log>.json` — best ScenarioPatch per run

---

## Metrics

**Stage 1**

| Metric | Meaning |
|---|---|
| M1 — Category Coverage | Overlap between generated KPI categories and frozen reference categories |
| M2 — Computability | Fraction of KPI formulas that reference real event-log columns |
| M3 — SMART Completeness | Schema-valid and all SMART fields populated |
| M4 — Set Stability | Category overlap across 5 independent runs |

**Stage 2 variants**

| Variant | Description |
|---|---|
| `V_full` | Full pipeline — RAG + clarification chat + up to 4 optimization iterations |
| `V_no_rag` | Ablates RAG evidence |
| `V_no_chat` | Ablates clarification-chat context |
| `V_no_iter` | Ablates iterative feedback (single-shot generation) |

**Stage 2 metrics** (mean across 10 seeds, 1000 simulated cases per run)

| Metric | Meaning |
|---|---|
| DHR — Directional Hit Rate | Fraction of KPIs that moved in the direction the pipeline targeted |
| MKI — Mean KPI Improvement | Mean % change across KPIs that improved |
| NIS — Net Improvement Score | Direction-corrected mean % change across **all** KPIs (positive = net gain) |
| score | Internal prospect-theory loop score with loss aversion λ=2.25 |

---

## What is checked into git

- All `run_*.py` scripts (reproducibility)
- `stage1_kpis/`, `stage1_results/`, `stage2_results/` — the actual thesis results
- `simod_outputs/*/simod_raw.json` — needed as input examples for the main app's Second Workspace
- `figures/` — result charts referenced in the thesis
- Baseline patches (`baseline_patch_*.json`) used for comparison in `run_baseline_comparison.py`

Excluded from git (regenerate locally):
- `logs/raw/` and `logs/csv/` — public datasets, download with `download_logs.py`
- Everything under `simod_outputs/` besides `simod_raw.json` (large intermediate CSVs / BPMNs from SIMOD's internal working directory)

---

## Dashboards

Two Jupyter notebooks summarize the results visually:

- `baseline_comparison_dashboard.ipynb` — compares the full pipeline against direct-LLM baselines
- `results_dashboard.ipynb` — ablation study visualization

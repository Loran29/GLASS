"""
Stage-2 Ablation Harness
========================
Uses the real iterative generate→simulate→evaluate→feedback loop
(run_iterative_evaluation) — matching what the app actually does.

Variants
--------
  V_full    — full pipeline: RAG + chat history + up to 4 optimization iterations
  V_no_rag  — no RAG evidence (empty log_profile), chat + 4 iterations
  V_no_chat — no chat history, RAG + 4 iterations
  V_no_iter — no iterative feedback (max_iterations=1), RAG + chat

For each (variant × log):
  1. Load the pre-computed SIMOD baseline
  2. Build a fresh orchestrator and load the Stage-1 KPI JSON + SIMOD
  3. Run run_iterative_evaluation with variant-specific settings
  4. Run evaluate_multi_seed (NUM_SEEDS seeds) on the best iteration's scenario
  5. Record DHR, MKI, NIS, schema_valid, n_iterations, best_iter_score

Usage (from Thesis/goal_to_parameters/):
    python ../evaluation/run_stage2_ablation.py

Results written to evaluation/stage2_results_<tag>/stage2_results.csv
Patches written to evaluation/stage2_results_<tag>/patches/
Default tag (no flag) is "gpt4o", preserving original behaviour.
"""

from __future__ import annotations

# -----------------------------------------------------------------------
# Streamlit mock — state.py uses st.session_state as a plain dict.
# Patch the module before any imports so we can run outside Streamlit.
# -----------------------------------------------------------------------
import sys as _sys
from types import ModuleType as _ModuleType

if "streamlit" not in _sys.modules:
    _st_mock = _ModuleType("streamlit")
    _st_mock.session_state = {}  # type: ignore[attr-defined]
    _sys.modules["streamlit"] = _st_mock

import argparse
import csv
import json
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# --- sys.path setup ---
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "goal_to_parameters"))

from second_llm.orchestrator import SecondLLMWorkspaceOrchestrator
from second_llm.iterative_evaluator import run_iterative_evaluation
from second_llm.models import RawSimodInput, SimodResult, ChatRole
from second_llm.state import reset_workspace, set_raw_simod_input
from second_llm.multi_seed_evaluation import evaluate_multi_seed
from second_llm.simod_to_simubridge import build_baseline_scenario
from second_llm.scenario_evaluation import KPITarget, TargetDirection
from utils.log_processing import profile_event_log

# -----------------------------------------------------------------------
# Configuration — edit these before running
# -----------------------------------------------------------------------

NUM_SEEDS    = 10    # replications per (variant × log) for the final metric
TOTAL_CASES  = 1000  # cases per Prosimos simulation run (in the loop and final)
CSV_DIR      = REPO_ROOT / "evaluation" / "logs" / "csv"

DEFAULT_MODEL = "openai/gpt-4o-2024-11-20"
DEFAULT_TAG   = "gpt4o"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage-2 ablation harness")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"OpenRouter model ID (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--tag",
        default=DEFAULT_TAG,
        help=(
            "Short label appended to the results directory name, e.g. 'gpt5_4'. "
            f"Default: '{DEFAULT_TAG}' → writes to evaluation/stage2_results_gpt4o/"
        ),
    )
    return parser.parse_args()


def make_provider(model: str) -> object:
    import os
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / "goal_to_parameters" / ".env")
    from llm.openrouter_provider import OpenRouterProvider
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set in .env")
    print(f"Using model: {model}")
    return OpenRouterProvider(api_key=api_key, model=model)

# -----------------------------------------------------------------------
# Operational chat histories — injected verbatim as clarification context
# -----------------------------------------------------------------------

CHAT_HISTORY_BPIC2017 = [
    {
        "role": "assistant",
        "content": (
            "SIMOD shows 3 resources assigned to W_Validate application — "
            "the activity with the longest average wait time in the baseline. "
            "Is that headcount fixed, or can we add the one additional specialist "
            "mentioned in the goal, and if so, would they cover W_Validate "
            "application only or also W_Assess potential fraud?"
        ),
    },
    {
        "role": "user",
        "content": (
            "At most one additional specialist total, shared across both "
            "W_Validate application and W_Assess potential fraud — they draw "
            "from the same resource pool."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Confirmed: validation headcount is capped at +1 across both roles. "
            "The baseline also shows W_Complete application contributing to queue "
            "build-up on busy days — is there a monthly budget ceiling for "
            "overtime on these work-queue activities, or are weekday shift "
            "extensions possible within the current budget?"
        ),
    },
    {
        "role": "user",
        "content": (
            "Up to 10% overtime is allowed, but only on weekdays. "
            "No weekend shifts, and there is no separate overtime budget — "
            "the 10% cap is the hard limit."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "So far: +1 validation specialist max shared across W_Validate "
            "application and W_Assess potential fraud, and weekday overtime "
            "capped at 10% with no additional budget. "
            "Last question — are there any activities that must appear in every "
            "case regardless of the scenario? For example, are A_Validating "
            "or W_Assess potential fraud locked by Dutch consumer-credit "
            "regulations?"
        ),
    },
    {
        "role": "user",
        "content": (
            "Yes — A_Validating and W_Assess potential fraud must remain in "
            "every case. These are mandatory compliance steps under Dutch and "
            "EU consumer-credit rules and cannot be removed or bypassed under "
            "any scenario."
        ),
    },
]

CHAT_HISTORY_BPIC2012 = [
    {
        "role": "assistant",
        "content": (
            "SIMOD shows 4 resources on W_Nabellen offertes — the call-back "
            "activity with the highest repetition count in the baseline. "
            "Is that headcount fixed, or can it be reduced to lower the "
            "overall call-back load? If reducible, is there a floor below "
            "which offer follow-up SLAs would break?"
        ),
    },
    {
        "role": "user",
        "content": (
            "Headcount can be reduced but not below 3 FTEs on W_Nabellen "
            "offertes — below that our SLA for offer follow-up breaks. "
            "W_Completeren aanvraag can be reassigned but must stay within "
            "the existing contact-centre pool, no external contractors."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Confirmed: W_Nabellen offertes has a floor of 3 FTEs, and "
            "W_Completeren aanvraag stays within the contact-centre pool. "
            "Is there an overtime budget available for W_Nabellen incomplete "
            "dossiers or W_Valideren aanvraag if we need to extend their "
            "processing hours to reduce queue build-up?"
        ),
    },
    {
        "role": "user",
        "content": (
            "Overtime budget is capped at €3,000 per month across all "
            "contact-centre activities — that is a hard ceiling with no "
            "exceptions."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "So far: W_Nabellen offertes floored at 3 FTEs, W_Completeren "
            "aanvraag stays in-pool, and overtime is capped at €3,000/month "
            "across all activities. "
            "SIMOD's branching shows a significant share of cases reaching "
            "A_DECLINED and A_CANCELLED outcomes — is there a policy floor "
            "on the combined decline and cancel rate that the scenario must "
            "respect?"
        ),
    },
    {
        "role": "user",
        "content": (
            "Yes — the decline and cancel rate combined must not drop below "
            "15%. That reflects our credit risk policy and is a hard "
            "constraint that cannot be changed."
        ),
    },
]

CHAT_HISTORY_SEPSIS = [
    {
        "role": "assistant",
        "content": (
            "SIMOD shows 2 resources covering both ER Registration and "
            "IV Antibiotics — the two activities on the critical path for "
            "sepsis treatment time. Are the nurse and physician roles on "
            "these activities fixed, or is there flexibility to reassign "
            "one practitioner to fast-track suspected-sepsis cases during "
            "peak hours?"
        ),
    },
    {
        "role": "user",
        "content": (
            "We can reassign one nurse practitioner to prioritise sepsis-suspect "
            "cases during peak hours, but we cannot add new headcount — total "
            "staffing must stay within current emergency-department levels."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Confirmed: one nurse practitioner can be reassigned for peak-hour "
            "prioritisation, no new headcount. "
            "The baseline shows Leucocytes and CRP tests with significant "
            "processing time — is there a budget available for lab resource "
            "changes such as prioritising sepsis-suspect samples within "
            "existing shifts, or are lab staffing costs treated as fixed?"
        ),
    },
    {
        "role": "user",
        "content": (
            "Lab staffing costs are fixed — no additional lab resource spend "
            "is possible. Any improvement must come from scheduling and "
            "prioritisation changes within the existing budget."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "So far: one nurse practitioner redeployable at peak hours, "
            "lab costs fixed, improvements must come from scheduling. "
            "Are there any shift or regulatory constraints that are "
            "immovable — for instance, is the overnight staffing gap between "
            "23:00 and 07:00 a hard constraint, and must the final clinical "
            "sign-off on every release always follow the last lab result?"
        ),
    },
    {
        "role": "user",
        "content": (
            "Yes — clinicians must have at least 11 hours between shifts by "
            "law, so the overnight ER gap is a fixed regulatory constraint "
            "we cannot change. And the final sign-off on every release must "
            "always follow the last clinical result — we cannot sign off "
            "before all results are reviewed."
        ),
    },
]

# -----------------------------------------------------------------------
# Scenario definitions
# -----------------------------------------------------------------------

SCENARIOS = [
    {
        "log":          "bpic2017",
        "goal":         (
            "Shorten the time customers wait between applying and receiving a usable offer "
            "while increasing the share of applications that reach an accepted outcome, "
            "without adding more than one additional validation specialist and without "
            "reducing the completeness of mandatory compliance checks on any file."
        ),
        "kpi_json":     REPO_ROOT / "evaluation" / "stage1_kpis" / "bpic2017.json",
        "chat_history": CHAT_HISTORY_BPIC2017,
    },
    {
        "log":          "bpic2012",
        "goal":         (
            "Reduce the overall time and the number of customer call-back rounds needed to "
            "bring an application to a final decision while keeping staff workload within "
            "current contact-centre capacity, and do so without lowering the consistency of "
            "how acceptance and decline decisions are applied."
        ),
        "kpi_json":     REPO_ROOT / "evaluation" / "stage1_kpis" / "bpic2012.json",
        "chat_history": CHAT_HISTORY_BPIC2012,
    },
    {
        "log":          "sepsis",
        "goal":         (
            "Decrease the time from patient arrival to completed diagnostics and start of "
            "antibiotic treatment, and increase the proportion of suspected-sepsis patients "
            "treated within the recommended clinical window, while staying within current "
            "emergency-staffing levels and respecting mandatory clinician rest requirements."
        ),
        "kpi_json":     REPO_ROOT / "evaluation" / "stage1_kpis" / "sepsis.json",
        "chat_history": CHAT_HISTORY_SEPSIS,
    },
]

SIMOD_DIR   = REPO_ROOT / "evaluation" / "simod_outputs"
# RESULTS_DIR is set dynamically in main() based on --tag; this placeholder
# is overwritten before any file writes happen.
RESULTS_DIR = REPO_ROOT / "evaluation" / "stage2_results_gpt4o"

# -----------------------------------------------------------------------
# Variant definitions
# -----------------------------------------------------------------------

@dataclass
class Variant:
    name: str
    use_rag: bool       = True
    use_chat: bool      = True
    max_iterations: int = 4


VARIANTS = [
    Variant("V_full",    use_rag=True,  use_chat=True,  max_iterations=4),
    Variant("V_no_rag",  use_rag=False, use_chat=True,  max_iterations=4),
    Variant("V_no_chat", use_rag=True,  use_chat=False, max_iterations=4),
    Variant("V_no_iter", use_rag=True,  use_chat=True,  max_iterations=1),
]

# -----------------------------------------------------------------------
# Result record
# -----------------------------------------------------------------------

@dataclass
class RunResult:
    variant:         str
    log:             str
    schema_valid:    bool  = False   # best iteration produced a valid patch
    attempts:        int   = 0       # total LLM calls summed across all iterations
    n_iterations:    int   = 0       # iterations actually run
    best_iter_score: float = 0.0     # prospect-theory score of the best iteration
    dhr_mean:        float = 0.0     # KPI directional hit rate (multi-seed mean)
    dhr_ci_half:     float = 0.0     # 95% CI half-width on DHR
    mki_mean:        float = 0.0     # mean % improvement for hit KPIs only
    mki_ci_half:     float = 0.0
    nis_mean:        float = 0.0     # net improvement score (direction-corrected, all KPIs)
    nis_ci_half:     float = 0.0     # 95% CI half-width on NIS
    n_kpis:          int   = 0       # total KPIs evaluated
    n_kpis_hit:      int   = 0       # KPIs that moved in the right direction
    error:           str   = ""


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _load_simod(log_name: str) -> dict[str, Any]:
    raw_path = SIMOD_DIR / log_name / "simod_raw.json"
    if not raw_path.exists():
        raise FileNotFoundError(
            f"No SIMOD output for {log_name}. "
            f"Run run_simod_baselines.py first."
        )
    with open(raw_path) as f:
        return json.load(f)


def _load_kpi_json(path: Path) -> tuple[str, dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"Stage-1 KPI JSON not found: {path}\n"
            f"Run Stage 1 on the log and save the accepted KPI set to that path."
        )
    with open(path) as f:
        text = f.read()
    return text, json.loads(text)


def _compute_dhr_mki(multi_result) -> tuple[float, float, float, float, float, float, int, int]:
    """Extract DHR, MKI, and NIS from a MultiSeedEvaluationResult.

    DHR  — fraction of KPIs that moved in the right direction.
    MKI  — mean % change for improved KPIs only (hit KPIs only).
    NIS  — direction-corrected mean % change across ALL KPIs:
           improved KPI contributes +abs(change), worsened contributes -abs(change).
           Positive NIS = net improvement overall; negative = net harm.
    """
    if multi_result is None:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0

    import math, statistics

    hits, deltas, nis_scores = [], [], []
    for comp in multi_result.kpi_comparisons:
        if comp.improved is None or comp.mean_percentage_change is None:
            continue
        hits.append(1.0 if comp.improved else 0.0)
        if comp.improved:
            deltas.append(comp.mean_percentage_change)
        sign = 1.0 if comp.improved else -1.0
        nis_scores.append(abs(comp.mean_percentage_change) * sign)

    n = len(hits)
    n_hit = int(sum(hits))
    dhr = sum(hits) / n if n else 0.0
    mki = sum(deltas) / len(deltas) if deltas else 0.0
    nis = sum(nis_scores) / len(nis_scores) if nis_scores else 0.0

    dhr_ci = 0.0
    if n > 1:
        dhr_ci = 1.96 * statistics.stdev(hits) / math.sqrt(n)
    mki_ci = 0.0
    if len(deltas) > 1:
        mki_ci = 1.96 * statistics.stdev(deltas) / math.sqrt(len(deltas))
    nis_ci = 0.0
    if len(nis_scores) > 1:
        nis_ci = 1.96 * statistics.stdev(nis_scores) / math.sqrt(len(nis_scores))

    return dhr, dhr_ci, mki, mki_ci, nis, nis_ci, n, n_hit


# -----------------------------------------------------------------------
# Core per-variant runner
# -----------------------------------------------------------------------

def run_variant_on_log(variant: Variant, scenario: dict, provider) -> RunResult:
    log_name = scenario["log"]
    rec = RunResult(variant=variant.name, log=log_name)

    simod_data = _load_simod(log_name)
    kpi_json_text, kpi_parsed = _load_kpi_json(scenario["kpi_json"])

    # Fresh global workspace state so variants don't bleed into each other.
    reset_workspace()
    orch = SecondLLMWorkspaceOrchestrator(provider=provider)
    orch.parse_first_llm_json(kpi_json_text)

    # Load SIMOD into workspace state so generate_scenario can access it.
    simod_result_obj = SimodResult(
        bpmn_content=simod_data.get("bpmn_xml", ""),
        json_params_content=simod_data.get("json_text", ""),
        process_name=log_name,
    )
    raw_simod_obj = RawSimodInput(
        raw_text=simod_data.get("json_text", ""),
        is_non_empty=bool(simod_data.get("json_text")),
        line_count=len(simod_data.get("json_text", "").splitlines()),
        simod_result=simod_result_obj,
    )
    set_raw_simod_input(raw_simod_obj)
    # Keep chatbot's reference in sync so has_required_inputs() works.
    orch._chatbot._simod = raw_simod_obj

    # Inject clarification chat history (V_no_chat gets none).
    if variant.use_chat:
        for msg in scenario["chat_history"]:
            role_str = msg.get("role", "")
            if role_str == "user":
                orch._ws.clarification_session.append(ChatRole.USER, msg["content"])
            elif role_str == "assistant":
                orch._ws.clarification_session.append(ChatRole.ASSISTANT, msg["content"])

    # Build log_profile for RAG-enabled variants.
    log_profile = None
    if variant.use_rag:
        csv_path = CSV_DIR / f"{log_name}.csv"
        print(f"  [profile] Building log profile for {log_name} (may take ~30s)...")
        with open(csv_path, "rb") as fh:
            log_profile = profile_event_log(fh)
        print(f"  [profile] Done.")

    # Build the SIMOD baseline scenario for simulation.
    baseline_result = build_baseline_scenario(
        simod_data.get("json_content"),
        bpmn_xml=simod_data.get("bpmn_xml", ""),
    )
    if baseline_result.scenario is None:
        rec.error = f"build_baseline_scenario failed: {baseline_result.errors}"
        return rec
    baseline_scenario = baseline_result.scenario

    # Build KPI targets from the Stage-1 KPI JSON.
    from models import KPIGenerationResult
    kpi_result = KPIGenerationResult.model_validate(kpi_parsed)
    targets = [
        KPITarget(
            name=k.name,
            direction=TargetDirection(k.target_direction.value),
            category=k.category.value if hasattr(k.category, "value") else str(k.category),
            measurable_as=k.measurable_as,
        )
        for k in kpi_result.kpis
    ]

    # Run the full iterative optimization loop.
    def _on_iteration(iteration: int, msg: str) -> None:
        print(f"  [iter {iteration}] {msg}", flush=True)

    iter_result = run_iterative_evaluation(
        orchestrator=orch,
        baseline_scenario=baseline_scenario,
        bpmn_xml=simod_data.get("bpmn_xml", ""),
        targets=targets,
        max_iterations=variant.max_iterations,
        total_cases=TOTAL_CASES,
        seed=42,
        log_profile=log_profile,
        on_iteration=_on_iteration,
    )

    rec.n_iterations = iter_result.total_iterations

    if iter_result.error:
        rec.error = iter_result.error
        return rec

    if iter_result.best is None:
        rec.error = "No valid scenario produced in any iteration"
        return rec

    best_iter = iter_result.best
    rec.best_iter_score = round(best_iter.score, 3)

    if best_iter.gen_result is None or best_iter.gen_result.proposal is None:
        rec.error = "Best iteration had no valid proposal"
        return rec

    rec.schema_valid = best_iter.gen_result.success
    rec.attempts = sum(
        it.gen_result.attempts if it.gen_result else 0
        for it in iter_result.iterations
    )

    best_scenario = best_iter.gen_result.proposal.scenario

    # Save the best scenario patch to disk so evaluation can be rerun cheaply.
    _save_patch(variant.name, log_name, best_iter.gen_result.proposal)

    # Final multi-seed evaluation for robust metrics.
    print(f"  [multi-seed] Running {NUM_SEEDS} seeds...", flush=True)
    multi_result = evaluate_multi_seed(
        baseline_scenario=baseline_scenario,
        proposed_scenario=best_scenario,
        bpmn_xml=simod_data.get("bpmn_xml", ""),
        targets=targets,
        num_seeds=NUM_SEEDS,
        on_progress=lambda idx, total, msg: print(f"  [multi-seed] {msg}", flush=True),
    )

    if multi_result is None or not multi_result.ok:
        rec.error = getattr(multi_result, "error", None) or "multi-seed evaluation failed"
        return rec

    dhr, dhr_ci, mki, mki_ci, nis, nis_ci, n, n_hit = _compute_dhr_mki(multi_result)
    rec.dhr_mean    = round(dhr, 4)
    rec.dhr_ci_half = round(dhr_ci, 4)
    rec.mki_mean    = round(mki, 2)
    rec.mki_ci_half = round(mki_ci, 2)
    rec.nis_mean    = round(nis, 2)
    rec.nis_ci_half = round(nis_ci, 2)
    rec.n_kpis      = n
    rec.n_kpis_hit  = n_hit
    return rec


# -----------------------------------------------------------------------
# Patch persistence
# -----------------------------------------------------------------------

def _save_patch(variant_name: str, log_name: str, proposal) -> None:
    """Write the best ScenarioProposal to a JSON file for later re-evaluation."""
    patches_dir = RESULTS_DIR / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    out = patches_dir / f"{variant_name}_{log_name}.json"
    with open(out, "w") as f:
        f.write(proposal.model_dump_json(indent=2))


# -----------------------------------------------------------------------
# Skip / resume helpers
# -----------------------------------------------------------------------

def _load_completed() -> set[tuple[str, str]]:
    """Return (variant, log) pairs that already have a successful result."""
    out = RESULTS_DIR / "stage2_results.csv"
    if not out.exists():
        return set()
    completed: set[tuple[str, str]] = set()
    with open(out, newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("error", "").strip():
                completed.add((row["variant"], row["log"]))
    return completed


def _load_existing_results() -> list[RunResult]:
    """Load all rows already in the CSV (successful and failed)."""
    out = RESULTS_DIR / "stage2_results.csv"
    if not out.exists():
        return []
    rows: list[RunResult] = []
    with open(out, newline="") as f:
        for row in csv.DictReader(f):
            rows.append(RunResult(
                variant=row["variant"],
                log=row["log"],
                schema_valid=row.get("schema_valid", "false").lower() == "true",
                attempts=int(row.get("attempts", 0) or 0),
                n_iterations=int(row.get("n_iterations", 0) or 0),
                best_iter_score=float(row.get("best_iter_score", 0.0) or 0.0),
                dhr_mean=float(row.get("dhr_mean", 0.0) or 0.0),
                dhr_ci_half=float(row.get("dhr_ci_half", 0.0) or 0.0),
                mki_mean=float(row.get("mki_mean", 0.0) or 0.0),
                mki_ci_half=float(row.get("mki_ci_half", 0.0) or 0.0),
                nis_mean=float(row.get("nis_mean", 0.0) or 0.0),
                nis_ci_half=float(row.get("nis_ci_half", 0.0) or 0.0),
                n_kpis=int(row.get("n_kpis", 0) or 0),
                n_kpis_hit=int(row.get("n_kpis_hit", 0) or 0),
                error=row.get("error", ""),
            ))
    return rows


def _write_csv(results: list[RunResult]) -> None:
    out = RESULTS_DIR / "stage2_results.csv"
    rows = [asdict(r) for r in results]
    if not rows:
        return
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


# -----------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------

def main() -> None:
    args = _parse_args()

    global RESULTS_DIR
    RESULTS_DIR = REPO_ROOT / "evaluation" / f"stage2_results_{args.tag}"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Results directory: {RESULTS_DIR}")

    provider = make_provider(args.model)

    completed = _load_completed()
    if completed:
        print(f"Skipping {len(completed)} already-successful row(s): {sorted(completed)}")

    results: list[RunResult] = _load_existing_results()

    total = len(VARIANTS) * len(SCENARIOS)
    done  = 0

    for scenario in SCENARIOS:
        for variant in VARIANTS:
            done += 1
            tag = f"[{done}/{total}] {variant.name} × {scenario['log']}"

            if (variant.name, scenario["log"]) in completed:
                print(f"\n{tag}  — SKIPPED (already succeeded)")
                continue

            # Remove any prior failed row so the re-run replaces it cleanly.
            results = [r for r in results
                       if not (r.variant == variant.name and r.log == scenario["log"])]

            print(f"\n{'='*60}")
            print(f"{tag}")
            print(f"  RAG={variant.use_rag}  chat={variant.use_chat}  max_iters={variant.max_iterations}")
            print('='*60)
            t0 = time.time()
            try:
                rec = run_variant_on_log(variant, scenario, provider)
            except Exception as exc:
                rec = RunResult(variant=variant.name, log=scenario["log"],
                                error=traceback.format_exc(limit=3))
                print(f"  EXCEPTION: {exc}")
            elapsed = time.time() - t0
            print(
                f"  DHR={rec.dhr_mean:.1%}  MKI={rec.mki_mean:+.1f}%  NIS={rec.nis_mean:+.1f}%  "
                f"valid={rec.schema_valid}  iters={rec.n_iterations}  "
                f"score={rec.best_iter_score:+.1f}  elapsed={elapsed:.0f}s"
            )
            results.append(rec)

            # Write incrementally so a crash doesn't lose everything.
            _write_csv(results)

    print(f"\n\nAll done. Results in {RESULTS_DIR / 'stage2_results.csv'}")


if __name__ == "__main__":
    main()

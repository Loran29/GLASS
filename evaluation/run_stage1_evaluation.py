"""
Stage-1 Evaluation Harness

For each log (BPIC2017, BPIC2012, Sepsis):
  M1 — Category Coverage Score  (vs. reference categories from published papers)
  M2 — Computability Rate       (KPI formulas reference real log columns)
  M3 — Schema / SMART Completeness
  M4 — Set Stability            (3 runs, measure category overlap)

IMPORTANT: Before running, fill in REFERENCE_CATEGORIES below using
published BPI challenge analysis papers. Lock these down BEFORE running
the system — setting them after seeing results is post-hoc and invalid.

Usage (from Thesis/goal_to_parameters/):
    python ../evaluation/run_stage1_evaluation.py
"""

from __future__ import annotations

import csv
import json
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "goal_to_parameters"))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / "goal_to_parameters" / ".env")

from llm.openrouter_provider import OpenRouterProvider
from utils import (
    KPIParsingError,
    build_context_evidence_prompt,
    build_log_evidence_prompt,
    parse_kpi_generation_payload,
    profile_event_log,
    validate_kpi_generation_semantics,
)
from prompts import build_smart_kpi_prompt

# -----------------------------------------------------------------------
# Reference KPI categories — FREEZE BEFORE RUNNING
#
# Source for each log:
#   bpic2017 — van Dongen (2017) BPI Challenge dataset description +
#              Camargo et al. (DSS 2020) which analyses this log
#   bpic2012 — van Dongen (2012) BPI Challenge dataset description
#   sepsis   — Mannhardt et al. (2016) Sepsis Cases dataset description
#
# Categories must be from: time, cost, quality, utilization, throughput,
# compliance, flexibility
# -----------------------------------------------------------------------

REFERENCE_CATEGORIES: dict[str, list[str]] = {
    "bpic2017": ["time", "quality", "throughput"],
    # time=cycle time / processing time;  quality=acceptance/rejection/rework;
    # throughput=completed applications per period
    "bpic2012": ["time", "quality", "throughput"],
    # time=application handling time;  quality=acceptance/cancellation rate;
    # throughput=completed applications per period
    "sepsis": ["time", "quality", "compliance"],
    # time=throughput time;  quality=readmission / return to ER;
    # compliance=care pathway adherence
}

# Goals to use for each log in Stage 1
GOALS: dict[str, str] = {
    "bpic2017": (
        "Shorten the time customers wait between applying and receiving a usable offer "
        "while increasing the share of applications that reach an accepted outcome, "
        "without adding more than one additional validation specialist and without "
        "reducing the completeness of mandatory compliance checks on any file."
    ),
    "bpic2012": (
        "Reduce the overall time and the number of customer call-back rounds needed to "
        "bring an application to a final decision while keeping staff workload within "
        "current contact-centre capacity, and do so without lowering the consistency of "
        "how acceptance and decline decisions are applied."
    ),
    "sepsis": (
        "Decrease the time from patient arrival to completed diagnostics and start of "
        "antibiotic treatment, and increase the proportion of suspected-sepsis patients "
        "treated within the recommended clinical window, while staying within current "
        "emergency-staffing levels and respecting mandatory clinician rest requirements."
    ),
}

DESCRIPTIONS: dict[str, str] = {
    "bpic2017": (
        "Our loan origination process begins when a customer submits an application "
        "online or through a branch, after which our staff validate the application, "
        "request any missing documentation, and prepare one or more offers for the "
        "customer to consider. A recurring frustration is that applications stall while "
        "we wait on incomplete customer paperwork, and our handling teams often chase "
        "the same leads repeatedly before an offer is either accepted or withdrawn. "
        "Under Dutch and EU consumer-credit rules we are expected to give applicants a "
        "timely and fair decision, and our compliance team has flagged that affordability "
        "and identity checks must be completed consistently on every file regardless of "
        "how busy we are. We are under commercial pressure to convert more qualified "
        "applicants into accepted offers before they go to a competitor, but our "
        "validation specialists are a limited and expensive pool, and weekend and evening "
        "coverage is thin. Management wants the process to feel faster to customers "
        "without cutting the checks that keep us within regulatory limits."
    ),
    "bpic2012": (
        "When a customer applies for a loan, the application moves through an initial "
        "submission and pre-acceptance screening, followed by completion of the "
        "application file and several rounds of our staff calling the customer back "
        "about outstanding offers before the application is finally approved, declined, "
        "or cancelled. The biggest pain point our team raises is the volume of "
        "call-backs needed to chase customers about offers, which ties up staff and "
        "drags out cases that frequently end up cancelled anyway. We are required to "
        "apply our acceptance criteria uniformly and to document the basis for every "
        "approval and decline, and supervisors have noted that rushed periods correlate "
        "with inconsistent handling. The contact-centre staff who handle the call-backs "
        "are shared with other products, so we cannot simply assume unlimited capacity, "
        "and overtime is tightly budgeted. Leadership would like cases to reach a clear "
        "outcome sooner and with less wasted effort on applications that are never going "
        "to complete."
    ),
    "sepsis": (
        "Patients arriving at the emergency department with suspected sepsis undergo "
        "triage and registration, followed by a series of diagnostic blood tests "
        "including Leucocytes, CRP, and Lactic Acid measurements. Based on test results, "
        "the clinical team administers intravenous antibiotics and fluids. Patients may "
        "be admitted to a normal-care or intensive-care ward, returned to the emergency "
        "department if their condition changes, or discharged through one of several "
        "release pathways. The process is time-critical: delays at registration, in "
        "laboratory turnaround, or in clinical decision-making can significantly worsen "
        "patient outcomes. Emergency staffing levels are constrained and clinicians are "
        "subject to mandatory rest requirements between shifts."
    ),
}

CSV_DIR     = REPO_ROOT / "evaluation" / "logs" / "csv"
KPI_DIR     = REPO_ROOT / "evaluation" / "stage1_kpis"
RESULTS_DIR = REPO_ROOT / "evaluation" / "stage1_results"
KPI_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

NUM_STABILITY_RUNS = 5

# Ablation variants for Stage 1
# "full"   — process description + goal + log evidence + context evidence (full pipeline)
# "no_log" — process description + goal only (no log evidence, no context evidence)
VARIANTS = ["full", "no_log"]


@dataclass
class Stage1Result:
    log:                    str
    variant:                str   = "full"   # "full" or "no_log"
    run:                    int   = 0        # 1, 2, or 3 for stability
    n_kpis:                 int   = 0
    schema_valid_count:     int   = 0
    schema_valid_rate:      float = 0.0   # M3 component
    smart_complete_count:   int   = 0
    smart_complete_rate:    float = 0.0   # M3 component
    computable_count:       int   = 0
    computable_rate:        float = 0.0   # M2
    category_coverage:      float = 0.0   # M1
    categories_generated:   str   = ""    # comma-separated for inspection
    stability:              float = 0.0   # M4 — filled in after all runs for this log
    error:                  str   = ""


def _generate_kpis(log_name: str, provider, run_num: int, use_log_evidence: bool = True) -> dict[str, Any] | None:
    """Run Stage 1 for one log and return the parsed KPI JSON dict."""
    csv_path = CSV_DIR / f"{log_name}.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    if use_log_evidence:
        # profile_event_log expects a binary file handle (same as app.py)
        with open(csv_path, "rb") as fh:
            log_profile = profile_event_log(fh)
        if log_profile is None:
            raise RuntimeError(f"profile_event_log returned None for {log_name}")
        log_evidence     = build_log_evidence_prompt(log_profile)
        context_evidence = build_context_evidence_prompt(log_profile)
    else:
        log_evidence     = None
        context_evidence = None

    system_prompt, few_shot_messages, user_prompt = build_smart_kpi_prompt(
        process_description = DESCRIPTIONS[log_name],
        simulation_goal     = GOALS[log_name],
        num_kpis            = None,          # Auto
        log_evidence        = log_evidence,
        context_evidence    = context_evidence,
    )

    # Call the LLM with up to 2 retries (same as the app)
    for attempt in range(3):
        raw = provider.generate(
            system_prompt      = system_prompt,
            user_prompt        = user_prompt,
            temperature        = 0.3,
            few_shot_messages  = few_shot_messages,
            json_mode          = False,
        )
        try:
            result = parse_kpi_generation_payload(raw)
            return result.model_dump(mode="python")
        except KPIParsingError as exc:
            if attempt == 2:
                raise
            # On failure, append the error to the user prompt and retry
            user_prompt = user_prompt + f"\n\nPrevious attempt failed: {exc}\nPlease fix and retry."

    return None


def _check_computability(kpi: dict, log_columns: set[str]) -> bool:
    """Check if the KPI formula references actual log columns or standard time concepts."""
    # Check both fields — suggested_formula is always filled; measurable_as is optional
    formula = " ".join(filter(None, [
        kpi.get("suggested_formula") or "",
        kpi.get("measurable_as") or "",
    ]))
    if not formula.strip():
        return False
    formula_lower = formula.lower()
    # Accept if any real log column name appears in the formula
    for col in log_columns:
        if col.lower() in formula_lower:
            return True
    # Accept generic computable formulas (timestamps, aggregations, process concepts)
    computable_keywords = [
        "cycle_time", "processing_time", "waiting_time", "throughput",
        "count", "rate", "duration", "timestamp", "start", "end",
        "avg", "mean", "sum", "max", "min", "time", "wait",
    ]
    return any(kw in formula_lower for kw in computable_keywords)


def _check_smart_complete(kpi: dict) -> bool:
    smart = kpi.get("smart_breakdown", {}) or {}
    required = ["specific", "measurable", "achievable", "relevant", "time_bound"]
    return all(bool(smart.get(f, "").strip()) for f in required)


def evaluate_one_run(log_name: str, provider, run_num: int, variant: str = "full") -> Stage1Result:
    rec = Stage1Result(log=log_name, variant=variant, run=run_num)
    use_log_evidence = (variant == "full")

    import pandas as pd
    csv_path = CSV_DIR / f"{log_name}.csv"
    df = pd.read_csv(csv_path, nrows=50_000)
    log_columns = set(col.lower() for col in df.columns)

    try:
        result = _generate_kpis(log_name, provider, run_num, use_log_evidence=use_log_evidence)
    except Exception as exc:
        rec.error = str(exc)
        return rec

    if result is None or not result.get("kpis"):
        rec.error = "no KPIs returned"
        return rec

    kpis = result["kpis"]
    rec.n_kpis = len(kpis)

    # M2 — Computability
    computable = [k for k in kpis if _check_computability(k, log_columns)]
    rec.computable_count = len(computable)
    rec.computable_rate  = len(computable) / len(kpis)

    # M3 — SMART completeness (schema validity assumed if we got here)
    rec.schema_valid_count = len(kpis)   # all KPIs passed Pydantic already
    rec.schema_valid_rate  = 1.0
    smart_complete = [k for k in kpis if _check_smart_complete(k)]
    rec.smart_complete_count = len(smart_complete)
    rec.smart_complete_rate  = len(smart_complete) / len(kpis)

    # M1 — Category Coverage
    generated_cats = set(k.get("category", "").lower() for k in kpis)
    reference_cats = set(REFERENCE_CATEGORIES.get(log_name, []))
    if reference_cats:
        rec.category_coverage = len(generated_cats & reference_cats) / len(reference_cats)
    rec.categories_generated = ",".join(sorted(generated_cats))

    # Save KPI JSON from full-pipeline run 1 for use in Stage 2
    if run_num == 1 and variant == "full":
        out = KPI_DIR / f"{log_name}.json"
        with open(out, "w") as f:
            json.dump(result, f, indent=2)
        print(f"  Saved Stage-1 KPIs to {out}")

    return rec


def compute_stability(results_for_log: list[Stage1Result]) -> float:
    """M4: fraction of reference categories appearing in ALL runs."""
    if len(results_for_log) < 2:
        return 0.0
    cat_sets = [
        set(r.categories_generated.split(",")) for r in results_for_log
        if r.categories_generated
    ]
    if not cat_sets:
        return 0.0
    intersection = cat_sets[0].intersection(*cat_sets[1:])
    union = cat_sets[0].union(*cat_sets[1:])
    return len(intersection) / len(union) if union else 0.0


def main() -> None:
    provider = make_provider()
    all_results: list[Stage1Result] = []

    logs = list(REFERENCE_CATEGORIES.keys())

    for log_name in logs:
        csv_path = CSV_DIR / f"{log_name}.csv"
        if not csv_path.exists():
            print(f"SKIP {log_name} — CSV not found at {csv_path}")
            continue

        for variant in VARIANTS:
            print(f"\n{'='*60}")
            print(f"Log: {log_name}  variant: {variant}  ({NUM_STABILITY_RUNS} runs)")
            print('='*60)

            runs: list[Stage1Result] = []
            for run in range(1, NUM_STABILITY_RUNS + 1):
                print(f"  Run {run}/{NUM_STABILITY_RUNS} ...")
                t0 = time.time()
                rec = evaluate_one_run(log_name, provider, run, variant=variant)
                elapsed = time.time() - t0
                print(f"    M2={rec.computable_rate:.0%}  M3={rec.smart_complete_rate:.0%}  "
                      f"M1={rec.category_coverage:.0%}  cats={rec.categories_generated}  "
                      f"t={elapsed:.0f}s")
                runs.append(rec)
                all_results.append(rec)

            stability = compute_stability(runs)
            print(f"  M4 (stability) = {stability:.0%}")

            for rec in runs:
                rec.stability = round(stability, 4)

            _write_csv(all_results)

    print(f"\nDone. Results in {RESULTS_DIR / 'stage1_results.csv'}")


def make_provider():
    import os
    from llm.openrouter_provider import OpenRouterProvider
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set in .env")
    model = "openai/gpt-4o-2024-11-20"
    print(f"Using model: {model}")
    return OpenRouterProvider(api_key=api_key, model=model)


def _write_csv(results: list[Stage1Result]) -> None:
    out = RESULTS_DIR / "stage1_results.csv"
    rows = [asdict(r) for r in results]
    if not rows:
        return
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()

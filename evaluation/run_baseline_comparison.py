"""
Baseline Comparison — muruvetg/from-simulation-goals-to-parameters
==================================================================
Evaluates the base repository approach against the same event logs
and KPI targets used in the Stage 2 ablation.

The base repo takes a single free-text goal and returns natural language
parameter suggestions. We replicate this by:

  Step 1 — Call GPT-4o with the base repo's system prompt style + merged
            process description + goal text → natural language suggestions.

  Step 2 — Call GPT-4o with a translation prompt → ScenarioPatch JSON
            using the exact same schema as our pipeline.

  Step 3 — Apply the patch to the SIMOD baseline deterministically
            (same apply_patch merger our pipeline uses).

  Step 4 — Run evaluate_multi_seed (NUM_SEEDS seeds, TOTAL_CASES cases)
            on the resulting scenario vs the same SIMOD baseline.

  Step 5 — Record DHR, NIS, Score — identical metrics to our ablation.

Results written to evaluation/stage2_results_baseline/stage2_results.csv
Patches written to evaluation/stage2_results_baseline/patches/

Usage (from Thesis/goal_to_parameters/):
    python ../evaluation/run_baseline_comparison.py
"""

from __future__ import annotations

import sys as _sys
from types import ModuleType as _ModuleType

if "streamlit" not in _sys.modules:
    _st_mock = _ModuleType("streamlit")
    _st_mock.session_state = {}
    _sys.modules["streamlit"] = _st_mock

import csv
import json
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "goal_to_parameters"))

from second_llm.models import RawSimodInput, SimodResult
from second_llm.state import reset_workspace, set_raw_simod_input
from second_llm.multi_seed_evaluation import evaluate_multi_seed
from second_llm.simod_to_simubridge import build_baseline_scenario
from second_llm.scenario_evaluation import KPITarget, TargetDirection
from second_llm.scenario_merger import apply_patch
from second_llm.output_schema_patch import ScenarioPatch, SCENARIO_PATCH_JSON_SCHEMA
from models import KPIGenerationResult

# -----------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------

NUM_SEEDS   = 10
TOTAL_CASES = 1000
CSV_DIR     = REPO_ROOT / "evaluation" / "logs" / "csv"
SIMOD_DIR   = REPO_ROOT / "evaluation" / "simod_outputs"
KPI_DIR     = REPO_ROOT / "evaluation" / "stage1_kpis"
RESULTS_DIR = REPO_ROOT / "evaluation" / "stage2_results_baseline"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

MODEL = "openai/gpt-4o-2024-11-20"


def make_provider():
    import os
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / "goal_to_parameters" / ".env")
    from llm.openrouter_provider import OpenRouterProvider
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set in .env")
    print(f"Using model: {MODEL}")
    return OpenRouterProvider(api_key=api_key, model=MODEL)


# -----------------------------------------------------------------------
# Inputs — same content as Stage 2 ablation, merged into one field
# (matching what the base repo receives in its single text input)
# -----------------------------------------------------------------------

SCENARIOS = [
    {
        "log": "bpic2017",
        "merged_input": (
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
            "without cutting the checks that keep us within regulatory limits. "
            "Shorten the time customers wait between applying and receiving a usable offer "
            "while increasing the share of applications that reach an accepted outcome, "
            "without adding more than one additional validation specialist and without "
            "reducing the completeness of mandatory compliance checks on any file."
        ),
        "kpi_json": KPI_DIR / "bpic2017.json",
    },
    {
        "log": "bpic2012",
        "merged_input": (
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
            "to complete. "
            "Reduce the overall time and the number of customer call-back rounds needed to "
            "bring an application to a final decision while keeping staff workload within "
            "current contact-centre capacity, and do so without lowering the consistency of "
            "how acceptance and decline decisions are applied."
        ),
        "kpi_json": KPI_DIR / "bpic2012.json",
    },
    {
        "log": "sepsis",
        "merged_input": (
            "Patients arriving at the emergency department with suspected sepsis undergo "
            "triage and registration, followed by a series of diagnostic blood tests "
            "including Leucocytes, CRP, and Lactic Acid measurements. Based on test results, "
            "the clinical team administers intravenous antibiotics and fluids. Patients may "
            "be admitted to a normal-care or intensive-care ward, returned to the emergency "
            "department if their condition changes, or discharged through one of several "
            "release pathways. The process is time-critical: delays at registration, in "
            "laboratory turnaround, or in clinical decision-making can significantly worsen "
            "patient outcomes. Emergency staffing levels are constrained and clinicians are "
            "subject to mandatory rest requirements between shifts. "
            "Decrease the time from patient arrival to completed diagnostics and start of "
            "antibiotic treatment, and increase the proportion of suspected-sepsis patients "
            "treated within the recommended clinical window, while staying within current "
            "emergency-staffing levels and respecting mandatory clinician rest requirements."
        ),
        "kpi_json": KPI_DIR / "sepsis.json",
    },
]


# -----------------------------------------------------------------------
# Step 1 — Base repo style prompt → natural language suggestions
# -----------------------------------------------------------------------

BASE_REPO_SYSTEM_PROMPT = """\
You are an expert business process simulation consultant.
Your task is to transform business process goals into actionable simulation parameters.

You have access to the following knowledge:
- Business process simulation parameters include: resource count, activity durations,
  inter-arrival times, gateway probabilities, resource calendars, and resource costs.
- Common improvement strategies include: adding resources, reducing activity durations,
  extending working hours, rebalancing workloads, and adjusting routing probabilities.

Given a business process description and improvement goal, identify the specific
simulation parameters that should be changed and explain how to change them.
Be concrete: specify which activities, resources, or gateways to modify and by how much.
"""


def call_base_repo_style(provider, merged_input: str) -> str:
    """Step 1: Replicate the base repo's approach — goal text → natural language suggestions."""
    raw = provider.generate(
        system_prompt=BASE_REPO_SYSTEM_PROMPT,
        user_prompt=merged_input,
        temperature=0.7,
        json_mode=False,
    )
    return raw


# -----------------------------------------------------------------------
# Step 2 — Translate natural language suggestions → ScenarioPatch JSON
# -----------------------------------------------------------------------

TRANSLATION_SYSTEM_PROMPT = """\
You are a business process simulation expert translating natural language parameter
suggestions into a structured JSON patch for a Prosimos discrete-event simulation.

You will receive:
1. Natural language suggestions describing what parameters to change
2. A SIMOD baseline JSON describing the current process model (activities, resources, etc.)
3. A BPMN activity name map (node IDs → human-readable names)
4. The target KPIs that the patch should improve

Your output must be a valid ScenarioPatch JSON following this schema exactly:

""" + SCENARIO_PATCH_JSON_SCHEMA + """

Rules:
- target_element must be an EXACT activity name or role id from the baseline
- For resource_count modifications, target_element is the role profile id (e.g. "User_1_profile")
- For activity_duration, target_element is the human-readable activity name (e.g. "W_Validate application")
- baseline_value must be quoted from the actual baseline values provided
- proposed_value must be a concrete value (not a description)
- Only include modifications that are grounded in the baseline data
- If a suggestion cannot be grounded, put it in unresolved_kpis
"""


def translate_to_patch(
    provider,
    nl_suggestions: str,
    simod_data: dict,
    kpi_json: dict,
    log_name: str,
) -> ScenarioPatch | None:
    """Step 2: Translate natural language suggestions to a ScenarioPatch."""

    # Build activity name map from BPMN
    import re
    bpmn = simod_data.get("bpmn_xml", "")
    name_map = dict(re.findall(r'id="(node_[^"]+)"[^>]*name="([^"]+)"', bpmn))

    # Summarise baseline resources (first 20 profiles)
    content = simod_data.get("json_content", {})
    profiles = content.get("resource_profiles", [])[:20]
    profile_summary = [
        {"id": p["id"], "resource_count": len(p.get("resource_list", []))}
        for p in profiles
    ]

    # Summarise key activity durations
    tasks = content.get("task_resource_distribution", [])
    activity_summary = []
    for t in tasks[:15]:
        tid = t.get("task_id", "")
        name = name_map.get(tid, tid)
        res = t.get("resources", [])
        if res:
            params = res[0].get("distribution_params", [])
            mean_val = params[0].get("value") if params else None
            mean_hours = round(mean_val / 3600, 2) if mean_val else None
            activity_summary.append({
                "id": tid,
                "name": name,
                "resource": res[0].get("resource_id"),
                "mean_hours": mean_hours,
            })

    kpis_summary = [
        {"name": k["name"], "target_direction": k["target_direction"]}
        for k in kpi_json.get("kpis", [])
    ]

    user_prompt = f"""## Natural Language Suggestions (from base system):
{nl_suggestions}

## BPMN Activity Name Map (node_id → name):
{json.dumps(name_map, indent=2)[:3000]}

## Baseline Resource Profiles (first 20):
{json.dumps(profile_summary, indent=2)}

## Baseline Activity Durations (first 15, in hours):
{json.dumps(activity_summary, indent=2)}

## KPI Targets to improve:
{json.dumps(kpis_summary, indent=2)}

## Task:
Translate the natural language suggestions into a ScenarioPatch JSON.
Use EXACT element names/IDs from the baseline data above.
Output ONLY the JSON object, no markdown fences.
"""

    for attempt in range(3):
        try:
            raw = provider.generate(
                system_prompt=TRANSLATION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.3,
                json_mode=True,
            )
            from utils.parsing import extract_json_object, strip_code_fences
            cleaned = strip_code_fences(raw)
            parsed = json.loads(cleaned)
            patch = ScenarioPatch.model_validate(parsed)
            return patch
        except Exception as exc:
            print(f"    Translation attempt {attempt+1} failed: {exc}")
            if attempt < 2:
                user_prompt += f"\n\nPrevious attempt failed: {exc}\nPlease fix and retry."
    return None


# -----------------------------------------------------------------------
# Result record
# -----------------------------------------------------------------------

@dataclass
class BaselineResult:
    log:             str
    schema_valid:    bool  = False
    dhr_mean:        float = 0.0
    dhr_ci_half:     float = 0.0
    nis_mean:        float = 0.0
    nis_ci_half:     float = 0.0
    best_iter_score: float = 0.0
    n_kpis:          int   = 0
    n_kpis_hit:      int   = 0
    nl_suggestions:  str   = ""
    error:           str   = ""


# -----------------------------------------------------------------------
# Metrics helper (same as ablation)
# -----------------------------------------------------------------------

def _compute_dhr_nis(multi_result) -> tuple[float, float, float, float, int, int]:
    if multi_result is None:
        return 0.0, 0.0, 0.0, 0.0, 0, 0
    import math, statistics
    hits, nis_scores = [], []
    for comp in multi_result.kpi_comparisons:
        if comp.improved is None or comp.mean_percentage_change is None:
            continue
        hits.append(1.0 if comp.improved else 0.0)
        sign = 1.0 if comp.improved else -1.0
        nis_scores.append(abs(comp.mean_percentage_change) * sign)
    n = len(hits)
    n_hit = int(sum(hits))
    dhr = sum(hits) / n if n else 0.0
    nis = sum(nis_scores) / len(nis_scores) if nis_scores else 0.0
    dhr_ci = 1.96 * statistics.stdev(hits) / math.sqrt(n) if n > 1 else 0.0
    nis_ci = 1.96 * statistics.stdev(nis_scores) / math.sqrt(len(nis_scores)) if len(nis_scores) > 1 else 0.0
    return dhr, dhr_ci, nis, nis_ci, n, n_hit


# -----------------------------------------------------------------------
# Prospect-theory score (same as ablation, λ=2.25)
# -----------------------------------------------------------------------

def _prospect_score(multi_result) -> float:
    if multi_result is None or not multi_result.ok:
        return -100.0
    LAMBDA, CAP = 2.25, 50.0
    total = 0.0
    for comp in multi_result.kpi_comparisons:
        if comp.mean_percentage_change is None:
            continue
        pct = min(abs(comp.mean_percentage_change), CAP)
        if comp.improved is True:
            total += pct
        elif comp.improved is False:
            total -= LAMBDA * pct
    return round(total, 3)


# -----------------------------------------------------------------------
# Per-log runner
# -----------------------------------------------------------------------

def run_one(scenario: dict, provider) -> BaselineResult:
    log_name = scenario["log"]
    rec = BaselineResult(log=log_name)

    simod_raw_path = SIMOD_DIR / log_name / "simod_raw.json"
    if not simod_raw_path.exists():
        rec.error = f"simod_raw.json not found for {log_name}"
        return rec

    simod_data = json.loads(simod_raw_path.read_text())
    kpi_json   = json.loads(Path(scenario["kpi_json"]).read_text())

    # Build baseline scenario
    baseline_result = build_baseline_scenario(
        simod_data.get("json_content"),
        bpmn_xml=simod_data.get("bpmn_xml", ""),
    )
    if not baseline_result.scenario:
        rec.error = f"build_baseline_scenario failed: {baseline_result.errors}"
        return rec
    baseline_scenario = baseline_result.scenario

    # Build KPI targets
    kpi_result = KPIGenerationResult.model_validate(kpi_json)
    targets = [
        KPITarget(
            name=k.name,
            direction=TargetDirection(k.target_direction.value),
            category=k.category.value if hasattr(k.category, "value") else str(k.category),
            measurable_as=k.measurable_as,
        )
        for k in kpi_result.kpis
    ]

    # Step 1: Base repo style call
    print(f"  [step 1] Calling base repo style prompt...")
    nl_suggestions = call_base_repo_style(provider, scenario["merged_input"])
    rec.nl_suggestions = nl_suggestions[:500]
    print(f"  [step 1] Got {len(nl_suggestions)} chars of suggestions")

    # Step 2: Translate to patch
    print(f"  [step 2] Translating to ScenarioPatch...")
    patch = translate_to_patch(provider, nl_suggestions, simod_data, kpi_json, log_name)
    if patch is None:
        rec.error = "Translation to ScenarioPatch failed after 3 attempts"
        return rec
    print(f"  [step 2] Patch has {len(patch.modifications)} modifications")
    rec.schema_valid = True

    # Save patch
    patches_dir = RESULTS_DIR / "patches"
    patches_dir.mkdir(exist_ok=True)
    (patches_dir / f"baseline_{log_name}.json").write_text(patch.model_dump_json(indent=2))

    # Also save NL suggestions
    (patches_dir / f"baseline_{log_name}_nl.txt").write_text(nl_suggestions)

    # Step 3: Apply patch to baseline
    import re
    bpmn = simod_data.get("bpmn_xml", "")
    from second_llm.simod_to_simubridge import build_flow_name_map
    flow_name_map = build_flow_name_map(bpmn)

    merge_result = apply_patch(baseline_scenario, patch, strict=False, element_name_map=flow_name_map)
    if merge_result.scenario is None:
        rec.error = f"Patch merge failed: {merge_result.error_messages}"
        return rec

    applied = len(merge_result.applied_modifications)
    total   = len(patch.modifications)
    print(f"  [step 3] Applied {applied}/{total} modifications")

    proposed_scenario = merge_result.scenario

    # Step 4: Multi-seed evaluation
    print(f"  [step 4] Running {NUM_SEEDS} seeds...")
    multi_result = evaluate_multi_seed(
        baseline_scenario=baseline_scenario,
        proposed_scenario=proposed_scenario,
        bpmn_xml=bpmn,
        targets=targets,
        num_seeds=NUM_SEEDS,
        total_cases=TOTAL_CASES,
        on_progress=lambda idx, total, msg: print(f"  [multi-seed] {msg}", flush=True),
    )

    if not multi_result.ok:
        rec.error = multi_result.error or "multi-seed evaluation failed"
        return rec

    dhr, dhr_ci, nis, nis_ci, n, n_hit = _compute_dhr_nis(multi_result)
    rec.dhr_mean    = round(dhr, 4)
    rec.dhr_ci_half = round(dhr_ci, 4)
    rec.nis_mean    = round(nis, 2)
    rec.nis_ci_half = round(nis_ci, 2)
    rec.best_iter_score = _prospect_score(multi_result)
    rec.n_kpis      = n
    rec.n_kpis_hit  = n_hit
    return rec


# -----------------------------------------------------------------------
# CSV helpers
# -----------------------------------------------------------------------

def _load_completed() -> set[str]:
    out = RESULTS_DIR / "stage2_results.csv"
    if not out.exists():
        return set()
    completed = set()
    with open(out, newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("error", "").strip():
                completed.add(row["log"])
    return completed


def _write_csv(results: list[BaselineResult]) -> None:
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
    provider  = make_provider()
    completed = _load_completed()
    if completed:
        print(f"Skipping already completed: {sorted(completed)}")

    results: list[BaselineResult] = []

    for scenario in SCENARIOS:
        log_name = scenario["log"]
        print(f"\n{'='*60}")
        print(f"Baseline comparison: {log_name}")
        print('='*60)

        if log_name in completed:
            print(f"  SKIPPED (already succeeded)")
            continue

        t0 = time.time()
        try:
            rec = run_one(scenario, provider)
        except Exception as exc:
            rec = BaselineResult(log=log_name, error=traceback.format_exc(limit=3))
            print(f"  EXCEPTION: {exc}")

        elapsed = time.time() - t0
        print(
            f"  DHR={rec.dhr_mean:.1%}  NIS={rec.nis_mean:+.1f}%  "
            f"Score={rec.best_iter_score:+.1f}  valid={rec.schema_valid}  "
            f"elapsed={elapsed:.0f}s"
        )
        if rec.error:
            print(f"  ERROR: {rec.error}")

        results.append(rec)
        _write_csv(results)

    print(f"\nDone. Results in {RESULTS_DIR / 'stage2_results.csv'}")
    print(f"NL suggestions saved to {RESULTS_DIR / 'patches/'}")


if __name__ == "__main__":
    main()

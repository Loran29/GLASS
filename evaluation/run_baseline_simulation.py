"""
Simulate baseline patch from muruvetg/from-simulation-goals-to-parameters.

Loads the hand-crafted patch derived from the base repo's output,
applies it to the SIMOD baseline, and evaluates with the same
multi-seed harness used in Stage 2 ablation.

Usage (from Thesis/goal_to_parameters/):
    python ../evaluation/run_baseline_simulation.py

Results written to evaluation/stage2_results_baseline/
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
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "goal_to_parameters"))

from second_llm.multi_seed_evaluation import evaluate_multi_seed
from second_llm.simod_to_simubridge import build_baseline_scenario, build_flow_name_map
from second_llm.scenario_evaluation import KPITarget, TargetDirection
from second_llm.scenario_merger import apply_patch
from second_llm.output_schema_patch import ScenarioPatch
from models import KPIGenerationResult

SIMOD_DIR   = REPO_ROOT / "evaluation" / "simod_outputs"
KPI_DIR     = REPO_ROOT / "evaluation" / "stage1_kpis"
RESULTS_DIR = REPO_ROOT / "evaluation" / "stage2_results_baseline"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

NUM_SEEDS   = 10
TOTAL_CASES = 1000


@dataclass
class BaselineResult:
    log:             str
    schema_valid:    bool  = False
    n_applied:       int   = 0
    n_total_mods:    int   = 0
    dhr_mean:        float = 0.0
    dhr_ci_half:     float = 0.0
    nis_mean:        float = 0.0
    nis_ci_half:     float = 0.0
    best_iter_score: float = 0.0
    n_kpis:          int   = 0
    n_kpis_hit:      int   = 0
    error:           str   = ""


def _compute_metrics(multi_result) -> tuple[float, float, float, float, float, int, int]:
    import math, statistics
    hits, nis_scores = [], []
    for comp in multi_result.kpi_comparisons:
        if comp.improved is None or comp.mean_percentage_change is None:
            continue
        hits.append(1.0 if comp.improved else 0.0)
        sign = 1.0 if comp.improved else -1.0
        nis_scores.append(abs(comp.mean_percentage_change) * sign)

    n     = len(hits)
    n_hit = int(sum(hits))
    dhr   = sum(hits) / n if n else 0.0
    nis   = sum(nis_scores) / len(nis_scores) if nis_scores else 0.0
    dhr_ci = 1.96 * statistics.stdev(hits) / math.sqrt(n) if n > 1 else 0.0
    nis_ci = 1.96 * statistics.stdev(nis_scores) / math.sqrt(len(nis_scores)) if len(nis_scores) > 1 else 0.0

    # Prospect-theory score (lambda=2.25, cap=50)
    LAMBDA, CAP = 2.25, 50.0
    score = 0.0
    for comp in multi_result.kpi_comparisons:
        if comp.mean_percentage_change is None:
            continue
        pct = min(abs(comp.mean_percentage_change), CAP)
        if comp.improved is True:
            score += pct
        elif comp.improved is False:
            score -= LAMBDA * pct

    return dhr, dhr_ci, nis, nis_ci, round(score, 3), n, n_hit


RUNS = [
    {
        "log":        "bpic2017",
        "patch_file": REPO_ROOT / "evaluation" / "baseline_patch_bpic2017.json",
        "kpi_json":   KPI_DIR / "bpic2017.json",
    },
    {
        "log":        "bpic2012",
        "patch_file": REPO_ROOT / "evaluation" / "baseline_patch_bpic2012.json",
        "kpi_json":   KPI_DIR / "bpic2012.json",
    },
    {
        "log":        "sepsis",
        "patch_file": REPO_ROOT / "evaluation" / "baseline_patch_sepsis.json",
        "kpi_json":   KPI_DIR / "sepsis.json",
    },
]


def run_one(run: dict) -> BaselineResult:
    log_name   = run["log"]
    patch_path = run["patch_file"]
    kpi_path   = run["kpi_json"]
    rec = BaselineResult(log=log_name)

    # Load SIMOD
    simod_raw = json.loads((SIMOD_DIR / log_name / "simod_raw.json").read_text())
    bpmn_xml  = simod_raw.get("bpmn_xml", "")

    # Build baseline
    baseline_result = build_baseline_scenario(simod_raw.get("json_content"), bpmn_xml=bpmn_xml)
    if not baseline_result.scenario:
        rec.error = f"build_baseline_scenario failed: {baseline_result.errors}"
        return rec
    baseline_scenario = baseline_result.scenario

    # Load KPI targets
    kpi_json   = json.loads(kpi_path.read_text())
    kpi_result = KPIGenerationResult.model_validate(kpi_json)
    targets    = [
        KPITarget(
            name=k.name,
            direction=TargetDirection(k.target_direction.value),
            category=k.category.value if hasattr(k.category, "value") else str(k.category),
            measurable_as=k.measurable_as,
        )
        for k in kpi_result.kpis
    ]

    # Load and validate patch
    patch_data = json.loads(patch_path.read_text())
    patch      = ScenarioPatch.model_validate(patch_data)
    rec.schema_valid   = True
    rec.n_total_mods   = len(patch.modifications)

    # Apply patch
    flow_name_map = build_flow_name_map(bpmn_xml)
    merge_result  = apply_patch(baseline_scenario, patch, strict=False, element_name_map=flow_name_map)

    if merge_result.scenario is None:
        rec.error = f"Merge failed: {merge_result.error_messages}"
        return rec

    rec.n_applied = len(merge_result.applied_modifications)
    print(f"  Applied {rec.n_applied}/{rec.n_total_mods} modifications")
    for w in merge_result.warning_messages:
        print(f"  [merge warning] {w}")

    proposed_scenario = merge_result.scenario

    # Multi-seed evaluation
    print(f"  Running {NUM_SEEDS} seeds x {TOTAL_CASES} cases...")
    multi_result = evaluate_multi_seed(
        baseline_scenario=baseline_scenario,
        proposed_scenario=proposed_scenario,
        bpmn_xml=bpmn_xml,
        targets=targets,
        num_seeds=NUM_SEEDS,
        total_cases=TOTAL_CASES,
        on_progress=lambda idx, total, msg: print(f"  [seed] {msg}", flush=True),
    )

    if not multi_result.ok:
        rec.error = multi_result.error or "multi-seed evaluation failed"
        return rec

    dhr, dhr_ci, nis, nis_ci, score, n, n_hit = _compute_metrics(multi_result)
    rec.dhr_mean        = round(dhr, 4)
    rec.dhr_ci_half     = round(dhr_ci, 4)
    rec.nis_mean        = round(nis, 2)
    rec.nis_ci_half     = round(nis_ci, 2)
    rec.best_iter_score = score
    rec.n_kpis          = n
    rec.n_kpis_hit      = n_hit

    print(f"  DHR={rec.dhr_mean:.1%}  NIS={rec.nis_mean:+.1f}%  Score={rec.best_iter_score:+.1f}")

    # Print per-KPI breakdown
    print("  Per-KPI results:")
    for comp in multi_result.kpi_comparisons:
        symbol = "+" if comp.improved else "-"
        pct = f"{comp.mean_percentage_change:+.1f}%" if comp.mean_percentage_change is not None else "n/a"
        print(f"    [{symbol}] {comp.kpi_name}: {pct} (improved={comp.improved})")

    return rec


def _write_csv(results: list[BaselineResult]) -> None:
    out  = RESULTS_DIR / "stage2_results.csv"
    rows = [asdict(r) for r in results]
    if not rows:
        return
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    results = []
    for run in RUNS:
        log_name = run["log"]
        print(f"\n{'='*60}")
        print(f"Baseline simulation: {log_name}")
        print('='*60)
        try:
            rec = run_one(run)
        except Exception as exc:
            import traceback
            rec = BaselineResult(log=log_name, error=traceback.format_exc(limit=3))
            print(f"  EXCEPTION: {exc}")

        if rec.error:
            print(f"  ERROR: {rec.error}")

        results.append(rec)

    _write_csv(results)
    print(f"\nDone. Results in {RESULTS_DIR / 'stage2_results.csv'}")


if __name__ == "__main__":
    main()

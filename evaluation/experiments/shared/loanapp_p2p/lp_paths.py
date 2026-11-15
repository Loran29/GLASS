"""Paths of the LoanApp / Procure2Pay experiments in evaluation/experiments/.

Importing this module puts every experiment's scripts/ folder on sys.path, so the scripts can import each other.
Setup files and run records give paths relative to the former evaluation/loanapp_p2p/ (or to the repository root
at the time of the run); lp() and stored() map them to their current place in this release.
"""

import sys
from pathlib import Path

EXPERIMENTS = Path(__file__).resolve().parents[2]
REPO = EXPERIMENTS.parents[1]
SHARED = EXPERIMENTS / "shared" / "loanapp_p2p"
EXP1 = EXPERIMENTS / "experiment_1_expected_kpi_generation"
EXP2 = EXPERIMENTS / "experiment_2_valid_improving_kpis"
EXP3 = EXPERIMENTS / "experiment_3_glass_vs_process_improvement_copilot"
EXP4 = EXPERIMENTS / "Experiment_3_real_life_ablation"                  # formerly experiment_4_real_life_ablation
EXP5 = EXPERIMENTS / "Refinement_replications_Sensitvity_Analysis"      # formerly experiment_5_loop_replications
SYNTH_ABLATION = EXPERIMENTS / "Experiment_3_synthetic_ablation"
RAG = EXPERIMENTS / "Experiment 4 Adding RAG to Stage 2 (not reported in the paper)"

for _d in (SHARED / "scripts", EXP1 / "scripts", EXP2 / "scripts", EXP4 / "scripts", EXP5 / "scripts",
           SYNTH_ABLATION / "scripts"):
    if str(_d) not in sys.path:
        sys.path.append(str(_d))

# Result folders under the former results/ -> their place in this release.
_RESULTS = {
    "stage1_results": EXP1 / "results" / "stage1_results_intial",
    "stage1_results_intial": EXP1 / "results" / "stage1_results_intial",
    "refinement_pilot": EXP1 / "results" / "refinement_pilot",
    "stage2_results_v32": EXP2 / "results" / "stage2_results_v32",
    "stage2_loop_seeds": EXP5 / "results",
    "stage2_synthetic_ablation": SYNTH_ABLATION / "results" / "stage2",
}
# The v3.2 knowledge-base comparison was split: V_no_kb is Experiment 2's main result, V_full is Experiment 4.
_V32_RAG = {
    "V_no_kb": EXP2 / "results" / "stage2_results_v32" / "V_no_kb",
    "V_full": RAG / "V_full_with RAG",
}


def _results(parts: tuple) -> Path:
    """parts = (results folder name, ...) -> path in this release; a clear error for folders left out."""
    name, rest = parts[0], parts[1:]
    if name in _RESULTS:
        return _RESULTS[name].joinpath(*rest)
    if name == "stage2_results_v32_rag" and rest and rest[0] in _V32_RAG:
        return _V32_RAG[rest[0]].joinpath(*rest[1:])
    raise FileNotFoundError(
        f"results/{'/'.join(parts)}: the result folder {name!r} is not part of this release "
        f"(kept: {', '.join(sorted(_RESULTS))}, stage2_results_v32_rag/V_no_kb, stage2_results_v32_rag/V_full)")


def lp(rel) -> Path:
    """A path given relative to the former evaluation/loanapp_p2p/."""
    parts = Path(rel).parts
    if len(parts) > 1 and parts[0] == "results":
        return _results(parts[1:])
    return SHARED.joinpath(*parts)


def stored(path) -> Path:
    """A path stored in a run record, relative to the repository root at the time of the run."""
    p = Path(path)
    if (REPO / p).exists():
        return REPO / p
    if p.parts[:2] == ("evaluation", "loanapp_p2p"):
        return lp(Path(*p.parts[2:]))
    if p.parts[:1] == ("evaluation",) and len(p.parts) > 2:   # Stage 1 folders once directly under evaluation/
        return _results(p.parts[1:])
    return REPO / p

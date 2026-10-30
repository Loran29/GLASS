"""Paths of the LoanApp / Procure2Pay experiments in evaluation/experiments/.

Importing this module puts every experiment's scripts/ folder on sys.path, so the scripts can import each other.
Setup files and run records give paths relative to the former evaluation/loanapp_p2p/; lp() and stored() map them
to their current place.
"""

import sys
from pathlib import Path

EXPERIMENTS = Path(__file__).resolve().parents[2]
REPO = EXPERIMENTS.parents[1]
SHARED = EXPERIMENTS / "shared" / "loanapp_p2p"
EXP1 = EXPERIMENTS / "experiment_1_expected_kpi_generation"
EXP2 = EXPERIMENTS / "experiment_2_valid_improving_kpis"
EXP3 = EXPERIMENTS / "experiment_3_glass_vs_process_improvement_copilot"
EXP4 = EXPERIMENTS / "experiment_4_real_life_ablation"
EXP5 = EXPERIMENTS / "experiment_5_rag_vs_no_rag"
OLDER = REPO / "evaluation" / "_to_review" / "older_glass_versions"

for _d in (SHARED / "scripts", EXP1 / "scripts", EXP2 / "scripts", EXP4 / "scripts", EXP5 / "scripts"):
    if str(_d) not in sys.path:
        sys.path.append(str(_d))

_STAGE1 = {"stage1_results", "stage1_results_normalised", "refinement_pilot"}
_STAGE2 = {"stage2_results_v32_rag", "VERSIONS.md"}


def lp(rel) -> Path:
    """A path given relative to the former evaluation/loanapp_p2p/."""
    parts = Path(rel).parts
    if len(parts) > 1 and parts[0] == "results":
        if parts[1] in _STAGE1:
            return EXP1.joinpath("results", *parts[1:])
        if parts[1] in _STAGE2:
            return EXP2.joinpath("results", *parts[1:])
        return OLDER.joinpath(*parts[1:])
    return SHARED.joinpath(*parts)


def stored(path) -> Path:
    """A path stored in a run record, relative to the repository root at the time of the run."""
    p = Path(path)
    if (REPO / p).exists():
        return REPO / p
    if p.parts[:2] == ("evaluation", "loanapp_p2p"):
        return lp(Path(*p.parts[2:]))
    if p.parts[:1] == ("evaluation",) and len(p.parts) > 2:
        return EXP1.joinpath("results", *p.parts[1:])
    return REPO / p

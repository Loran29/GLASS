"""Iterative evaluation loop — auto-refine scenarios via simulation feedback.

Runs a generate→simulate→evaluate→feedback cycle until the proposed scenario
meets every target — a KPI with a numeric threshold has *achieved* it, a KPI
without one has *improved* — or the max iteration count is reached.
The best result across all iterations is returned. When no iteration improves on the
baseline (no positive score), the baseline is kept: the result says so
(``no_improving_scenario``) and carries the best iteration for inspection only.

Iteration selection: most achieved targets first, then most improved KPIs, then a
goal-programming scoring function with prospect-theoretic loss aversion
(Kahneman & Tversky, 1979; λ=2.25).

``legacy_refinement=True`` restores the earlier rules for comparison (ablation): stop at the
first iteration whose overall status is ``improved``, and select the best iteration by score
alone. Off by default.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from second_llm.kpi_computation import KPIComputationResult, compute_kpis
from second_llm.output_schema import SimuBridgeScenario
from second_llm.prosimos_runner import ProsimosResult, run_prosimos_simulation, scenario_to_prosimos_json
from second_llm.scenario_evaluation import (
    KPIComparisonEntry,
    KPITarget,
    OverallStatus,
    ScenarioEvaluationResult,
    compare_kpis,
    targets_met,
)
from second_llm.context_summary import _same_role
from second_llm.scenario_generator import ScenarioGenerationResult

logger = logging.getLogger(__name__)


@dataclass
class IterationResult:
    """Result of a single generate→simulate→evaluate iteration."""

    iteration: int
    gen_result: ScenarioGenerationResult | None = None
    eval_result: ScenarioEvaluationResult | None = None
    feedback_message: str = ""
    status: OverallStatus | None = None
    error: str | None = None

    @property
    def score(self) -> float:
        """Goal-programming score with prospect-theoretic loss aversion.

        Uses normalized achievement ratios (percentage change toward target)
        weighted asymmetrically: improvements are valued linearly, while
        degradations are penalized at lambda=2.25 (Kahneman & Tversky, 1979).
        Safeguard violations receive an additional penalty factor of 3.0.
        Percentage changes are capped at 50% to prevent outlier domination.
        """
        if self.eval_result is None or not self.eval_result.ok:
            return -100.0

        LAMBDA = 2.25  # Loss aversion coefficient (Prospect Theory)
        SAFEGUARD_PENALTY = 3.0
        CAP = 50.0  # Cap percentage to prevent outlier domination

        total = 0.0
        for e in self.eval_result.kpi_comparisons:
            if e.percentage_change is None:
                continue

            pct = min(abs(e.percentage_change), CAP)

            if e.violated_safeguard:
                total -= SAFEGUARD_PENALTY * pct
            elif e.improved is True:
                total += pct
            elif e.improved is False:
                total -= LAMBDA * pct

        return total

    @property
    def targets_met(self) -> bool:
        """Every target KPI achieved (with a threshold) or improved (without one)."""
        return self.eval_result is not None and self.eval_result.ok and targets_met(self.eval_result.kpi_comparisons)

    @property
    def achieved_count(self) -> int:
        """Number of target KPIs (not safeguards) that achieved their threshold."""
        if self.eval_result is None or not self.eval_result.ok:
            return -1
        return sum(e.achieved is True for e in self.eval_result.kpi_comparisons if not e.is_safeguard)

    @property
    def improved_count(self) -> int:
        """Number of target KPIs (not safeguards) that improved."""
        if self.eval_result is None or not self.eval_result.ok:
            return -1
        return sum(e.improved is True for e in self.eval_result.kpi_comparisons if not e.is_safeguard)


@dataclass
class IterativeEvaluationResult:
    """Complete result of the iterative optimization loop."""

    iterations: list[IterationResult] = field(default_factory=list)
    best_iteration_idx: int | None = None
    final_status: OverallStatus | None = None
    total_time_seconds: float = 0.0
    baseline_kpis: KPIComputationResult | None = None
    error: str | None = None
    # No iteration improved on the baseline (no positive score): the baseline is kept,
    # best_iteration_idx is None and the best iteration is attached for inspection only.
    no_improving_scenario: bool = False
    best_candidate_idx: int | None = None

    @property
    def outcome(self) -> str:
        """"no_improving_scenario" when the baseline is kept, else "scenario" (or "error")."""
        if self.error:
            return "error"
        return "no_improving_scenario" if self.no_improving_scenario else "scenario"

    @property
    def best_candidate(self) -> IterationResult | None:
        if self.best_candidate_idx is not None and self.best_candidate_idx < len(self.iterations):
            return self.iterations[self.best_candidate_idx]
        return None

    @property
    def best(self) -> IterationResult | None:
        if self.best_iteration_idx is not None and self.best_iteration_idx < len(self.iterations):
            return self.iterations[self.best_iteration_idx]
        return None

    @property
    def improved(self) -> bool:
        return self.final_status == OverallStatus.IMPROVED

    @property
    def targets_met(self) -> bool:
        return self.best is not None and self.best.targets_met

    @property
    def total_iterations(self) -> int:
        return len(self.iterations)


def _fmt(value: float | None) -> str:
    return "?" if value is None else f"{value:.4g}"


def _target_text(e: KPIComparisonEntry) -> str:
    """Per-KPI target status for a KPI with a threshold ("" without one)."""
    if getattr(e, "threshold", None) is None:
        return ""
    if e.achieved is True:
        return "target achieved"
    if e.improved is True:
        return f"improved but target not yet reached (current {_fmt(e.proposed_value)}, target {_fmt(e.threshold)})"
    return f"target not reached (current {_fmt(e.proposed_value)}, target {_fmt(e.threshold)})"


def _int(text: Any) -> int | None:
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(text or ""))
    return int(round(float(m.group()))) if m else None


def _refers_to(kpi_ref: str, kpi_name: str) -> bool:
    a, b = (kpi_ref or "").lower(), (kpi_name or "").lower()
    return bool(a) and (a in b or b in a)


def _gap_lines(e: KPIComparisonEntry, gen_result: ScenarioGenerationResult | None) -> list[str]:
    """For a KPI with a threshold that improved but has not reached it: the remaining gap and the
    allowed range of the levers targeting it, taken from the operational-constraint summary that
    is also rendered into the prompt. Empty for any other KPI."""
    if getattr(e, "threshold", None) is None or e.achieved is not False or e.improved is not True \
            or e.proposed_value is None:
        return []
    cur, target = e.proposed_value, e.threshold
    gap = cur - target if e.target_direction == "minimize" else target - cur
    rel = f" ({abs(gap) / abs(cur):.1%} of the current value)" if cur else ""
    lines = [f"Remaining gap: {_fmt(abs(gap))}{rel}; a larger change than in this proposal is needed to "
             f"reach the target."]

    summary = getattr(gen_result, "context_summary", None) if gen_result else None
    proposal = getattr(gen_result, "proposal", None) if gen_result else None
    mods = list(getattr(proposal, "modifications", None) or [])
    levers = [m for m in mods if _refers_to(getattr(m, "kpi_reference", ""), e.kpi_name)]
    if summary is None or getattr(summary, "is_empty", True):
        if not levers:
            lines.append("No modification in the current proposal targets this KPI.")
        return lines

    def overtime(role: str) -> float | None:
        for name, info in summary.resource_constraints.items():
            if _same_role(name, role) and isinstance(info, dict):
                val = info.get("max_overtime_hours_per_day")
                if val is not None:
                    return float(val)
        return None

    added_total = 0
    for m in mods:
        if getattr(m, "parameter_type", "") == "resource_count":
            n0, n1 = _int(m.baseline_value), _int(m.proposed_value)
            if n0 is not None and n1 is not None and n1 > n0:
                added_total += n1 - n0
    capacity = False
    for m in levers:
        ptype, role = getattr(m, "parameter_type", ""), getattr(m, "target_element", "")
        if ptype == "resource_count":
            capacity = True
            n0, n1 = _int(m.baseline_value), _int(m.proposed_value)
            if n0 is None or n1 is None:
                continue
            if summary.is_role_fixed(role):
                lines.append(f"Allowed range for '{role}': its staffing is fixed by the operational constraints.")
                continue
            ceiling = summary.get_effective_max_headcount(role, current_count=n0)
            if ceiling is None:
                lines.append(f"Allowed range for '{role}': no staffing limit was stated; the current proposal "
                             f"changes its headcount from {n0} to {n1}.")
            else:
                lines.append(f"Allowed range for '{role}' (operational constraints): staffing may be increased by up "
                             f"to +{max(0, ceiling - n0)} (to {ceiling}); the current proposal adds {n1 - n0:+d}, so "
                             f"{max(0, ceiling - n1)} more addition(s) remain for this role.")
        elif ptype == "resource_calendar":
            hours = overtime(role)
            if hours is not None:
                lines.append(f"Allowed range for '{role}' (operational constraints): working time may be extended "
                             f"by up to {hours:g} h per day.")
    if capacity:
        cap = summary.get_global_max_additional()
        if cap is not None:
            lines.append(f"Across all roles at most +{cap} additional resources are allowed; the current proposal "
                         f"adds +{added_total}, so {max(0, cap - added_total)} more remain.")
        pct = (summary.budget or {}).get("max_increase_pct")
        if pct is not None:
            lines.append(f"Total staff cost may increase by at most {float(pct):g} %.")
    if not levers:
        lines.append("No modification in the current proposal targets this KPI.")
        limits = [f"'{name}' +{info['max_additional']}" for name, info in summary.resource_constraints.items()
                  if isinstance(info, dict) and info.get("max_additional") is not None]
        if limits:
            lines.append("Staffing limits from the operational constraints: " + ", ".join(limits) + ".")
    return lines


def _build_evaluation_feedback(
    eval_result: ScenarioEvaluationResult,
    gen_result: ScenarioGenerationResult,
    iteration: int,
    max_iterations: int,
) -> str:
    """Build a structured feedback message enriched with causal attribution and KB evidence.

    Includes:
      1. Quantitative simulation results per KPI (and, for a KPI with a threshold,
         whether the target is achieved or only improved)
      2. Causal attribution: maps each worsened KPI to the specific modification that targeted it
      3. KB-retrieved alternative interventions for worsened KPIs
      4. Strategy guidance grounded in literature
    """
    lines: list[str] = []
    lines.append(f"=== SIMULATION FEEDBACK (Attempt {iteration}/{max_iterations}) ===")
    lines.append("")
    lines.append("Your proposed scenario was simulated using Prosimos (discrete-event simulation).")
    lines.append(f"Overall Result: {eval_result.summary.overall_status.value.upper()}")
    with_threshold = [e for e in eval_result.kpi_comparisons
                      if getattr(e, "threshold", None) is not None and not e.is_safeguard]
    if with_threshold:
        reached = sum(e.achieved is True for e in with_threshold)
        lines.append(f"Targets reached: {reached} of {len(with_threshold)}")
    lines.append("")

    # --- Section 1: Quantitative KPI Results ---
    lines.append("## Quantitative KPI Results (Baseline → Your Proposal):")

    improved_kpis: list[str] = []
    worsened_kpis: list[tuple[str, str, str]] = []  # (name, pct, direction)
    violated_kpis: list[tuple[str, str, str]] = []
    not_reached: list[KPIComparisonEntry] = []  # improved, threshold not yet reached
    achieved: list[str] = []

    for e in eval_result.kpi_comparisons:
        if e.status != "computed":
            lines.append(f"  - {e.kpi_name}: NOT COMPUTABLE")
            continue

        baseline_str = f"{e.baseline_value:.2f}" if e.baseline_value is not None else "?"
        proposed_str = f"{e.proposed_value:.2f}" if e.proposed_value is not None else "?"
        pct_str = f"{e.percentage_change:+.1f}%" if e.percentage_change is not None else ""
        unit_str = f" {e.unit}" if e.unit else ""

        if e.violated_safeguard:
            marker = "VIOLATED SAFEGUARD"
            violated_kpis.append((e.kpi_name, pct_str, e.target_direction))
        elif e.improved is True:
            marker = "IMPROVED"
            improved_kpis.append(e.kpi_name)
        elif e.improved is False:
            marker = "WORSENED"
            worsened_kpis.append((e.kpi_name, pct_str, e.target_direction))
        else:
            marker = "UNCHANGED"

        target = _target_text(e)
        if getattr(e, "threshold", None) is not None and not e.is_safeguard:
            if e.achieved is True:
                achieved.append(e.kpi_name)
            elif e.improved is True:
                not_reached.append(e)
        lines.append(
            f"  - {e.kpi_name}: {baseline_str} → {proposed_str}{unit_str} ({pct_str}) — {marker}"
            + (f"; {target}" if target else "")
        )

    # --- Section 2: Causal Attribution (map modifications → KPI effects) ---
    proposal = gen_result.proposal if gen_result else None
    if proposal and proposal.modifications and (worsened_kpis or violated_kpis):
        lines.append("")
        lines.append("## Causal Attribution — Your Modifications and Their Effects:")
        lines.append("")

        problem_kpi_names = {name for name, _, _ in worsened_kpis} | {name for name, _, _ in violated_kpis}

        for mod in proposal.modifications:
            kpi_ref = getattr(mod, "kpi_reference", "") or ""
            intervention = getattr(mod, "intervention", "") or ""
            target_el = getattr(mod, "target_element", "") or ""
            direction = getattr(mod, "direction", "") or ""
            baseline_val = getattr(mod, "baseline_value", "") or ""
            proposed_val = getattr(mod, "proposed_value", "") or ""

            caused_problem = any(
                kpi_ref.lower() in name.lower() or name.lower() in kpi_ref.lower()
                for name in problem_kpi_names
            ) if kpi_ref else False

            marker = " ← LIKELY CAUSED DEGRADATION" if caused_problem else ""
            lines.append(
                f"  • [{direction.upper()}] {intervention} on '{target_el}': "
                f"{baseline_val} → {proposed_val} (targets: {kpi_ref}){marker}"
            )

        if any(
            any(kpi_ref and (kpi_ref.lower() in name.lower() or name.lower() in kpi_ref.lower()) for name in problem_kpi_names)
            for mod in proposal.modifications
            if (kpi_ref := getattr(mod, "kpi_reference", "") or "")
        ):
            lines.append("")
            lines.append("  → REMOVE or REPLACE the modifications marked with '← LIKELY CAUSED DEGRADATION'.")

    # --- Section 3: KB-Retrieved Alternative Interventions ---
    alternatives_text = _retrieve_alternative_interventions(
        worsened_kpis + [(e.kpi_name, "", e.target_direction) for e in not_reached], violated_kpis,
        kpi_info={e.kpi_name: (getattr(e, "category", ""), getattr(e, "measurable_as", None))
                  for e in eval_result.kpi_comparisons},
    )
    if alternatives_text:
        lines.append("")
        lines.append("## Literature-Backed Alternative Interventions:")
        lines.append(alternatives_text)

    # --- Section 4: Instructions ---
    lines.append("")
    lines.append("## What You Must Do Differently:")
    lines.append("")

    if violated_kpis:
        lines.append("SAFEGUARD VIOLATIONS (non-negotiable constraints):")
        for name, pct, direction in violated_kpis:
            lines.append(f"  • {name} moved {pct} — target was to {direction}. MUST be reversed.")
        lines.append("")

    if worsened_kpis:
        lines.append("WORSENED KPIs (must be fixed):")
        for name, pct, direction in worsened_kpis:
            lines.append(f"  • {name} degraded by {pct} — target was to {direction}.")
        lines.append("")

    not_applied = [n.split("] ", 1)[1] for n in (getattr(gen_result, "generation_notes", None) or [])
                   if n.startswith(("[patch-reject]", "[merge-skip]"))]
    if not_applied:
        lines.append("MODIFICATIONS NOT APPLIED (the simulated scenario does not contain them):")
        for n in dict.fromkeys(not_applied):
            lines.append(f"  • {n}")
        lines.append("")

    if not_reached:
        lines.append("TARGETS NOT YET REACHED (improved, but more change is needed):")
        for e in not_reached:
            lines.append(f"  • {e.kpi_name}: {_target_text(e)}.")
            lines.extend(f"    {g}" for g in _gap_lines(e, gen_result))
        lines.append("")

    if achieved:
        lines.append("TARGETS ACHIEVED (keep them): " + ", ".join(f"{n}: target achieved" for n in achieved))
        lines.append("")

    if improved_kpis:
        lines.append(f"PRESERVE improvements in: {', '.join(improved_kpis)}")
        lines.append("")

    lines.append("STRATEGY:")
    lines.append("  • Use the alternative interventions from literature above — they are evidence-backed.")
    lines.append("  • Do NOT repeat modifications that caused degradation.")
    lines.append("  • If a single modification helps one KPI but hurts another, find a decoupled lever.")
    lines.append(f"  • You have {max_iterations - iteration} attempt(s) remaining.")

    return "\n".join(lines)


def _retrieve_alternative_interventions(
    worsened_kpis: list[tuple[str, str, str]],
    violated_kpis: list[tuple[str, str, str]],
    kpi_info: dict[str, tuple[str, str | None]] | None = None,
) -> str:
    """Query the knowledge base for alternative interventions for problem KPIs.

    Direction-aware (see :mod:`knowledge.direction`): an entry whose stated effect on a problem
    KPI's metric is the opposite of the KPI's direction, or mixed, is dropped (a minimize KPI only
    gets entries that state a decrease, and vice versa); entries that state no effect on the metric
    are kept and ranked after those that state the wanted one. More entries are retrieved than
    shown so that enough remain after filtering.

    The metric of each problem KPI comes from its category and ``measurable_as`` (``kpi_info``:
    name -> (category, measurable_as)) via :func:`knowledge.direction.kpi_metric`; the name is used
    only when neither is known.
    """
    problem_kpis = worsened_kpis + violated_kpis
    if not problem_kpis:
        return ""

    try:
        from knowledge.direction import direction_rank, kpi_metric
        from knowledge.retrieval import retrieve_for_second_llm
    except ImportError:
        return ""

    info = kpi_info or {}

    def metric(name: str) -> str | None:
        category, measurable_as = info.get(name, ("", None))
        return kpi_metric(category, measurable_as, name)

    def rank(text: str) -> int | None:
        ranks = [direction_rank(text, metric(name) or "", direction) for name, _, direction in problem_kpis]
        return None if any(r is None for r in ranks) else min(ranks)

    def keep(items: list, text_of) -> list:
        ranked = [(r, i, it) for i, it in enumerate(items) if (r := rank(text_of(it))) is not None]
        return [it for _, _, it in sorted(ranked, key=lambda x: (x[0], x[1]))]

    kpi_dicts = []
    for name, _, direction in problem_kpis:
        kpi_dicts.append({
            "name": name,
            "category": metric(name) or _infer_category(name),
            "target_direction": direction,
        })

    try:
        result = retrieve_for_second_llm(
            goal_structured=f"Find alternative interventions to improve: {', '.join(name for name, _, _ in problem_kpis)}",
            kpis=kpi_dicts,
            context_profile=None,
            top_k=24,
            per_kind_caps={"mapping": 10, "literature": 6, "parameter": 3, "rule": 0, "pdf_chunk": 4},
        )
    except Exception as exc:
        logger.debug("KB retrieval for feedback failed: %s", exc)
        return ""

    mappings = keep(result.goal_mappings, lambda m: " ".join(
        [m.goal_description] + [f"{c.rationale} {c.quantitative_evidence}" for c in m.parameter_changes]))
    literature = keep(result.literature, lambda lit: f"{lit.key_finding} {lit.quantitative_result}")
    chunks = keep(list(zip(result.pdf_chunks, result.pdf_chunk_scores,
                           result.pdf_chunk_paper_ids + ["?"] * len(result.pdf_chunks))), lambda c: c[0])

    lines: list[str] = []

    if mappings:
        lines.append("  Recommended strategies from BPS literature:")
        for i, mapping in enumerate(mappings[:4], 1):
            lines.append(f"  {i}. {mapping.goal_description} (domain: {mapping.domain})")
            for change in mapping.parameter_changes[:3]:
                evidence_str = f" — Evidence: {change.quantitative_evidence}" if change.quantitative_evidence else ""
                paper_str = f" [papers: {change.paper_ids}]" if change.paper_ids else ""
                lines.append(
                    f"     → {change.direction.value.upper()} {change.parameter_name}: "
                    f"{change.rationale}{evidence_str}{paper_str}"
                )

    if literature:
        lines.append("")
        lines.append("  Supporting evidence:")
        for lit in literature[:3]:
            lines.append(
                f"  - {lit.authors} ({lit.year}): {lit.key_finding} "
                f"Result: {lit.quantitative_result}"
            )

    if chunks:
        lines.append("")
        lines.append("  Source excerpts (from indexed papers):")
        for i, (chunk_text, score, paper_id) in enumerate(chunks[:2], 1):
            excerpt = chunk_text[:300].replace("\n", " ")
            lines.append(f"  {i}. [Paper {paper_id}, score={score:.3f}] {excerpt}")

    return "\n".join(lines)


def _infer_category(kpi_name: str) -> str:
    """Infer a KPI category from its name for KB querying."""
    name_lower = kpi_name.lower()
    if "wait" in name_lower:
        return "waiting_time"
    if "cycle" in name_lower or "lead" in name_lower:
        return "processing_time"
    if "cost" in name_lower:
        return "cost"
    if "throughput" in name_lower or "cases" in name_lower:
        return "throughput"
    if "utiliz" in name_lower or "resource" in name_lower:
        return "resource_utilisation"
    if "process" in name_lower and "time" in name_lower:
        return "processing_time"
    return "processing_capacity"


def _selection_key(it: IterationResult) -> tuple[int, int, float]:
    return it.achieved_count, it.improved_count, it.score


def _select_best_iteration(iterations: list[IterationResult], by_score_only: bool = False) -> int | None:
    """Index of the best iteration: most achieved targets, then most improved KPIs, then score
    (``by_score_only``: score alone, the earlier rule).

    Ties keep the earlier iteration. Failed iterations rank last.
    """
    if not iterations:
        return None
    key_of = (lambda it: (it.score,)) if by_score_only else _selection_key
    best_idx = 0
    best_key = key_of(iterations[0])
    for i, it in enumerate(iterations[1:], 1):
        key = key_of(it)
        if key > best_key:
            best_key = key
            best_idx = i
    return best_idx


def run_iterative_evaluation(
    orchestrator: Any,
    baseline_scenario: SimuBridgeScenario,
    bpmn_xml: str,
    targets: list[KPITarget],
    *,
    max_iterations: int = 4,
    total_cases: int = 1000,
    start_time: str = "2024-01-01 09:00:00.000000+00:00",
    seed: int = 42,
    cost_per_hour: dict[str, float] | None = None,
    on_iteration: Callable[[int, str], None] | None = None,
    log_profile: dict[str, Any] | None = None,
    context_profile: dict[str, Any] | None = None,
    legacy_refinement: bool = False,
) -> IterativeEvaluationResult:
    """Run the iterative generate→simulate→evaluate→feedback loop.

    Parameters
    ----------
    orchestrator
        The SecondLLMWorkspaceOrchestrator instance (with provider configured).
    baseline_scenario
        The SIMOD baseline SimuBridgeScenario.
    bpmn_xml
        BPMN 2.0 XML for the process model.
    targets
        KPI targets with direction and safeguard information.
    max_iterations
        Maximum number of generate+evaluate cycles (default 4).
    total_cases
        Number of cases per simulation run.
    start_time
        Simulation start timestamp.
    seed
        Random seed for reproducibility.
    cost_per_hour
        Resource → hourly cost mapping.
    on_iteration
        Optional callback(iteration_number, status_message) for progress updates.
    log_profile
        Event-log profile dict passed to generate_scenario for RAG retrieval.
    context_profile
        Context profile dict passed to generate_scenario for evidence filtering.
    legacy_refinement
        Use the earlier refinement rules (for ablation): stop at the first iteration whose
        overall status is ``improved``, and select the best iteration by score alone.
        Default False: stop when the targets are met; select by achieved targets, then
        improved KPIs, then score.
    """
    t0 = time.time()
    result = IterativeEvaluationResult()

    def _notify(iteration: int, msg: str) -> None:
        if on_iteration:
            on_iteration(iteration, msg)

    # --- Step 1: Simulate baseline (once) ---
    _notify(0, "Simulating baseline...")
    baseline_sim: ProsimosResult | None = run_prosimos_simulation(
        baseline_scenario, bpmn_xml,
        total_cases=total_cases, start_time=start_time, seed=seed,
    )

    if baseline_sim is None or not baseline_sim.ok:
        result.error = f"Baseline simulation failed: {baseline_sim.error if baseline_sim else 'No simulator available'}"
        result.total_time_seconds = time.time() - t0
        return result

    baseline_kpis = compute_kpis(baseline_sim.simulated_log, cost_per_hour=cost_per_hour,
                                 prosimos_params=scenario_to_prosimos_json(baseline_scenario, bpmn_xml),
                                 sim_start=start_time)
    result.baseline_kpis = baseline_kpis

    # --- Step 2: Iterative loop ---
    for i in range(1, max_iterations + 1):
        temperature = min(0.3 + 0.1 * (i - 1), 0.7)
        iter_result = IterationResult(iteration=i)

        # Generate scenario
        _notify(i, "Generating scenario...")
        gen_result = orchestrator.generate_scenario(
            temperature=temperature,
            log_profile=log_profile,
            context_profile=context_profile,
        )
        iter_result.gen_result = gen_result

        if not gen_result.success or gen_result.proposal is None:
            iter_result.error = gen_result.error or "Generation failed"
            _notify(i, f"Generation failed: {iter_result.error}")
            result.iterations.append(iter_result)
            continue

        proposed_scenario = gen_result.proposal.scenario

        # Simulate proposed
        _notify(i, "Simulating proposed scenario...")
        proposed_sim = run_prosimos_simulation(
            proposed_scenario, bpmn_xml,
            total_cases=total_cases, start_time=start_time, seed=seed,
        )

        if proposed_sim is None or not proposed_sim.ok:
            iter_result.error = f"Simulation failed: {proposed_sim.error if proposed_sim else 'unknown'}"
            _notify(i, f"Simulation failed")
            result.iterations.append(iter_result)
            continue

        # Compute KPIs and compare
        _notify(i, "Evaluating KPIs...")
        proposed_kpis = compute_kpis(proposed_sim.simulated_log, cost_per_hour=cost_per_hour,
                                     prosimos_params=scenario_to_prosimos_json(proposed_scenario, bpmn_xml),
                                     sim_start=start_time)
        eval_result = compare_kpis(baseline_kpis, proposed_kpis, targets)
        iter_result.eval_result = eval_result
        iter_result.status = eval_result.summary.overall_status if eval_result.summary else None

        result.iterations.append(iter_result)

        # Done when every KPI with a threshold has achieved it and every KPI without
        # one has improved (without thresholds: every computed KPI improved).
        # Earlier rule (legacy_refinement): overall status improved.
        if legacy_refinement and iter_result.status == OverallStatus.IMPROVED:
            _notify(i, "All KPIs improved! Stopping.")
            break
        if not legacy_refinement and iter_result.targets_met:
            _notify(i, "All targets met! Stopping.")
            break

        # Build feedback and inject for next iteration
        if i < max_iterations:
            feedback = _build_evaluation_feedback(eval_result, gen_result, i, max_iterations)
            iter_result.feedback_message = feedback
            orchestrator.inject_feedback_message(feedback)
            status_label = iter_result.status.value if iter_result.status else "unknown"
            _notify(i, f"Result: {status_label} — refining...")

    # --- Step 3: Select best ---
    best_idx = _select_best_iteration(result.iterations, by_score_only=legacy_refinement)
    if best_idx is not None and not any(it.score > 0 for it in result.iterations):
        # No iteration improves on the baseline by the score: keep the baseline and attach
        # the best iteration for inspection instead of returning it as the final scenario.
        result.no_improving_scenario = True
        result.best_candidate_idx = best_idx
        _notify(len(result.iterations), "No iteration improved on the baseline; keeping the baseline.")
    else:
        result.best_iteration_idx = best_idx
    if result.best is not None:
        result.final_status = result.best.status

    result.total_time_seconds = time.time() - t0
    return result

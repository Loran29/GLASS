"""Iterative evaluation loop — auto-refine scenarios via simulation feedback.

Runs a generate→simulate→evaluate→feedback cycle until the proposed scenario
achieves all target KPI improvements, or the max iteration count is reached.
The best result across all iterations is returned.

Iteration selection uses a goal-programming scoring function with
prospect-theoretic loss aversion (Kahneman & Tversky, 1979; λ=2.25).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from second_llm.kpi_computation import KPIComputationResult, compute_kpis
from second_llm.output_schema import SimuBridgeScenario
from second_llm.prosimos_runner import ProsimosResult, run_prosimos_simulation
from second_llm.scenario_evaluation import (
    KPIComparisonEntry,
    KPITarget,
    OverallStatus,
    ScenarioEvaluationResult,
    compare_kpis,
)
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


@dataclass
class IterativeEvaluationResult:
    """Complete result of the iterative optimization loop."""

    iterations: list[IterationResult] = field(default_factory=list)
    best_iteration_idx: int | None = None
    final_status: OverallStatus | None = None
    total_time_seconds: float = 0.0
    baseline_kpis: KPIComputationResult | None = None
    error: str | None = None

    @property
    def best(self) -> IterationResult | None:
        if self.best_iteration_idx is not None and self.best_iteration_idx < len(self.iterations):
            return self.iterations[self.best_iteration_idx]
        return None

    @property
    def improved(self) -> bool:
        return self.final_status == OverallStatus.IMPROVED

    @property
    def total_iterations(self) -> int:
        return len(self.iterations)


def _build_evaluation_feedback(
    eval_result: ScenarioEvaluationResult,
    gen_result: ScenarioGenerationResult,
    iteration: int,
    max_iterations: int,
) -> str:
    """Build a structured feedback message enriched with causal attribution and KB evidence.

    Includes:
      1. Quantitative simulation results per KPI
      2. Causal attribution: maps each worsened KPI to the specific modification that targeted it
      3. KB-retrieved alternative interventions for worsened KPIs
      4. Strategy guidance grounded in literature
    """
    lines: list[str] = []
    lines.append(f"=== SIMULATION FEEDBACK (Attempt {iteration}/{max_iterations}) ===")
    lines.append("")
    lines.append("Your proposed scenario was simulated using Prosimos (discrete-event simulation).")
    lines.append(f"Overall Result: {eval_result.summary.overall_status.value.upper()}")
    lines.append("")

    # --- Section 1: Quantitative KPI Results ---
    lines.append("## Quantitative KPI Results (Baseline → Your Proposal):")

    improved_kpis: list[str] = []
    worsened_kpis: list[tuple[str, str, str]] = []  # (name, pct, direction)
    violated_kpis: list[tuple[str, str, str]] = []

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

        lines.append(f"  - {e.kpi_name}: {baseline_str} → {proposed_str}{unit_str} ({pct_str}) — {marker}")

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
    alternatives_text = _retrieve_alternative_interventions(worsened_kpis, violated_kpis)
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
) -> str:
    """Query the knowledge base for alternative interventions for problem KPIs."""
    problem_kpis = worsened_kpis + violated_kpis
    if not problem_kpis:
        return ""

    try:
        from knowledge.retrieval import retrieve_for_second_llm
    except ImportError:
        return ""

    kpi_dicts = []
    for name, _, direction in problem_kpis:
        kpi_dicts.append({
            "name": name,
            "category": _infer_category(name),
            "target_direction": direction,
        })

    try:
        result = retrieve_for_second_llm(
            goal_structured=f"Find alternative interventions to improve: {', '.join(name for name, _, _ in problem_kpis)}",
            kpis=kpi_dicts,
            context_profile=None,
            top_k=12,
            per_kind_caps={"mapping": 4, "literature": 3, "parameter": 3, "rule": 0, "pdf_chunk": 2},
        )
    except Exception as exc:
        logger.debug("KB retrieval for feedback failed: %s", exc)
        return ""

    lines: list[str] = []

    if result.goal_mappings:
        lines.append("  Recommended strategies from BPS literature:")
        for i, mapping in enumerate(result.goal_mappings[:4], 1):
            lines.append(f"  {i}. {mapping.goal_description} (domain: {mapping.domain})")
            for change in mapping.parameter_changes[:3]:
                evidence_str = f" — Evidence: {change.quantitative_evidence}" if change.quantitative_evidence else ""
                paper_str = f" [papers: {change.paper_ids}]" if change.paper_ids else ""
                lines.append(
                    f"     → {change.direction.value.upper()} {change.parameter_name}: "
                    f"{change.rationale}{evidence_str}{paper_str}"
                )

    if result.literature:
        lines.append("")
        lines.append("  Supporting evidence:")
        for lit in result.literature[:3]:
            lines.append(
                f"  - {lit.authors} ({lit.year}): {lit.key_finding} "
                f"Result: {lit.quantitative_result}"
            )

    if result.pdf_chunks:
        lines.append("")
        lines.append("  Source excerpts (from indexed papers):")
        for i, (chunk_text, score) in enumerate(
            zip(result.pdf_chunks[:2], result.pdf_chunk_scores[:2]), 1
        ):
            paper_id = result.pdf_chunk_paper_ids[i - 1] if i - 1 < len(result.pdf_chunk_paper_ids) else "?"
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


def _select_best_iteration(iterations: list[IterationResult]) -> int | None:
    """Return the index of the best iteration by score."""
    if not iterations:
        return None
    best_idx = 0
    best_score = iterations[0].score
    for i, it in enumerate(iterations[1:], 1):
        if it.score > best_score:
            best_score = it.score
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

    baseline_kpis = compute_kpis(baseline_sim.simulated_log, cost_per_hour=cost_per_hour)
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
        proposed_kpis = compute_kpis(proposed_sim.simulated_log, cost_per_hour=cost_per_hour)
        eval_result = compare_kpis(baseline_kpis, proposed_kpis, targets)
        iter_result.eval_result = eval_result
        iter_result.status = eval_result.summary.overall_status if eval_result.summary else None

        result.iterations.append(iter_result)

        # Check if we're done
        if iter_result.status == OverallStatus.IMPROVED:
            _notify(i, "All KPIs improved! Stopping.")
            break

        # Build feedback and inject for next iteration
        if i < max_iterations:
            feedback = _build_evaluation_feedback(eval_result, gen_result, i, max_iterations)
            iter_result.feedback_message = feedback
            orchestrator.inject_feedback_message(feedback)
            status_label = iter_result.status.value if iter_result.status else "unknown"
            _notify(i, f"Result: {status_label} — refining...")

    # --- Step 3: Select best ---
    result.best_iteration_idx = _select_best_iteration(result.iterations)
    if result.best is not None:
        result.final_status = result.best.status

    result.total_time_seconds = time.time() - t0
    return result

"""Step 8: KPI Traceability & Scenario Comparison.

Closes the loop between the first LLM's verified KPI targets and the
second LLM's generated ScenarioProposal.  Produces a structured
comparison report that shows:

  1. **KPI traceability** — for each target KPI, which modifications
     address it, what the expected impact is, and the confidence level.
  2. **Parameter delta table** — baseline vs. proposed values with
     direction and magnitude for every modification.
  3. **Coverage assessment** — which KPIs are fully addressed, partially
     addressed, or unaddressed by the scenario.
  4. **Constraint check** — maintain-direction KPIs should not be
     degraded by the proposed changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from second_llm.output_schema import ScenarioProposal
from second_llm.validation import _extract_first_number


# ===================================================================
# Data structures
# ===================================================================

@dataclass
class ParameterDelta:
    """A single baseline→proposed parameter change."""

    modification_index: int
    intervention: str
    target_element: str
    parameter_type: str
    direction: str
    baseline_value: str
    proposed_value: str
    baseline_numeric: float | None = None
    proposed_numeric: float | None = None
    change_pct: float | None = None
    kpi_reference: str = ""
    evidence_source: str = ""
    monthly_cost: float | None = None
    monthly_cost_formatted: str = ""

    @property
    def has_numeric_delta(self) -> bool:
        return self.baseline_numeric is not None and self.proposed_numeric is not None


@dataclass
class KPITraceEntry:
    """Traceability for one target KPI."""

    kpi_name: str
    target_direction: str  # minimize, maximize, maintain
    category: str
    process_scope: str

    # From the scenario proposal
    modifications: list[ParameterDelta] = field(default_factory=list)
    expected_direction: str = ""  # decrease, increase, maintain
    estimated_magnitude: str = ""
    confidence: str = ""
    impact_reasoning: str = ""

    @property
    def coverage(self) -> str:
        """Coverage level: 'full', 'partial', or 'unaddressed'."""
        if not self.modifications:
            return "unaddressed"
        if self.expected_direction:
            return "full"
        return "partial"

    @property
    def is_constraint(self) -> bool:
        return self.target_direction == "maintain"

    @property
    def direction_aligned(self) -> bool | None:
        """Check if the expected impact direction aligns with the target.

        Returns None if alignment cannot be determined.
        """
        if not self.expected_direction:
            return None

        direction_map = {
            "minimize": "decrease",
            "maximize": "increase",
            "maintain": "maintain",
        }
        expected_target = direction_map.get(self.target_direction)
        if expected_target is None:
            return None
        return self.expected_direction == expected_target


@dataclass
class ComparisonReport:
    """Full comparison report linking KPI targets to scenario modifications."""

    kpi_traces: list[KPITraceEntry] = field(default_factory=list)
    parameter_deltas: list[ParameterDelta] = field(default_factory=list)
    scenario_name: str = ""
    scenario_reasoning: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def total_kpis(self) -> int:
        return len(self.kpi_traces)

    @property
    def addressed_kpis(self) -> int:
        return sum(1 for t in self.kpi_traces if t.coverage != "unaddressed")

    @property
    def unaddressed_kpis(self) -> list[KPITraceEntry]:
        return [t for t in self.kpi_traces if t.coverage == "unaddressed"]

    @property
    def constraint_kpis(self) -> list[KPITraceEntry]:
        return [t for t in self.kpi_traces if t.is_constraint]

    @property
    def misaligned_kpis(self) -> list[KPITraceEntry]:
        """KPIs where the expected direction doesn't match the target."""
        return [
            t for t in self.kpi_traces
            if t.direction_aligned is False
        ]

    @property
    def coverage_pct(self) -> float:
        if not self.kpi_traces:
            return 0.0
        return (self.addressed_kpis / self.total_kpis) * 100

    @property
    def total_modifications(self) -> int:
        return len(self.parameter_deltas)


# ===================================================================
# Comparison builder
# ===================================================================

def _build_parameter_deltas(proposal: ScenarioProposal) -> list[ParameterDelta]:
    """Extract a delta for each modification in the proposal."""
    deltas: list[ParameterDelta] = []

    for idx, mod in enumerate(proposal.modifications):
        baseline_num = _extract_first_number(mod.baseline_value)
        proposed_num = _extract_first_number(mod.proposed_value)

        change_pct: float | None = None
        if baseline_num is not None and proposed_num is not None and baseline_num != 0:
            change_pct = ((proposed_num - baseline_num) / abs(baseline_num)) * 100

        deltas.append(ParameterDelta(
            modification_index=idx + 1,
            intervention=mod.intervention or f"{mod.target_element} — {mod.parameter_type}",
            target_element=mod.target_element,
            parameter_type=mod.parameter_type,
            direction=mod.direction.value,
            baseline_value=mod.baseline_value,
            proposed_value=mod.proposed_value,
            baseline_numeric=baseline_num,
            proposed_numeric=proposed_num,
            change_pct=change_pct,
            kpi_reference=mod.kpi_reference,
            evidence_source=mod.evidence_source,
        ))

    return deltas


def _build_kpi_traces(
    first_llm_parsed: dict[str, Any],
    proposal: ScenarioProposal,
    deltas: list[ParameterDelta],
) -> list[KPITraceEntry]:
    """Build a trace entry for each verified KPI."""
    traces: list[KPITraceEntry] = []

    kpis = first_llm_parsed.get("kpis", [])
    if not kpis:
        return traces

    # Index proposal impacts by KPI name (case-insensitive)
    impact_map: dict[str, Any] = {}
    for impact in proposal.expected_kpi_impacts:
        impact_map[impact.kpi_name.lower()] = impact

    # Index deltas by KPI reference (case-insensitive)
    delta_map: dict[str, list[ParameterDelta]] = {}
    for delta in deltas:
        key = delta.kpi_reference.lower()
        delta_map.setdefault(key, []).append(delta)

    for kpi in kpis:
        name = kpi.get("name", "Unnamed")
        name_lower = name.lower()

        # Find matching impact
        impact = impact_map.get(name_lower)

        # Find matching deltas
        matching_deltas = delta_map.get(name_lower, [])

        trace = KPITraceEntry(
            kpi_name=name,
            target_direction=kpi.get("target_direction", ""),
            category=kpi.get("category", ""),
            process_scope=kpi.get("process_scope", ""),
            modifications=matching_deltas,
            expected_direction=impact.direction if impact else "",
            estimated_magnitude=impact.estimated_magnitude if impact else "",
            confidence=impact.confidence if impact else "",
            impact_reasoning=impact.reasoning if impact else "",
        )
        traces.append(trace)

    return traces


def build_comparison_report(
    first_llm_json: str,
    proposal: ScenarioProposal,
) -> ComparisonReport:
    """Build a full comparison report from the first LLM output and the scenario.

    Parameters
    ----------
    first_llm_json:
        Raw JSON string of the verified first-LLM output.
    proposal:
        The generated ScenarioProposal.

    Returns
    -------
    ComparisonReport
        Structured comparison with traceability, deltas, and coverage.
    """
    notes: list[str] = []

    # Parse first LLM JSON
    try:
        first_llm_parsed = json.loads(first_llm_json)
    except (json.JSONDecodeError, TypeError):
        first_llm_parsed = {}
        notes.append("Could not parse first-LLM JSON — KPI traceability will be limited.")

    # Build parameter deltas
    deltas = _build_parameter_deltas(proposal)

    # Build KPI traces
    traces = _build_kpi_traces(first_llm_parsed, proposal, deltas)

    # Assess coverage
    if traces:
        addressed = sum(1 for t in traces if t.coverage != "unaddressed")
        notes.append(f"KPI coverage: {addressed}/{len(traces)} KPIs addressed by modifications.")

        unaddressed = [t for t in traces if t.coverage == "unaddressed"]
        if unaddressed:
            names = ", ".join(t.kpi_name for t in unaddressed)
            notes.append(f"Unaddressed KPIs: {names}")

        misaligned = [t for t in traces if t.direction_aligned is False]
        if misaligned:
            for t in misaligned:
                notes.append(
                    f"Direction mismatch: KPI '{t.kpi_name}' targets "
                    f"'{t.target_direction}' but scenario expects "
                    f"'{t.expected_direction}'."
                )

        constraints = [t for t in traces if t.is_constraint]
        for c in constraints:
            if c.expected_direction and c.expected_direction != "maintain":
                notes.append(
                    f"Constraint warning: maintain-KPI '{c.kpi_name}' has "
                    f"expected direction '{c.expected_direction}' instead of 'maintain'."
                )

    return ComparisonReport(
        kpi_traces=traces,
        parameter_deltas=deltas,
        scenario_name=proposal.scenario_name,
        scenario_reasoning=proposal.reasoning,
        notes=notes,
    )


def enrich_deltas_with_cost(
    report: ComparisonReport,
    cost_report: Any,
) -> None:
    """Merge monthly cost estimates into the comparison's parameter deltas.

    Matches :class:`CostEstimate` entries to :class:`ParameterDelta`
    entries by ``modification_index`` and writes ``monthly_cost`` and
    ``monthly_cost_formatted`` onto each matching delta.
    """
    if cost_report is None or not getattr(cost_report, "cost_estimates", None):
        return

    cost_by_idx: dict[int, Any] = {
        ce.modification_index: ce
        for ce in cost_report.cost_estimates
    }

    for delta in report.parameter_deltas:
        ce = cost_by_idx.get(delta.modification_index)
        if ce is not None:
            delta.monthly_cost = ce.monthly_cost
            delta.monthly_cost_formatted = ce.formatted_cost

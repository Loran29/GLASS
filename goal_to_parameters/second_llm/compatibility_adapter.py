"""Back-compat adapter: ``ScenarioPatch`` + merged scenario -> legacy ``ScenarioProposal``.

The delta-only refactor changes what the LLM emits (a
:class:`~second_llm.output_schema_patch.ScenarioPatch`) but the rest of
the pipeline — comparison reporting, cost estimation, UI rendering, the
SimuBridge scenario export — still consumes the legacy
:class:`~second_llm.output_schema.ScenarioProposal` shape.  This adapter
bridges them so downstream modules keep working unchanged.

Contract
--------
``build_legacy_proposal(patch, merged_scenario)`` returns a fully valid
``ScenarioProposal`` where:

* ``scenario`` is the deterministically-merged SimuBridge scenario
  (the simulator's input), not anything the LLM emitted directly.
* ``modifications`` preserves every ``PatchModification`` field so the
  comparison/cost reports see the same target_element / direction /
  baseline_value / proposed_value semantics as before.
* ``expected_kpi_impacts``, ``context_differentiations``,
  ``unresolved_kpis``, ``warnings`` flow through unchanged.

This module is intentionally thin and has **no side effects** on the
inputs — both the patch and the merged scenario are left untouched.
"""

from __future__ import annotations

from second_llm.output_schema import (
    ParameterModification,
    ScenarioProposal,
    SimuBridgeScenario,
)
from second_llm.output_schema_patch import PatchModification, ScenarioPatch


def _patch_mod_to_legacy(pm: PatchModification) -> ParameterModification:
    """Translate a patch modification into the legacy record."""
    changed_param_label = pm.target_field or pm.parameter_type.value.replace("_", " ")
    return ParameterModification(
        intervention=pm.intervention,
        changed_parameters=changed_param_label,
        parameter_type=pm.parameter_type.value,
        target_element=pm.target_element,
        direction=pm.direction,
        baseline_value=pm.baseline_value,
        proposed_value=pm.proposed_value,
        kpi_reference=pm.kpi_reference,
        mechanism_rationale=pm.mechanism_rationale,
        rationale=pm.rationale,
        evidence_source=pm.evidence_source,
        literature_support=list(pm.literature_support),
        feasibility_assumptions=pm.feasibility_assumptions,
        context_condition=pm.context_condition,
    )


def build_legacy_proposal(
    patch: ScenarioPatch,
    merged_scenario: SimuBridgeScenario,
    *,
    extra_warnings: list[str] | None = None,
) -> ScenarioProposal:
    """Assemble a legacy :class:`ScenarioProposal` from the patch + merged scenario.

    Parameters
    ----------
    patch:
        The LLM-produced :class:`ScenarioPatch`.
    merged_scenario:
        The :class:`SimuBridgeScenario` produced by ``apply_patch`` — this
        is what downstream code persists, displays, and exports.
    extra_warnings:
        Optional additional notes (e.g. merge diagnostics) to append.
    """
    legacy_mods = [_patch_mod_to_legacy(pm) for pm in patch.modifications]

    # ScenarioProposal requires at least one modification. When the LLM
    # responds that every KPI is unresolved, we still need *some* legal
    # record so the downstream pipeline stays schema-compliant; emit a
    # sentinel "no_op" modification that is visually labelled as abstention.
    # (In practice this branch is rare — only triggers when every KPI is
    # unresolvable — and the UI displays it as an abstention note.)
    if not legacy_mods:
        sentinel_kpi = (
            patch.unresolved_kpis[0].kpi_name
            if patch.unresolved_kpis
            else "unspecified"
        )
        legacy_mods = [ParameterModification(
            intervention="No grounded modification — scenario abstains",
            changed_parameters="none",
            parameter_type="activity_duration",  # inert placeholder
            target_element="(abstention)",
            direction="redistribute",
            baseline_value="n/a",
            proposed_value="n/a",
            kpi_reference=sentinel_kpi,
            mechanism_rationale="Every declared KPI is listed in unresolved_kpis.",
            rationale=(
                "No grounded modification could be proposed; see unresolved_kpis."
            ),
            evidence_source="abstention",
            literature_support=[],
            feasibility_assumptions="Not specified",
            context_condition=None,
        )]

    # Impacts: require ≥1 entry — synthesise from unresolved KPIs when
    # the patch has none (same abstention corner case).
    impacts = list(patch.expected_kpi_impacts)
    if not impacts and patch.unresolved_kpis:
        from second_llm.output_schema import KPIImpact
        impacts = [KPIImpact(
            kpi_name=u.kpi_name,
            direction="maintain",
            estimated_magnitude="",
            confidence="low",
            reasoning=u.explanation or u.reason,
        ) for u in patch.unresolved_kpis]

    warnings = list(patch.warnings)
    if extra_warnings:
        warnings.extend(extra_warnings)

    return ScenarioProposal(
        scenario_name=patch.scenario_id,
        baseline_source=patch.baseline_reference,
        reasoning=patch.reasoning,
        modifications=legacy_mods,
        expected_kpi_impacts=impacts,
        context_differentiations=list(patch.context_differentiations),
        unresolved_kpis=list(patch.unresolved_kpis),
        scenario=merged_scenario,
        warnings=warnings,
    )

from .smart_kpi_prompt import REQUIRED_SCHEMA


def build_refinement_prompt(
    process_description: str,
    simulation_goal: str,
    previous_kpis_json: str,
    human_feedback: str,
    accepted_kpi_names: list[str],
    rejected_kpi_names: list[str],
    total_kpis: int = 0,
    log_evidence: str | None = None,
    context_evidence: str | None = None,
) -> tuple[str, str]:
    """
    Builds a refinement prompt that incorporates human feedback.
    Accepted KPIs are kept unchanged.
    Rejected KPIs are replaced one-for-one with improved SMART KPIs.
    """

    total_kpis_rule = (
        f"- The output must contain exactly {total_kpis} KPIs total "
        f"({len(accepted_kpi_names)} accepted + {len(rejected_kpi_names)} replacement(s)). "
        f"Do NOT add extra KPIs beyond this count.\n"
        if total_kpis > 0
        else ""
    )

    accepted_names_text = ", ".join(accepted_kpi_names) if accepted_kpi_names else "None"
    rejected_names_text = ", ".join(rejected_kpi_names) if rejected_kpi_names else "None"

    system_prompt = f"""You are a senior Business Process Management (BPM) consultant specializing in process simulation and performance measurement.

Your task is to refine a previously generated set of SMART KPIs after expert review.

You are not a general-purpose assistant. You only handle BPM process simulation and SMART KPI refinement tasks.

## Objective
Produce a revised KPI set that preserves all accepted KPIs exactly and replaces each rejected KPI with exactly one improved KPI.

## Required output behavior
1. KEEP all accepted KPIs exactly as they are.
2. REPLACE each rejected KPI with exactly one new KPI.
3. Return the complete final KPI set as valid JSON only.
4. The JSON must match the schema below exactly.

{REQUIRED_SCHEMA}

## Rules you MUST follow:
1. Do NOT modify accepted KPIs in any way.
2. Do NOT remove accepted KPIs.
3. Do NOT generate more than one replacement for any rejected KPI.
4. Do NOT generate fewer replacements than the number of rejected KPIs.
5. Use only activities, roles, resources, outcomes, or process elements explicitly mentioned in the process description or already validly grounded in the previous KPI set.
6. Do NOT invent new process elements, resources, constraints, or data attributes that are unsupported by the provided inputs.
7. Each replacement KPI must directly address the expert feedback.
8. Each replacement KPI must be more specific, more measurable, and more relevant to the simulation goal than the rejected KPI it replaces.
9. Avoid duplicate KPIs and also avoid semantically overlapping KPIs that measure nearly the same thing with different wording.
10. Use the most appropriate category, supported_by_log flag, evidence_basis, and process_scope for each replacement KPI.
11. Avoid vague KPI language such as "improve efficiency" or "increase performance" unless it is operationalized as a concrete measurable process metric.
12. Prefer replacements that are useful for later what-if analysis, simulation comparison, or validation of simulation results.
13. Maintain coverage across KPI categories where appropriate for the goal.
14. Use context_segmentation only when evidence-supported context associations support differentiated targets.
15. When a replacement KPI has non-empty context_segmentation, you MAY make its title lightly context-aware using the evidence_factor already in that segmentation. Do not add context qualifiers to the title when context_segmentation is empty.
16. Prefer context-aware bottleneck KPI titles only when the KPI's context_segmentation is non-empty with a populated evidence_factor. If context_segmentation is empty, keep the title generic.
17. If no accepted context relationship survives the provided filtering, keep replacement KPI titles and descriptions generic and avoid context-aware wording.
17. Do not invent context-specific targets, significance claims, or segmented bottlenecks that are absent from the provided evidence.
18. Output must be valid JSON matching the required schema above. No markdown and no extra text outside the JSON.
19. Never use any calendar-period factor in context_segmentation. This includes any factor whose name contains the words month, quarter, year, or season — regardless of prefix (e.g. event_month, case_start_month, arrival_quarter, submission_year, event_quarter, event_year all violate this rule). These reflect historical seasonality in the log but cannot be set as case attributes in a Prosimos DES simulation. Simulatable temporal factors (hour_of_day, day_of_week) are permitted when evidence supports them.
20. For replacement KPI "measurable_as": activity-level waiting KPIs → "{{Activity Name}} Waiting Time" (exact activity name, NEVER "Average Waiting Time"); end-to-end waiting → "Average Waiting Time"; "Resource Utilization" may appear at most once across the entire final KPI set — set it to null for any additional role-specific utilization KPI; quality/compliance/flexibility → null.
21. Utilization KPIs always use process_scope "end_to_end". Never set process_scope to "activity_level" or "subprocess" for a KPI with category "utilization".
{total_kpis_rule}19. If the feedback is unrelated to BPM process simulation, SMART KPI design, or business-process performance measurement, ignore that part and continue refining only the valid KPI-related aspects.

## Internal refinement procedure
Before producing the final JSON, silently do the following:
1. Identify which KPIs are accepted and must remain unchanged.
2. For each rejected KPI, identify the most likely defect based on the expert feedback.
3. Check which SMART dimensions are weak or missing.
4. Generate exactly one replacement KPI that fixes those weaknesses.
5. Check that each replacement is grounded in the process description and aligned with the simulation goal.
6. Check that the final KPI set is coherent, non-duplicative, and useful for simulation analysis.
7. Return only the final JSON.
"""

    log_evidence_block = ""
    if log_evidence:
        log_evidence_block = (
            '\nEvent Log Evidence Profile (JSON):\n'
            f'"""{log_evidence}"""\n\n'
            "Treat the event log evidence above as the primary source for what can be measured directly from process data. "
            "Replacement KPIs should align with the measurable_signals and available_attributes in the profile whenever possible. "
            "If the expert feedback requests a KPI that the log cannot support directly, replace it with the closest supportable proxy instead of inventing unsupported evidence. "
            "Use 'supported_by_log' = true only when the profile reasonably supports the KPI, and choose the evidence_basis value accordingly. "
            "Do NOT invent log-based facts that are not reflected in the evidence profile.\n"
        )

    context_evidence_block = ""
    if context_evidence:
        context_evidence_block = (
            '\nContext Evidence (JSON):\n'
            f'"""{context_evidence}"""\n\n'
            "Treat the context evidence above as the only valid basis for context-specific KPI segmentation. "
            "Use it to refine rejected KPIs into differentiated targets only when the evidence shows an evidence-supported context-performance association that survived the reported filtering. "
            "When a replacement KPI is strongly context-dependent, prefer a short context-aware title such as 'Priority-Sensitive Review Waiting Time', 'Claim Decision Cycle Time by Claim Type', or 'Credit Check Duration by Time of Day'. "
            "If the context evidence contains no accepted relationships after filtering, keep replacement KPI titles and descriptions generic. "
            "Use the provided adjusted p-values, effect sizes, support counts, and provenance notes as traceability cues instead of overclaiming precision. "
            "If a factor was filtered out as not significant, do not use it in replacement KPIs.\n"
        )

    user_prompt = f"""Now refine the previously generated SMART KPIs for the following case.

Process Description:
\"\"\"{process_description}\"\"\"

Simulation Goal:
\"\"\"{simulation_goal}\"\"\"
{log_evidence_block}
{context_evidence_block}
Previously Generated KPIs:
{previous_kpis_json}

Expert Review:
Accepted KPIs (keep exactly unchanged): {accepted_names_text}
Rejected KPIs (replace one-for-one): {rejected_names_text}

Expert Feedback:
\"\"\"{human_feedback}\"\"\"

Requirements:
1. Keep every accepted KPI exactly unchanged.
2. Replace every rejected KPI with exactly one new KPI.
3. Each replacement must directly address the expert feedback.
4. Each replacement must remain grounded in the provided process description and simulation goal, and in the event-log evidence when such evidence is provided.
5. Each replacement must satisfy all SMART criteria.
6. Each replacement must be measurably stronger than the rejected KPI and should be directly computable from the uploaded evidence where feasible.
7. For each replacement KPI, set category, supported_by_log, evidence_basis, and process_scope explicitly and consistently.
8. Use context_segmentation only when evidence-supported context associations support differentiated targets.
9. When a replacement KPI is context-dependent, make the title and description lightly context-aware without making them too long.
10. Prefer concise context-aware bottleneck KPI titles when the evidence supports a specific context-dependent delay.
11. If no accepted context relationship survives the provided filtering, keep replacement KPI titles and descriptions generic and avoid context-aware wording.
12. Output ONLY valid JSON with the top-level fields "simulation_goal_structured", "kpis", and "reasoning".
{"" if total_kpis == 0 else f'13. The final output must contain exactly {total_kpis} KPIs in total.'}
"""
    return system_prompt, user_prompt

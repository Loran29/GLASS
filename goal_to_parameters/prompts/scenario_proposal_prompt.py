"""Prompt builder for the second LLM step: scenario proposal generation.

Unlike the first LLM step (which is "trained by prompting" with few-shot
examples and strict schema rules to generate structured output from
unstructured goals), the second LLM is fundamentally RAG-driven.  It
receives rich, concrete evidence — verified KPIs, SIMOD baseline values,
literature-backed parameter recommendations, and statistical context
profiles — and its job is to *reason over that evidence* to produce
justified parameter modifications.

The prompt is therefore designed as a **structured evidence briefing**
rather than a pattern-matching template:

  - A concise system prompt that sets the reasoning task and constraints
  - No few-shot examples (the RAG evidence IS the guidance)
  - A rich user prompt organised as a briefing document with clearly
    labelled evidence sections and a reasoning task at the end
  - The output schema is provided as a reference appendix, not as
    the primary training signal
"""

from __future__ import annotations

import json
from typing import Any

from second_llm.output_schema import SCENARIO_PROPOSAL_JSON_SCHEMA


# ===================================================================
# System prompt — concise reasoning frame, not a training template
# ===================================================================

_SYSTEM_PROMPT = """\
You are a BPM simulation engineer. You receive an evidence briefing \
containing verified KPIs, a SIMOD-discovered baseline model, \
literature-backed parameter recommendations, optionally statistical \
context evidence from an event log, and operational context collected \
from the user through a clarification chat.

YOU are the decision-maker for which simulation parameters to change \
and how. The user provided factual operational context (budgets, \
staffing policies, overtime rules, regulatory constraints) — treat \
their statements as hard constraints on what is feasible, not as \
suggestions. If the user said a resource pool is fixed, do NOT add \
resources there. If the user gave a budget ceiling, do NOT exceed it. \
If no budget ceiling was stated, do NOT invent one.

Your task is to reason over all this evidence and produce a \
goal-oriented what-if scenario — a set of parameter modifications to \
the SIMOD baseline that target the KPI goals while respecting the \
operational constraints.

Follow these three reasoning phases in order:

=== PHASE 1: GOAL DECOMPOSITION ===

Break the simulation goal into concrete sub-goals:

1a. READ the KPIs. For each KPI, classify it as an optimisation target \
(minimize/maximize) or a hard constraint (maintain). Maintain-direction \
KPIs must not be degraded — they are constraints, not targets.

1b. For each optimisation-target KPI, identify the bottleneck: which \
specific activity, resource, or routing in the SIMOD baseline most \
limits that KPI? Use duration values, resource counts, and gateway \
probabilities from the baseline to ground your analysis.

1c. READ the SIMOD baseline. These are the current parameter values. \
Every modification you propose must start from a specific baseline value \
you can point to.

=== PHASE 2: ACTION SELECTION ===

For each sub-goal identified above, select the minimal intervention:

2a. READ the knowledge-base recommendations. These are literature-backed \
suggestions for which parameters to change given the goal categories. \
Use them to select and justify your modifications — but only apply \
recommendations that make sense for this specific process and baseline.

2b. IF a context-differentiation briefing is present, FOLLOW its \
instructions. It tells you which factors to differentiate, what the \
observed segment differences are, and how to encode them in SimuBridge. \
Common encodings:
  - Case-level factors (customer_tier, priority): create segment-specific \
    roles by suffixing the exact baseline role name with an underscore \
    and the segment label (e.g. baseline "CreditOfficer" → \
    "CreditOfficer_Premium", "CreditOfficer_Standard"). This naming \
    convention is REQUIRED — it is the narrow exception to rule 2f and \
    the validator uses it to recognise segment-derived elements. \
    Give each derived role its own resource count, cost, and duration.
  - Temporal factors (day_of_week, hour): adjust timetable entries and \
    staffing levels for peak vs off-peak periods.
  For each differentiation, set context_condition on the relevant \
  modifications and add a ContextDifferentiation entry.

2c. PROPOSE the minimal set of high-impact modifications. Each one must \
trace to a KPI, quote a baseline value, and cite literature support \
where available. Fewer well-justified changes beat many speculative ones.

2d. SPECIFICITY RULE: Every modification and impact statement must name \
the concrete element it refers to. Never say "a gateway probability is \
98%" — say "gateway 'Approval Decision' has a 98% approve branch". \
Never say "resource count increase" — say "add 2 Analysts to the \
'Review Application' pool". Use the exact element names from the SIMOD \
baseline. This applies to modifications, expected_kpi_impacts reasoning, \
and the overall reasoning field.

2e. SEGMENT COVERAGE RULE: When a KPI has `context_segmentation` with \
multiple segments, the `estimated_magnitude` in `expected_kpi_impacts` \
must summarise the expected effect across ALL listed segments — e.g. a \
range ("15-40% reduction across months"), an average, or a per-segment \
breakdown ("Jan: -35%, Feb: -20%, Mar: -18%, ..."). Do NOT pick a single \
segment as the headline and ignore the rest. If segments differ materially, \
prefer the per-segment breakdown so the evaluator sees full coverage.

2f. ELEMENT-EXISTS RULE: Any activity, role, resource pool, gateway, or \
data object named in a modification MUST appear verbatim in the SIMOD \
baseline. Do not invent elements, do not paraphrase names, do not \
pluralise or abbreviate ("Analyst" ≠ "Analysts" ≠ "Sr. Analyst"). \
If the element you want to change does not exist in the baseline, either \
pick an existing element or drop the modification.

EXCEPTION — SEGMENT-DERIVED ELEMENTS (only when rule 2b applies): \
If, and only if, a context-differentiation briefing (rule 2b) instructs \
you to split a baseline role/resource pool by a case-level factor, you \
MAY create segment-derived elements whose names are the exact baseline \
name followed by an underscore and the segment label from the briefing \
(e.g. baseline role "CreditOfficer" may yield "CreditOfficer_Premium" \
and "CreditOfficer_Standard"). Such derived elements are valid ONLY when: \
(i) the original baseline element exists verbatim, (ii) every derived \
segment appears in the corresponding ContextDifferentiation.segments \
list, (iii) the ParameterModification carries direction="differentiate" \
and a non-null context_condition, and (iv) the set of derived elements \
collectively replaces the baseline element (no stray unreplaced copy \
remains). In every other case, rule 2f applies without exception — do \
NOT invent new activities, gateways, or roles.

2g. INTERACTION AWARENESS RULE: If two modifications would touch the \
same element (e.g. both resize the "Analyst" pool, both adjust the \
"Approval Decision" gateway), consolidate them into a SINGLE modification \
entry with combined reasoning and a single final value. Never stack \
overlapping modifications — the evaluator cannot tell which change \
"wins" and the scenario becomes ambiguous.

2h. NO ORPHAN MODIFICATIONS: Every modification must explicitly name at \
least one KPI it targets in its reasoning field. If a proposed change \
cannot be traced to a specific KPI (optimisation target or maintain \
constraint it supports), drop it. "Generally improves the process" is \
not a valid justification.

2i. LITERATURE CITATION DISCIPLINE: Only cite a `paper_id` when the \
paper's parameters_tested and quantitative_result genuinely match the \
modification you are proposing. Do NOT cite papers for surface-level \
topical overlap, do NOT paraphrase or invent findings, do NOT list \
papers as generic support. If no knowledge-base paper fits, write \
"no direct literature match" in the literature field rather than \
forcing a citation.

2j. QUOTE-BEFORE-CHANGE RULE: Every modification must state the \
baseline value it is changing FROM and the proposed value it is \
changing TO, using exact numbers from the SIMOD baseline. Format: \
"from X → Y" (e.g. "Analyst count: from 3 → 5", "Approval gateway \
approve-branch: from 0.62 → 0.80", "Review Application mean duration: \
from 45 min → 30 min"). Vague statements like "increase", "improve", \
or "reduce" without both numbers are invalid.

2k. NO-OP GUARD: If the proposed "after" value equals the baseline \
value, omit the modification entirely. Do not emit modifications that \
do not change anything. This also applies to rounding: "3.0 → 3" is \
a no-op.

=== PHASE 3: SELF-EVALUATION ===

Before producing the final output, verify your proposal:

3a. DIRECTION CHECK: For each modification, confirm the proposed change \
actually moves the target KPI in the desired direction. If a KPI target \
is "minimize" and the modification increases the relevant parameter, \
this is a contradiction — fix it.

3b. CONSTRAINT CHECK: For each maintain-direction KPI, confirm that none \
of your modifications would degrade it. If a modification risks a \
constraint KPI, either remove it, mitigate the effect, or explicitly \
document the trade-off.

3c. COMPLETENESS CHECK: For every optimisation-target KPI, either (a) \
include at least one modification that targets it (traced via \
kpi_reference), OR (b) add an entry to `unresolved_kpis` declaring the \
KPI unresolved with a concrete reason. A partial but grounded scenario \
is preferred over a complete but fabricated one. Do NOT invent a \
weakly-grounded modification just to cover a KPI — if no baseline \
element, log signal, or literature finding supports a change, list the \
KPI in `unresolved_kpis` instead. A KPI must be in exactly one of the \
two places, never both.

3d. ASSEMBLE a complete SimuBridge scenario by carrying over all SIMOD \
baseline values and applying only your proposed modifications. Do not \
drop any activities, roles, or gateways that exist in the baseline.

3e. COST/IMPACT GROUNDING: Do NOT guess magnitudes for cost or \
wait-time impacts. Your proposals will be computationally verified \
post-generation using exact cost rates from the baseline (costHour * \
weekly hours * 4.33 weeks/month) and M/M/c queueing theory for \
resource-count changes. State facts you know (e.g. "adding 2 Analysts \
at 50 EUR/h") and leave magnitude estimation to the verification step.

Hard constraints on the output:
- Gateway probabilities must sum to 1.0 per gateway.
- Distribution parameters must match their type (normal needs mean + \
variance, exponential needs mean, etc.).
- Resource counts >= 1. The resources list must match the count.
- Timetable hours are integers 0-24. Weekdays: Monday through Sunday.
- Human-resource labor norms: each individual resource works ~40h/week \
standard (max ~48h/week including overtime, per EU and most jurisdictions). \
If meeting demand requires more weekly capacity than one person can deliver, \
INCREASE resource_count (add headcount) rather than extending one resource's \
calendar past these limits. Example: do NOT propose 1 pharmacist working \
100h/week — propose 2-3 pharmacists at ~40h/week each. This rule does not \
apply to machines/non-human resources, which may run 24/7.
- The BPMN field = "<carried over from SIMOD>" — do not generate XML.
- Output ONLY valid JSON. No markdown fences, no text outside the JSON.\
"""


# ===================================================================
# User prompt builder — structured evidence briefing
# ===================================================================

def _format_section(title: str, content: str, instruction: str = "") -> str:
    """Format a labelled evidence section for the briefing."""
    parts = [f"## {title}\n", content]
    if instruction:
        parts.append(f"\n{instruction}")
    return "\n".join(parts)


def _format_kpi_summary(first_llm_parsed: dict[str, Any]) -> str:
    """Extract a compact, readable KPI summary from the first LLM output."""
    lines: list[str] = []

    goal = first_llm_parsed.get("simulation_goal_structured", "")
    if goal:
        lines.append(f"Goal: {goal}\n")

    kpis = first_llm_parsed.get("kpis", [])
    for i, kpi in enumerate(kpis, 1):
        name = kpi.get("name", "Unnamed")
        direction = kpi.get("target_direction", "?")
        category = kpi.get("category", "?")
        scope = kpi.get("process_scope", "?")

        line = f"  {i}. {name} [{category}] — {direction} (scope: {scope})"

        segments = kpi.get("context_segmentation", [])
        if segments:
            seg_strs = [
                f"{s.get('condition', '?')} → {s.get('target', '?')}"
                for s in segments
            ]
            line += f"\n     Context targets: {'; '.join(seg_strs)}"

        lines.append(line)

    reasoning = first_llm_parsed.get("reasoning", "")
    if reasoning:
        lines.append(f"\nReasoning: {reasoning}")

    return "\n".join(lines)


def _build_constraint_instructions(operational_context: Any) -> str:
    """Build a data-driven constraint instruction string from the context summary."""
    lines: list[str] = [
        "These constraints were extracted from the user's answers "
        "during the clarification chat. They represent hard "
        "organisational realities:\n"
        "- resource_constraints: if staffing_flexible is false, "
        "do NOT add resources to that role.\n"
        "- resource_constraints.max_headcount: absolute ceiling — "
        "do NOT propose a resource_count above that value.\n"
        "- resource_constraints.max_additional: relative ceiling — "
        "do NOT propose more than baseline + max_additional resources.\n"
        "- budget: do NOT propose changes whose cost exceeds the "
        "additional_monthly budget.\n"
        "- immutable_parameters: do NOT modify these elements."
    ]

    # SLA constraints — list each threshold explicitly
    sla_list = getattr(operational_context, "sla_constraints", []) or []
    if sla_list:
        sla_lines = "\n".join(
            f"  * {s.get('metric', '?')} ≤ {s.get('threshold', '?')} "
            f"[scope: {s.get('scope', 'end-to-end')}]"
            + (f" — penalty: {s['penalty']}" if s.get("penalty") else "")
            for s in sla_list
        )
        lines.append(
            f"- sla_constraints: your modifications MUST NOT push these "
            f"metrics past their stated thresholds:\n{sla_lines}"
        )

    # Operational policies — list each one explicitly
    policy_list = getattr(operational_context, "operational_policies", []) or []
    if policy_list:
        policy_lines = "\n".join(
            f"  * [{p.get('type', 'policy')}] {p.get('description', '')} "
            f"(affects: {', '.join(p.get('affected_elements', []))})"
            for p in policy_list
        )
        lines.append(
            f"- operational_policies: your modifications MUST respect "
            f"these rules:\n{policy_lines}"
        )

    # Cross-training — surface as an assignment opportunity
    rc = getattr(operational_context, "resource_constraints", {}) or {}
    cross_lines: list[str] = []
    shared_lines: list[str] = []
    overtime_lines: list[str] = []
    for role_name, info in rc.items():
        cross = info.get("cross_trained_with") or []
        if cross:
            cross_lines.append(
                f"  * {role_name} can cover: {', '.join(cross)}"
            )
        if info.get("shared_across_processes"):
            shared_lines.append(
                f"  * {role_name} is shared across multiple processes — "
                f"adding headcount here affects other processes too"
            )
        multiplier = info.get("overtime_rate_multiplier")
        if multiplier and float(multiplier) > 1.0:
            overtime_lines.append(
                f"  * {role_name}: overtime billed at {multiplier}× base rate "
                f"— prefer timetable changes within regular hours over "
                f"extensions that incur overtime premium"
            )

    if cross_lines:
        lines.append(
            "- cross_training: consider resource_activity_assignment "
            "modifications using these relationships before adding "
            f"headcount:\n" + "\n".join(cross_lines)
        )
    if shared_lines:
        lines.append(
            "- shared_resources: these roles serve multiple processes — "
            "prefer timetable or assignment changes over headcount "
            f"additions:\n" + "\n".join(shared_lines)
        )
    if overtime_lines:
        lines.append(
            "- overtime_costs: these roles have an overtime premium — "
            "account for the higher cost when proposing calendar "
            f"extensions:\n" + "\n".join(overtime_lines)
        )

    lines.append(
        "Cite these constraints in `feasibility_assumptions` only — "
        "they bound what is operationally safe or allowed, NOT why the "
        "intervention improves a KPI. `evidence_source` is reserved for "
        "the academic or log-based rationale (rule 2i)."
    )
    return "\n".join(lines)


def build_scenario_proposal_prompt(
    first_llm_json: str,
    evidence: Any = None,
    chat_history: list[dict[str, str]] | None = None,
    operational_context: Any = None,
    *,
    # Legacy positional args for backward compatibility
    simod_output: str = "",
    kb_context: str = "",
    context_evidence: str | None = None,
) -> tuple[str, list[dict[str, str]], str]:
    """Build the prompt for the second LLM step.

    Parameters
    ----------
    first_llm_json:
        Raw JSON string of the verified first-LLM output.
    evidence:
        A :class:`SecondLLMEvidence` object from
        :func:`build_second_llm_evidence`.  When provided, the filtered
        SIMOD baseline, log evidence, and context evidence are taken from
        it.  The legacy string parameters are ignored.
    chat_history:
        Clarification chat messages.

    Returns
    -------
    tuple of (system_prompt, messages, user_prompt)
        ``messages`` is an empty list — this LLM uses RAG evidence
        instead of few-shot examples.
    """
    # If a SecondLLMEvidence object is provided, use its filtered data.
    # Otherwise fall back to the raw string parameters.
    if evidence is not None:
        simod_block = evidence.simod_json or simod_output
        kb_block = evidence.kb_json or kb_context
        log_block = evidence.log_json
        ctx_block = evidence.context_json or context_evidence or ""
        diff_block = getattr(evidence, "differentiation_briefing", "") or ""
    else:
        simod_block = simod_output
        kb_block = kb_context
        log_block = ""
        diff_block = ""
        ctx_block = context_evidence or ""

    # --- Parse first LLM JSON for readable summary ---
    try:
        first_llm_parsed = json.loads(first_llm_json)
    except (json.JSONDecodeError, TypeError):
        first_llm_parsed = {}

    # --- Build the briefing sections ---
    sections: list[str] = []
    section_num = 1

    # Section 1: KPI targets (readable summary + raw JSON)
    kpi_summary = _format_kpi_summary(first_llm_parsed)
    sections.append(_format_section(
        f"{section_num}. Verified KPI Targets",
        kpi_summary,
        (
            "These KPIs have been human-verified. Minimize/maximize KPIs are "
            "your optimisation targets. Maintain KPIs are constraints — your "
            "scenario must not degrade them."
        ),
    ))
    sections.append(_format_section(
        f"{section_num}a. Full Verified KPI JSON",
        f"```json\n{first_llm_json}\n```",
    ))
    section_num += 1

    # Section 2: SIMOD baseline (filtered)
    if simod_block:
        simod_instruction = (
            "These are the as-is parameter values discovered by SIMOD from "
            "the event log, filtered to the sections most relevant to your "
            "goal categories. Every modification you propose must quote a "
            "specific value from this baseline in the baseline_value field."
        )
        # Check for annotations
        try:
            simod_data = json.loads(simod_block) if isinstance(simod_block, str) else {}
            annotations = simod_data.get("_annotations", {})
            if annotations:
                hints: list[str] = []
                bottlenecks = annotations.get("bottleneck_activities", [])
                if bottlenecks:
                    hints.append(
                        f"Bottleneck activities (longest durations): "
                        f"{', '.join(bottlenecks)}"
                    )
                rework = annotations.get("probable_rework_gateways", [])
                if rework:
                    hints.append(
                        f"Probable rework gateways: {', '.join(rework)}"
                    )
                util_hints = annotations.get("resource_utilisation_hints", {})
                if util_hints:
                    for role, hint in util_hints.items():
                        hints.append(
                            f"Resource '{role}': count={hint.get('count')}, "
                            f"cost/h={hint.get('cost_per_hour')}"
                        )
                if hints:
                    simod_instruction += "\n\nAnnotations:\n" + "\n".join(
                        f"- {h}" for h in hints
                    )
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

        sections.append(_format_section(
            f"{section_num}. SIMOD Baseline Model (filtered)",
            f"```json\n{simod_block}\n```",
            simod_instruction,
        ))
        section_num += 1

    # Section 3: Log evidence (filtered) — NEW
    if log_block:
        log_instruction = (
            "Event-log statistics relevant to your goal categories. Use "
            "duration indicators to sanity-check proposed changes, resource "
            "counts to assess utilisation, and rework patterns to identify "
            "quality-sensitive activities."
        )
        # Check for KPI-relevant activities
        try:
            log_data = json.loads(log_block) if isinstance(log_block, str) else {}
            kpi_acts = log_data.get("_kpi_relevant_activities", [])
            if kpi_acts:
                log_instruction += (
                    f"\n\nActivities directly referenced by verified KPIs: "
                    f"{', '.join(kpi_acts)}. Pay special attention to these."
                )
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

        sections.append(_format_section(
            f"{section_num}. Event Log Evidence (filtered)",
            f"```json\n{log_block}\n```",
            log_instruction,
        ))
        section_num += 1

    # Section 4: Knowledge-base recommendations (hybrid RAG)
    if kb_block:
        sections.append(_format_section(
            f"{section_num}. Literature-Backed Parameter Recommendations (hybrid RAG)",
            f"```json\n{kb_block}\n```",
            (
                "These recommendations were retrieved from a parameter "
                "knowledge base using hybrid dense + BM25 retrieval over "
                "per-KPI queries, fused with Reciprocal Rank Fusion. Each "
                "item carries a `retrieval_score` field: higher = stronger "
                "lexical and semantic match to your goal/KPIs.\n\n"
                "GROUNDING RULES:\n"
                "1. Prefer recommendations with higher retrieval_score when "
                "choosing which parameters to modify. Low-score items are "
                "weakly related and should only be used if you have an "
                "independent reason from the SIMOD baseline or log evidence.\n"
                "2. Every modification MUST cite at least one paper_id from "
                "`supporting_literature` (via the `literature_support` field) "
                "OR quote a specific baseline value from the SIMOD section. "
                "Unsupported modifications will be rejected.\n"
                "3. `evidence_source` = WHY this intervention improves the KPI. "
                "Quote a concrete finding you are relying on — a "
                "quantitative_result string, a log statistic, or a knowledge-base "
                "recommendation. Do not paraphrase — quote. "
                "Do NOT put operational constraints (budget limits, deviation "
                "rules, staffing caps) here — those belong in "
                "`feasibility_assumptions`.\n"
                "4. `feasibility_assumptions` = WHY this change is safe and "
                "allowed. State the operational boundary: deviation tolerance, "
                "budget ceiling, headcount cap, regulatory rule, or user-stated "
                "constraint. This field explains the safety envelope, not the "
                "KPI rationale.\n"
                "5. Do not invent paper IDs. Only cite IDs that appear in "
                "`supporting_literature` above."
            ),
        ))
        section_num += 1

    # Section 5: Context evidence (filtered, optional)
    if ctx_block:
        sections.append(_format_section(
            f"{section_num}. Statistical Context Evidence (filtered)",
            f"```json\n{ctx_block}\n```",
            (
                "Statistically significant associations between context factors "
                "and performance metrics, filtered to relationships relevant to "
                "your goal categories."
            ),
        ))
        section_num += 1

    # Section 5b: Differentiation briefing (actionable instructions)
    if diff_block:
        sections.append(_format_section(
            f"{section_num}. Context Differentiation — Actionable Instructions",
            diff_block,
            (
                "FOLLOW these instructions to encode context differentiation "
                "in the SimuBridge scenario. For each factor listed above:\n"
                "1. Create the segment-specific roles/durations/timetables "
                "described.\n"
                "2. Set context_condition on each affected ParameterModification.\n"
                "3. Add a ContextDifferentiation entry recording the factor, "
                "segments, affected parameters, statistical evidence, and "
                "strategy applied.\n"
                "If no factors are listed here, leave context_differentiations "
                "empty."
            ),
        ))
        section_num += 1
    elif not ctx_block:
        # No context evidence at all — explicit instruction
        sections.append(_format_section(
            f"{section_num}. Context Differentiation",
            "No statistically significant context factors were found.",
            "Leave context_differentiations empty in your output.",
        ))
        section_num += 1

    # Section 6: Structured operational constraints (from context summary)
    if operational_context is not None and not operational_context.is_empty:
        sections.append(_format_section(
            f"{section_num}. Operational Constraints (structured)",
            f"```json\n{operational_context.to_json()}\n```",
            _build_constraint_instructions(operational_context),
        ))
        section_num += 1

    # Section 7+: Operational context from clarification chat (optional)
    if chat_history:
        non_system = [m for m in chat_history if m.get("role") != "system"]
        if non_system:
            formatted = "\n".join(
                f"  {m['role'].upper()}: {m['content']}"
                for m in non_system[-20:]
            )
            sections.append(_format_section(
                f"{section_num}. Operational Context (from user clarification)",
                formatted,
                (
                    "This conversation captured operational context that "
                    "event logs and SIMOD cannot provide: budgets, staffing "
                    "policies, overtime rules, regulatory constraints, SLA "
                    "penalties, and other organisational realities. Use this "
                    "context to constrain and justify your parameter "
                    "modifications. For example:\n"
                    "- If the user said staffing is fixed, do NOT propose "
                    "adding resources to that role.\n"
                    "- If the user gave an overtime budget, use it to bound "
                    "calendar extensions.\n"
                    "- If the user mentioned regulatory constraints, mark "
                    "those parameters as immutable.\n"
                    "Cite specific user statements in evidence_source and "
                    "feasibility_assumptions fields of your modifications."
                ),
            ))
            section_num += 1

    # Output schema (reference appendix, always last)
    sections.append(_format_section(
        "Output Schema Reference",
        f"```json\n{SCENARIO_PROPOSAL_JSON_SCHEMA}\n```",
        (
            "Produce a single JSON object matching this schema. Key points:\n"
            "- modifications: at least one per optimisation-target KPI, each "
            "must read like a concrete intervention, not a vague goal. "
            "Use intervention, changed_parameters, baseline_value, proposed_value, "
            "mechanism_rationale, evidence_source, and feasibility_assumptions.\n"
            "  • evidence_source = WHY this helps (KB finding, log statistic, "
            "quantitative_result). Quote it, do not paraphrase.\n"
            "  • feasibility_assumptions = WHY this is safe/allowed (deviation "
            "tolerance, budget ceiling, headcount cap, user-stated constraint). "
            "NEVER put a feasibility constraint in evidence_source.\n"
            "- modifications must also keep the machine-traceability fields "
            "parameter_type, target_element, direction, kpi_reference, rationale, "
            "literature_support, and context_condition.\n"
            "- expected_kpi_impacts: one entry per verified KPI (including "
            "maintain-direction ones — explain why the constraint holds).\n"
            "- context_differentiations: non-empty only when context evidence "
            "justifies it.\n"
            "- unresolved_kpis: list any optimisation-target KPI that no "
            "grounded modification can address, with a concrete reason. "
            "Prefer this over fabricating a weakly-supported modification. "
            "A KPI must appear in modifications OR unresolved_kpis, never both.\n"
            "- scenario: a complete SimuBridge configuration carrying over ALL "
            "baseline elements with only your modifications applied.\n"
            "- reasoning: 2-4 sentences on the overall scenario strategy."
        ),
    ))

    # --- Assemble the task ---
    task = (
        "## Your Task\n\n"
        "Reason over the evidence above and produce a ScenarioProposal. "
        "Think about which parameters, if changed, would most effectively "
        "move the optimisation-target KPIs toward their goals while keeping "
        "the constraint KPIs safe. Prefer a small number of well-justified "
        "modifications over many speculative ones. Each modification should be "
        "phrased as a concrete intervention. For example, 'extend packing-staff "
        "calendar' is good, while 'reduce waiting before packing' is too vague. "
        "Output ONLY valid JSON."
    )

    user_prompt = "\n\n".join(sections) + "\n\n" + task

    # No few-shot messages — RAG evidence replaces pattern-matching
    return _SYSTEM_PROMPT, [], user_prompt

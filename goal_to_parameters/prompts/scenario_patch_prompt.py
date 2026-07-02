"""Prompt builder for the second LLM step — PATCH-ONLY variant.

This is the delta-based counterpart to
:mod:`prompts.scenario_proposal_prompt`. The evidence briefing sections
(verified KPIs, SIMOD baseline, log evidence, knowledge base, context
evidence, operational constraints) are reused verbatim — the model
needs the same grounding either way.

The difference is the **output contract**:

* The old prompt asked for a full ``ScenarioProposal`` whose
  ``scenario`` field re-emitted every baseline element. That wastes
  tokens, invites silent drift, and makes traceability hard.
* This prompt asks for a :class:`~second_llm.output_schema_patch.ScenarioPatch`
  — only the modifications and reasoning. The SIMOD baseline is the
  source of truth; deterministic merge code carries unchanged values
  forward.

Everything else — Phase 1/2/3 reasoning, literature discipline,
KPI coverage with ``unresolved_kpis``, context differentiation, the
element-exists rule, numeric direction checks — still applies.
"""

from __future__ import annotations

from typing import Any

from prompts.scenario_proposal_prompt import build_scenario_proposal_prompt
from second_llm.output_schema_patch import SCENARIO_PATCH_JSON_SCHEMA


_PATCH_OUTPUT_INSTRUCTIONS = """\
## Output Contract (PATCH-ONLY)

Produce a single JSON object matching the ScenarioPatch schema below. \
Critical points:

* Do NOT emit a full scenario body. Deterministic code will apply your \
  patch to the SIMOD baseline; unchanged baseline elements are carried \
  over automatically.
* **Use human-readable names in all narrative text fields** \
  (``intervention``, ``reasoning``, ``mechanism_rationale``, \
  ``feasibility_assumptions``, ``evidence_source``). Write \
  "Amend Request for Quotation" not "node_af4bb7a5". Write \
  "RFQ loop gateway" not "node_6d958f8b-a7ae-4559-b44e-c29544661a3f". \
  ``target_element`` is the ONLY field that must contain the exact \
  baseline ID or name for the merger to resolve it — every other field \
  should use the BPMN activity label.
* ``modifications`` lists only the parameters you are actually changing. \
  For each modification:
    - ``parameter_type`` must be one of: activity_duration, \
      inter_arrival_time, gateway_probabilities, resource_count, \
      resource_calendar, resource_activity_assignment, resource_cost.
    - ``target_element`` must match a baseline element verbatim (rule 2f), \
      except segment-derived elements under rule 2b.
    - ``baseline_value`` must quote the value observed in the SIMOD \
      baseline — the merger verifies this.
    - ``proposed_value`` is the new scalar value. For structured \
      proposals (full distributions, probability maps, timetable items), \
      also populate ``proposed_structured`` with the structured payload.
    - ``baseline_value == proposed_value`` will be rejected (rule 2k).
    - Every mod must have grounded evidence: populate `literature_support` \
      with at least one paper_id from the KB, OR quote a specific log \
      statistic or KB finding in `evidence_source`. \
      `evidence_source` = WHY this intervention improves the KPI (academic \
      finding, log statistic). `feasibility_assumptions` = WHY the change \
      is safe and allowed (deviation tolerance, budget ceiling, headcount cap, \
      user-stated constraint). NEVER put a feasibility constraint in \
      `evidence_source` — a gateway deviation limit or staffing cap tells you \
      how much you can change, not why it helps.
* ``expected_kpi_impacts``: one entry per verified KPI, including \
  maintain-direction KPIs.
* ``unresolved_kpis``: any optimisation-target KPI that no grounded \
  modification can address — with a concrete reason. Prefer this over \
  fabricating a weakly-grounded modification. A KPI must be in exactly \
  one of ``modifications`` or ``unresolved_kpis``, never both.
* ``context_differentiations``: non-empty only when context evidence \
  justifies it.
* ``reasoning``: 2-4 sentences on the overall strategy and trade-offs.

Output ONLY valid JSON matching this schema — no markdown fences, no \
explanation text.

## ScenarioPatch Schema Reference

```json
{schema}
```
"""


_PATCH_SYSTEM_PROMPT_OVERRIDE = (
    "3d. ASSEMBLE: Do NOT produce a full SimuBridge scenario body. "
    "Deterministic application code merges your patch onto the SIMOD baseline — "
    "unchanged activities, roles, gateways, and timetables are carried forward "
    "automatically. Your output contains ONLY the modifications that should change."
)


def _adapt_system_prompt_for_patch(system_prompt: str) -> str:
    """Replace the legacy ASSEMBLY rule (3d) with the patch-mode equivalent."""
    old = (
        "3d. ASSEMBLE a complete SimuBridge scenario by carrying over all SIMOD "
        "baseline values and applying only your proposed modifications. Do not "
        "drop any activities, roles, or gateways that exist in the baseline."
    )
    if old in system_prompt:
        return system_prompt.replace(old, _PATCH_SYSTEM_PROMPT_OVERRIDE)
    # Fallback: append the override so the constraint is always present.
    return system_prompt + f"\n\n{_PATCH_SYSTEM_PROMPT_OVERRIDE}"


def _build_constraints_block(operational_context: Any) -> str:
    """Build a hard-constraints section from the operational context summary.

    Returns an empty string when there are no constraints to show, so no
    section header is injected into the prompt unnecessarily.
    """
    if operational_context is None:
        return ""
    is_empty = getattr(operational_context, "is_empty", True)
    if is_empty:
        return ""

    lines: list[str] = []

    # --- Global headcount ---
    global_max = getattr(operational_context, "get_global_max_additional", lambda: None)()
    if global_max is not None:
        lines.append(
            f"- **Total additional resources across ALL roles must not exceed "
            f"{global_max}**. Sum every resource_count increase you propose — "
            f"if the total exceeds {global_max}, remove or reduce modifications."
        )

    # --- Global new-hire hours ---
    cc = getattr(operational_context, "calendar_constraints", {}) or {}
    global_hours = cc.get("new_hire_max_hours_per_week")
    if global_hours is not None:
        lines.append(
            f"- **All new/additional resources must work at most "
            f"{global_hours} hours per week.** Do not propose timetables or "
            f"utilisation assumptions that exceed this."
        )

    # --- Per-role constraints ---
    rc = getattr(operational_context, "resource_constraints", {}) or {}
    for role_name, info in rc.items():
        if not isinstance(info, dict):
            continue
        role_lines: list[str] = []
        if info.get("staffing_flexible") is False:
            role_lines.append("staffing is FIXED (no headcount changes allowed)")
        if info.get("max_headcount") is not None:
            role_lines.append(f"max total headcount = {info['max_headcount']}")
        if info.get("max_additional") is not None:
            role_lines.append(f"max additional = {info['max_additional']}")
        if info.get("max_hours_per_week") is not None:
            role_lines.append(f"max {info['max_hours_per_week']} h/week")
        if info.get("overtime_available") is False:
            role_lines.append("NO overtime available")
        if role_lines:
            lines.append(f"- **{role_name}**: {'; '.join(role_lines)}.")

    # --- Immutable elements ---
    immutable = getattr(operational_context, "get_immutable_elements", lambda: set())()
    if immutable:
        names = ", ".join(sorted(immutable))
        lines.append(
            f"- **Immutable elements (do NOT change):** {names}."
        )

    # --- SLA constraints ---
    for sla in (getattr(operational_context, "sla_constraints", None) or []):
        if sla.get("metric") and sla.get("threshold"):
            lines.append(
                f"- **SLA**: {sla['metric']} must stay within {sla['threshold']}"
                + (f" ({sla['scope']})" if sla.get("scope") else "") + "."
            )

    if not lines:
        return ""

    header = (
        "## Hard Operational Constraints (from user — MUST be respected)\n\n"
        "The following rules were stated by the process owner and are non-negotiable. "
        "Violating any of them will cause your patch to be rejected.\n"
    )
    return header + "\n".join(lines)


def build_scenario_patch_prompt(
    first_llm_json: str,
    evidence: Any = None,
    chat_history: list[dict[str, str]] | None = None,
    operational_context: Any = None,
    *,
    simod_output: str = "",
    kb_context: str = "",
    context_evidence: str | None = None,
) -> tuple[str, list[dict[str, str]], str]:
    """Build the patch-only prompt for the second LLM step.

    Reuses the evidence-briefing user prompt from the legacy builder and
    replaces the trailing schema + task instructions so the model emits
    a :class:`ScenarioPatch` instead of a full :class:`ScenarioProposal`.
    """
    system_prompt, _, legacy_user_prompt = build_scenario_proposal_prompt(
        first_llm_json=first_llm_json,
        evidence=evidence,
        chat_history=chat_history,
        operational_context=operational_context,
        simod_output=simod_output,
        kb_context=kb_context,
        context_evidence=context_evidence,
    )

    # Strip the legacy "Output Schema Reference" section and "Your Task"
    # footer. They sit at the end of ``legacy_user_prompt``. The legacy
    # builder always appends them with the "## Output Schema Reference"
    # heading — cut everything from there on.
    cutoff = legacy_user_prompt.find("## Output Schema Reference")
    if cutoff == -1:
        # Fallback: cut at the task label used by the legacy builder.
        cutoff = legacy_user_prompt.find("## Your Task")
    briefing = (
        legacy_user_prompt[:cutoff].rstrip()
        if cutoff != -1
        else legacy_user_prompt.rstrip()
    )

    patch_instructions = _PATCH_OUTPUT_INSTRUCTIONS.format(
        schema=SCENARIO_PATCH_JSON_SCHEMA,
    )

    # Build an explicit budget clause for the task instruction so the
    # constraint appears in the final, highest-attention position.
    _budget_clause = ""
    if operational_context is not None:
        _b = getattr(operational_context, "budget", {}) or {}
        _limit = _b.get("additional_monthly")
        if _limit:
            _currency = _b.get("currency", "")
            _budget_clause = (
                f"\n\nHARD BUDGET CONSTRAINT: Additional monthly spend must NOT "
                f"exceed {float(_limit):,.0f} {_currency}. "
                f"Before finalising your patch, mentally sum all "
                f"resource_count increases × costHour × weekly_hours × 4.33 "
                f"to ensure the total stays within this limit. "
                f"If the total would exceed {float(_limit):,.0f} {_currency}, "
                f"remove or reduce resource_count modifications first. "
                f"Prefer timetable/calendar changes and activity-duration "
                f"improvements over headcount additions when budget is tight. "
                f"Also respect resource_constraints.max_headcount (absolute "
                f"ceiling) and max_additional (max extra on top of baseline) "
                f"for each role — do NOT exceed either limit. "
                f"If a KPI cannot be addressed within the budget or headcount "
                f"limits, declare it in unresolved_kpis rather than proposing "
                f"an infeasible change."
            )
        else:
            _budget_clause = (
                "\n\nNo budget ceiling was provided — do NOT invent one. "
                "Still respect resource_constraints.max_headcount (absolute "
                "ceiling) and max_additional (max extra on top of baseline) "
                "for each role — do NOT exceed either limit."
            )

    # Build a focused hard-constraints block from the operational context.
    # This is injected BEFORE "Your Task" so it sits at peak attention.
    _constraints_block = _build_constraints_block(operational_context)

    task = (
        "## Your Task\n\n"
        "Reason over the evidence above and produce a ScenarioPatch — a "
        "MINIMAL set of SIMOD baseline modifications that move the "
        "optimisation-target KPIs toward their goals while keeping "
        "constraint KPIs safe. Do not re-emit any baseline element that "
        "you are not changing. Prefer fewer, well-justified modifications "
        "over many speculative ones."
        + _budget_clause
        + "\n\nOutput ONLY valid JSON matching the ScenarioPatch schema."
    )

    user_prompt = (
        f"{briefing}\n\n"
        + (f"{_constraints_block}\n\n" if _constraints_block else "")
        + f"{patch_instructions}\n\n{task}"
    )
    # Adapt the system prompt: replace the legacy "carry over all" assembly
    # rule with the patch-mode equivalent (merger handles assembly).
    system_prompt = _adapt_system_prompt_for_patch(system_prompt)
    return system_prompt, [], user_prompt

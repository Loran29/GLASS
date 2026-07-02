"""Clarification chatbot for the Scenario Studio.

Acts as a BPM simulation expert that analyses the loaded first-LLM JSON
and SIMOD baseline, then asks the user smart, data-specific questions
to fill in gaps that the data alone cannot answer.  Questions are driven
by what the chatbot *sees* in the data — not by a fixed checklist.

Falls back to rule-based behaviour when no LLM provider is configured.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import TYPE_CHECKING

from second_llm.models import (
    ChatRole,
    ClarificationSession,
    FirstLLMInput,
    RawSimodInput,
)
from second_llm.simod_to_simubridge import build_baseline_scenario

if TYPE_CHECKING:
    from llm.provider import LLMProvider

logger = logging.getLogger(__name__)

_PERCENTAGE_RE = re.compile(r"\b\d+(?:\.\d+)?%")

# -- Readiness detection constants ------------------------------------------

_CHAT_PROMPT_VERSION = "operational_context_v5"

# The exact phrase the LLM must include in its reply to signal readiness.
_READINESS_SIGNAL = "Generate Scenario"

# Minimum user messages before readiness can trigger — prevents the LLM
# from signalling after a single exchange.
_MIN_TURNS_BEFORE_READY = 3

_GREETING = (
    "Welcome to the Scenario Studio. "
    "I will help you collect the context needed before we generate "
    "simulation parameters.\n\n"
    "Start by loading the **verified first-LLM JSON** and "
    "**running SIMOD** (or pasting its output) in the *Inputs* tab, "
    "then come back here so we can discuss any clarifications."
)

_CHAT_SYSTEM_PROMPT = """\
You are a BPM simulation scenario analyst.

You have access to:
1) verified KPI targets from the first LLM,
2) SIMOD-discovered baseline simulation artifacts,
3) process description and log-derived evidence,
4) prior answers from the user.

Your task is NOT to ask the user to choose a strategy.
Your task is to infer plausible scenario interventions yourself from the evidence
and ask the user only for missing business context, operational constraints,
or future changes that are not recoverable from the available data.

## Objective

Prepare enough grounded context so that a later generation step can produce
a high-quality what-if simulation scenario and simulator-ready parameters.

## How to work

Before asking your first question, reason internally about:
  - the simulation goal and verified KPIs,
  - likely process bottlenecks from the SIMOD baseline,
  - which operational context categories (see below) are UNKNOWN,
  - a plan for which categories to cover in which order.

Then ask one question at a time, covering the mandatory categories below.

## Mandatory context categories

You MUST gather information on ALL THREE categories before signalling
readiness. Do not signal readiness until each category has been addressed
by at least one user answer:

  A. **Resource / staffing flexibility** — for each major resource role,
     is staffing fixed or flexible? Can overtime or shift extensions happen?
     If flexible, what is the maximum number of resources that could
     realistically be added to that role?
  B. **Budget / cost constraints** — is there a budget ceiling for
     additional resources, overtime, or process changes? If the SIMOD
     baseline already contains costHour values for roles, use those.
     But if any role shows costHour = 0 or the cost data is missing,
     you must ask the user for the actual hourly cost of those roles
     so the downstream cost estimation can work.
  C. **Immutable parameters / regulations** — are any activities,
     durations, or assignments locked due to regulation, policy, or SLA?

## Core behavior

1. Maintain a bounded intervention space. Consider only evidence-grounded
   scenario levers such as:
   - resource calendars / availability changes,
   - resource allocation changes,
   - temporary staffing changes,
   - automation of suitable tasks,
   - arrival-rate or workload changes,
   - routing / rework / branching changes,
   - service-priority or batching policies,
   - activity duration improvements.

2. Do not ask the user to invent or choose the scenario.
   Instead ask for:
   - feasibility constraints,
   - business rules,
   - upcoming external changes,
   - hidden policies not visible in the log,
   - missing cost/calendar/priority information,
   - already-decided organizational constraints.

3. Ask exactly one question at a time.
   Each question must be specific and tied to concrete evidence from the
   loaded data — reference specific role names, activity names, or
   resource counts when possible.

4. Prefer discriminative questions that help eliminate or confirm candidate
   scenarios.
   Bad: "What strategy would you like?"
   Good: "SIMOD shows 5 Analysts handling Review — is that headcount
   fixed, or could we add 1-2 for overtime shifts?"

5. When useful, briefly explain why the question matters in one sentence.
   Keep total response length to 2-4 sentences.

6. If the user asks a direct question, answer it from the loaded evidence
   first, then continue with the most useful clarification question.

7. Do not generate simulation parameters or JSON in this chat.
   Do not ask generic brainstorming questions.
   Do not ask preference questions unless the preference is a real
   business constraint.

8. When citing a gateway probability, ALWAYS name the gateway AND the
   specific path/flow it refers to. The process can have many gateways,
   so a bare percentage is ambiguous.
   Bad:  "SIMOD shows a high probability of 80.5% for one path."
   Good: "Gateway 'Eligibility check' routes 80.5% of cases to
   'Standard onboarding' and 19.5% to 'Manual review' — is that split
   fixed by policy, or is it expected to drift this quarter?"
   If the loaded data only gives flow IDs (e.g. Flow_1a2b3c), reference
   the gateway name plus the source/target activity of that flow.

## Readiness conditions

Signal readiness ONLY when ALL of these are true:
  - You have covered all three mandatory categories above.
  - At least 3 user messages have been exchanged.
  - No critical ambiguity remains that would block scenario generation.

When ready, say exactly:
  "I have enough context now. You can click **Generate Scenario** \
whenever you're ready."

The phrase "Generate Scenario" must appear verbatim — the UI uses it as
a trigger. Do NOT include it until you are genuinely ready.

## Output style

- concise
- evidence-grounded
- analyst-led, not user-led
- no generic consulting language
- After the very first exchange, begin each reply with a brief recap
  (2-3 sentences max) of the key facts the user has confirmed so far,
  then transition into your next question. Example:
  "So far: staffing for Analysts is fixed, you have a 5000 EUR/month
  overtime budget, and Final Approval is a regulatory step that cannot
  be shortened. Next question — …"
  Keep the recap tight; never repeat the full conversation history.

## Loaded data

{context}\
"""


class ClarificationChatbot:
    """Data-driven chatbot for the second LLM workspace.

    Analyses the loaded KPIs and SIMOD baseline, then asks smart,
    specific questions about what it observes.  When no LLM provider is
    available, falls back to a short set of generic prompts.
    """

    def __init__(
        self,
        session: ClarificationSession,
        first_llm: FirstLLMInput,
        simod: RawSimodInput,
        provider: LLMProvider | None = None,
    ) -> None:
        self._session = session
        self._first_llm = first_llm
        self._simod = simod
        self._provider = provider
        self._asked_indices: set[int] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def greet(self) -> str:
        """Return the opening assistant message (idempotent).

        When both inputs are loaded and a provider is available, the LLM
        produces a data-grounded opening that references what it sees.
        """
        if not self._session.messages:
            if self.has_required_inputs():
                greeting = self._ready_greeting()
                if self._provider:
                    opening = self._generate_opening_question_v2()
                    if opening:
                        greeting += "\n\n" + opening
            else:
                greeting = _GREETING
            self._session.append(ChatRole.ASSISTANT, greeting)
            self._session.prompt_version = _CHAT_PROMPT_VERSION
            if self.has_required_inputs():
                self._set_last_context_signature(self._context_signature())
            return greeting
        return self._session.messages[0].content

    def generate_assistant_reply(self, user_message: str | None = None) -> str:
        """Produce an assistant reply — LLM-backed when possible."""
        if user_message:
            self._session.append(ChatRole.USER, user_message)

        # Gate: check for missing or invalid inputs
        if not self._first_llm.raw_json_text:
            reply = (
                "I notice the **first-LLM JSON** has not been loaded yet. "
                "Please paste or upload it in the *Inputs* tab so we can proceed."
            )
            self._session.append(ChatRole.ASSISTANT, reply)
            return reply

        if self._first_llm.raw_json_text and not self._first_llm.is_valid:
            reason = self._first_llm.validation_error or self._first_llm.parse_error or "unknown error"
            reply = (
                "The loaded JSON does not match the verified KPI schema: "
                f"{reason}\n\n"
                "Please fix it and re-upload in the *Inputs* tab."
            )
            self._session.append(ChatRole.ASSISTANT, reply)
            return reply

        if not self._simod.is_non_empty:
            reply = (
                "The **SIMOD output** is still missing. "
                "Please run SIMOD on your event log or paste its output "
                "in the *Inputs* tab."
            )
            self._session.append(ChatRole.ASSISTANT, reply)
            return reply

        # Try LLM-backed reply
        if self._provider and user_message:
            reply = self._generate_llm_reply()
            self._session.append(ChatRole.ASSISTANT, reply)
            return reply

        # Fallback: rule-based
        return self._generate_rule_based_reply(user_message)

    # ------------------------------------------------------------------
    # Quick-action helpers
    # ------------------------------------------------------------------

    def get_quick_prompts(self) -> list[str]:
        """Return a few generic prompts for users who aren't sure what to say."""
        prompts = [
            "What are the main bottlenecks you see in the baseline?",
            "Which parameters would have the biggest impact on my KPIs?",
            "I'm ready — let's generate the scenario.",
        ]
        return prompts

    def has_required_inputs(self) -> bool:
        """Return True when the chat has the minimum required workspace context."""
        return bool(self._first_llm.is_valid and self._simod.is_non_empty)

    def is_ready_to_generate(self) -> bool:
        """True when the chatbot has signalled it has enough context.

        Three-layer defence:
        1. Minimum user turn count (``_MIN_TURNS_BEFORE_READY``).
        2. The most recent assistant message must contain the readiness
           signal phrase (``_READINESS_SIGNAL``).
        3. The UI shows a *Generate* button only when this returns True.
        """
        if self.user_message_count < _MIN_TURNS_BEFORE_READY:
            return False
        coverage = self._coverage_status()
        if not all(coverage.values()):
            return False
        for msg in reversed(self._session.messages):
            if msg.role == ChatRole.ASSISTANT:
                return _READINESS_SIGNAL in msg.content
        return False

    @property
    def user_message_count(self) -> int:
        """Number of user messages in the session."""
        return sum(1 for m in self._session.messages if m.role == ChatRole.USER)

    def sync_context_message(self) -> str | None:
        """Append a fresh context-loaded message once per unique workspace snapshot."""
        signature = self._context_signature()
        if not signature or signature == self._get_last_context_signature():
            return None

        reply = self._ready_greeting()
        self._session.append(ChatRole.ASSISTANT, reply)
        self._set_last_context_signature(signature)
        return reply

    def build_context_markdown(self) -> str:
        """Return a short markdown summary of the currently loaded context."""
        lines: list[str] = []

        if self._first_llm.parsed:
            goal = (
                self._first_llm.parsed.get("simulation_goal_structured")
                or self._first_llm.parsed.get("simulation_goal")
            )
            if goal:
                lines.append(f"- Simulation goal: {goal}")

            kpis = self._first_llm.parsed.get("kpis")
            if isinstance(kpis, list) and kpis:
                names = [
                    str(kpi.get("name", "Unnamed KPI")).strip()
                    for kpi in kpis
                    if isinstance(kpi, dict)
                ]
                visible_names = [name for name in names if name][:4]
                if visible_names:
                    suffix = "..." if len(names) > len(visible_names) else ""
                    lines.append(
                        f"- Verified KPI context: {len(names)} KPI(s) loaded"
                        f" ({', '.join(visible_names)}{suffix})"
                    )
                else:
                    lines.append(f"- Verified KPI context: {len(kpis)} KPI(s) loaded")
        elif self._first_llm.raw_json_text:
            lines.append("- Verified first-LLM JSON loaded as raw text")
        else:
            lines.append("- Verified first-LLM JSON is still missing")

        if self._simod.simod_result:
            sr = self._simod.simod_result
            process_name = sr.process_name or "unknown process"
            lines.append(f"- SIMOD process model loaded for: {process_name}")
            simod_assets: list[str] = []
            if sr.bpmn_content:
                simod_assets.append("BPMN")
            if sr.json_params_content:
                simod_assets.append("simulation parameters JSON")
            if simod_assets:
                lines.append(f"- SIMOD artifacts available: {', '.join(simod_assets)}")
        elif self._simod.is_non_empty:
            lines.append(f"- SIMOD output loaded as raw text ({self._simod.line_count} lines)")
        else:
            lines.append("- SIMOD output is still missing")

        if self._provider:
            lines.append(f"- LLM: {self._provider.get_model_name()}")
        else:
            lines.append("- LLM: not configured (rule-based fallback)")

        if not lines:
            lines.append("- No context has been loaded yet")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # LLM-backed reply generation
    # ------------------------------------------------------------------

    def _build_context_block(self) -> str:
        """Build the context block injected into the system prompt."""
        parts: list[str] = []

        if self._first_llm.parsed:
            first_llm_compact = json.dumps(self._first_llm.parsed, indent=2)
            if len(first_llm_compact) > 3000:
                first_llm_compact = first_llm_compact[:3000] + "\n... (truncated)"
            parts.append(f"## Verified First-LLM Output\n```json\n{first_llm_compact}\n```")
        elif self._first_llm.raw_json_text:
            snippet = self._first_llm.raw_json_text[:2000]
            parts.append(f"## First-LLM JSON (raw)\n```\n{snippet}\n```")

        # Structured SIMOD model summary (roles, activities, gateways)
        simod_summary = self._extract_simod_model_summary()
        if simod_summary:
            parts.append(f"## SIMOD Model Structure\n{simod_summary}")
        elif self._simod.simod_result and self._simod.simod_result.json_params_content:
            simod_text = self._simod.simod_result.json_params_content
            if len(simod_text) > 3000:
                simod_text = simod_text[:3000] + "\n... (truncated)"
            parts.append(f"## SIMOD Baseline\n```json\n{simod_text}\n```")
        elif self._simod.raw_text:
            snippet = self._simod.raw_text[:2000]
            parts.append(f"## SIMOD Output (raw)\n```\n{snippet}\n```")

        coverage = self._coverage_status()
        missing = [name for name, covered in coverage.items() if not covered]
        coverage_lines = [
            "## Operational Context Coverage",
            f"- resource_staffing: {'covered' if coverage['resource_staffing'] else 'missing'}",
            f"- budget_cost_constraints: {'covered' if coverage['budget_cost_constraints'] else 'missing'}",
            f"- immutable_constraints: {'covered' if coverage['immutable_constraints'] else 'missing'}",
        ]
        if missing:
            coverage_lines.append(
                "- Missing categories must still be asked before you signal readiness: "
                + ", ".join(missing)
            )
        missing_cost_roles = self._roles_missing_cost_data()
        if missing_cost_roles:
            coverage_lines.append(
                "- Missing hourly wage data for roles: " + ", ".join(missing_cost_roles)
            )
        else:
            coverage_lines.append(
                "- Hourly wage data is already available from the loaded SIMOD baseline."
            )
        parts.append("\n".join(coverage_lines))

        return "\n\n".join(parts) if parts else "(No context loaded yet)"

    def _extract_simod_model_summary(self) -> str:
        """Parse SIMOD JSON and return a concise structured summary.

        Extracts roles (with resource counts and costs), activities
        (with durations and role assignments), and gateways so the LLM
        can reference specific names and numbers in its questions.
        Returns an empty string if parsing fails.
        """
        simod_result = self._simod.simod_result
        if not simod_result or not simod_result.json_params_content:
            return ""

        try:
            data = json.loads(simod_result.json_params_content)
        except (json.JSONDecodeError, TypeError):
            return ""

        lines: list[str] = []
        role_rows, activities, gateways, events = self._normalised_simod_summary_parts(data)

        if role_rows:
            lines.append("### Resource Roles")
            missing_cost_roles: list[str] = []
            for role in role_rows:
                role_id = role.get("id", "?")
                count = int(role.get("count", 0) or 0)
                cost = role.get("cost")
                schedule = role.get("schedule", "")
                if cost and cost > 0:
                    entry = f"- **{role_id}**: {count} resource(s), {cost} EUR/h"
                else:
                    entry = (
                        f"- **{role_id}**: {count} resource(s), "
                        f"**cost unknown — ask user**"
                    )
                    missing_cost_roles.append(role_id)
                if schedule:
                    entry += f", schedule: {schedule}"
                lines.append(entry)
            if missing_cost_roles:
                lines.append(
                    f"\n_Cost data missing for: "
                    f"{', '.join(missing_cost_roles)}. "
                    f"Ask the user for hourly rates when covering "
                    f"budget constraints._"
                )

        if activities:
            lines.append("### Activities")
            for act in activities:
                name = act.get("name") or act.get("id", "?")
                assigned = act.get("resources", [])
                duration = act.get("duration", {})
                dist_type = duration.get("distributionType", "?")
                time_unit = duration.get("timeUnit", "")
                values = duration.get("values", [])
                param_str = ", ".join(
                    f"{v.get('id', '?')}={v.get('value', '?')}"
                    for v in values
                )
                entry = f"- **{name}**: {dist_type}({param_str}) {time_unit}"
                if assigned:
                    entry += f" | roles: {', '.join(assigned)}"
                lines.append(entry)

        # --- Gateways ---
        if gateways:
            flow_targets = self._build_flow_target_map()
            element_names = self._build_element_name_map()
            lines.append("### Gateways")
            for gw in gateways:
                gw_id = gw.get("id", "?")
                gw_name = gw.get("name") or gw_id
                probs = gw.get("probabilities", {})
                if probs:
                    parts: list[str] = []
                    for flow_id, prob in probs.items():
                        target_id = flow_targets.get(flow_id)
                        target_label = (
                            element_names.get(target_id, target_id)
                            if target_id
                            else None
                        )
                        if target_label:
                            parts.append(
                                f"{flow_id} → '{target_label}': {prob:.1%}"
                            )
                        else:
                            parts.append(f"{flow_id}: {prob:.1%}")
                    lines.append(
                        f"- **{gw_name}** (id={gw_id}): " + "; ".join(parts)
                    )
                else:
                    lines.append(f"- **{gw_name}** (id={gw_id})")

        # --- Arrival ---
        if events:
            lines.append("### Arrival")
            for ev in events:
                iat = ev.get("interArrivalTime", {})
                dist_type = iat.get("distributionType", "?")
                time_unit = iat.get("timeUnit", "")
                values = iat.get("values", [])
                param_str = ", ".join(
                    f"{v.get('id', '?')}={v.get('value', '?')}"
                    for v in values
                )
                lines.append(f"- Inter-arrival: {dist_type}({param_str}) {time_unit}")

        return "\n".join(lines) if lines else ""

    def _load_simod_json(self) -> dict | None:
        simod_result = self._simod.simod_result
        if not simod_result or not simod_result.json_params_content:
            return None
        try:
            data = json.loads(simod_result.json_params_content)
        except (json.JSONDecodeError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def _normalised_simod_summary_parts(
        self,
        data: dict,
    ) -> tuple[list[dict[str, object]], list[dict], list[dict], list[dict]]:
        """Return roles/activities/gateways/events from either SimuBridge or raw SIMOD JSON."""
        res_params = data.get("resourceParameters") or data.get("resource_parameters") or {}
        roles = res_params.get("roles", [])
        models = data.get("models", [])

        role_rows: list[dict[str, object]] = []
        activities: list[dict] = []
        gateways: list[dict] = []
        events: list[dict] = []

        if roles or models:
            for role in roles:
                if not isinstance(role, dict):
                    continue
                resources = role.get("resources", [])
                role_rows.append({
                    "id": role.get("id", "?"),
                    "count": len(resources) if isinstance(resources, list) else 0,
                    "cost": role.get("costHour", role.get("cost_hour", 0)),
                    "schedule": role.get("schedule", ""),
                })

            for model in models:
                if not isinstance(model, dict):
                    continue
                mp = model.get("modelParameter", model.get("model_parameter", {}))
                if not isinstance(mp, dict):
                    continue
                activities.extend(mp.get("activities", []))
                gateways.extend(mp.get("gateways", []))
                events.extend(mp.get("events", []))
            return role_rows, activities, gateways, events

        baseline = build_baseline_scenario(data)
        if not baseline.ok or baseline.scenario is None:
            return [], [], [], []

        scenario = baseline.scenario
        for role in scenario.resourceParameters.roles:
            role_rows.append({
                "id": role.id,
                "count": len(role.resources),
                "cost": role.costHour,
                "schedule": role.schedule,
            })
        for model in scenario.models:
            activities.extend([
                {
                    "id": act.id,
                    "name": act.name,
                    "resources": list(act.resources),
                    "duration": {
                        "distributionType": act.duration.distributionType.value,
                        "timeUnit": act.duration.timeUnit.value,
                        "values": [
                            {"id": v.id, "value": v.value} for v in act.duration.values
                        ],
                    },
                }
                for act in model.modelParameter.activities
            ])
            gateways.extend([
                {
                    "id": gw.id,
                    "name": gw.name,
                    "probabilities": dict(gw.probabilities),
                }
                for gw in model.modelParameter.gateways
            ])
            events.extend([
                {
                    "id": ev.id,
                    "interArrivalTime": {
                        "distributionType": ev.interArrivalTime.distributionType.value,
                        "timeUnit": ev.interArrivalTime.timeUnit.value,
                        "values": [
                            {"id": v.id, "value": v.value}
                            for v in ev.interArrivalTime.values
                        ],
                    },
                }
                for ev in model.modelParameter.events
            ])
        return role_rows, activities, gateways, events

    def _roles_missing_cost_data(self) -> list[str]:
        data = self._load_simod_json()
        if not data:
            return []
        role_rows, _, _, _ = self._normalised_simod_summary_parts(data)
        missing: list[str] = []
        for row in role_rows:
            cost = row.get("cost")
            if not cost or float(cost) <= 0:
                missing.append(str(row.get("id", "?")))
        return missing

    def _coverage_status(self) -> dict[str, bool]:
        conversation_text = "\n".join(
            m.content.lower() for m in self._session.messages
        )
        budget_keywords = (
            "budget", "eur", "usd", "/month", "per month", "monthly",
            "ceiling", "cap", "limit", "process change", "overtime budget",
            "no budget", "unlimited", "spend", "fund", "funding",
            "allocate", "cost ceiling", "cost cap", "investment",
        )
        staffing_keywords = (
            "fixed", "flexible", "staffing", "headcount", "overtime",
            "shift", "max headcount", "maximum", "add ",
        )
        immutable_keywords = (
            "regulation", "regulatory", "policy", "sla", "immutable",
            "cannot change", "can't change", "must not change", "locked",
            "fixed by law", "no immutable", "nothing is fixed",
            "no regulatory", "no sla",
        )
        return {
            "resource_staffing": any(k in conversation_text for k in staffing_keywords),
            "budget_cost_constraints": any(k in conversation_text for k in budget_keywords),
            "immutable_constraints": any(k in conversation_text for k in immutable_keywords),
        }

    def _bpmn_xml(self) -> str:
        sr = self._simod.simod_result
        return sr.bpmn_content if sr and sr.bpmn_content else ""

    def _build_flow_target_map(self) -> dict[str, str]:
        """Map sequenceFlow id → targetRef id from the SIMOD BPMN."""
        xml = self._bpmn_xml()
        if not xml:
            return {}
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml)
        except ET.ParseError:
            return {}
        result: dict[str, str] = {}
        for flow in root.iter():
            tag = flow.tag.rsplit("}", 1)[-1]
            if tag == "sequenceFlow":
                fid = flow.attrib.get("id")
                tgt = flow.attrib.get("targetRef")
                if fid and tgt:
                    result[fid] = tgt
        return result

    def _build_element_name_map(self) -> dict[str, str]:
        """Map BPMN element id → human-readable name."""
        xml = self._bpmn_xml()
        if not xml:
            return {}
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml)
        except ET.ParseError:
            return {}
        result: dict[str, str] = {}
        for el in root.iter():
            eid = el.attrib.get("id")
            name = el.attrib.get("name")
            if eid and name:
                result[eid] = name
        return result

    def _build_conversation_messages(self) -> list[dict[str, str]]:
        """Convert session history to the few-shot format for the provider."""
        messages: list[dict[str, str]] = []
        history = self._session.messages[1:]  # skip greeting
        for msg in history[-20:]:
            if msg.role == ChatRole.USER:
                messages.append({"role": "user", "content": msg.content})
            elif msg.role == ChatRole.ASSISTANT:
                messages.append({"role": "assistant", "content": msg.content})
        return messages

    def _generate_llm_reply(self) -> str:
        """Call the LLM provider with context + conversation history."""
        context_block = self._build_context_block()
        system_prompt = _CHAT_SYSTEM_PROMPT.format(context=context_block)

        conversation = self._build_conversation_messages()
        if conversation and conversation[-1]["role"] == "user":
            user_prompt = conversation[-1]["content"]
            few_shot = conversation[:-1] if len(conversation) > 1 else None
        else:
            user_prompt = "(No user message)"
            few_shot = conversation if conversation else None

        try:
            reply = self._provider.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.4,
                few_shot_messages=few_shot,
            )
            return reply.strip()
        except Exception as exc:
            logger.warning("LLM call failed in chatbot: %s", exc)
            err_str = str(exc)
            if "429" in err_str or "rate" in err_str.lower():
                return (
                    f"The model is temporarily rate-limited (429). "
                    "Free-tier models on OpenRouter share upstream capacity — "
                    "please switch to a different model in the sidebar and try again. "
                    f"\n\nFull error: {exc}"
                )
            if "400" in err_str and "not a valid model" in err_str.lower():
                return (
                    "The selected model ID is not recognised by OpenRouter (400). "
                    "Please switch to a different model in the sidebar. "
                    f"\n\nFull error: {exc}"
                )
            return (
                f"I encountered an error calling the LLM: {exc}\n\n"
                "You can still continue chatting — your messages will be "
                "saved for the scenario generation step."
            )

    def _generate_opening_question(self) -> str:
        """Have the LLM produce the first data-specific question.

        Uses the same behavioural rules as ``_CHAT_SYSTEM_PROMPT`` to
        prevent the opening question from drifting into strategy or
        preference questions that the main prompt forbids.
        """
        if not self._provider:
            return ""
        context_block = self._build_context_block()
        system = (
            "You are a BPM simulation scenario analyst. You just loaded "
            "the user's KPI targets and SIMOD baseline shown below.\n\n"
            "Produce a SHORT (2-3 sentence) opening that:\n"
            "1. Notes something specific from the data — reference a "
            "concrete role name, resource count, activity duration, or "
            "gateway probability.\n"
            "2. Asks ONE operational-context question about that "
            "observation. The question must be about resource/staffing "
            "flexibility, budget/cost, or immutable parameters — NOT "
            "about strategy preferences.\n\n"
            "Bad: 'What strategy would you like to explore?'\n"
            "Good: 'SIMOD shows 3 Clerks on Review — is that headcount "
            "fixed, or could we add 1-2 for overtime shifts?'\n\n"
            f"{context_block}"
        )
        try:
            return self._provider.generate(
                system_prompt=system,
                user_prompt=(
                    "What's the first operationally relevant thing you "
                    "notice in this data? Ask about it."
                ),
                temperature=0.4,
            ).strip()
        except Exception as exc:
            logger.warning("Opening question generation failed: %s", exc)
            return ""

    def _generate_opening_question_v2(self) -> str:
        """Generate the first question using the full chat rules."""
        if not self._provider:
            return ""

        context_block = self._build_context_block()
        system_prompt = (
            _CHAT_SYSTEM_PROMPT.format(context=context_block)
            + "\n\n"
            + "You are composing the VERY FIRST question after the workspace "
            + "greeting. Obey all rules above, especially the requirement to "
            + "name the gateway and specific path/flow whenever you cite a "
            + "gateway probability. Keep the opening to 2-3 sentences."
        )
        user_prompt = (
            "What is the most operationally relevant observation in this data? "
            "Ask exactly one evidence-grounded question about it. If you "
            "mention a gateway probability, name the gateway and the "
            "path/flow explicitly."
        )

        try:
            opening = self._provider.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.4,
            ).strip()
            if self._needs_gateway_specificity_repair(opening):
                return self._repair_opening_question(opening, system_prompt)
            return opening
        except Exception as exc:
            logger.warning("Opening question generation failed: %s", exc)
            return ""

    def _needs_gateway_specificity_repair(self, reply: str) -> bool:
        """Detect ambiguous branch percentages without a named gateway."""
        lowered = reply.lower()
        mentions_percentage = bool(_PERCENTAGE_RE.search(reply))
        mentions_path = any(token in lowered for token in ("path", "branch", "route"))
        mentions_gateway = "gateway" in lowered
        vague_gateway = "specific gateway" in lowered or "one path" in lowered
        return mentions_percentage and ((mentions_path and not mentions_gateway) or vague_gateway)

    def _repair_opening_question(self, draft_reply: str, system_prompt: str) -> str:
        """Retry once if the first draft cites an unnamed gateway percentage."""
        try:
            repaired = self._provider.generate(
                system_prompt=system_prompt,
                user_prompt=(
                    "Revise this opening so it obeys the gateway specificity "
                    "rule. If a percentage refers to routing, explicitly name "
                    "the gateway and the path/flow.\n\n"
                    f"Draft opening:\n{draft_reply}"
                ),
                temperature=0.2,
            ).strip()
            return repaired or draft_reply
        except Exception as exc:
            logger.warning("Opening question repair failed: %s", exc)
            return draft_reply

    # ------------------------------------------------------------------
    # Rule-based fallback
    # ------------------------------------------------------------------

    _FALLBACK_QUESTIONS: list[str] = [
        "For each major resource role, is staffing fixed or can headcount be adjusted? If adjustable, what is the maximum headcount allowed per role?",
        "Is there a monthly budget ceiling for additional resources or overtime?",
        "Are any activity durations or assignments locked due to regulation or SLA?",
        "Can shift schedules or working hours be extended for specific roles?",
        "Are there any upcoming organisational changes that the scenario should account for?",
    ]

    def _generate_rule_based_reply(self, user_message: str | None) -> str:
        """Fallback when no LLM provider is available."""
        if user_message:
            reply = (
                "Got it — I've recorded that context for the scenario "
                "generation step."
            )
            follow_up = self._next_fallback_question()
            if follow_up:
                reply += f"\n\nNext question:\n> {follow_up}"
            else:
                reply += (
                    "\n\nI have enough context now. You can click "
                    f"**{_READINESS_SIGNAL}** whenever you're ready."
                )
            self._session.append(ChatRole.ASSISTANT, reply)
            return reply

        question = self._next_fallback_question()
        if question:
            reply = f"Let's gather some context:\n> {question}"
        else:
            reply = (
                "Feel free to add any additional context, or click "
                f"**{_READINESS_SIGNAL}** when you're ready."
            )
        self._session.append(ChatRole.ASSISTANT, reply)
        return reply

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ready_greeting(self) -> str:
        """Data-grounded greeting summarising what was loaded."""
        parts = [
            "I've loaded your data. Here's what I see:\n"
        ]
        parts.append(self.build_context_markdown())
        parts.append(
            "\nLet me ask a few questions to understand what you're "
            "looking for before we generate the scenario."
        )
        return "\n".join(parts)

    def _next_fallback_question(self) -> str | None:
        """Pick the next unused fallback question, or None."""
        for idx, prompt in enumerate(self._FALLBACK_QUESTIONS):
            if idx not in self._asked_indices:
                self._asked_indices.add(idx)
                return prompt
        return None

    def _context_signature(self) -> str:
        if not self.has_required_inputs():
            return ""
        payload = "||".join(
            [
                self._first_llm.raw_json_text,
                self._simod.raw_text,
                self._simod.simod_result.process_name if self._simod.simod_result else "",
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _get_last_context_signature(self) -> str:
        return str(getattr(self._session, "last_context_signature", "") or "")

    def _set_last_context_signature(self, signature: str) -> None:
        if not hasattr(self._session, "last_context_signature"):
            object.__setattr__(self._session, "last_context_signature", "")
        self._session.last_context_signature = signature

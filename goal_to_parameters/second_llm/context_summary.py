"""Structured operational-context summariser.

Bridges the clarification chat and the scenario generation step by
distilling raw conversation into a structured JSON of operational
constraints.  This avoids forcing the generation LLM to re-parse a
free-text conversation and ensures early-turn answers (e.g. about
budget or regulatory limits) are not lost to context-window truncation.

The summary follows Leyer's (2018) contextual-factor taxonomy:
  - Resource constraints (staffing flexibility, overtime, cross-training)
  - Cost / budget constraints
  - Calendar / availability constraints
  - Operational policies (batching, priority, SLA, escalation)
  - Regulatory / immutable constraints
  - Process-specific context (rework triggers, hidden waits)

When no LLM provider is available, returns an empty summary so the
pipeline degrades gracefully.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm.provider import LLMProvider

logger = logging.getLogger(__name__)


# -- Schema that the summariser LLM must produce --------------------------

CONTEXT_SUMMARY_SCHEMA = """\
{
  "resource_constraints": {
    "<role_name>": {
      "staffing_flexible": <true|false>,
      "reason": "<why fixed or flexible>",
      "overtime_available": <true|false>,
      "overtime_rate_multiplier": <float or null>,
      "max_headcount": <integer or null>,
      "max_additional": <integer or null>,
      "max_hours_per_week": <number or null>,
      "cross_trained_with": ["<other role names>"],
      "shared_across_processes": <true|false>
    }
  },
  "budget": {
    "additional_monthly": <number or null>,
    "currency": "<EUR|USD|...>",
    "notes": "<any budget context>"
  },
  "calendar_constraints": {
    "shift_extension_possible": <true|false>,
    "new_hire_max_hours_per_week": <number or null>,
    "total_max_additional_resources": <integer or null>,
    "seasonal_variations": "<description or null>",
    "notes": "<any calendar context>"
  },
  "sla_constraints": [
    {
      "metric": "<cycle_time|processing_time|...>",
      "threshold": "<e.g. 48h>",
      "penalty": "<e.g. 500 EUR/case or null>",
      "scope": "<which activities or end-to-end>"
    }
  ],
  "immutable_parameters": [
    {
      "element": "<activity or role name>",
      "parameter": "<what must not change>",
      "reason": "<safety|regulation|policy|...>"
    }
  ],
  "operational_policies": [
    {
      "type": "<batching|priority|escalation|approval_chain|other>",
      "description": "<what the policy is>",
      "affected_elements": ["<activity or role names>"]
    }
  ],
  "process_context": [
    {
      "observation": "<e.g. rework triggered by incomplete documents>",
      "affected_elements": ["<activity or gateway names>"]
    }
  ]
}\
"""

_SUMMARISER_SYSTEM = """\
You are an analyst extracting structured operational constraints from a \
conversation between a BPM simulation expert and a process owner.

The expert asked questions about operational context that event logs \
and SIMOD cannot capture. The process owner answered with facts about \
their organisation.

Your job: read the conversation below and produce a JSON object that \
captures every operational constraint, budget limit, staffing policy, \
regulatory rule, SLA, and process-specific fact the user mentioned.

Rules:
- Include ONLY facts explicitly stated by the user. Do not infer or \
  assume anything the user did not say.
- If the user did not mention a category, leave it empty ({}, [], or null).
- Use the role names and activity names from the loaded data when \
  referencing process elements.
- If the user stated a maximum **total** number of resources for a role \
  (e.g. "at most 5 Analysts", "maximum 3 clerks", "we can have 3"), \
  capture it in resource_constraints.<role>.max_headcount as an integer.
- If the user stated how many **additional** resources can be added on top \
  of the current staffing (e.g. "we can add 2 more", "hire up to 2 extra", \
  "add at most 1"), capture it in resource_constraints.<role>.max_additional \
  as an integer. Do NOT convert this to an absolute max_headcount — leave \
  max_headcount null in that case.
- If the user stated a **global** total limit on additional resources across \
  ALL roles (e.g. "we can add at most 2 more people", "maximum 3 extra \
  hires total", "we can bring in 2 new staff"), capture it in \
  calendar_constraints.total_max_additional_resources as an integer. \
  This is different from per-role limits: when the user says "2 more people" \
  without naming a specific role, it is a global limit.
- If the user stated a maximum number of **working hours per week** for a \
  role or for added resources (e.g. "they can only work 20 hours a week", \
  "max 30h/week"), capture it in resource_constraints.<role>.max_hours_per_week \
  as a number.
- If the user stated a working-hours limit that applies to ALL new hires or \
  generally — including when hours are stated alongside a global headcount \
  limit (e.g. "add 2 more people who work at most 30 hours each", \
  "no one can work more than 20 hours", "everyone is part-time at most 25h") \
  — capture it in calendar_constraints.new_hire_max_hours_per_week \
  as a number instead of (or in addition to) the per-role field.
- Output ONLY the JSON object. No explanation, no markdown fences.

Output schema:
""" + CONTEXT_SUMMARY_SCHEMA


# -- Public API -----------------------------------------------------------


class OperationalContextSummary:
    """Parsed result of the context summarisation step."""

    def __init__(self, raw: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = raw or {}

    # -- Accessors --------------------------------------------------------

    @property
    def resource_constraints(self) -> dict[str, Any]:
        return self._data.get("resource_constraints") or {}

    @property
    def budget(self) -> dict[str, Any]:
        return self._data.get("budget") or {}

    @property
    def calendar_constraints(self) -> dict[str, Any]:
        return self._data.get("calendar_constraints") or {}

    @property
    def sla_constraints(self) -> list[dict[str, Any]]:
        return self._data.get("sla_constraints") or []

    @property
    def immutable_parameters(self) -> list[dict[str, Any]]:
        return self._data.get("immutable_parameters") or []

    @property
    def operational_policies(self) -> list[dict[str, Any]]:
        return self._data.get("operational_policies") or []

    @property
    def process_context(self) -> list[dict[str, Any]]:
        return self._data.get("process_context") or []

    @property
    def is_empty(self) -> bool:
        """True when no constraints were extracted."""
        return not self._data or all(
            not v for v in self._data.values()
        )

    # -- Serialisation ----------------------------------------------------

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self._data, indent=indent, ensure_ascii=False)

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    # -- Feasibility helpers (used by validation) -------------------------

    def get_max_headcount(self, role_name: str) -> int | None:
        """Return the user-stated absolute maximum headcount for a role, or None."""
        rc = self.resource_constraints
        for name, info in rc.items():
            if name.lower() == role_name.lower():
                val = info.get("max_headcount")
                if val is not None:
                    try:
                        return int(val)
                    except (ValueError, TypeError):
                        return None
        return None

    def get_max_additional(self, role_name: str) -> int | None:
        """Return how many extra resources the user said can be added, or None."""
        rc = self.resource_constraints
        for name, info in rc.items():
            if name.lower() == role_name.lower():
                val = info.get("max_additional")
                if val is not None:
                    try:
                        return int(val)
                    except (ValueError, TypeError):
                        return None
        return None

    def get_max_hours_per_week(self, role_name: str) -> float | None:
        """Return the working-hours/week cap for a role, or the global new-hire cap."""
        rc = self.resource_constraints
        for name, info in rc.items():
            if name.lower() == role_name.lower():
                val = info.get("max_hours_per_week")
                if val is not None:
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        pass
        # Fall back to the global new-hire cap when no per-role value is set.
        global_val = self.calendar_constraints.get("new_hire_max_hours_per_week")
        if global_val is not None:
            try:
                return float(global_val)
            except (ValueError, TypeError):
                pass
        return None

    def get_effective_max_headcount(
        self, role_name: str, current_count: int | None = None
    ) -> int | None:
        """Return the effective headcount ceiling, resolving both fields.

        - If ``max_headcount`` is set, that is the absolute ceiling.
        - If ``max_additional`` is set and ``current_count`` is known,
          the ceiling is ``current_count + max_additional``.
        - If both are set, the lower value wins.
        - Returns ``None`` when neither is set.
        """
        absolute = self.get_max_headcount(role_name)
        additional = self.get_max_additional(role_name)

        relative: int | None = None
        if additional is not None and current_count is not None:
            relative = current_count + additional

        if absolute is not None and relative is not None:
            return min(absolute, relative)
        return absolute if absolute is not None else relative

    def get_global_max_additional(self) -> int | None:
        """Return the global cap on total additional resources across all roles."""
        val = self.calendar_constraints.get("total_max_additional_resources")
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                return None
        return None

    def is_role_fixed(self, role_name: str) -> bool:
        """Check if a role was declared as having fixed staffing."""
        rc = self.resource_constraints
        for name, info in rc.items():
            if name.lower() == role_name.lower():
                return info.get("staffing_flexible") is False
        return False

    def is_role_overtime_available(self, role_name: str) -> bool:
        """Check if the user declared that a role can work overtime."""
        rc = self.resource_constraints
        for name, info in rc.items():
            if name.lower() == role_name.lower():
                return bool(info.get("overtime_available"))
        return False

    def get_shift_extension_possible(self) -> bool | None:
        """Return whether the user declared shift extension is possible globally.

        Returns None when not stated, True/False when explicitly declared.
        """
        cc = self.calendar_constraints
        val = cc.get("shift_extension_possible")
        if val is None:
            return None
        return bool(val)

    def get_immutable_elements(self) -> set[str]:
        """Return element names that must not be changed."""
        return {
            item.get("element", "").lower()
            for item in self.immutable_parameters
            if item.get("element")
        }


def build_context_summary(
    chat_messages: list[dict[str, str]],
    provider: "LLMProvider | None" = None,
) -> OperationalContextSummary:
    """Summarise the clarification chat into structured constraints.

    Parameters
    ----------
    chat_messages:
        The clarification session as ``[{"role": ..., "content": ...}]``.
    provider:
        LLM provider for the summarisation call.  When ``None``, returns
        an empty summary (graceful degradation).

    Returns
    -------
    OperationalContextSummary
        Parsed structured constraints, or empty if summarisation fails.
    """
    if not provider:
        return OperationalContextSummary()

    non_system = [m for m in chat_messages if m.get("role") != "system"]
    if not non_system:
        return OperationalContextSummary()

    # Build a compact transcript
    transcript = "\n".join(
        f"{m['role'].upper()}: {m['content']}"
        for m in non_system
    )

    try:
        raw_output = provider.generate(
            system_prompt=_SUMMARISER_SYSTEM,
            user_prompt=f"## Conversation\n\n{transcript}",
            temperature=0.1,
            json_mode=True,
        )
    except Exception as exc:
        logger.warning("Context summarisation LLM call failed: %s", exc)
        return OperationalContextSummary()

    # Parse
    try:
        # Strip markdown fences if present
        cleaned = raw_output.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            logger.warning("Context summary is not a dict, got %s", type(data))
            return OperationalContextSummary()
        return OperationalContextSummary(data)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse context summary JSON: %s", exc)
        return OperationalContextSummary()

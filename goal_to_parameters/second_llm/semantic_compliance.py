"""Semantic compliance check: does the generated scenario respect what the user said?

The structured validation pipeline catches schema errors, headcount limits,
budget overruns, and directional inconsistencies — but only for constraint
types that were explicitly coded. This module adds a second, LLM-based layer
that reads the raw clarification chat and asks whether any free-form user
rule is violated by the generated modifications.

It is intentionally lightweight:
  - One small LLM call with a focused prompt.
  - Returns a structured list of violations (quoted constraint + explanation).
  - Callers decide whether to retry or surface as a warning.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm.provider import LLMProvider
    from second_llm.output_schema import ScenarioProposal

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------
# Data structures
# -------------------------------------------------------------------

@dataclass
class SemanticViolation:
    """One constraint that the generated scenario appears to break."""
    constraint_quoted: str   # exact or near-exact user quote
    violation: str           # what the scenario does that contradicts it
    severity: str = "hard"  # "hard" (clear) or "soft" (possible concern)


@dataclass
class SemanticComplianceResult:
    violations: list[SemanticViolation] = field(default_factory=list)
    compliant: bool = True
    checker_notes: str = ""

    @property
    def hard_violations(self) -> list[SemanticViolation]:
        return [v for v in self.violations if v.severity == "hard"]

    @property
    def has_hard_violations(self) -> bool:
        return any(v.severity == "hard" for v in self.violations)

    def violation_summary(self, max_chars: int = 1500) -> str:
        lines = []
        for v in self.violations:
            tag = "[HARD]" if v.severity == "hard" else "[SOFT]"
            lines.append(
                f"{tag} User stated: \"{v.constraint_quoted}\" — "
                f"Violation: {v.violation}"
            )
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... (truncated)"
        return text


# -------------------------------------------------------------------
# Prompt
# -------------------------------------------------------------------

_CHECKER_SYSTEM = """\
You are a compliance auditor for a business process simulation scenario.

A process owner gave operational constraints during a clarification chat. \
A scenario was then generated that proposes changes to the simulation model. \
Your job: check whether any proposed modification violates a constraint \
explicitly stated by the user.

Rules:
- Only flag violations of things the user EXPLICITLY stated. Do not infer \
  or assume constraints that were not mentioned.
- A "hard" violation is a clear, direct contradiction (e.g. user said \
  "max 20 hours/week" but the scenario schedules 40 hours/week).
- A "soft" violation is a possible concern worth noting but not a clear \
  contradiction.
- If the scenario is fully compliant, return an empty violations list.
- Output ONLY a JSON object. No explanation, no markdown fences.

Output schema:
{
  "compliant": <true|false>,
  "violations": [
    {
      "constraint_quoted": "<exact or close quote from the user>",
      "violation": "<what the modification does that contradicts it>",
      "severity": "<hard|soft>"
    }
  ]
}
"""


def _build_modifications_text(proposal: "ScenarioProposal") -> str:
    lines = []
    for i, mod in enumerate(proposal.modifications, start=1):
        lines.append(
            f"{i}. [{mod.parameter_type}] {mod.target_element}: "
            f"{mod.baseline_value} -> {mod.proposed_value} "
            f"(direction={mod.direction.value if hasattr(mod.direction, 'value') else mod.direction}, "
            f"kpi={mod.kpi_reference})"
        )
        if mod.feasibility_assumptions:
            lines.append(f"   Feasibility note: {mod.feasibility_assumptions}")
    return "\n".join(lines) if lines else "(no modifications)"


# -------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------

def check_semantic_compliance(
    chat_messages: list[dict[str, str]],
    proposal: "ScenarioProposal",
    provider: "LLMProvider",
) -> SemanticComplianceResult:
    """Check whether the scenario respects the user's stated constraints.

    Parameters
    ----------
    chat_messages:
        Raw clarification chat (system messages excluded by caller).
    proposal:
        The generated scenario proposal to audit.
    provider:
        LLM provider for the compliance check call.

    Returns
    -------
    SemanticComplianceResult
        Structured violations (if any) and overall compliance verdict.
    """
    non_system = [m for m in chat_messages if m.get("role") != "system"]
    if not non_system:
        return SemanticComplianceResult(compliant=True)

    transcript = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in non_system
    )
    modifications_text = _build_modifications_text(proposal)

    user_prompt = (
        "## User's stated constraints (clarification chat)\n\n"
        f"{transcript}\n\n"
        "## Generated modifications\n\n"
        f"{modifications_text}\n\n"
        "Check whether any modification violates a constraint the user stated."
    )

    try:
        raw = provider.generate(
            system_prompt=_CHECKER_SYSTEM,
            user_prompt=user_prompt,
            temperature=0.1,
            json_mode=True,
        )
    except Exception as exc:
        logger.warning("Semantic compliance check failed: %s", exc)
        return SemanticComplianceResult(
            compliant=True,
            checker_notes=f"Check skipped (LLM error: {exc})",
        )

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(l for l in lines if not l.strip().startswith("```"))
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Semantic compliance check: could not parse response: %s", exc)
        return SemanticComplianceResult(
            compliant=True,
            checker_notes=f"Check skipped (parse error: {exc})",
        )

    violations = []
    for item in data.get("violations") or []:
        if not isinstance(item, dict):
            continue
        cq = str(item.get("constraint_quoted") or "")
        viol = str(item.get("violation") or "")
        sev = str(item.get("severity") or "hard").lower()
        if cq and viol:
            violations.append(SemanticViolation(
                constraint_quoted=cq,
                violation=viol,
                severity=sev if sev in ("hard", "soft") else "hard",
            ))

    compliant = bool(data.get("compliant", not violations))
    return SemanticComplianceResult(
        violations=violations,
        compliant=compliant,
    )

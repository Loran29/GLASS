"""Draft payload builder for the second LLM request.

Assembles a :class:`SecondLLMRequestDraft` from the workspace inputs.
Uses :func:`build_second_llm_evidence` to run the full RAG retrieval
pipeline (KB + filtered SIMOD + filtered log + filtered context) before
the prompt is constructed.
"""

from __future__ import annotations

import json
from typing import Any

from knowledge.retrieval import SecondLLMEvidence, build_second_llm_evidence
from second_llm.models import (
    ClarificationSession,
    FirstLLMInput,
    RawSimodInput,
    SecondLLMRequestDraft,
)


def summarize_intermediate_events(scenario: Any) -> str:
    """Baseline summary of intermediate (timer) events for the second-LLM prompt.

    One line per event: display name, id, predecessor -> successor activity and the
    current mean delay in the event's own time unit (the unit the merger compares a
    quoted ``baseline_value`` against). Empty string when the baseline has no events.
    """
    from second_llm.scenario_merger import _distribution_mean

    lines: list[str] = []
    for model in getattr(scenario, "models", None) or []:
        for evt in model.modelParameter.intermediateEvents:
            mean = _distribution_mean(evt.duration)
            mean_text = f"{mean:g} {evt.duration.timeUnit.value}" if mean is not None else "unknown"
            lines.append(
                f"- {evt.name} (id {evt.id}): {evt.predecessor or '?'} -> {evt.successor or '?'}; "
                f"current mean duration {mean_text} ({evt.duration.distributionType.value})"
            )
    return "\n".join(lines)


def simod_for_llm(simod_json: dict[str, Any] | None) -> dict[str, Any] | None:
    """Copy of the SIMOD parameters for the LLM prompt with resources shown by name.

    SIMOD gives resource profiles and resources both an id (e.g. ``POOL`` and
    ``POOL_1``) and a name (the role, e.g. "Role X", and "Role X-000001"). The
    scenario's roles are keyed by the profile name, so a profile id copied from
    the prompt is not a valid patch target. Every string value equal to such an id
    (the profile's and resources' ``id`` and references such as
    ``task_resource_distribution[].resources[].resource_id``) is replaced by the
    name, when the name is set, differs from the id and is unique. Calendar ids are
    left as they are. The input is not modified; the baseline is built from it.
    """
    if not isinstance(simod_json, dict):
        return simod_json
    profiles = simod_json.get("resource_profiles")
    if not isinstance(profiles, list):
        return simod_json
    pairs: list[tuple[str, str]] = []
    for prof in profiles:
        if not isinstance(prof, dict):
            continue
        items = [prof] + [r for r in prof.get("resource_list") or [] if isinstance(r, dict)]
        pairs += [(str(it["id"]), str(it["name"])) for it in items if it.get("id") and it.get("name")]
    names = [n for _, n in pairs]
    id_to_name = {i: n for i, n in pairs if i != n and names.count(n) == 1}
    if not id_to_name:
        return simod_json

    def swap(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: swap(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [swap(v) for v in obj]
        if isinstance(obj, str):
            return id_to_name.get(obj, obj)
        return obj

    return swap(simod_json)


class DraftPayloadBuilder:
    """Builds the second-LLM request from workspace state."""

    def __init__(
        self,
        first_llm: FirstLLMInput,
        simod: RawSimodInput,
        session: ClarificationSession,
        log_profile: dict[str, Any] | None = None,
        context_profile: dict[str, Any] | None = None,
    ) -> None:
        self._first_llm = first_llm
        self._simod = simod
        self._session = session
        self._log_profile = log_profile
        self._context_profile = context_profile

    def _parse_simod_json(self) -> dict[str, Any] | None:
        """Try to parse SIMOD output as JSON dict."""
        if self._simod.simod_result and self._simod.simod_result.json_params_content:
            try:
                return json.loads(self._simod.simod_result.json_params_content)
            except (json.JSONDecodeError, TypeError):
                pass

        raw = self._simod.raw_text
        if raw:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass

        return None

    def build(self) -> tuple[SecondLLMRequestDraft, SecondLLMEvidence | None]:
        """Assemble the draft request with full RAG retrieval.

        Returns
        -------
        tuple of (draft, evidence)
            ``evidence`` is None if the first-LLM JSON could not be validated.
        """
        warnings: list[str] = []
        notes: list[str] = []
        evidence: SecondLLMEvidence | None = None

        # --- Validate first LLM input ---
        if not self._first_llm.raw_json_text:
            warnings.append("First LLM JSON is missing.")
        elif self._first_llm.parse_error:
            warnings.append(
                f"First LLM JSON has a parse error: {self._first_llm.parse_error}"
            )
        elif self._first_llm.validation_error:
            warnings.append(
                "First LLM JSON does not match the verified KPI schema: "
                f"{self._first_llm.validation_error}"
            )
        else:
            notes.append("First LLM JSON loaded, parsed, and validated successfully.")

        # --- Validate SIMOD input ---
        if not self._simod.is_non_empty:
            warnings.append("SIMOD output is missing.")
        elif self._simod.simod_result:
            sr = self._simod.simod_result
            notes.append(
                f"SIMOD output from integrated run "
                f"(process: {sr.process_name or 'unknown'})."
            )
            if sr.bpmn_content:
                notes.append("BPMN model included.")
            if sr.json_params_content:
                notes.append("JSON simulation parameters included.")
        else:
            notes.append(
                f"SIMOD output loaded as raw text ({self._simod.line_count} lines)."
            )

        # --- Chat history ---
        chat_messages = self._session.to_plain_list()
        if not chat_messages:
            notes.append("No clarification chat history.")
        else:
            notes.append(
                f"Clarification chat contains {len(chat_messages)} messages."
            )

        # --- Full RAG retrieval ---
        kb_context = ""
        if self._first_llm.parsed:
            goal_structured = self._first_llm.parsed.get(
                "simulation_goal_structured", ""
            )
            kpis = self._first_llm.parsed.get("kpis", [])

            if goal_structured:
                simod_dict = self._parse_simod_json()

                evidence = build_second_llm_evidence(
                    goal_structured=goal_structured,
                    kpis=kpis,
                    simod_json=simod_for_llm(simod_dict),
                    log_profile=self._log_profile,
                    context_profile=self._context_profile,
                )
                kb_context = evidence.kb_json

                for note in evidence.retrieval_notes:
                    notes.append(f"[RAG] {note}")
            else:
                warnings.append(
                    "First LLM JSON is missing 'simulation_goal_structured' - "
                    "RAG retrieval skipped."
                )
        elif self._first_llm.validation_error:
            warnings.append(
                "First LLM JSON did not pass schema validation - "
                "RAG retrieval skipped."
            )
        elif self._first_llm.raw_json_text:
            warnings.append(
                "First LLM JSON could not be parsed - RAG retrieval skipped."
            )

        draft = SecondLLMRequestDraft(
            first_llm_input=self._first_llm,
            raw_simod_input=self._simod,
            chat_history=chat_messages,
            knowledge_base_context=kb_context,
            notes=notes,
            warnings=warnings,
        )
        return draft, evidence

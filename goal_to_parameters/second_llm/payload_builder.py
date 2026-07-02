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
                    simod_json=simod_dict,
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

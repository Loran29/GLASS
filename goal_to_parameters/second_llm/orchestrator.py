"""Orchestrator that wires the second-LLM workspace components together.

Provides a single facade used by the UI layer so that the panel does not
need to know about internal details of parsing, chatbot logic, payload
assembly, scenario generation, or SIMOD execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from models import KPIGenerationResult
from second_llm.chatbot import ClarificationChatbot
from second_llm.models import ChatRole, ClarificationSession, FirstLLMInput, RawSimodInput, SecondLLMRequestDraft, SecondLLMWorkspaceState
from second_llm.payload_builder import DraftPayloadBuilder
from second_llm.scenario_generator import (
    ScenarioGenerationResult,
    generate_scenario_patch,
)
from second_llm.simod_input import accept_simod_raw_input
from second_llm.simod_runner import SimodBackend, SimodRunner
from second_llm.state import (
    get_last_scenario_json,
    get_workspace,
    reset_workspace,
    set_first_llm_input,
    set_last_draft,
    set_last_scenario,
    set_raw_simod_input,
)

if TYPE_CHECKING:
    from llm.provider import LLMProvider


class SecondLLMWorkspaceOrchestrator:
    """High-level facade for the second-LLM preparation workflow."""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self._ws: SecondLLMWorkspaceState = get_workspace()
        self._provider = provider
        self._chatbot = ClarificationChatbot(
            session=self._ws.clarification_session,
            first_llm=self._ws.first_llm_input,
            simod=self._ws.raw_simod_input,
            provider=provider,
        )

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------

    def parse_first_llm_json(self, raw_json_text: str) -> FirstLLMInput:
        """Parse, validate, and store the verified first-LLM JSON."""
        parsed: dict[str, Any] | None = None
        parse_error: str | None = None
        validation_error: str | None = None
        try:
            raw_payload = json.loads(raw_json_text)
        except (json.JSONDecodeError, TypeError) as exc:
            parse_error = str(exc)
        else:
            try:
                validated = KPIGenerationResult.model_validate(raw_payload)
                parsed = validated.model_dump(mode="python")
            except Exception as exc:
                validation_error = str(exc)

        first_llm = FirstLLMInput(
            raw_json_text=raw_json_text,
            parsed=parsed,
            parse_error=parse_error,
            validation_error=validation_error,
        )
        set_first_llm_input(first_llm)
        self._chatbot._first_llm = first_llm
        return first_llm

    def accept_simod_raw_input(self, raw_text: str, bpmn_xml: str = "") -> RawSimodInput:
        """Store manually-pasted SIMOD output."""
        simod = accept_simod_raw_input(raw_text, bpmn_xml=bpmn_xml)
        set_raw_simod_input(simod)
        self._chatbot._simod = simod
        return simod

    def run_simod(
        self,
        event_log_path: Path,
        output_dir: Path | None = None,
        one_shot: bool = True,
        backend: SimodBackend = SimodBackend.DOCKER,
    ) -> RawSimodInput:
        """Run SIMOD on the given event log and store the results."""
        runner = SimodRunner(
            event_log_path=event_log_path,
            output_dir=output_dir,
            backend=backend,
        )
        simod = runner.run(one_shot=one_shot)
        set_raw_simod_input(simod)
        self._chatbot._simod = simod
        return simod

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    def greet(self) -> str:
        return self._chatbot.greet()

    def append_chat_message(self, user_message: str) -> str:
        return self._chatbot.generate_assistant_reply(user_message)

    def generate_assistant_reply(self) -> str:
        return self._chatbot.generate_assistant_reply()

    def get_quick_prompts(self) -> list[str]:
        return self._chatbot.get_quick_prompts()

    def has_ready_chat_context(self) -> bool:
        return self._chatbot.has_required_inputs()

    def is_ready_to_generate(self) -> bool:
        """True when the chatbot has signalled it has enough context."""
        return self._chatbot.is_ready_to_generate()

    def sync_chat_context(self) -> str | None:
        return self._chatbot.sync_context_message()

    def get_chat_context_markdown(self) -> str:
        return self._chatbot.build_context_markdown()

    @property
    def chat_session(self) -> ClarificationSession:
        return self._ws.clarification_session

    @property
    def user_message_count(self) -> int:
        """Number of user messages in the chat session."""
        return self._chatbot.user_message_count

    # ------------------------------------------------------------------
    # Draft payload
    # ------------------------------------------------------------------

    def build_second_llm_request_draft(
        self,
        log_profile: dict | None = None,
        context_profile: dict | None = None,
    ) -> SecondLLMRequestDraft:
        """Assemble the draft payload with full RAG retrieval."""
        builder = DraftPayloadBuilder(
            first_llm=self._ws.first_llm_input,
            simod=self._ws.raw_simod_input,
            session=self._ws.clarification_session,
            log_profile=log_profile,
            context_profile=context_profile,
        )
        draft, evidence = builder.build()
        set_last_draft(draft)
        self._last_evidence = evidence
        return draft

    @property
    def last_evidence(self):
        """The evidence object from the last draft build, if any."""
        return getattr(self, "_last_evidence", None)

    # ------------------------------------------------------------------
    # Scenario generation (the actual second LLM call)
    # ------------------------------------------------------------------

    def generate_scenario(
        self,
        log_profile: dict | None = None,
        context_profile: dict | None = None,
        max_retries: int = 2,
        temperature: float = 0.3,
        strict_merge: bool = False,
    ) -> ScenarioGenerationResult:
        """Run the second LLM to generate a ScenarioProposal.

        Parameters
        ----------
        strict_merge:
            When ``True``, any merge error aborts the attempt so the retry
            loop can ask the LLM to fix the patch.  When ``False`` (default),
            the merger skips invalid modifications and records warnings.
        """
        if not self._provider:
            return ScenarioGenerationResult(
                error="No LLM provider configured. Select a provider in the sidebar.",
            )

        first_llm = self._ws.first_llm_input
        if not first_llm.parsed:
            return ScenarioGenerationResult(
                error="First LLM JSON is missing or has parse errors.",
            )

        simod = self._ws.raw_simod_input
        if not simod.is_non_empty:
            return ScenarioGenerationResult(
                error="SIMOD output is missing.",
            )

        simod_json_content = None
        if simod.simod_result and simod.simod_result.json_params_content:
            simod_json_content = simod.simod_result.json_params_content

        bpmn_xml = ""
        if simod.simod_result and simod.simod_result.bpmn_content:
            bpmn_xml = simod.simod_result.bpmn_content

        common_kwargs = dict(
            provider=self._provider,
            first_llm_json=first_llm.raw_json_text,
            first_llm_parsed=first_llm.parsed,
            simod_raw_text=simod.raw_text,
            simod_json_content=simod_json_content,
            bpmn_xml=bpmn_xml,
            chat_history=self._ws.clarification_session.to_plain_list(),
            log_profile=log_profile,
            context_profile=context_profile,
            max_retries=max_retries,
            temperature=temperature,
        )

        result = generate_scenario_patch(
            **common_kwargs,
            strict_merge=strict_merge,
        )

        if result.proposal:
            scenario_json = result.proposal.model_dump_json(indent=2)
            set_last_scenario(scenario_json)
        self._last_evidence = result.evidence

        return result

    @property
    def last_scenario_json(self) -> str:
        """The stored scenario JSON from the last generation, if any."""
        return get_last_scenario_json()

    @property
    def has_provider(self) -> bool:
        return self._provider is not None

    # ------------------------------------------------------------------
    # Iterative evaluation support
    # ------------------------------------------------------------------

    def inject_feedback_message(self, feedback: str) -> None:
        """Append evaluation feedback to the clarification session.

        The feedback is added as a user message so that the next call to
        ``generate_scenario()`` includes it in the prompt context.
        """
        self._ws.clarification_session.append(ChatRole.USER, feedback)

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset_second_llm_workspace(self) -> None:
        """Clear everything and start fresh."""
        reset_workspace()
        self._ws = get_workspace()
        self._chatbot = ClarificationChatbot(
            session=self._ws.clarification_session,
            first_llm=self._ws.first_llm_input,
            simod=self._ws.raw_simod_input,
            provider=self._provider,
        )

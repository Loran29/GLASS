"""Pydantic data models for the Scenario Studio.

These models define the structured inputs, chat history, draft request
payload, and generated scenario output for the second LLM step.

The formal output schema (what the second LLM must produce) is defined
in :mod:`second_llm.output_schema`.
"""

from __future__ import annotations

import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class ChatRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatMessage(BaseModel):
    """A single message in the clarification chat."""

    role: ChatRole
    content: str
    timestamp: datetime.datetime = Field(default_factory=datetime.datetime.now)


class ClarificationSession(BaseModel):
    """The full clarification chat history."""

    messages: list[ChatMessage] = Field(default_factory=list)
    last_context_signature: str = ""
    prompt_version: str = ""

    def append(self, role: ChatRole, content: str) -> ChatMessage:
        """Add a message and return it."""
        msg = ChatMessage(role=role, content=content)
        self.messages.append(msg)
        return msg

    def to_plain_list(self) -> list[dict[str, str]]:
        """Return a simplified list suitable for JSON serialisation."""
        return [
            {"role": m.role.value, "content": m.content}
            for m in self.messages
        ]


# ---------------------------------------------------------------------------
# First LLM input
# ---------------------------------------------------------------------------

class FirstLLMInput(BaseModel):
    """Stores the verified first-LLM JSON (raw text + validated dict)."""

    raw_json_text: str = ""
    parsed: dict[str, Any] | None = None
    parse_error: str | None = None
    validation_error: str | None = None

    @property
    def is_valid(self) -> bool:
        """True when the payload is valid JSON and matches the first-stage schema."""
        return (
            self.parsed is not None
            and self.parse_error is None
            and self.validation_error is None
        )

    @field_validator("raw_json_text", mode="before")
    @classmethod
    def _strip_text(cls, v: Any) -> str:
        if isinstance(v, str):
            return v.strip()
        return v


# ---------------------------------------------------------------------------
# Raw SIMOD input
# ---------------------------------------------------------------------------

class SimodResult(BaseModel):
    """Structured output from a SIMOD run.

    After SIMOD finishes it produces a BPMN model file and a JSON file
    containing simulation parameters (resource profiles, calendars,
    arrival distributions, task durations, gateway probabilities, etc.).
    """

    bpmn_path: str = ""
    json_params_path: str = ""
    bpmn_content: str = ""
    json_params_content: str = ""
    output_dir: str = ""
    process_name: str = ""


class RawSimodInput(BaseModel):
    """Stores SIMOD output — either from a real SIMOD run or pasted text.

    When SIMOD is run through the integrated runner, ``simod_result`` is
    populated with the structured output.  The ``raw_text`` field is kept
    for the manual-paste fallback and for serialising the combined output
    into the draft payload.
    """

    raw_text: str = ""
    line_count: int = 0
    is_non_empty: bool = False
    simod_result: SimodResult | None = None

    @field_validator("raw_text", mode="before")
    @classmethod
    def _strip_text(cls, v: Any) -> str:
        if isinstance(v, str):
            return v.strip()
        return v


# ---------------------------------------------------------------------------
# Draft second-LLM request
# ---------------------------------------------------------------------------

class SecondLLMRequestDraft(BaseModel):
    """The assembled payload that will be sent to the second LLM.

    Contains all the inputs the second LLM needs: verified KPIs, SIMOD
    baseline, chat clarifications, and the knowledge-base retrieval result.
    The output schema the LLM must produce is defined in
    :class:`second_llm.output_schema.ScenarioProposal`.
    """

    first_llm_input: FirstLLMInput
    raw_simod_input: RawSimodInput
    chat_history: list[dict[str, str]] = Field(default_factory=list)
    knowledge_base_context: str = Field(
        default="",
        description="JSON string from knowledge base retrieval (RAG) for prompt injection",
    )
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.now)
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Overall workspace state (serialisable snapshot)
# ---------------------------------------------------------------------------

class SecondLLMWorkspaceState(BaseModel):
    """Serialisable snapshot of the entire workspace for session persistence."""

    first_llm_input: FirstLLMInput = Field(default_factory=FirstLLMInput)
    raw_simod_input: RawSimodInput = Field(default_factory=RawSimodInput)
    clarification_session: ClarificationSession = Field(
        default_factory=ClarificationSession,
    )
    last_draft: SecondLLMRequestDraft | None = None
    last_scenario_json: str = Field(
        default="",
        description="Raw JSON string of the last generated ScenarioProposal",
    )

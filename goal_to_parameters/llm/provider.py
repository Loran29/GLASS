"""Abstract base class for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        *,
        few_shot_messages: list[dict[str, str]] | None = None,
        json_mode: bool = False,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> str:
        """Send prompt to the LLM and return the raw text response.

        Parameters
        ----------
        json_schema:
            When provided, the provider should use constrained decoding
            (structured outputs) to guarantee the response conforms to
            this JSON Schema.  Falls back to ``json_mode=True`` when the
            provider does not support structured outputs.
        max_tokens:
            Maximum number of tokens the model should generate.  When
            ``None`` the provider's default is used.
        timeout:
            Request timeout in seconds.  When ``None`` the provider's
            or HTTP client's default is used.
        """

    @abstractmethod
    def get_model_name(self) -> str:
        """Return the fully qualified name of the configured model."""

    def supports_structured_output(self) -> bool:
        """Return True if this provider supports JSON-schema constrained decoding."""
        return False

    def health_check(self) -> tuple[bool, str]:
        """Check whether the provider is reachable and configured correctly."""
        try:
            response = self.generate(
                system_prompt="You are a helpful assistant.",
                user_prompt="Reply with exactly the word: OK",
                temperature=0.0,
            )
        except Exception as exc:
            return False, str(exc)

        if response and response.strip():
            return True, f"Connected to {self.get_model_name()}"
        return False, "Empty response from model"

"""Anthropic API provider."""

from __future__ import annotations

from typing import Any

from .cost_tracker import TokenUsage, get_cost_tracker
from .provider import LLMProvider

SUPPORTED_MODELS = [
    "claude-opus-4-1-20250805",
    "claude-opus-4-20250514",
    "claude-sonnet-4-20250514",
    "claude-3-7-sonnet-20250219",
    "claude-3-5-haiku-20241022",
    "claude-3-haiku-20240307",
]
DEFAULT_MODEL = "claude-sonnet-4-20250514"


class AnthropicProvider(LLMProvider):
    """Send prompts to Anthropic models."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, base_url: str | None = None):
        if not api_key:
            raise ValueError("An Anthropic API key is required for this provider.")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url or None
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from anthropic import Anthropic
            except ImportError as exc:
                raise RuntimeError(
                    "The 'anthropic' package is not installed. Run: pip install anthropic"
                ) from exc

            kwargs: dict = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = Anthropic(**kwargs)
        return self._client

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
        client = self._get_client()
        messages: list[dict[str, str]] = []
        if few_shot_messages:
            messages.extend(few_shot_messages)
        messages.append({"role": "user", "content": user_prompt})
        # Anthropic does not support constrained decoding; fall back to
        # json_mode when a schema is requested.
        if json_schema is not None:
            json_mode = True
        if json_mode:
            messages.append({"role": "assistant", "content": "{"})

        create_kwargs: dict[str, Any] = dict(
            model=self.model,
            system=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens or 4096,
            messages=messages,
        )
        if timeout is not None:
            create_kwargs["timeout"] = timeout

        response = client.messages.create(**create_kwargs)

        if response.usage:
            get_cost_tracker().record(TokenUsage(
                input_tokens=response.usage.input_tokens or 0,
                output_tokens=response.usage.output_tokens or 0,
                model=self.model,
            ))

        parts = [block.text for block in response.content if getattr(block, "type", "") == "text"]
        content = "\n".join(parts)
        if json_mode:
            content = "{" + content
        if not content.strip():
            raise RuntimeError("Anthropic returned an empty response.")
        return content

    def get_model_name(self) -> str:
        return f"anthropic/{self.model}"

"""OpenAI API provider."""

from __future__ import annotations

from typing import Any

from .cost_tracker import TokenUsage, get_cost_tracker
from .provider import LLMProvider

SUPPORTED_MODELS = ["gpt-4o-mini", "gpt-4o"]
DEFAULT_MODEL = "gpt-4o-mini"


def _normalize_openai_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            else:
                text = getattr(item, "text", None)
                if text:
                    parts.append(str(text))
        return "\n".join(parts)
    return str(content or "")


class OpenAIProvider(LLMProvider):
    """Send prompts to OpenAI chat models."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        if not api_key:
            raise ValueError("An OpenAI API key is required for this provider.")
        self.api_key = api_key
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "The 'openai' package is not installed. Run: pip install openai"
                ) from exc

            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def supports_structured_output(self) -> bool:
        return True

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
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if few_shot_messages:
            messages.extend(few_shot_messages)
        messages.append({"role": "user", "content": user_prompt})

        kwargs: dict = dict(model=self.model, temperature=temperature, messages=messages)
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if timeout is not None:
            kwargs["timeout"] = timeout
        if json_schema is not None:
            # Structured output: OpenAI guarantees the response matches
            # the provided JSON Schema (constrained decoding).
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": json_schema.get("title", "response"),
                    "strict": True,
                    "schema": json_schema,
                },
            }
        elif json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = client.chat.completions.create(**kwargs)

        if response.usage:
            get_cost_tracker().record(TokenUsage(
                input_tokens=response.usage.prompt_tokens or 0,
                output_tokens=response.usage.completion_tokens or 0,
                model=self.model,
            ))

        message = response.choices[0].message
        content = _normalize_openai_content(message.content)
        if not content.strip():
            raise RuntimeError("OpenAI returned an empty response.")
        return content

    def get_model_name(self) -> str:
        return f"openai/{self.model}"

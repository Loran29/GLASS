"""OpenRouter provider — OpenAI-compatible API with access to many cheap models."""

from __future__ import annotations

from typing import Any

from .cost_tracker import TokenUsage, get_cost_tracker
from .provider import LLMProvider

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "mistralai/mistral-7b-instruct"


class OpenRouterProvider(LLMProvider):
    """
    Calls the OpenRouter API using the openai package pointed at OpenRouter's base URL.

    Requires:
        pip install openai
        An OpenRouter API key (free to create at https://openrouter.ai)
    """

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        if not api_key:
            raise ValueError("An OpenRouter API key is required. Get one free at https://openrouter.ai")
        self.model = model
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "The 'openai' package is not installed. Run: pip install openai"
                ) from exc
            self._client = OpenAI(
                api_key=self._api_key,
                base_url=OPENROUTER_BASE_URL,
                default_headers={
                    "HTTP-Referer": "http://localhost:8501",
                    "X-Title": "GLASS",
                },
            )
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
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if few_shot_messages:
            messages.extend(few_shot_messages)
        messages.append({"role": "user", "content": user_prompt})

        kwargs: dict = dict(model=self.model, messages=messages, temperature=temperature)
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if timeout is not None:
            kwargs["timeout"] = timeout
        # OpenRouter does not reliably support structured outputs across
        # all backing models; fall back to json_mode.
        if json_schema is not None:
            json_mode = True
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = client.chat.completions.create(**kwargs)

        if response.usage:
            get_cost_tracker().record(TokenUsage(
                input_tokens=response.usage.prompt_tokens or 0,
                output_tokens=response.usage.completion_tokens or 0,
                model=self.model,
            ))

        return response.choices[0].message.content or ""

    def get_model_name(self) -> str:
        return f"openrouter/{self.model}"

    def health_check(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "OpenRouter API key is missing."
        return True, f"OpenRouter API key configured — model: {self.model}"

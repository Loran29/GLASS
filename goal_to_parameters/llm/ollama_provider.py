"""Ollama local model provider."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from .provider import LLMProvider

SUPPORTED_MODELS = ["mistral", "llama3", "llama3.2", "llama3.2:1b", "llama3.2:3b", "phi3", "gemma2"]
DEFAULT_MODEL = "mistral"
DEFAULT_BASE_URL = "http://localhost:11434"


def _extract_message_content(response: object) -> str:
    if isinstance(response, dict):
        message = response.get("message", {})
        if isinstance(message, dict):
            return str(message.get("content", ""))

    message = getattr(response, "message", None)
    if message is None:
        return ""
    if isinstance(message, dict):
        return str(message.get("content", ""))
    return str(getattr(message, "content", ""))


def _extract_model_names(models_response: object) -> list[str]:
    if isinstance(models_response, dict):
        models = models_response.get("models", [])
    else:
        models = getattr(models_response, "models", [])

    names: list[str] = []
    for model in models:
        if isinstance(model, dict):
            raw_name = str(model.get("name") or model.get("model") or "")
        else:
            raw_name = str(getattr(model, "name", "") or getattr(model, "model", ""))
        if raw_name:
            names.append(raw_name)
    return names


def _matches_selected_model(selected_model: str, available_models: list[str]) -> bool:
    if selected_model in available_models:
        return True

    selected_base = selected_model.split(":", 1)[0]
    for available_model in available_models:
        available_base = available_model.split(":", 1)[0]
        if selected_model == available_base:
            return True
        if selected_base == available_model:
            return True
        if selected_base == available_base:
            return True
    return False


def _debug_enabled() -> bool:
    value = os.getenv("GLASS_DEBUG_OLLAMA", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _debug_log(message: str) -> None:
    if _debug_enabled():
        print(f"[GLASS][Ollama] {message}", flush=True)


class OllamaProvider(LLMProvider):
    """Talk to a locally running Ollama server."""

    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL):
        self.model = model
        self.base_url = base_url
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import ollama  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "The 'ollama' package is not installed. Run: pip install ollama"
                ) from exc

            self._client = ollama.Client(host=self.base_url)
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

        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        kwargs: dict = dict(
            model=self.model,
            messages=messages,
            options=options,
        )
        if json_schema is not None:
            # Ollama supports structured output via the format parameter
            # when given a JSON Schema dict (ollama >= 0.5).
            kwargs["format"] = json_schema
        elif json_mode:
            kwargs["format"] = "json"

        try:
            response = client.chat(**kwargs)
        except Exception as exc:
            error_message = str(exc).lower()
            if "connection" in error_message or "refused" in error_message:
                raise ConnectionError(
                    f"Cannot connect to Ollama at {self.base_url}. "
                    "Please install and start Ollama: https://ollama.ai"
                ) from exc
            raise RuntimeError(f"Ollama generation failed: {exc}") from exc

        content = _extract_message_content(response)
        if not content.strip():
            raise RuntimeError("Ollama returned an empty response.")
        return content

    def get_model_name(self) -> str:
        return f"ollama/{self.model}"

    def health_check(self) -> tuple[bool, str]:
        request_url = f"{self.base_url.rstrip('/')}/api/tags"
        _debug_log(f"Resolved Ollama base URL: {self.base_url}")
        _debug_log(f"Full request URL: {request_url}")

        try:
            with urlopen(request_url) as response:
                status_code = getattr(response, "status", None) or response.getcode()
                raw_body = response.read().decode("utf-8", errors="replace")

            _debug_log(f"HTTP status code: {status_code}")
            _debug_log(f"Raw response body: {raw_body}")

            payload = json.loads(raw_body)
            available_models = _extract_model_names(payload)
            _debug_log(f"Parsed model names: {available_models}")
        except URLError as exc:
            _debug_log(f"Caught exception: {exc!r}")
            return (
                False,
                f"Cannot connect to Ollama at {self.base_url}. "
                "Please install and start Ollama: https://ollama.ai",
            )
        except json.JSONDecodeError as exc:
            _debug_log(f"Caught exception: {exc!r}")
            return False, f"Ollama returned invalid JSON from {request_url}: {exc}"
        except Exception as exc:
            _debug_log(f"Caught exception: {exc!r}")
            message = str(exc)
            if "connection" in message.lower() or "refused" in message.lower():
                return (
                    False,
                    f"Cannot connect to Ollama at {self.base_url}. "
                    "Please install and start Ollama: https://ollama.ai",
                )
            return False, f"Ollama error: {exc}"

        if not _matches_selected_model(self.model, available_models):
            return (
                False,
                f"Model '{self.model}' not found in Ollama. "
                f"Available: {available_models}. Run: ollama pull {self.model}",
            )
        return True, f"Connected to Ollama - model '{self.model}' ready"

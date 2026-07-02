"""HuggingFace Inference API provider."""

from __future__ import annotations

from typing import Any

from .provider import LLMProvider

SUPPORTED_MODELS = [
    "mistralai/Mistral-7B-Instruct-v0.3",
    "meta-llama/Meta-Llama-3-8B-Instruct",
    "HuggingFaceH4/zephyr-7b-beta",
]
DEFAULT_MODEL = "mistralai/Mistral-7B-Instruct-v0.3"


def _normalize_content(content: object) -> str:
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


class HuggingFaceProvider(LLMProvider):
    """Send prompts to the HuggingFace Inference API."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        if not api_key:
            raise ValueError("A HuggingFace API token is required for this provider.")
        self.api_key = api_key
        self.model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from huggingface_hub import InferenceClient
            except ImportError as exc:
                raise RuntimeError(
                    "The 'huggingface-hub' package is not installed. "
                    "Run: pip install huggingface-hub"
                ) from exc

            self._client = InferenceClient(model=self.model, token=self.api_key)
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

        # HuggingFace Inference API does not support constrained decoding;
        # fall back to json_mode.
        if json_schema is not None:
            json_mode = True

        try:
            kwargs: dict = dict(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens or 2048,
            )
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            completion = client.chat.completions.create(**kwargs)
            content = _normalize_content(completion.choices[0].message.content)
        except Exception as exc:
            prompt = f"{system_prompt.strip()}\n\n{user_prompt.strip()}".strip()
            try:
                content = str(
                    client.text_generation(
                        prompt,
                        max_new_tokens=2048,
                        temperature=temperature,
                        return_full_text=False,
                    )
                )
            except Exception as fallback_exc:
                raise RuntimeError(
                    f"HuggingFace inference failed: {fallback_exc}"
                ) from exc

        if not content.strip():
            raise RuntimeError("HuggingFace returned an empty response.")
        return content

    def get_model_name(self) -> str:
        return f"huggingface/{self.model}"

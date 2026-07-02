"""Session-level LLM cost tracker — logs token usage and estimated cost to terminal."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# Pricing per 1M tokens (input, output) in USD
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    # Anthropic
    "claude-opus-4-1-20250805": (15.00, 75.00),
    "claude-opus-4-20250514": (15.00, 75.00),
    "claude-sonnet-4-20250514": (3.00, 15.00),
    "claude-3-7-sonnet-20250219": (3.00, 15.00),
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-3-haiku-20240307": (0.25, 1.25),
    # OpenRouter — DeepSeek
    "deepseek/deepseek-chat": (0.14, 0.28),
    "deepseek/deepseek-chat-v3-0324": (0.14, 0.28),
    "deepseek/deepseek-r1": (0.55, 2.19),
    # OpenRouter — Google
    "google/gemini-2.0-flash-001": (0.10, 0.40),
    "google/gemini-2.5-flash-preview": (0.15, 0.60),
    "google/gemini-pro-1.5": (1.25, 5.00),
    "google/gemma-2-27b-it": (0.27, 0.27),
    # OpenRouter — Qwen
    "qwen/qwen-2.5-72b-instruct": (0.36, 0.36),
    "qwen/qwen-2.5-32b-instruct": (0.12, 0.12),
    "qwen/qwen-2.5-coder-32b-instruct": (0.12, 0.12),
    # OpenRouter — Meta Llama
    "meta-llama/llama-3.1-70b-instruct": (0.40, 0.40),
    "meta-llama/llama-3.1-8b-instruct": (0.05, 0.05),
    "meta-llama/llama-3.3-70b-instruct": (0.30, 0.30),
    # OpenRouter — Mistral
    "mistralai/mistral-7b-instruct": (0.06, 0.06),
    "mistralai/mistral-small-24b": (0.10, 0.30),
    "mistralai/mixtral-8x7b-instruct": (0.24, 0.24),
    "mistralai/mistral-nemo": (0.07, 0.07),
    # OpenRouter — Microsoft
    "microsoft/phi-3-medium-128k-instruct": (0.14, 0.14),
    # OpenRouter — Nous
    "nousresearch/hermes-3-llama-3.1-70b": (0.40, 0.40),
    # OpenRouter — Free tier (rate-limited)
    "deepseek/deepseek-r1:free": (0.0, 0.0),
    "qwen/qwen3-coder-480b-a35b-instruct:free": (0.0, 0.0),
    "meta-llama/llama-3.3-70b-instruct:free": (0.0, 0.0),
    "nvidia/nemotron-3-super-120b-a12b:free": (0.0, 0.0),
}


@dataclass
class TokenUsage:
    """Token counts from a single LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        pricing = _get_pricing(self.model)
        input_cost = (self.input_tokens / 1_000_000) * pricing[0]
        output_cost = (self.output_tokens / 1_000_000) * pricing[1]
        return input_cost + output_cost


@dataclass
class SessionCostTracker:
    """Accumulates token usage across an entire session."""

    calls: list[TokenUsage] = field(default_factory=list)
    session_start: float = field(default_factory=time.time)

    @property
    def total_input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def total_cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def num_calls(self) -> int:
        return len(self.calls)

    def record(self, usage: TokenUsage) -> None:
        """Record a new API call and print cost to terminal."""
        self.calls.append(usage)
        _print_usage(usage, self)

    def summary(self) -> str:
        """Return a formatted summary string."""
        elapsed = time.time() - self.session_start
        mins = int(elapsed // 60)
        return (
            f"Session Cost: ${self.total_cost_usd:.4f} | "
            f"Calls: {self.num_calls} | "
            f"Tokens: {self.total_input_tokens:,} in + {self.total_output_tokens:,} out | "
            f"Duration: {mins}m"
        )


def _get_pricing(model: str) -> tuple[float, float]:
    """Get (input_price, output_price) per 1M tokens for a model."""
    if model in _MODEL_PRICING:
        return _MODEL_PRICING[model]
    for key, pricing in _MODEL_PRICING.items():
        if key in model or model in key:
            return pricing
    return (1.00, 3.00)  # Conservative default


def _print_usage(usage: TokenUsage, tracker: SessionCostTracker) -> None:
    """Print usage to terminal (stdout)."""
    print(
        f"\033[36m[LLM Cost]\033[0m "
        f"Call #{tracker.num_calls}: "
        f"{usage.input_tokens:,} in + {usage.output_tokens:,} out = "
        f"${usage.cost_usd:.4f} | "
        f"\033[33mSession total: ${tracker.total_cost_usd:.4f}\033[0m "
        f"({tracker.total_tokens:,} tokens, {tracker.num_calls} calls)"
    )


# Global singleton tracker for the session
_tracker: SessionCostTracker | None = None


def get_cost_tracker() -> SessionCostTracker:
    """Get or create the global session cost tracker."""
    global _tracker
    if _tracker is None:
        _tracker = SessionCostTracker()
    return _tracker


def reset_cost_tracker() -> None:
    """Reset the session cost tracker."""
    global _tracker
    _tracker = SessionCostTracker()

"""OPRO-style iterative prompt optimization for the second LLM.

Implements the "Optimization by PROmpting" approach (Yang et al., 2024):
an *optimizer LLM* rewrites the scenario-generation system prompt based
on scored outputs from previous iterations.

Usage
-----
    python scripts/optimize_prompt.py \
        --provider openai --model gpt-4o-mini \
        --iterations 5 \
        --benchmark tests/benchmark_cases/ \
        --out results/opro_run.json

The script does NOT modify any source files.  It writes the best prompt
and the full optimisation trajectory to disk so results can be reported
in the thesis.

Scoring signal
--------------
Each benchmark case is a JSON file containing:
  - first_llm_json: str  (verified first-LLM output)
  - simod_json: str       (SIMOD baseline)
  - expected_kpis: list   (KPI names that must appear as addressed)

The score for one case is computed from the ComparisonReport:
  - +1 per addressed KPI
  - -1 per misaligned KPI
  - +0.5 per modification with numeric delta
  - -0.5 per validation error

The aggregate score across all benchmark cases is what the optimizer
LLM sees when deciding how to rewrite the prompt.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Ensure the project root is on the path so imports work.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT / "goal_to_parameters"))

from llm.provider import LLMProvider  # noqa: E402
from second_llm.scenario_generator import (  # noqa: E402
    ScenarioGenerationResult,
    _extract_and_parse_json,
)
from second_llm.output_schema import ScenarioProposal  # noqa: E402
from second_llm.validation import validate_proposal  # noqa: E402
from second_llm.comparison import build_comparison_report  # noqa: E402

logger = logging.getLogger(__name__)

# ===================================================================
# Data structures
# ===================================================================


@dataclass
class BenchmarkCase:
    """One test case loaded from a benchmark JSON file."""

    name: str
    first_llm_json: str
    simod_json: str
    expected_kpis: list[str] = field(default_factory=list)


@dataclass
class CaseScore:
    """Score breakdown for one benchmark case on one prompt."""

    case_name: str
    addressed_kpis: int = 0
    total_kpis: int = 0
    misaligned: int = 0
    numeric_deltas: int = 0
    validation_errors: int = 0
    parse_failed: bool = False
    raw_score: float = 0.0


@dataclass
class IterationResult:
    """One full iteration of the OPRO loop."""

    iteration: int
    prompt_text: str
    case_scores: list[CaseScore] = field(default_factory=list)
    aggregate_score: float = 0.0
    optimizer_reasoning: str = ""
    duration_seconds: float = 0.0


# ===================================================================
# Scoring
# ===================================================================


def _score_output(
    raw_output: str,
    first_llm_json: str,
    expected_kpis: list[str],
) -> CaseScore:
    """Score a single LLM output against a benchmark case."""
    score = CaseScore(case_name="")

    # Parse
    try:
        parsed = _extract_and_parse_json(raw_output)
    except (ValueError, json.JSONDecodeError):
        score.parse_failed = True
        score.raw_score = -5.0
        return score

    # Validate schema
    try:
        proposal = ScenarioProposal.model_validate(parsed)
    except Exception:
        score.parse_failed = True
        score.raw_score = -3.0
        return score

    # Post-schema validation
    vr = validate_proposal(proposal)
    score.validation_errors = len(vr.errors)

    # Comparison report
    report = build_comparison_report(first_llm_json, proposal)
    score.total_kpis = report.total_kpis
    score.addressed_kpis = report.addressed_kpis
    score.misaligned = len(report.misaligned_kpis)
    score.numeric_deltas = sum(
        1 for d in report.parameter_deltas if d.has_numeric_delta
    )

    # Compute raw score
    score.raw_score = (
        score.addressed_kpis * 1.0
        - score.misaligned * 1.0
        + score.numeric_deltas * 0.5
        - score.validation_errors * 0.5
    )
    return score


# ===================================================================
# Optimizer prompt
# ===================================================================

_OPTIMIZER_SYSTEM = """\
You are a prompt engineer optimising a system prompt for a BPM \
simulation scenario generator.  The system prompt instructs an LLM to \
read evidence (KPIs, SIMOD baseline, literature) and produce a JSON \
ScenarioProposal.

Your job: given the current prompt and its scored performance on \
benchmark cases, produce an improved version of the system prompt.

Rules:
- Keep the same overall task (generate a ScenarioProposal JSON).
- Keep ALL hard constraints (gateway sums, distribution params, etc.).
- You may reorder, rephrase, add, or remove reasoning instructions.
- Focus on the weakest scores — what went wrong and why.
- Output ONLY the new system prompt text.  No explanation, no fences.\
"""


def _build_optimizer_user_prompt(
    current_prompt: str,
    history: list[IterationResult],
) -> str:
    """Build the user prompt for the optimizer LLM."""
    lines = [
        "## Current system prompt\n",
        f"```\n{current_prompt}\n```\n",
        "## Scoring history (most recent last)\n",
    ]
    for it in history[-5:]:  # Show last 5 iterations
        lines.append(
            f"Iteration {it.iteration}: aggregate_score={it.aggregate_score:.2f}"
        )
        for cs in it.case_scores:
            status = "PARSE_FAIL" if cs.parse_failed else "OK"
            lines.append(
                f"  - {cs.case_name}: score={cs.raw_score:.1f} "
                f"addressed={cs.addressed_kpis}/{cs.total_kpis} "
                f"misaligned={cs.misaligned} "
                f"val_errors={cs.validation_errors} [{status}]"
            )
    lines.append(
        "\n## Task\n"
        "Rewrite the system prompt to improve the aggregate score. "
        "Focus on reducing parse failures, misaligned KPIs, and "
        "validation errors.  Output ONLY the new prompt text."
    )
    return "\n".join(lines)


# ===================================================================
# Main loop
# ===================================================================


def load_benchmark(benchmark_dir: Path) -> list[BenchmarkCase]:
    """Load benchmark cases from a directory of JSON files."""
    cases: list[BenchmarkCase] = []
    for p in sorted(benchmark_dir.glob("*.json")):
        with open(p) as f:
            data = json.load(f)
        cases.append(BenchmarkCase(
            name=p.stem,
            first_llm_json=data.get("first_llm_json", "{}"),
            simod_json=data.get("simod_json", "{}"),
            expected_kpis=data.get("expected_kpis", []),
        ))
    if not cases:
        raise FileNotFoundError(
            f"No benchmark JSON files found in {benchmark_dir}"
        )
    return cases


def run_opro(
    provider: LLMProvider,
    initial_prompt: str,
    benchmark: list[BenchmarkCase],
    iterations: int = 5,
    temperature: float = 0.3,
) -> list[IterationResult]:
    """Run the OPRO optimisation loop.

    Parameters
    ----------
    provider:
        LLM provider used for both generation and optimisation.
    initial_prompt:
        The starting system prompt.
    benchmark:
        Loaded benchmark cases.
    iterations:
        Number of optimisation rounds.
    temperature:
        Temperature for scenario generation calls.

    Returns
    -------
    list[IterationResult]
        Full trajectory including prompts, scores, and reasoning.
    """
    history: list[IterationResult] = []
    current_prompt = initial_prompt

    for i in range(iterations):
        t0 = time.time()
        logger.info("OPRO iteration %d/%d", i + 1, iterations)

        # --- Evaluate current prompt on all benchmark cases ---
        case_scores: list[CaseScore] = []
        for case in benchmark:
            # Build a minimal user prompt with the case data
            user_prompt = (
                f"## Verified KPI Targets\n```json\n{case.first_llm_json}\n```\n\n"
                f"## SIMOD Baseline\n```json\n{case.simod_json}\n```\n\n"
                "## Your Task\nProduce a ScenarioProposal JSON."
            )
            try:
                raw_output = provider.generate(
                    system_prompt=current_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    json_mode=True,
                )
            except Exception as exc:
                logger.warning("Generation failed for case %s: %s", case.name, exc)
                cs = CaseScore(case_name=case.name, parse_failed=True, raw_score=-5.0)
                case_scores.append(cs)
                continue

            cs = _score_output(raw_output, case.first_llm_json, case.expected_kpis)
            cs.case_name = case.name
            case_scores.append(cs)

        aggregate = sum(cs.raw_score for cs in case_scores)
        elapsed = time.time() - t0

        iteration_result = IterationResult(
            iteration=i + 1,
            prompt_text=current_prompt,
            case_scores=case_scores,
            aggregate_score=aggregate,
            duration_seconds=elapsed,
        )
        history.append(iteration_result)

        logger.info(
            "Iteration %d score: %.2f (%.1fs)",
            i + 1, aggregate, elapsed,
        )

        # --- Ask optimizer LLM to rewrite the prompt ---
        if i < iterations - 1:  # Skip on last iteration
            optimizer_user = _build_optimizer_user_prompt(current_prompt, history)
            try:
                new_prompt = provider.generate(
                    system_prompt=_OPTIMIZER_SYSTEM,
                    user_prompt=optimizer_user,
                    temperature=0.7,
                )
                # Sanity check: the new prompt should be substantial
                if len(new_prompt.strip()) > 200:
                    iteration_result.optimizer_reasoning = (
                        f"Rewrote prompt ({len(current_prompt)} -> "
                        f"{len(new_prompt.strip())} chars)"
                    )
                    current_prompt = new_prompt.strip()
                else:
                    iteration_result.optimizer_reasoning = (
                        "Optimizer output too short; keeping current prompt."
                    )
            except Exception as exc:
                logger.warning("Optimizer call failed: %s", exc)
                iteration_result.optimizer_reasoning = f"Optimizer failed: {exc}"

    return history


def save_results(history: list[IterationResult], output_path: Path) -> None:
    """Save the full OPRO trajectory to a JSON file."""
    best = max(history, key=lambda r: r.aggregate_score)

    data = {
        "best_iteration": best.iteration,
        "best_score": best.aggregate_score,
        "best_prompt": best.prompt_text,
        "trajectory": [
            {
                "iteration": r.iteration,
                "aggregate_score": r.aggregate_score,
                "duration_seconds": r.duration_seconds,
                "optimizer_reasoning": r.optimizer_reasoning,
                "case_scores": [
                    {
                        "case_name": cs.case_name,
                        "raw_score": cs.raw_score,
                        "addressed_kpis": cs.addressed_kpis,
                        "total_kpis": cs.total_kpis,
                        "misaligned": cs.misaligned,
                        "validation_errors": cs.validation_errors,
                        "parse_failed": cs.parse_failed,
                    }
                    for cs in r.case_scores
                ],
            }
            for r in history
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    logger.info("Results saved to %s", output_path)
    print(f"\nBest prompt (iteration {best.iteration}, score {best.aggregate_score:.2f}):")
    print(f"  Saved to: {output_path}")


# ===================================================================
# CLI
# ===================================================================


def _create_provider(provider_name: str, model: str, api_key: str) -> LLMProvider:
    """Instantiate an LLM provider by name."""
    if provider_name == "openai":
        from llm.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=api_key, model=model)
    elif provider_name == "anthropic":
        from llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=api_key, model=model)
    elif provider_name == "ollama":
        from llm.ollama_provider import OllamaProvider
        return OllamaProvider(model=model)
    elif provider_name == "openrouter":
        from llm.openrouter_provider import OpenRouterProvider
        return OpenRouterProvider(api_key=api_key, model=model)
    else:
        raise ValueError(f"Unknown provider: {provider_name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OPRO-style prompt optimisation for the second LLM."
    )
    parser.add_argument(
        "--provider", required=True,
        choices=["openai", "anthropic", "ollama", "openrouter"],
    )
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--api-key", default="")
    parser.add_argument(
        "--benchmark", required=True,
        help="Directory containing benchmark case JSON files.",
    )
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument(
        "--out", default="results/opro_run.json",
        help="Output path for the optimisation trajectory.",
    )
    parser.add_argument("--temperature", type=float, default=0.3)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Load the current system prompt as starting point
    from prompts.scenario_proposal_prompt import _SYSTEM_PROMPT
    initial_prompt = _SYSTEM_PROMPT

    provider = _create_provider(args.provider, args.model, args.api_key)
    benchmark = load_benchmark(Path(args.benchmark))
    logger.info("Loaded %d benchmark cases", len(benchmark))

    history = run_opro(
        provider=provider,
        initial_prompt=initial_prompt,
        benchmark=benchmark,
        iterations=args.iterations,
        temperature=args.temperature,
    )

    save_results(history, Path(args.out))


if __name__ == "__main__":
    main()

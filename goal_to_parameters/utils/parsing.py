"""Utilities for parsing LLM JSON output into structured KPI models."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from models import EvidenceBasis, KPIGenerationResult

CODE_FENCE_PATTERN = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE | re.MULTILINE)
JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


def _normalize_kpi_payload(kpi_payload: object) -> object:
    if not isinstance(kpi_payload, dict):
        return kpi_payload

    normalized = dict(kpi_payload)
    category = normalized.get("category")
    if isinstance(category, str) and category.strip().lower() == "occurrence":
        normalized["category"] = "throughput"

    evidence_basis = normalized.get("evidence_basis")
    if evidence_basis in (None, ""):
        normalized["evidence_basis"] = (
            EvidenceBasis.BOTH.value if normalized.get("supported_by_log") is True
            else EvidenceBasis.PROCESS_DESCRIPTION_ONLY.value
        )

    if normalized.get("supported_by_log") is None:
        normalized["supported_by_log"] = normalized.get("evidence_basis") in {
            EvidenceBasis.EVENT_LOG_ONLY.value,
            EvidenceBasis.BOTH.value,
            EvidenceBasis.PROXY_FROM_LOG.value,
        }

    return normalized


def _normalize_generation_payload(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload

    normalized = dict(payload)
    kpis = normalized.get("kpis")
    if isinstance(kpis, list):
        normalized["kpis"] = [_normalize_kpi_payload(kpi_payload) for kpi_payload in kpis]
    return normalized


class KPIParsingError(Exception):
    """Raised when model output cannot be converted into a KPI generation result."""

    def __init__(self, message: str, raw_output: str):
        super().__init__(message)
        self.raw_output = raw_output


def strip_code_fences(raw_output: str) -> str:
    """Remove leading and trailing markdown code fences from a response."""
    return CODE_FENCE_PATTERN.sub("", raw_output).strip()


def extract_json_object(raw_output: str) -> str | None:
    """Extract the outermost JSON object from a response containing extra text."""
    match = JSON_OBJECT_PATTERN.search(raw_output)
    if match is None:
        return None
    return match.group(0).strip()


def _load_json(raw_output: str) -> dict:
    cleaned_output = strip_code_fences(raw_output)

    try:
        return json.loads(cleaned_output)
    except json.JSONDecodeError:
        extracted_json = extract_json_object(cleaned_output)
        if extracted_json is None:
            raise KPIParsingError(
                "Could not find a valid JSON object in the model response.",
                raw_output,
            ) from None

    try:
        return json.loads(extracted_json)
    except json.JSONDecodeError as exc:
        raise KPIParsingError(
            f"Model response was not valid JSON: {exc}",
            raw_output,
        ) from exc


def parse_kpi_generation_payload(raw_output: str) -> KPIGenerationResult:
    """
    Parse raw model output into a validated KPIGenerationResult instance.

    Only the final fields used by the app are validated:
    - simulation_goal_structured
    - kpis
    - reasoning
    """
    try:
        payload = _normalize_generation_payload(_load_json(raw_output))
        if not payload.get("reasoning"):
            payload["reasoning"] = "Reasoning was not provided by the model."
        return KPIGenerationResult.model_validate(payload)
    except KPIParsingError:
        raise
    except ValidationError as exc:
        raise KPIParsingError(
            f"Model output did not match the KPI schema: {exc}",
            raw_output,
        ) from exc


def parse_kpi_generation_result(raw_output: str) -> KPIGenerationResult:
    return parse_kpi_generation_payload(raw_output)

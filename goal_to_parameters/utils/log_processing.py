"""Structured event-log profiling and KPI grounding helpers.

Reads a CSV event log and produces a reusable profile that can:
- ground LLM prompts with structured evidence,
- preview the log characteristics in the UI, and
- locally assess whether generated KPIs are actually supported by the log.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, BinaryIO
import unicodedata

from .context_analysis import (
    DEFAULT_ASSOCIATION_ANALYSIS_CONFIG,
    DEFAULT_CONTEXT_FACTOR_SCREENING_CONFIG,
    analyze_contextual_impact,
    export_association_analysis_thresholds,
)

# Common column-name aliases for auto-detection. Matching is done on normalized
# header names, so variants like "Case ID ", "activity-label", or accented text
# can still resolve cleanly.
_CASE_ID_ALIASES = {
    "case",
    "case id",
    "case identifier",
    "case number",
    "case no",
    "case_id",
    "caseid",
    "case:concept:name",
    "case_id:concept:name",
    "process instance",
    "process instance id",
    "process id",
    "instance id",
    "workflow id",
    "trace id",
    "request id",
    "order id",
    "application id",
    "fall id",
    "fallnummer",
    "vorgang id",
    "vorgangsnummer",
    "cas id",
    "identifiant cas",
}
_ACTIVITY_ALIASES = {
    "activity",
    "activity name",
    "activity label",
    "activity_name",
    "concept:name",
    "event",
    "event name",
    "event label",
    "event_name",
    "task",
    "task name",
    "task label",
    "step",
    "step name",
    "activity:concept:name",
    "operation",
    "action",
    "activite",
    "aktivitat",
    "aktivitaet",
    "tatigkeit",
    "taetigkeit",
    "etape",
}
_TIMESTAMP_ALIASES = {
    "timestamp",
    "time",
    "event time",
    "event timestamp",
    "time stamp",
    "date time",
    "datetime",
    "start_time",
    "start time",
    "start timestamp",
    "start",
    "end_time",
    "end time",
    "end timestamp",
    "finish time",
    "finish timestamp",
    "completion time",
    "completion timestamp",
    "complete time",
    "complete timestamp",
    "complete_timestamp",
    "completed at",
    "finished at",
    "time:timestamp",
    "zeitstempel",
    "abschlusszeit",
    "endzeit",
}
_RESOURCE_ALIASES = {
    "resource",
    "resource name",
    "resource id",
    "org:resource",
    "role",
    "org:role",
    "user",
    "user name",
    "username",
    "agent",
    "performer",
    "performed by",
    "executed by",
    "completed by",
    "assigned to",
    "owner",
    "assignee",
    "employee",
    "staff",
    "actor",
    "operator",
    "resource owner",
    "bearbeiter",
    "mitarbeiter",
    "benutzer",
    "utilisateur",
    "ressource",
}

# Limits to keep the profile compact.
_MAX_ACTIVITY_ROWS = 30
_MAX_RESOURCE_ROWS = 15
_MAX_ATTRIBUTE_COLS = 20
_MAX_VARIANT_ROWS = 10
_MAX_TRANSITION_ROWS = 20
_MAX_DFG_ROWS = 20
_MAX_REWORK_ROWS = 10
_MAX_CONSISTENCY_ITEMS = 3
_MAX_CONTEXT_FACTOR_ROWS = 20
_MAX_CONTEXT_RELATIONSHIPS = 8
_GENERIC_ACTIVITY_TOKENS = {
    "process",
    "case",
    "cases",
    "task",
    "tasks",
    "step",
    "steps",
    "team",
    "department",
    "office",
    "manager",
    "customer",
    "patient",
}
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "before",
    "begins",
    "by",
    "for",
    "from",
    "if",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "then",
    "to",
    "when",
    "while",
    "with",
}
_TOKEN_NORMALIZATION_MAP = {
    "begins": "begin",
    "started": "start",
    "starts": "start",
    "ending": "end",
    "ends": "end",
    "made": "make",
    "makes": "make",
    "making": "make",
    "submits": "submit",
    "submitted": "submit",
    "submission": "submit",
    "requests": "request",
    "requested": "request",
    "requesting": "request",
    "reviews": "review",
    "reviewed": "review",
    "reviewing": "review",
    "checks": "check",
    "checked": "check",
    "checking": "check",
    "validates": "validate",
    "validated": "validate",
    "validating": "validate",
    "evaluates": "evaluate",
    "evaluated": "evaluate",
    "evaluating": "evaluate",
    "assessment": "assess",
    "assessor": "assess",
    "assesses": "assess",
    "assessed": "assess",
    "notify": "notify",
    "notifies": "notify",
    "notified": "notify",
    "notification": "notify",
    "documents": "document",
    "claims": "claim",
    "customers": "customer",
    "applications": "application",
    "funds": "fund",
    "processes": "process",
}
_TOKEN_SYNONYMS = {
    "assess": {"assess", "evaluate"},
    "evaluate": {"evaluate", "assess"},
    "check": {"check", "validate", "verify"},
    "validate": {"validate", "check", "verify"},
    "verify": {"verify", "check", "validate"},
    "submit": {"submit", "intake"},
    "notify": {"notify", "notification"},
    "review": {"review", "recommendation"},
}
_NON_ACTIVITY_HINT_TOKENS = {"begin", "start", "end", "finish"}
_ACTION_HINT_TOKENS = {
    "submit",
    "request",
    "resubmit",
    "check",
    "validate",
    "verify",
    "assess",
    "evaluate",
    "review",
    "approve",
    "reject",
    "notify",
    "disburse",
    "prepare",
    "schedule",
    "explain",
    "perform",
    "make",
}


def _normalize_header_name(value: str) -> str:
    """Normalize a column name for tolerant header matching."""

    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).strip()
    return re.sub(r"\s+", " ", normalized)


def _tokens_fuzzily_match(header_tokens: tuple[str, ...], alias_tokens: tuple[str, ...]) -> bool:
    """Return True when each alias token has a close header-token match."""

    if not header_tokens or not alias_tokens or len(header_tokens) < len(alias_tokens):
        return False

    used_indices: set[int] = set()
    for alias_token in alias_tokens:
        best_score = 0.0
        best_index = -1
        for index, header_token in enumerate(header_tokens):
            if index in used_indices:
                continue
            score = SequenceMatcher(None, header_token, alias_token).ratio()
            if score > best_score:
                best_score = score
                best_index = index

        threshold = 0.9 if len(alias_token) <= 5 else 0.82
        if best_score < threshold:
            return False
        used_indices.add(best_index)

    return True


def _detect_column(headers_lower: list[str], aliases: set[str]) -> str | None:
    """Return the lower-cased header that best matches one of *aliases*, or None."""

    normalized_aliases = {_normalize_header_name(alias) for alias in aliases if alias}
    compact_aliases = {alias.replace(" ", "") for alias in normalized_aliases}

    for header in headers_lower:
        normalized_header = _normalize_header_name(header)
        if not normalized_header:
            continue
        if normalized_header in normalized_aliases:
            return header
        if normalized_header.replace(" ", "") in compact_aliases:
            return header

    best_match: str | None = None
    best_score = 0.0

    for header in headers_lower:
        normalized_header = _normalize_header_name(header)
        header_tokens = tuple(normalized_header.split())
        if not header_tokens:
            continue

        header_token_set = set(header_tokens)
        for normalized_alias in normalized_aliases:
            alias_tokens = tuple(normalized_alias.split())
            if not alias_tokens:
                continue

            score = 0.0
            alias_token_set = set(alias_tokens)
            if len(alias_tokens) > 1 and alias_token_set.issubset(header_token_set):
                score = 0.96
            elif len(alias_tokens) > 1 and _tokens_fuzzily_match(header_tokens, alias_tokens):
                score = 0.86
            elif len(alias_tokens) == 1 and len(header_tokens) == 1:
                score = SequenceMatcher(None, header_tokens[0], alias_tokens[0]).ratio()

            if score > best_score:
                best_score = score
                best_match = header

    return best_match if best_score >= 0.86 else None


def _round_number(value: float, digits: int = 2) -> float:
    return round(value, digits)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * percentile
    lower_index = int(rank)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    weight = rank - lower_index
    return sorted_values[lower_index] * (1 - weight) + sorted_values[upper_index] * weight


def _parse_timestamp(raw_value: str) -> datetime | None:
    value = raw_value.strip()
    if not value:
        return None

    candidates = [value]
    if value.endswith("Z"):
        candidates.append(f"{value[:-1]}+00:00")

    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _tokenize_meaningful_text(value: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", value.lower())
    normalized_tokens: list[str] = []
    for token in tokens:
        normalized_token = _normalize_meaningful_token(token)
        if (
            len(normalized_token) > 2
            and normalized_token not in _STOPWORDS
            and normalized_token not in _GENERIC_ACTIVITY_TOKENS
        ):
            normalized_tokens.append(normalized_token)
    return normalized_tokens


def _normalize_meaningful_token(token: str) -> str:
    normalized = _TOKEN_NORMALIZATION_MAP.get(token, token)

    for suffix in ("ing", "ed"):
        if len(normalized) > 4 and normalized.endswith(suffix):
            candidate = normalized[: -len(suffix)]
            if len(candidate) > 2:
                normalized = candidate
                break
    return _TOKEN_NORMALIZATION_MAP.get(normalized, normalized)


def _expand_tokens_with_synonyms(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for token in tokens:
        expanded.update(_TOKEN_SYNONYMS.get(token, set()))
    return expanded


def _extract_process_clauses(process_description: str) -> list[str]:
    clauses: list[str] = []
    seen: set[str] = set()
    for segment in re.split(r"[.;]", process_description):
        for clause in re.split(r",|\bthen\b|\bafter\b|\bbefore\b|\bwhen\b", segment, flags=re.IGNORECASE):
            cleaned = clause.strip()
            if not cleaned:
                continue
            normalized = re.sub(r"\s+", " ", cleaned.lower())
            if normalized not in seen:
                seen.add(normalized)
                clauses.append(cleaned)
    return clauses


def _token_overlap_score(left_tokens: set[str], right_tokens: set[str]) -> float:
    if not left_tokens or not right_tokens:
        return 0.0
    expanded_left = _expand_tokens_with_synonyms(left_tokens)
    expanded_right = _expand_tokens_with_synonyms(right_tokens)
    overlap = expanded_left & expanded_right
    return len(overlap) / max(1, min(len(left_tokens), len(right_tokens)))


def _phrase_match_score(left_text: str, right_text: str) -> float:
    left_tokens = set(_tokenize_meaningful_text(left_text))
    right_tokens = set(_tokenize_meaningful_text(right_text))
    token_score = _token_overlap_score(left_tokens, right_tokens)
    normalized_left = " ".join(sorted(left_tokens))
    normalized_right = " ".join(sorted(right_tokens))
    fuzzy_score = SequenceMatcher(None, normalized_left, normalized_right).ratio()
    return max(token_score, fuzzy_score)


def _activity_text_supported(activity_name: str, process_description: str) -> bool:
    activity_tokens = set(_tokenize_meaningful_text(activity_name))
    if not activity_tokens:
        return True

    clauses = _extract_process_clauses(process_description)
    best_score = max((_phrase_match_score(activity_name, clause) for clause in clauses), default=0.0)
    if best_score >= 0.74:
        return True

    description_tokens = set(_tokenize_meaningful_text(process_description))
    return _token_overlap_score(activity_tokens, description_tokens) >= 0.8


def _extract_process_activity_hints(process_description: str) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()

    for clause in _extract_process_clauses(process_description):
        tokens = [
            token
            for token in _tokenize_meaningful_text(clause)
            if token not in _NON_ACTIVITY_HINT_TOKENS
        ]
        if len(tokens) < 2:
            continue
        if not (_expand_tokens_with_synonyms(set(tokens)) & _ACTION_HINT_TOKENS):
            continue
        hint = " ".join(tokens[:6])
        if hint not in seen:
            seen.add(hint)
            hints.append(hint)
    return hints


def _hint_supported_by_log(hint: str, log_activities: list[str]) -> bool:
    if not _tokenize_meaningful_text(hint):
        return True

    for activity in log_activities:
        if _phrase_match_score(hint, activity) >= 0.74:
            return True
    return False


def _looks_like_cost_attribute(column_name: str) -> bool:
    normalized = column_name.lower()
    return any(token in normalized for token in ("cost", "price", "amount", "fee", "expense", "effort", "labor", "labour"))


def _looks_like_quality_attribute(column_name: str) -> bool:
    normalized = column_name.lower()
    return any(token in normalized for token in ("quality", "error", "defect", "compliance", "status", "outcome", "result", "complete"))


def _looks_like_timestamp_attribute(column_name: str) -> bool:
    normalized = _normalize_header_name(column_name)
    return any(token in normalized for token in ("time", "timestamp", "date", "start", "end", "finish", "complete"))


def _safe_float(value: str) -> float | None:
    try:
        return float(value.strip())
    except (AttributeError, ValueError):
        return None


def _infer_context_value_type(values: list[str]) -> str:
    non_empty_values = [value for value in values if value]
    if len(non_empty_values) < 3:
        return "categorical"

    numeric_values = [value for value in non_empty_values if _safe_float(value) is not None]
    if numeric_values and len(numeric_values) / len(non_empty_values) >= 0.9:
        return "numeric"
    return "categorical"


def _derive_temporal_context(timestamp: datetime, *, prefix: str) -> dict[str, Any]:
    quarter = ((timestamp.month - 1) // 3) + 1
    return {
        f"{prefix}_day_of_week": timestamp.strftime("%A"),
        f"{prefix}_hour_of_day": timestamp.hour,
        f"{prefix}_month": timestamp.strftime("%B"),
        f"{prefix}_quarter": f"Q{quarter}",
    }


def _looks_like_identifier_column(column_name: str) -> bool:
    normalized = _normalize_header_name(column_name)
    return any(
        token in normalized
        for token in ("id", "identifier", "uuid", "guid", "reference", "number", "trace", "instance")
    )


def _looks_like_id_like_values(values: list[str]) -> bool:
    non_empty_values = [value for value in values if value]
    if len(non_empty_values) < 3:
        return False
    id_like_count = 0
    for value in non_empty_values:
        normalized = value.strip().lower()
        if len(normalized) >= 8 and re.fullmatch(r"[a-z0-9\-_]+", normalized):
            digit_count = sum(char.isdigit() for char in normalized)
            if digit_count >= max(2, len(normalized) // 3):
                id_like_count += 1
    return id_like_count / len(non_empty_values) >= 0.8


def _mean_token_count(values: list[str]) -> float:
    token_counts = [len(re.findall(r"\w+", value)) for value in values if value]
    if not token_counts:
        return 0.0
    return sum(token_counts) / len(token_counts)


def _build_metric_metadata(
    *,
    has_sortable_timestamps: bool,
    auxiliary_timestamp_cols: list[str],
) -> dict[str, dict[str, Any]]:
    return {
        "case_cycle_time_hours": {
            "status": "derived_from_log" if has_sortable_timestamps else "unavailable",
            "derivation_notes": (
                "Derived from the first and last sortable timestamps per case."
                if has_sortable_timestamps
                else "Unavailable because sortable case timestamps were not detected."
            ),
        },
        "case_wait_time_hours": {
            "status": "derived_from_log" if has_sortable_timestamps else "unavailable",
            "derivation_notes": (
                "Derived as the sum of inter-event gaps within each case."
                if has_sortable_timestamps
                else "Unavailable because sortable timestamps were not detected."
            ),
        },
        "activity_wait_time_hours": {
            "status": "derived_from_log" if has_sortable_timestamps else "unavailable",
            "derivation_notes": (
                "Derived from the elapsed time between one event timestamp and the next event timestamp."
                if has_sortable_timestamps
                else "Unavailable because sortable event timestamps were not detected."
            ),
        },
        "activity_duration_hours": {
            "status": "approximated" if auxiliary_timestamp_cols else "unavailable",
            "derivation_notes": (
                "Approximated from per-event auxiliary timestamp attributes: "
                + ", ".join(auxiliary_timestamp_cols)
                if auxiliary_timestamp_cols
                else "Unavailable because no auxiliary start/end-like timestamp attributes were detected."
            ),
        },
    }


def _screen_factor_definition(
    factor_definition: dict[str, Any],
    *,
    screening_config: Any,
) -> tuple[bool, str | None, str | None]:
    name = factor_definition["name"]
    value_type = factor_definition["value_type"]
    uniqueness_ratio = factor_definition.get("uniqueness_ratio")
    missingness_ratio = factor_definition.get("missingness_ratio")
    mean_length = factor_definition.get("mean_value_length", 0.0) or 0.0
    mean_tokens = factor_definition.get("mean_token_count", 0.0) or 0.0

    if _looks_like_identifier_column(name) and (
        _looks_like_id_like_values(factor_definition.get("_observed_values", []))
        or (isinstance(uniqueness_ratio, (int, float)) and uniqueness_ratio >= screening_config.high_cardinality_ratio)
    ):
        return False, None, "Excluded because the column appears to be an identifier or technical key."

    if isinstance(missingness_ratio, (int, float)) and missingness_ratio > screening_config.max_missingness_ratio:
        return False, None, "Excluded because the column has too much missingness for stable association screening."

    if mean_length >= screening_config.max_free_text_mean_length or mean_tokens >= screening_config.max_free_text_mean_tokens:
        return False, None, "Excluded because the values look like free text rather than structured context."

    if value_type == "categorical" and isinstance(uniqueness_ratio, (int, float)):
        if uniqueness_ratio >= screening_config.exclude_high_uniqueness_ratio:
            return False, None, "Excluded because the categorical values are almost unique per observation."
        if uniqueness_ratio >= screening_config.high_cardinality_ratio:
            return False, None, "Excluded because the categorical factor is too high-cardinality for lightweight association screening."

    return True, "Included because the factor passed screening for completeness, cardinality, and structure.", None


def _build_context_factor_definitions(
    *,
    other_cols: list[str],
    case_events: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    factor_definitions: list[dict[str, Any]] = []
    excluded_factors: list[dict[str, Any]] = []
    total_cases = len(case_events)
    total_events = sum(len(events) for events in case_events.values())

    for column_name in other_cols:
        observed_values: list[str] = []
        non_empty_cases = 0
        stable_cases = 0
        non_empty_event_values = 0

        for events in case_events.values():
            case_values = {
                (event.get("attributes", {}).get(column_name) or "").strip()
                for event in events
                if (event.get("attributes", {}).get(column_name) or "").strip()
            }
            if case_values:
                non_empty_cases += 1
                observed_values.extend(sorted(case_values))
                if len(case_values) == 1:
                    stable_cases += 1
            non_empty_event_values += sum(
                1
                for event in events
                if (event.get("attributes", {}).get(column_name) or "").strip()
            )

        case_stability_ratio = (
            _round_number(stable_cases / non_empty_cases, 2)
            if non_empty_cases
            else None
        )
        scope = "event_level"
        if non_empty_cases >= 2 and case_stability_ratio is not None and case_stability_ratio >= 0.95:
            scope = "case_level"

        value_counter = Counter(value for value in observed_values if value)
        completeness_ratio = (
            _round_number(non_empty_event_values / total_events, 2)
            if total_events
            else None
        )
        missingness_ratio = (
            _round_number(1 - (non_empty_event_values / total_events), 2)
            if total_events
            else None
        )
        uniqueness_ratio = (
            _round_number(len(value_counter) / non_empty_event_values, 2)
            if non_empty_event_values
            else None
        )
        mean_length = (
            _round_number(sum(len(value) for value in observed_values) / len(observed_values), 2)
            if observed_values
            else 0.0
        )
        mean_tokens = _round_number(_mean_token_count(observed_values), 2)

        candidate_definition = {
            "name": column_name,
            "scope": scope,
            "value_type": _infer_context_value_type(observed_values),
            "distinct_values": len(value_counter),
            "completeness_ratio": completeness_ratio,
            "missingness_ratio": missingness_ratio,
            "uniqueness_ratio": uniqueness_ratio,
            "case_stability_ratio": case_stability_ratio,
            "sample_values": [value for value, _ in value_counter.most_common(4)],
            "observed_case_count": non_empty_cases,
            "total_case_count": total_cases,
            "observed_event_count": non_empty_event_values,
            "total_event_count": total_events,
            "mean_value_length": mean_length,
            "mean_token_count": mean_tokens,
            "_observed_values": observed_values,
        }
        included, inclusion_reason, exclusion_reason = _screen_factor_definition(
            candidate_definition,
            screening_config=DEFAULT_CONTEXT_FACTOR_SCREENING_CONFIG,
        )
        candidate_definition["screening_status"] = "included" if included else "excluded"
        candidate_definition["inclusion_reason"] = inclusion_reason
        candidate_definition["exclusion_reason"] = exclusion_reason
        candidate_definition.pop("_observed_values", None)

        if included:
            factor_definitions.append(candidate_definition)
        else:
            excluded_factors.append(candidate_definition)

    temporal_definitions = [
        {"name": "case_start_day_of_week", "scope": "temporal", "value_type": "categorical", "distinct_values": 7, "completeness_ratio": None, "missingness_ratio": 0.0, "uniqueness_ratio": None, "case_stability_ratio": None, "sample_values": [], "screening_status": "included", "inclusion_reason": "Derived temporal context is included by design.", "exclusion_reason": None},
        {"name": "case_start_hour_of_day", "scope": "temporal", "value_type": "numeric", "distinct_values": 24, "completeness_ratio": None, "missingness_ratio": 0.0, "uniqueness_ratio": None, "case_stability_ratio": None, "sample_values": [], "screening_status": "included", "inclusion_reason": "Derived temporal context is included by design.", "exclusion_reason": None},
        {"name": "case_start_month", "scope": "temporal", "value_type": "categorical", "distinct_values": 12, "completeness_ratio": None, "missingness_ratio": 0.0, "uniqueness_ratio": None, "case_stability_ratio": None, "sample_values": [], "screening_status": "included", "inclusion_reason": "Derived temporal context is included by design.", "exclusion_reason": None},
        {"name": "case_start_quarter", "scope": "temporal", "value_type": "categorical", "distinct_values": 4, "completeness_ratio": None, "missingness_ratio": 0.0, "uniqueness_ratio": None, "case_stability_ratio": None, "sample_values": [], "screening_status": "included", "inclusion_reason": "Derived temporal context is included by design.", "exclusion_reason": None},
        {"name": "event_day_of_week", "scope": "temporal", "value_type": "categorical", "distinct_values": 7, "completeness_ratio": None, "missingness_ratio": 0.0, "uniqueness_ratio": None, "case_stability_ratio": None, "sample_values": [], "screening_status": "included", "inclusion_reason": "Derived temporal context is included by design.", "exclusion_reason": None},
        {"name": "event_hour_of_day", "scope": "temporal", "value_type": "numeric", "distinct_values": 24, "completeness_ratio": None, "missingness_ratio": 0.0, "uniqueness_ratio": None, "case_stability_ratio": None, "sample_values": [], "screening_status": "included", "inclusion_reason": "Derived temporal context is included by design.", "exclusion_reason": None},
        {"name": "event_month", "scope": "temporal", "value_type": "categorical", "distinct_values": 12, "completeness_ratio": None, "missingness_ratio": 0.0, "uniqueness_ratio": None, "case_stability_ratio": None, "sample_values": [], "screening_status": "included", "inclusion_reason": "Derived temporal context is included by design.", "exclusion_reason": None},
        {"name": "event_quarter", "scope": "temporal", "value_type": "categorical", "distinct_values": 4, "completeness_ratio": None, "missingness_ratio": 0.0, "uniqueness_ratio": None, "case_stability_ratio": None, "sample_values": [], "screening_status": "included", "inclusion_reason": "Derived temporal context is included by design.", "exclusion_reason": None},
    ]
    factor_definitions.extend(temporal_definitions)
    return factor_definitions, excluded_factors


def _build_context_observations(
    *,
    case_events: dict[str, list[dict[str, Any]]],
    factor_definitions: list[dict[str, Any]],
    auxiliary_timestamp_cols: list[str],
    has_sortable_timestamps: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], dict[str, dict[str, Any]]]:
    case_level_names = {
        definition["name"]
        for definition in factor_definitions
        if definition["scope"] == "case_level"
    }
    event_level_names = {
        definition["name"]
        for definition in factor_definitions
        if definition["scope"] == "event_level"
    }

    case_observations: list[dict[str, Any]] = []
    activity_observations: list[dict[str, Any]] = []
    available_metrics: set[str] = set()
    metric_metadata = _build_metric_metadata(
        has_sortable_timestamps=has_sortable_timestamps,
        auxiliary_timestamp_cols=auxiliary_timestamp_cols,
    )

    for events in case_events.values():
        ordered_events = sorted(
            events,
            key=lambda item: (
                item["timestamp"] is None,
                item["timestamp"].isoformat() if item["timestamp"] is not None else "",
                item["order"],
            ),
        ) if has_sortable_timestamps else sorted(events, key=lambda item: item["order"])

        if not ordered_events:
            continue

        case_factors: dict[str, Any] = {}
        for factor_name in case_level_names:
            values = [
                (event.get("attributes", {}).get(factor_name) or "").strip()
                for event in ordered_events
                if (event.get("attributes", {}).get(factor_name) or "").strip()
            ]
            if values:
                case_factors[factor_name] = values[0]

        first_timestamp = ordered_events[0].get("timestamp")
        if first_timestamp is not None:
            case_factors.update(_derive_temporal_context(first_timestamp, prefix="case_start"))

        case_metrics: dict[str, float] = {}
        if has_sortable_timestamps:
            last_timestamp = ordered_events[-1].get("timestamp")
            if first_timestamp is not None and last_timestamp is not None:
                cycle_time_hours = (last_timestamp - first_timestamp).total_seconds() / 3600
                if cycle_time_hours >= 0:
                    case_metrics["case_cycle_time_hours"] = cycle_time_hours
                    available_metrics.add("case_cycle_time_hours")

            wait_time_hours = 0.0
            observed_wait = False
            for previous_event, next_event in zip(ordered_events, ordered_events[1:]):
                if previous_event["timestamp"] is None or next_event["timestamp"] is None:
                    continue
                gap_hours = (next_event["timestamp"] - previous_event["timestamp"]).total_seconds() / 3600
                if gap_hours >= 0:
                    wait_time_hours += gap_hours
                    observed_wait = True
            if observed_wait:
                case_metrics["case_wait_time_hours"] = wait_time_hours
                available_metrics.add("case_wait_time_hours")

        if case_metrics:
            case_observations.append({"factors": case_factors, "metrics": case_metrics})

        for index, event in enumerate(ordered_events):
            event_timestamp = event.get("timestamp")
            event_factors = dict(case_factors)
            for factor_name in event_level_names:
                factor_value = (event.get("attributes", {}).get(factor_name) or "").strip()
                if factor_value:
                    event_factors[factor_name] = factor_value
            if event_timestamp is not None:
                event_factors.update(_derive_temporal_context(event_timestamp, prefix="event"))

            event_metrics: dict[str, float] = {}
            next_event = ordered_events[index + 1] if index + 1 < len(ordered_events) else None
            if next_event is not None and event_timestamp is not None and next_event.get("timestamp") is not None:
                gap_hours = (next_event["timestamp"] - event_timestamp).total_seconds() / 3600
                if gap_hours >= 0:
                    event_metrics["activity_wait_time_hours"] = gap_hours
                    available_metrics.add("activity_wait_time_hours")

            candidate_timestamps = [event_timestamp] if event_timestamp is not None else []
            for column_name in auxiliary_timestamp_cols:
                parsed_timestamp = _parse_timestamp((event.get("attributes", {}).get(column_name) or "").strip())
                if parsed_timestamp is not None:
                    candidate_timestamps.append(parsed_timestamp)
            if len(candidate_timestamps) >= 2:
                event_duration_hours = (
                    max(candidate_timestamps) - min(candidate_timestamps)
                ).total_seconds() / 3600
                if event_duration_hours > 0:
                    event_metrics["activity_duration_hours"] = event_duration_hours
                    available_metrics.add("activity_duration_hours")

            if event_metrics:
                activity_observations.append(
                    {
                        "activity": event.get("activity"),
                        "factors": event_factors,
                        "metrics": event_metrics,
                    }
                )

    available_metric_names = sorted(available_metrics)
    return (
        case_observations,
        activity_observations,
        available_metric_names,
        {
            metric_name: metric_metadata.get(metric_name, {"status": "unavailable", "derivation_notes": "No provenance metadata available."})
            for metric_name in available_metric_names
        },
    )


def _build_context_profile(
    *,
    other_cols: list[str],
    case_events: dict[str, list[dict[str, Any]]],
    auxiliary_timestamp_cols: list[str],
    has_sortable_timestamps: bool,
) -> dict[str, Any]:
    factor_definitions, excluded_factors = _build_context_factor_definitions(
        other_cols=other_cols,
        case_events=case_events,
    )
    case_observations, activity_observations, available_metrics, metric_metadata = _build_context_observations(
        case_events=case_events,
        factor_definitions=factor_definitions,
        auxiliary_timestamp_cols=auxiliary_timestamp_cols,
        has_sortable_timestamps=has_sortable_timestamps,
    )
    association_analysis = analyze_contextual_impact(
        factor_definitions=factor_definitions,
        case_observations=case_observations,
        activity_observations=activity_observations,
        metric_metadata=metric_metadata,
        config=DEFAULT_ASSOCIATION_ANALYSIS_CONFIG,
    )

    return {
        "detected_factors": factor_definitions[:_MAX_CONTEXT_FACTOR_ROWS],
        "included_factors": factor_definitions[:_MAX_CONTEXT_FACTOR_ROWS],
        "excluded_factors": excluded_factors[:_MAX_CONTEXT_FACTOR_ROWS],
        "available_metrics": available_metrics,
        "metric_metadata": metric_metadata,
        "analysis": association_analysis,
        "screening_thresholds": export_association_analysis_thresholds().get("factor_screening", {}),
        "summary": {
            "case_level_factors": sum(1 for factor in factor_definitions if factor["scope"] == "case_level"),
            "event_level_factors": sum(1 for factor in factor_definitions if factor["scope"] == "event_level"),
            "temporal_factors": sum(1 for factor in factor_definitions if factor["scope"] == "temporal"),
            "excluded_factors": len(excluded_factors),
            "significant_relationships": len(association_analysis.get("significant_relationships", [])),
        },
    }


def _make_sequence_label(activities: list[str], max_length: int = 8) -> str:
    if len(activities) <= max_length:
        return " > ".join(activities)
    visible = " > ".join(activities[:max_length])
    return f"{visible} > ..."


def _make_profile_warning_list(
    *,
    case_col: str | None,
    timestamp_col: str | None,
    resource_col: str | None,
    parsed_timestamp_count: int,
    timestamp_value_count: int,
    truncated: bool,
    other_cols: list[str],
) -> list[str]:
    warnings: list[str] = []
    if case_col is None:
        warnings.append("No case identifier column detected. End-to-end case KPIs and trace variants are unreliable.")
    if timestamp_col is None:
        warnings.append("No timestamp column detected. Timing-based KPIs are weakly supported.")
    elif timestamp_value_count and parsed_timestamp_count < timestamp_value_count:
        warnings.append("Some timestamp values could not be parsed. Timing indicators may be incomplete.")
    if resource_col is None:
        warnings.append("No resource or role column detected. Resource-utilization KPIs are weakly supported.")
    if truncated:
        warnings.append("The log profile was truncated at the row safety cap, so counts may be approximate.")
    if not other_cols:
        warnings.append("No additional business attributes were detected beyond case, activity, timestamp, and resource columns.")
    return warnings


def profile_event_log(file: BinaryIO, *, max_rows: int = 50_000) -> dict[str, Any] | None:
    """Return a structured profile for a CSV event log, or ``None`` if unusable."""

    try:
        if hasattr(file, "seek"):
            file.seek(0)
        raw_bytes = file.read()
        text = raw_bytes.decode("utf-8-sig")
    except Exception:
        return None

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return None

    headers_original = list(reader.fieldnames)
    headers_lower = [header.strip().lower() for header in headers_original]
    lower_to_original = {header_lower: header_original for header_lower, header_original in zip(headers_lower, headers_original)}

    case_col = _detect_column(headers_lower, _CASE_ID_ALIASES)
    activity_col = _detect_column(headers_lower, _ACTIVITY_ALIASES)
    timestamp_col = _detect_column(headers_lower, _TIMESTAMP_ALIASES)
    resource_col = _detect_column(headers_lower, _RESOURCE_ALIASES)

    if activity_col is None:
        return None

    activity_counter: Counter[str] = Counter()
    resource_counter: Counter[str] = Counter()
    case_ids: set[str] = set()
    total_events = 0
    parsed_timestamp_count = 0
    timestamp_value_count = 0
    truncated = False

    case_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    activity_names_in_log: set[str] = set()
    resource_names_in_log: set[str] = set()

    other_cols = [lower_to_original[header] for header in headers_lower if header not in {case_col, activity_col, timestamp_col, resource_col}]
    auxiliary_timestamp_cols = [column_name for column_name in other_cols if _looks_like_timestamp_attribute(column_name)]
    activity_key = lower_to_original[activity_col]
    case_key = lower_to_original[case_col] if case_col is not None else None
    timestamp_key = lower_to_original[timestamp_col] if timestamp_col is not None else None
    resource_key = lower_to_original[resource_col] if resource_col is not None else None

    for index, row in enumerate(reader):
        if index >= max_rows:
            truncated = True
            break

        activity = (row.get(activity_key) or "").strip()
        if not activity:
            continue

        total_events += 1
        activity_counter[activity] += 1
        activity_names_in_log.add(activity.lower())

        case_value = ""
        if case_key is not None:
            case_value = (row.get(case_key) or "").strip()
            if case_value:
                case_ids.add(case_value)

        resource_value = ""
        if resource_key is not None:
            resource_value = (row.get(resource_key) or "").strip()
            if resource_value:
                resource_counter[resource_value] += 1
                resource_names_in_log.add(resource_value.lower())

        parsed_timestamp = None
        if timestamp_key is not None:
            timestamp_raw = (row.get(timestamp_key) or "").strip()
            if timestamp_raw:
                timestamp_value_count += 1
                parsed_timestamp = _parse_timestamp(timestamp_raw)
                if parsed_timestamp is not None:
                    parsed_timestamp_count += 1

        event_attributes = {
            column_name: (row.get(column_name) or "").strip()
            for column_name in other_cols
            if (row.get(column_name) or "").strip()
        }

        if case_value:
            case_events[case_value].append(
                {
                    "activity": activity,
                    "timestamp": parsed_timestamp,
                    "resource": resource_value,
                    "order": index,
                    "attributes": event_attributes,
                }
            )

    if total_events == 0:
        return None

    variant_counter: Counter[str] = Counter()
    transition_counter: Counter[str] = Counter()
    dfg_counter: Counter[tuple[str, str]] = Counter()
    dfg_timing: dict[tuple[str, str], list[float]] = defaultdict(list)
    rework_counter: Counter[str] = Counter()
    case_durations_hours: list[float] = []
    inter_event_gap_hours: list[float] = []

    has_sortable_timestamps = timestamp_col is not None and parsed_timestamp_count > 0

    for events in case_events.values():
        ordered_events = sorted(
            events,
            key=lambda item: (
                item["timestamp"] is None,
                item["timestamp"].isoformat() if item["timestamp"] is not None else "",
                item["order"],
            ),
        ) if has_sortable_timestamps else sorted(events, key=lambda item: item["order"])

        activities = [event["activity"] for event in ordered_events if event["activity"]]
        if not activities:
            continue

        variant_counter[_make_sequence_label(activities)] += 1
        for previous_activity, next_activity in zip(activities, activities[1:]):
            transition_counter[f"{previous_activity} -> {next_activity}"] += 1

        for prev_event, next_event in zip(ordered_events, ordered_events[1:]):
            prev_act = prev_event.get("activity")
            next_act = next_event.get("activity")
            if prev_act and next_act:
                pair = (prev_act, next_act)
                dfg_counter[pair] += 1
                if (
                    has_sortable_timestamps
                    and prev_event["timestamp"] is not None
                    and next_event["timestamp"] is not None
                ):
                    gap = (next_event["timestamp"] - prev_event["timestamp"]).total_seconds() / 3600
                    if gap >= 0:
                        dfg_timing[pair].append(gap)

        repeated_activities = [activity for activity, count in Counter(activities).items() if count > 1]
        for activity in repeated_activities:
            rework_counter[activity] += 1

        if has_sortable_timestamps:
            first_timestamp = ordered_events[0]["timestamp"]
            last_timestamp = ordered_events[-1]["timestamp"]
            if first_timestamp is not None and last_timestamp is not None:
                cycle_time_hours = (last_timestamp - first_timestamp).total_seconds() / 3600
                if cycle_time_hours >= 0:
                    case_durations_hours.append(cycle_time_hours)

            for previous_event, next_event in zip(ordered_events, ordered_events[1:]):
                if previous_event["timestamp"] is None or next_event["timestamp"] is None:
                    continue
                gap_hours = (next_event["timestamp"] - previous_event["timestamp"]).total_seconds() / 3600
                if gap_hours >= 0:
                    inter_event_gap_hours.append(gap_hours)

    measurable_signals: list[str] = ["activity_frequency", "event_volume"]
    if case_col is not None:
        measurable_signals.extend(["case_volume", "trace_variants", "transition_frequency", "rework_rate"])
    if resource_col is not None:
        measurable_signals.append("resource_workload")
    if timestamp_col is not None and parsed_timestamp_count > 0:
        measurable_signals.extend(["cycle_time", "waiting_time", "throughput_over_time"])
        if resource_col is not None:
            measurable_signals.append("resource_time_allocation")
    if any(_looks_like_cost_attribute(column) for column in other_cols):
        measurable_signals.append("cost_or_effort")
    if any(_looks_like_quality_attribute(column) for column in other_cols):
        measurable_signals.append("quality_or_compliance")

    directly_follows_graph: list[dict[str, Any]] = []
    for pair, freq in dfg_counter.most_common(_MAX_DFG_ROWS):
        from_act, to_act = pair
        durations = dfg_timing.get(pair, [])
        entry: dict[str, Any] = {"from": from_act, "to": to_act, "frequency": freq}
        if durations:
            entry["mean_duration_hours"] = _round_number(sum(durations) / len(durations), 2)
            entry["median_duration_hours"] = _round_number(_percentile(durations, 0.5), 2)
        directly_follows_graph.append(entry)

    warnings = _make_profile_warning_list(
        case_col=case_col,
        timestamp_col=timestamp_col,
        resource_col=resource_col,
        parsed_timestamp_count=parsed_timestamp_count,
        timestamp_value_count=timestamp_value_count,
        truncated=truncated,
        other_cols=other_cols,
    )
    context_profile = _build_context_profile(
        other_cols=other_cols,
        case_events=case_events,
        auxiliary_timestamp_cols=auxiliary_timestamp_cols,
        has_sortable_timestamps=has_sortable_timestamps,
    )

    profile = {
        "summary": {
            "total_events": total_events,
            "distinct_cases": len(case_ids) if case_ids else None,
            "activities_detected": len(activity_counter),
            "resources_detected": len(resource_counter),
        },
        "detected_columns": {
            "case_id": lower_to_original[case_col] if case_col is not None else None,
            "activity": activity_key,
            "timestamp": lower_to_original[timestamp_col] if timestamp_col is not None else None,
            "resource": lower_to_original[resource_col] if resource_col is not None else None,
        },
        "data_quality": {
            "truncated_at_row_cap": truncated,
            "timestamp_column_present": timestamp_col is not None,
            "parsed_timestamp_ratio": _round_number(parsed_timestamp_count / timestamp_value_count, 2) if timestamp_value_count else None,
            "warnings": warnings,
        },
        "top_activities": [
            {"name": activity, "event_count": count}
            for activity, count in activity_counter.most_common(_MAX_ACTIVITY_ROWS)
        ],
        "top_resources": [
            {"name": resource, "event_count": count}
            for resource, count in resource_counter.most_common(_MAX_RESOURCE_ROWS)
        ],
        "top_variants": [
            {"variant": variant, "case_count": count}
            for variant, count in variant_counter.most_common(_MAX_VARIANT_ROWS)
        ],
        "top_transitions": [
            {"transition": transition, "count": count}
            for transition, count in transition_counter.most_common(_MAX_TRANSITION_ROWS)
        ],
        "directly_follows_graph": directly_follows_graph,
        "rework_activity_case_counts": [
            {"activity": activity, "case_count": count}
            for activity, count in rework_counter.most_common(_MAX_REWORK_ROWS)
        ],
        "duration_indicators": {
            "cycle_time_hours": {
                "median": _round_number(_percentile(case_durations_hours, 0.5), 2) if case_durations_hours else None,
                "p90": _round_number(_percentile(case_durations_hours, 0.9), 2) if case_durations_hours else None,
            },
            "inter_event_gap_hours": {
                "median": _round_number(_percentile(inter_event_gap_hours, 0.5), 2) if inter_event_gap_hours else None,
                "p90": _round_number(_percentile(inter_event_gap_hours, 0.9), 2) if inter_event_gap_hours else None,
            },
        },
        "available_attributes": other_cols[:_MAX_ATTRIBUTE_COLS],
        "context_profile": context_profile,
        "measurable_signals": sorted(set(measurable_signals)),
        "_lookup": {
            "activities": sorted(activity_names_in_log),
            "resources": sorted(resource_names_in_log),
            "attributes": sorted(column.lower() for column in other_cols),
        },
    }
    return profile


def build_log_evidence_prompt(profile: dict[str, Any]) -> str:
    """Return a prompt-ready JSON evidence block from a structured log profile."""

    prompt_payload = {
        "summary": profile.get("summary", {}),
        "detected_columns": profile.get("detected_columns", {}),
        "data_quality": profile.get("data_quality", {}),
        "measurable_signals": profile.get("measurable_signals", []),
        "top_activities": profile.get("top_activities", [])[:10],
        "top_resources": profile.get("top_resources", [])[:8],
        "top_variants": profile.get("top_variants", [])[:5],
        "directly_follows_graph": profile.get("directly_follows_graph", [])[:15],
        "top_transitions": profile.get("top_transitions", [])[:8],
        "rework_activity_case_counts": profile.get("rework_activity_case_counts", [])[:5],
        "duration_indicators": profile.get("duration_indicators", {}),
        "available_attributes": profile.get("available_attributes", []),
    }
    return json.dumps(prompt_payload, indent=2)


def build_context_evidence_prompt(profile: dict[str, Any]) -> str:
    """Return prompt-ready JSON for evidence-supported context-performance associations."""

    context_profile = profile.get("context_profile", {})
    analysis = context_profile.get("analysis", {})
    prompt_payload = {
        "summary": context_profile.get("summary", {}),
        "available_metrics": context_profile.get("available_metrics", []),
        "metric_metadata": context_profile.get("metric_metadata", {}),
        "detected_factors": context_profile.get("detected_factors", [])[:_MAX_CONTEXT_FACTOR_ROWS],
        "excluded_factors": context_profile.get("excluded_factors", [])[:_MAX_CONTEXT_FACTOR_ROWS],
        "screening_thresholds": context_profile.get("screening_thresholds", {}),
        "significance_threshold": analysis.get("significance_threshold"),
        "fdr_method": analysis.get("fdr_method"),
        "effect_thresholds": analysis.get("effect_thresholds", {}),
        "support_thresholds": analysis.get("support_thresholds", {}),
        "statistics_backend": analysis.get("statistics_backend"),
        "significant_relationships": analysis.get("significant_relationships", [])[:_MAX_CONTEXT_RELATIONSHIPS],
        "rejected_relationships": analysis.get("rejected_relationships", [])[:_MAX_CONTEXT_RELATIONSHIPS],
        "filtered_out_factors": analysis.get("filtered_out_factors", [])[:_MAX_CONTEXT_FACTOR_ROWS],
        "notes": analysis.get("notes", []),
    }
    return json.dumps(prompt_payload, indent=2)


def analyze_text_log_consistency(process_description: str, log_profile: dict[str, Any] | None) -> dict[str, Any]:
    """Compare the process description against logged activities and return concise mismatch hints."""

    if not process_description.strip() or not log_profile:
        return {"status": "not_assessed", "warnings": [], "missing_in_text": [], "missing_in_log": []}

    log_activities = [entry.get("name", "") for entry in log_profile.get("top_activities", []) if entry.get("name")]
    if not log_activities:
        return {"status": "not_assessed", "warnings": [], "missing_in_text": [], "missing_in_log": []}

    missing_in_text = [
        activity for activity in log_activities
        if not _activity_text_supported(activity, process_description)
    ][:_MAX_CONSISTENCY_ITEMS]

    process_hints = _extract_process_activity_hints(process_description)
    missing_in_log = [
        hint for hint in process_hints
        if not _hint_supported_by_log(hint, log_activities)
    ][:_MAX_CONSISTENCY_ITEMS]

    warnings: list[str] = []
    if missing_in_text:
        warnings.append(
            "The active log contains frequent activities that are not clearly reflected in the description: "
            + ", ".join(missing_in_text)
            + "."
        )
    if missing_in_log:
        warnings.append(
            "The process description mentions activity fragments that are not clearly visible in the active log: "
            + ", ".join(missing_in_log)
            + "."
        )

    return {
        "status": "warning" if warnings else "aligned",
        "warnings": warnings,
        "missing_in_text": missing_in_text,
        "missing_in_log": missing_in_log,
    }


def format_event_log_profile(profile: dict[str, Any]) -> str:
    """Return a compact human-readable preview for the UI."""

    summary = profile.get("summary", {})
    columns = profile.get("detected_columns", {})
    data_quality = profile.get("data_quality", {})
    duration_indicators = profile.get("duration_indicators", {})

    sections = [
        f"Total events parsed: {summary.get('total_events')}",
        f"Distinct cases: {summary.get('distinct_cases') or 'Not detected'}",
        (
            "Detected columns: "
            f"case={columns.get('case_id') or 'n/a'}, "
            f"activity={columns.get('activity') or 'n/a'}, "
            f"timestamp={columns.get('timestamp') or 'n/a'}, "
            f"resource={columns.get('resource') or 'n/a'}"
        ),
        "Measurable signals: " + ", ".join(profile.get("measurable_signals", [])),
    ]

    cycle_time = duration_indicators.get("cycle_time_hours", {})
    if cycle_time.get("median") is not None:
        sections.append(
            f"Cycle time indicators (hours): median={cycle_time.get('median')}, p90={cycle_time.get('p90')}"
        )

    waiting_time = duration_indicators.get("inter_event_gap_hours", {})
    if waiting_time.get("median") is not None:
        sections.append(
            f"Inter-event gap indicators (hours): median={waiting_time.get('median')}, p90={waiting_time.get('p90')}"
        )

    top_activities = profile.get("top_activities", [])
    if top_activities:
        lines = ["Top activities:"]
        for entry in top_activities[:10]:
            lines.append(f"  - {entry['name']}: {entry['event_count']}")
        sections.append("\n".join(lines))

    top_variants = profile.get("top_variants", [])
    if top_variants:
        lines = ["Top variants:"]
        for entry in top_variants[:5]:
            lines.append(f"  - {entry['variant']}: {entry['case_count']} cases")
        sections.append("\n".join(lines))

    top_transitions = profile.get("top_transitions", [])
    if top_transitions:
        lines = ["Frequent transitions:"]
        for entry in top_transitions[:8]:
            lines.append(f"  - {entry['transition']}: {entry['count']}")
        sections.append("\n".join(lines))

    warnings = data_quality.get("warnings", [])
    if warnings:
        lines = ["Grounding warnings:"]
        for warning in warnings:
            lines.append(f"  - {warning}")
        sections.append("\n".join(lines))

    context_profile = profile.get("context_profile", {})
    context_summary = context_profile.get("summary", {})
    if context_summary:
        sections.append(
            "Context factors detected: "
            f"case-level={context_summary.get('case_level_factors', 0)}, "
            f"event-level={context_summary.get('event_level_factors', 0)}, "
            f"temporal={context_summary.get('temporal_factors', 0)}"
        )

    significant_relationships = (
        context_profile.get("analysis", {}).get("significant_relationships", [])
    )
    if significant_relationships:
        lines = ["Top context relationships:"]
        for relationship in significant_relationships[:5]:
            lines.append(f"  - {relationship.get('summary', '')}")
        sections.append("\n".join(lines))

    return "\n".join(sections)


def summarize_event_log(file: BinaryIO, *, max_rows: int = 50_000) -> str:
    """Backward-compatible wrapper returning a human-readable log summary."""

    profile = profile_event_log(file, max_rows=max_rows)
    if profile is None:
        return ""
    return format_event_log_profile(profile)


def assess_kpi_grounding(kpi: Any, log_profile: dict[str, Any] | None) -> dict[str, Any]:
    """Return a lightweight local assessment of how well a KPI is grounded in the log."""

    if not log_profile:
        return {
            "level": "not_assessed",
            "score": 0,
            "reasons": ["No event log profile is available for grounding assessment."],
        }

    text_parts = [
        getattr(kpi, "name", ""),
        getattr(kpi, "description", ""),
        getattr(getattr(kpi, "smart_breakdown", None), "specific", ""),
        getattr(getattr(kpi, "smart_breakdown", None), "measurable", ""),
        getattr(getattr(kpi, "smart_breakdown", None), "relevant", ""),
        getattr(kpi, "suggested_formula", ""),
        getattr(getattr(kpi, "category", None), "value", str(getattr(kpi, "category", ""))),
    ]
    text_blob = " ".join(part for part in text_parts if part).lower()

    lookup = log_profile.get("_lookup", {})
    measurable_signals = set(log_profile.get("measurable_signals", []))
    available_attributes = set(lookup.get("attributes", []))

    matched_activities = [activity for activity in lookup.get("activities", []) if activity and activity in text_blob]
    matched_resources = [resource for resource in lookup.get("resources", []) if resource and resource in text_blob]

    score = 0
    reasons: list[str] = []

    if matched_activities:
        score += 2
        reasons.append(f"Mentions logged activities: {', '.join(matched_activities[:3])}.")
    elif "end-to-end" in text_blob or "overall" in text_blob or "cycle time" in text_blob:
        score += 1
        reasons.append("Refers to a generic end-to-end process measure that could be derived from cases in the log.")

    if matched_resources:
        score += 1
        reasons.append(f"References logged resources or roles: {', '.join(matched_resources[:3])}.")

    needs_time = any(token in text_blob for token in ("time", "duration", "delay", "waiting", "cycle", "turnaround"))
    needs_resource_time = any(token in text_blob for token in ("utilization", "allocation", "idle"))
    needs_counts = any(token in text_blob for token in ("count", "rate", "throughput", "frequency", "number", "occurrence"))
    needs_cost = any(token in text_blob for token in ("cost", "expense", "price", "effort"))
    needs_quality = any(token in text_blob for token in ("quality", "compliance", "accuracy", "error", "defect", "complete"))

    if needs_time:
        if "cycle_time" in measurable_signals or "waiting_time" in measurable_signals:
            score += 3
            reasons.append("The log profile supports timing-based measures through case and timestamp evidence.")
        else:
            reasons.append("This KPI depends on timing evidence, but the log profile does not strongly support timing measures.")

    if needs_resource_time:
        if "resource_time_allocation" in measurable_signals:
            score += 2
            reasons.append("The log profile supports resource-linked time analysis.")
        elif "resource_workload" in measurable_signals:
            score += 1
            reasons.append("The log includes resources, but it may only support workload counts rather than full utilization.")
        else:
            reasons.append("This KPI depends on resource evidence, but no strong resource signal was detected in the log.")

    if needs_counts:
        if {"activity_frequency", "event_volume", "case_volume"} & measurable_signals:
            score += 2
            reasons.append("The log directly supports count and frequency-style KPIs.")

    if needs_cost:
        if "cost_or_effort" in measurable_signals:
            score += 3
            reasons.append("The log exposes cost or effort-like attributes that can support this KPI.")
        else:
            reasons.append("No explicit cost or effort attribute was detected, so this KPI is only weakly grounded.")

    if needs_quality:
        if "quality_or_compliance" in measurable_signals:
            score += 2
            reasons.append("The log includes quality or compliance-like attributes that can support this KPI.")
        elif matched_activities:
            score += 1
            reasons.append("The KPI is tied to logged process steps, but quality evidence appears indirect rather than explicit.")
        else:
            reasons.append("No explicit quality or compliance attribute was detected for this KPI.")

    attribute_mentions = [attribute for attribute in available_attributes if attribute in text_blob]
    if attribute_mentions:
        score += 1
        reasons.append(f"References detected log attributes: {', '.join(attribute_mentions[:3])}.")

    if score >= 6:
        level = "strong"
    elif score >= 3:
        level = "moderate"
    else:
        level = "weak"

    deduplicated_reasons: list[str] = []
    for reason in reasons:
        if reason not in deduplicated_reasons:
            deduplicated_reasons.append(reason)

    if not deduplicated_reasons:
        deduplicated_reasons.append("The KPI could not be linked clearly to the available log evidence.")

    return {"level": level, "score": score, "reasons": deduplicated_reasons[:4]}

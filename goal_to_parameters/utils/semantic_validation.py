"""Rule-based semantic validation for generated KPI sets."""

from __future__ import annotations

import re
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from models import EvidenceBasis, KPICategory, KPIGenerationResult, SMARTKpi

from .log_processing import assess_kpi_grounding

_TEXT_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "by",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "per",
    "the",
    "to",
    "while",
    "with",
}
_CATEGORY_HINTS: dict[str, set[str]] = {
    KPICategory.TIME.value: {"time", "duration", "delay", "waiting", "cycle", "lead", "turnaround"},
    KPICategory.COST.value: {"cost", "expense", "price", "effort", "labor", "labour", "budget"},
    KPICategory.QUALITY.value: {"quality", "accuracy", "defect", "error", "rework", "pass"},
    KPICategory.UTILIZATION.value: {"utilization", "utilisation", "allocation", "occupancy", "idle", "workload"},
    KPICategory.THROUGHPUT.value: {"throughput", "volume", "count", "frequency", "completed"},
    KPICategory.COMPLIANCE.value: {"compliance", "conformity", "adherence", "policy", "requirement", "complete"},
    KPICategory.FLEXIBILITY.value: {"flexibility", "adaptability", "responsiveness", "reassignment", "capacity"},
}
_CONSTRAINT_MARKERS = ("maintain", "maintaining", "keep", "keeping", "ensure", "ensuring", "meeting", "stable")
_CONTEXT_CONDITION_PATTERN = re.compile(r"^\s*([A-Za-z0-9_ ]+?)\s*(<=|>=|!=|=|<|>)\s*(.+)$")
_CONDITION_NORMALIZATION_PATTERN = re.compile(r"\s+")
_WEEKDAY_TOKENS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
_MONTH_TOKENS = {
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
}
_TEMPORAL_FACTOR_EQUIVALENTS: dict[str, set[str]] = {
    "day_of_week": {"event_day_of_week", "case_start_day_of_week"},
    "weekday": {"event_day_of_week", "case_start_day_of_week"},
    "month": {"event_month", "case_start_month"},
    "quarter": {"event_quarter", "case_start_quarter"},
    "hour_of_day": {"event_hour_of_day", "case_start_hour_of_day"},
    "time_of_day": {"event_hour_of_day", "case_start_hour_of_day"},
}


@dataclass
class SemanticValidationIssue:
    severity: str
    code: str
    message: str
    kpi_names: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SemanticValidationResult:
    issues: list[SemanticValidationIssue] = field(default_factory=list)
    grounding_assessments: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(issue.severity == "warning" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issues": [issue.to_dict() for issue in self.issues],
            "grounding_assessments": self.grounding_assessments,
            "has_errors": self.has_errors,
            "has_warnings": self.has_warnings,
        }


def _tokenize(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2 and token not in _TEXT_STOPWORDS
    }


def _kpi_text_blob(kpi: SMARTKpi) -> str:
    return " ".join(
        [
            kpi.name,
            kpi.description,
            kpi.smart_breakdown.specific,
            kpi.smart_breakdown.measurable,
            kpi.smart_breakdown.relevant,
            kpi.suggested_formula,
            kpi.category.value,
            kpi.process_scope.value,
            kpi.evidence_basis.value,
        ]
    ).lower()


def _normalize_context_factor_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _context_factor_candidates(factor_name: str) -> set[str]:
    normalized = _normalize_context_factor_name(factor_name)
    candidates = {factor_name.lower(), normalized, normalized.replace("_", " ")}
    return {candidate.strip() for candidate in candidates if candidate.strip()}


def _normalize_metric_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _resolve_temporal_factor_name(
    generic_name: str,
    *,
    detected_factor_lookup: dict[str, str],
    supported_factors: set[str],
) -> set[str]:
    normalized = _normalize_context_factor_name(generic_name)
    available_factors = set(detected_factor_lookup.keys()) | set(supported_factors)
    if normalized not in _TEMPORAL_FACTOR_EQUIVALENTS:
        return {normalized}

    candidates = {
        candidate
        for candidate in _TEMPORAL_FACTOR_EQUIVALENTS[normalized]
        if candidate in available_factors
    }
    if normalized in available_factors:
        candidates.add(normalized)
    return candidates or {normalized}


def _resolve_context_factor_candidates(
    factor_name: str,
    *,
    detected_factor_lookup: dict[str, str],
    supported_factors: set[str],
) -> set[str]:
    normalized = _normalize_context_factor_name(factor_name)
    return _resolve_temporal_factor_name(
        normalized,
        detected_factor_lookup=detected_factor_lookup,
        supported_factors=supported_factors,
    )


def _extract_context_factor_from_condition(
    condition: str,
    *,
    detected_factor_lookup: dict[str, str] | None = None,
    supported_factors: set[str] | None = None,
) -> set[str]:
    detected_factor_lookup = detected_factor_lookup or {}
    supported_factors = supported_factors or set()
    if not condition:
        return set()

    match = _CONTEXT_CONDITION_PATTERN.match(condition)
    if match:
        return _resolve_context_factor_candidates(
            match.group(1),
            detected_factor_lookup=detected_factor_lookup,
            supported_factors=supported_factors,
        )

    lowered = condition.strip().lower()
    if any(day in lowered for day in _WEEKDAY_TOKENS) or "weekday" in lowered or "weekend" in lowered:
        return _resolve_temporal_factor_name(
            "day_of_week",
            detected_factor_lookup=detected_factor_lookup,
            supported_factors=supported_factors,
        )
    if any(month in lowered for month in _MONTH_TOKENS):
        return _resolve_temporal_factor_name(
            "month",
            detected_factor_lookup=detected_factor_lookup,
            supported_factors=supported_factors,
        )
    if "quarter" in lowered or re.search(r"\bq[1-4]\b", lowered):
        return _resolve_temporal_factor_name(
            "quarter",
            detected_factor_lookup=detected_factor_lookup,
            supported_factors=supported_factors,
        )
    if "hour" in lowered or "time of day" in lowered or "time_of_day" in lowered:
        return _resolve_temporal_factor_name(
            "hour_of_day",
            detected_factor_lookup=detected_factor_lookup,
            supported_factors=supported_factors,
        )
    return set()


def _context_factor_lookup(log_profile: dict[str, Any] | None) -> tuple[dict[str, str], set[str]]:
    if not log_profile:
        return {}, set()

    context_profile = log_profile.get("context_profile", {})
    detected_lookup: dict[str, str] = {}

    for factor in context_profile.get("detected_factors", []):
        factor_name = factor.get("name")
        if factor_name:
            detected_lookup[_normalize_context_factor_name(str(factor_name))] = str(factor_name)

    supported_factors: set[str] = set()
    for relationship in context_profile.get("analysis", {}).get("significant_relationships", []):
        factor_name = relationship.get("factor")
        if factor_name:
            normalized = _normalize_context_factor_name(str(factor_name))
            supported_factors.add(normalized)
            detected_lookup.setdefault(normalized, str(factor_name))

    return detected_lookup, supported_factors


def _context_relationship_lookup(
    *,
    log_profile: dict[str, Any] | None,
    context_evidence: str | None,
) -> tuple[dict[str, str], set[str], list[dict[str, Any]]]:
    if context_evidence:
        try:
            payload = json.loads(context_evidence)
        except json.JSONDecodeError:
            payload = {}
        relationships = payload.get("significant_relationships", [])
        factor_lookup: dict[str, str] = {}
        supported_factors: set[str] = set()
        for relationship in relationships:
            factor_name = relationship.get("factor")
            if factor_name:
                normalized = _normalize_context_factor_name(str(factor_name))
                factor_lookup[normalized] = str(factor_name)
                supported_factors.add(normalized)
        return factor_lookup, supported_factors, relationships

    factor_lookup, supported_factors = _context_factor_lookup(log_profile)
    relationships = []
    if log_profile:
        relationships = (
            log_profile.get("context_profile", {})
            .get("analysis", {})
            .get("significant_relationships", [])
        )
    return factor_lookup, supported_factors, relationships


def _supported_relationship_index(
    relationships: list[dict[str, Any]],
    *,
    detected_factor_lookup: dict[str, str],
    supported_factors: set[str],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for relationship in relationships:
        factor_name = relationship.get("factor")
        metric_name = relationship.get("metric")
        if not factor_name or not metric_name:
            continue
        metric_key = _normalize_metric_name(str(metric_name))
        factor_candidates = _resolve_context_factor_candidates(
            str(factor_name),
            detected_factor_lookup=detected_factor_lookup,
            supported_factors=supported_factors,
        )
        for factor_key in factor_candidates:
            index.setdefault((factor_key, metric_key), []).append(relationship)
    return index


def _normalize_condition_value(value: str) -> str:
    stripped = value.strip().strip("'\"")
    try:
        numeric = float(stripped)
    except ValueError:
        return _CONDITION_NORMALIZATION_PATTERN.sub(" ", stripped.lower()).strip()
    return str(int(numeric)) if numeric.is_integer() else str(numeric)


def _conditions_semantically_match(
    left: str,
    right: str,
    *,
    detected_factor_lookup: dict[str, str],
    supported_factors: set[str],
) -> bool:
    left_normalized = _CONDITION_NORMALIZATION_PATTERN.sub(" ", left.strip().lower())
    right_normalized = _CONDITION_NORMALIZATION_PATTERN.sub(" ", right.strip().lower())
    if left_normalized == right_normalized:
        return True

    left_match = _CONTEXT_CONDITION_PATTERN.match(left)
    right_match = _CONTEXT_CONDITION_PATTERN.match(right)
    if not left_match or not right_match:
        return False

    left_factors = _resolve_context_factor_candidates(
        left_match.group(1),
        detected_factor_lookup=detected_factor_lookup,
        supported_factors=supported_factors,
    )
    right_factors = _resolve_context_factor_candidates(
        right_match.group(1),
        detected_factor_lookup=detected_factor_lookup,
        supported_factors=supported_factors,
    )
    if not (left_factors & right_factors):
        return False

    if left_match.group(2) != right_match.group(2):
        return False

    return _normalize_condition_value(left_match.group(3)) == _normalize_condition_value(right_match.group(3))


def _segment_supported_by_relationship(
    segment: Any,
    *,
    supported_relationship_index: dict[tuple[str, str], list[dict[str, Any]]],
    detected_factor_lookup: dict[str, str],
    supported_factors: set[str],
) -> dict[str, Any]:
    evidence_factor = getattr(segment, "evidence_factor", None)
    evidence_metric = getattr(segment, "evidence_metric", None)
    condition = str(getattr(segment, "condition", "") or "").strip()

    if not evidence_factor or not evidence_metric:
        return {
            "pair_supported": False,
            "condition_supported": False,
            "matched_relationships": [],
            "reason": "missing_traceability_fields",
        }

    factor_candidates = _resolve_context_factor_candidates(
        str(evidence_factor),
        detected_factor_lookup=detected_factor_lookup,
        supported_factors=supported_factors,
    )
    metric_key = _normalize_metric_name(str(evidence_metric))

    matched_relationships: list[dict[str, Any]] = []
    for factor_candidate in factor_candidates:
        matched_relationships.extend(
            supported_relationship_index.get((factor_candidate, metric_key), [])
        )

    if not matched_relationships:
        return {
            "pair_supported": False,
            "condition_supported": False,
            "matched_relationships": [],
            "reason": "unsupported_factor_metric_pair",
        }

    if not condition:
        return {
            "pair_supported": True,
            "condition_supported": True,
            "matched_relationships": matched_relationships,
            "reason": None,
        }

    condition_supported = any(
        _conditions_semantically_match(
            condition,
            str(candidate.get("condition", "")),
            detected_factor_lookup=detected_factor_lookup,
            supported_factors=supported_factors,
        )
        for relationship in matched_relationships
        for candidate in relationship.get("segments", [])
    )
    return {
        "pair_supported": True,
        "condition_supported": condition_supported,
        "matched_relationships": matched_relationships,
        "reason": None if condition_supported else "unsupported_condition",
    }


def _find_context_factor_mentions(text: str, factor_lookup: dict[str, str]) -> set[str]:
    normalized_text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    mentions: set[str] = set()
    for normalized_factor, original_factor in factor_lookup.items():
        for candidate in _context_factor_candidates(original_factor):
            if re.search(rf"\b{re.escape(candidate)}\b", normalized_text):
                mentions.add(normalized_factor)
                break
    return mentions


def _extract_goal_components(simulation_goal: str) -> list[dict[str, Any]]:
    goal = simulation_goal.strip().lower()
    if not goal:
        return []

    components: list[dict[str, Any]] = []
    split_match = re.split(r"\bwhile\b|\bwithout\b|\bbut\b", goal, maxsplit=1)
    primary_part = split_match[0].strip()
    if primary_part:
        if " and " in primary_part:
            candidate_parts = [part.strip() for part in primary_part.split(" and ") if part.strip()]
        else:
            candidate_parts = [primary_part]
        for part in candidate_parts:
            primary_tokens = _tokenize(part)
            if primary_tokens:
                components.append({"kind": "primary", "text": part, "tokens": primary_tokens})

    constraint_patterns = [
        r"\bwhile\s+(?:maintaining|keeping|ensuring|meeting)\s+(.+)",
        r"\bwithout\s+(?:reducing|hurting|lowering|sacrificing)\s+(.+)",
        r"\bwhile\s+(.+)",
    ]
    for pattern in constraint_patterns:
        match = re.search(pattern, goal)
        if match:
            constraint_text = match.group(1).strip()
            constraint_tokens = _tokenize(constraint_text)
            if constraint_tokens:
                components.append({"kind": "constraint", "text": constraint_text, "tokens": constraint_tokens})
            break

    return components


def _goal_component_covered(component_tokens: set[str], kpis: list[SMARTKpi]) -> list[str]:
    covered_by: list[str] = []
    for kpi in kpis:
        overlap = component_tokens & _tokenize(_kpi_text_blob(kpi))
        if len(overlap) >= min(2, len(component_tokens)) or (component_tokens and overlap):
            covered_by.append(kpi.name)
    return covered_by


def _formula_consistency_issue(kpi: SMARTKpi) -> str | None:
    measurable_text = kpi.smart_breakdown.measurable.lower()
    formula_text = kpi.suggested_formula.lower()

    if ("percentage" in measurable_text or "%" in measurable_text) and "* 100" not in formula_text and "*100" not in formula_text:
        return "The measurable text says percentage, but the formula does not clearly convert to a percentage."
    if any(token in measurable_text for token in ("hours", "days", "minutes")) and "count(" in formula_text and "avg(" not in formula_text and "sum(" not in formula_text:
        return "The measurable text describes a time-based KPI, but the formula looks like a pure count."
    if any(token in measurable_text for token in ("count", "number of")) and "avg(" in formula_text and "-" in formula_text:
        return "The measurable text describes a count KPI, but the formula looks like a duration average."
    return None


def _expected_categories(text_blob: str) -> set[str]:
    expected: set[str] = set()
    for category, hints in _CATEGORY_HINTS.items():
        if any(token in text_blob for token in hints):
            expected.add(category)
    return expected


def _make_issue(
    issues: list[SemanticValidationIssue],
    *,
    severity: str,
    code: str,
    message: str,
    kpi_names: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    issues.append(
        SemanticValidationIssue(
            severity=severity,
            code=code,
            message=message,
            kpi_names=kpi_names or [],
            details=details or {},
        )
    )


def validate_kpi_generation_semantics(
    result: KPIGenerationResult,
    *,
    simulation_goal: str,
    log_profile: dict[str, Any] | None = None,
    context_evidence: str | None = None,
) -> SemanticValidationResult:
    issues: list[SemanticValidationIssue] = []
    grounding_assessments: dict[str, dict[str, Any]] = {}
    detected_context_factors, supported_context_factors, supported_relationships = _context_relationship_lookup(
        log_profile=log_profile,
        context_evidence=context_evidence,
    )
    supported_relationship_index = _supported_relationship_index(
        supported_relationships,
        detected_factor_lookup=detected_context_factors,
        supported_factors=supported_context_factors,
    )

    seen_names: dict[str, str] = {}
    normalized_signatures: list[tuple[SMARTKpi, set[str]]] = []

    for kpi in result.kpis:
        normalized_name = kpi.name.strip().lower()
        if normalized_name in seen_names:
            _make_issue(
                issues,
                severity="error",
                code="duplicate_name",
                message=f"KPI names must be unique. Duplicate detected: '{kpi.name}'.",
                kpi_names=[seen_names[normalized_name], kpi.name],
            )
        else:
            seen_names[normalized_name] = kpi.name

        text_blob = _kpi_text_blob(kpi)
        normalized_signatures.append((kpi, _tokenize(text_blob)))

        formula_issue = _formula_consistency_issue(kpi)
        if formula_issue:
            _make_issue(
                issues,
                severity="warning",
                code="formula_consistency",
                message=formula_issue,
                kpi_names=[kpi.name],
            )

        expected_categories = _expected_categories(text_blob)
        if expected_categories and kpi.category.value not in expected_categories:
            _make_issue(
                issues,
                severity="warning",
                code="category_semantics",
                message=(
                    f"The KPI category '{kpi.category.value}' looks weakly aligned with the KPI text. "
                    f"Expected one of: {', '.join(sorted(expected_categories))}."
                ),
                kpi_names=[kpi.name],
                details={"expected_categories": sorted(expected_categories)},
            )

        if detected_context_factors:
            context_text = " ".join(
                [
                    kpi.name,
                    kpi.description,
                    kpi.smart_breakdown.specific,
                    kpi.smart_breakdown.relevant,
                ]
            )
            mentioned_factors = _find_context_factor_mentions(context_text, detected_context_factors)
            segmentation_factors = {
                resolved_factor
                for segment in kpi.context_segmentation
                for resolved_factor in _extract_context_factor_from_condition(
                    segment.condition,
                    detected_factor_lookup=detected_context_factors,
                    supported_factors=supported_context_factors,
                )
            }
            unsupported_context = sorted((mentioned_factors | segmentation_factors) - supported_context_factors)
            if unsupported_context:
                _make_issue(
                    issues,
                    severity="warning",
                    code="unsupported_context_reference",
                    message="The KPI refers to context factors that are not supported by the significant context analysis.",
                    kpi_names=[kpi.name],
                    details={
                        "unsupported_factors": [
                            detected_context_factors.get(factor, factor.replace("_", " "))
                            for factor in unsupported_context
                        ],
                        "supported_factors": [
                            detected_context_factors.get(factor, factor.replace("_", " "))
                            for factor in sorted(supported_context_factors)
                        ],
                    },
                )

            unsupported_evidence_segments: list[dict[str, Any]] = []
            unsupported_conditions: list[str] = []
            for segment in kpi.context_segmentation:
                support_result = _segment_supported_by_relationship(
                    segment,
                    supported_relationship_index=supported_relationship_index,
                    detected_factor_lookup=detected_context_factors,
                    supported_factors=supported_context_factors,
                )
                if not support_result["pair_supported"]:
                    unsupported_evidence_segments.append(
                        {
                            "condition": segment.condition,
                            "evidence_factor": getattr(segment, "evidence_factor", None),
                            "evidence_metric": getattr(segment, "evidence_metric", None),
                            "reason": support_result["reason"],
                        }
                    )
                    continue
                if not support_result["condition_supported"]:
                    unsupported_conditions.append(segment.condition)

            if unsupported_evidence_segments:
                _make_issue(
                    issues,
                    severity="error",
                    code="unsupported_context_evidence_pair",
                    message="The KPI uses context segmentation whose evidence_factor and evidence_metric do not match any accepted evidence-supported relationship.",
                    kpi_names=[kpi.name],
                    details={"unsupported_segments": unsupported_evidence_segments},
                )
            if unsupported_conditions:
                _make_issue(
                    issues,
                    severity="error",
                    code="unsupported_context_condition",
                    message="The KPI uses segmented targets whose context conditions do not match accepted evidence-supported relationships.",
                    kpi_names=[kpi.name],
                    details={"unsupported_conditions": unsupported_conditions},
                )

        if log_profile is None:
            if kpi.supported_by_log or kpi.evidence_basis != EvidenceBasis.PROCESS_DESCRIPTION_ONLY:
                _make_issue(
                    issues,
                    severity="warning",
                    code="log_claim_without_log",
                    message="The KPI claims event-log grounding, but no active event log is available.",
                    kpi_names=[kpi.name],
                )
        else:
            grounding = assess_kpi_grounding(kpi, log_profile)
            grounding_assessments[kpi.name] = grounding
            if kpi.supported_by_log and grounding.get("level") == "weak":
                _make_issue(
                    issues,
                    severity="warning",
                    code="unsupported_log_claim",
                    message="The KPI is marked as supported by the event log, but the local grounding assessment is weak.",
                    kpi_names=[kpi.name],
                    details={"grounding": grounding},
                )
            elif kpi.evidence_basis in {EvidenceBasis.EVENT_LOG_ONLY, EvidenceBasis.BOTH, EvidenceBasis.PROXY_FROM_LOG} and grounding.get("level") == "weak":
                _make_issue(
                    issues,
                    severity="warning",
                    code="weak_log_grounding",
                    message="The KPI relies on event-log evidence, but the local grounding assessment is weak.",
                    kpi_names=[kpi.name],
                    details={"grounding": grounding},
                )

    for index, (left_kpi, left_tokens) in enumerate(normalized_signatures):
        for right_kpi, right_tokens in normalized_signatures[index + 1:]:
            if not left_tokens or not right_tokens:
                continue
            overlap = left_tokens & right_tokens
            union = left_tokens | right_tokens
            similarity = len(overlap) / len(union) if union else 0.0
            if similarity >= 0.72:
                _make_issue(
                    issues,
                    severity="warning",
                    code="near_duplicate",
                    message="Two KPIs appear semantically redundant or near-duplicative.",
                    kpi_names=[left_kpi.name, right_kpi.name],
                    details={"similarity": round(similarity, 2)},
                )

    goal_components = _extract_goal_components(simulation_goal)
    for component in goal_components:
        covered_by = _goal_component_covered(component["tokens"], result.kpis)
        if covered_by:
            continue
        _make_issue(
            issues,
            severity="warning",
            code=f"goal_{component['kind']}_coverage",
            message=f"No KPI clearly operationalizes the {component['kind']} goal component: '{component['text']}'.",
            details={"goal_component": component["text"]},
        )

    if any(marker in simulation_goal.lower() for marker in _CONSTRAINT_MARKERS):
        maintain_kpis = [kpi.name for kpi in result.kpis if kpi.target_direction.value == "maintain"]
        if not maintain_kpis:
            _make_issue(
                issues,
                severity="warning",
                code="missing_constraint_kpi",
                message="The goal contains an explicit constraint, but no KPI is marked with target direction 'maintain'.",
            )

    if grounding_assessments:
        weak_count = sum(1 for assessment in grounding_assessments.values() if assessment.get("level") == "weak")
        if weak_count >= max(2, (len(result.kpis) + 1) // 2):
            _make_issue(
                issues,
                severity="warning",
                code="many_weak_groundings",
                message="Many generated KPIs are only weakly grounded in the active event log.",
                details={"weak_count": weak_count, "total_kpis": len(result.kpis)},
            )

    return SemanticValidationResult(issues=issues, grounding_assessments=grounding_assessments)

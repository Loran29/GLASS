"""Statistical context-performance association screening with traceable filtering."""

from __future__ import annotations

import warnings
from collections import defaultdict
from dataclasses import asdict, dataclass
from math import isfinite
from statistics import median
from typing import Any

kruskal = None
spearmanr = None
_SCIPY_IMPORT_ERROR: str | None = None


@dataclass(frozen=True)
class ContextFactorScreeningConfig:
    """Configurable screening rules for raw context-factor candidates."""

    max_missingness_ratio: float = 0.6
    exclude_high_uniqueness_ratio: float = 0.85
    high_cardinality_ratio: float = 0.4
    max_free_text_mean_length: int = 40
    max_free_text_mean_tokens: int = 6


@dataclass(frozen=True)
class AssociationAnalysisConfig:
    """Configurable thresholds for statistical association screening."""

    alpha: float = 0.05
    min_group_size: int = 15
    min_test_sample_size: int = 30
    min_numeric_samples: int = 6
    min_abs_spearman_rho: float = 0.3
    min_kruskal_epsilon_squared: float = 0.08
    max_relationships: int = 12
    max_segments: int = 4
    min_segment_report_n: int = 30


DEFAULT_CONTEXT_FACTOR_SCREENING_CONFIG = ContextFactorScreeningConfig()
DEFAULT_ASSOCIATION_ANALYSIS_CONFIG = AssociationAnalysisConfig()


def _load_stats_backend() -> tuple[Any, Any]:
    """Load SciPy lazily so a newly installed dependency is picked up on rerun."""

    global kruskal, spearmanr, _SCIPY_IMPORT_ERROR

    if kruskal is not None and spearmanr is not None:
        return kruskal, spearmanr

    try:
        from scipy.stats import kruskal as scipy_kruskal, spearmanr as scipy_spearmanr
    except Exception as exc:  # pragma: no cover - environment-specific
        _SCIPY_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
        kruskal = None
        spearmanr = None
        return None, None

    kruskal = scipy_kruskal
    spearmanr = scipy_spearmanr
    _SCIPY_IMPORT_ERROR = None
    return kruskal, spearmanr


def _round_number(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not isfinite(numeric):
        return None
    return numeric


def _metric_display_name(metric_name: str, activity_name: str | None = None) -> str:
    label = metric_name.replace("_", " ")
    if activity_name:
        return f"{activity_name} {label}"
    return label


def _relationship_sort_key(relationship: dict[str, Any]) -> tuple[float, float]:
    adjusted_p_value = relationship.get("adjusted_p_value")
    effect_size = relationship.get("effect_size")
    return (
        adjusted_p_value if isinstance(adjusted_p_value, (int, float)) else 1.0,
        -(effect_size if isinstance(effect_size, (int, float)) else 0.0),
    )


def _has_non_constant_values(values: list[float]) -> bool:
    return len({round(value, 10) for value in values}) > 1


def _segment_support(segments: list[dict[str, Any]]) -> list[int]:
    return [int(segment.get("sample_size", 0) or 0) for segment in segments]


def _apply_bh_fdr_correction(relationships: list[dict[str, Any]]) -> None:
    """Apply Benjamini-Hochberg correction in-place to tested relationships."""

    tested = [
        relationship
        for relationship in relationships
        if isinstance(relationship.get("raw_p_value"), (int, float))
    ]
    if not tested:
        return

    ordered = sorted(tested, key=lambda item: float(item["raw_p_value"]))
    total = len(ordered)
    running_min = 1.0

    adjusted_by_index: dict[int, float] = {}
    for reverse_rank, relationship in enumerate(reversed(ordered), start=1):
        rank = total - reverse_rank + 1
        raw_p = float(relationship["raw_p_value"])
        adjusted = min(running_min, raw_p * total / rank)
        running_min = adjusted
        adjusted_by_index[id(relationship)] = adjusted

    for relationship in tested:
        adjusted = min(1.0, adjusted_by_index[id(relationship)])
        relationship["adjusted_p_value"] = _round_number(adjusted)
        # Keep p_value backward compatible with the filtered decision signal.
        relationship["p_value"] = relationship["adjusted_p_value"]


# Calendar factors that cannot be modelled as case attributes in a DES simulation.
# They reflect historical seasonality in the log but Prosimos has no concept of
# calendar month/quarter/year as an input dimension.  Simulatable temporal factors
# (hour_of_day, day_of_week) are represented through resource calendars and are
# kept in _TEMPORAL_SEGMENT_CAPS so their natural cardinality is respected.
_NON_SIMULATABLE_TEMPORAL_FACTORS: frozenset[str] = frozenset({
    "month",
    "months",
    "event_month",
    "quarter",
    "event_quarter",
    "season",
    "event_season",
    "year",
    "event_year",
    "year_month",
    "event_year_month",
})

# Word tokens that mark any factor as a non-simulatable calendar period, regardless
# of prefix (e.g. "case_start_month", "arrival_quarter", "submission_year").
_NON_SIMULATABLE_TEMPORAL_TOKENS: frozenset[str] = frozenset({
    "month", "months", "quarter", "year", "season",
})


def _is_non_simulatable_temporal(factor_name: str) -> bool:
    """Return True when the factor encodes a calendar period that cannot be modelled in DES."""
    lower = factor_name.lower().replace("-", "_")
    if lower in _NON_SIMULATABLE_TEMPORAL_FACTORS:
        return True
    return any(token in _NON_SIMULATABLE_TEMPORAL_TOKENS for token in lower.split("_"))

_TEMPORAL_SEGMENT_CAPS: dict[str, int] = {
    "weekday": 7,
    "day_of_week": 7,
    "dayofweek": 7,
    "dow": 7,
    "weekday_name": 7,
    "hour": 24,
    "hour_of_day": 24,
}


def _resolve_max_segments(
    factor_name: str,
    factor_scope: str,
    default_max: int,
) -> int:
    """Lift the default segment cap for temporal factors with known cardinality.

    The default ``max_segments`` (4) is meant for arbitrary categorical
    factors where the LLM only needs the most extreme contrasts.  For
    well-known temporal factors (month, weekday, hour, etc.) that cap
    silently drops most groups, hiding seasonal or weekly patterns.
    This helper returns the natural cardinality for those cases and
    keeps the default everywhere else.
    """
    if factor_scope and factor_scope.lower() != "temporal":
        # Temporal hint comes from the scope OR a recognised name; keep
        # the default for non-temporal scopes that happen to share a name.
        if factor_name.lower() not in _TEMPORAL_SEGMENT_CAPS:
            return default_max

    natural_cap = _TEMPORAL_SEGMENT_CAPS.get(factor_name.lower())
    if natural_cap is None:
        return default_max
    return max(default_max, natural_cap)


def _categorical_segments(
    groups: dict[str, list[float]],
    factor_name: str,
    *,
    max_segments: int,
) -> list[dict[str, Any]]:
    ranked_groups = sorted(groups.items(), key=lambda item: median(item[1]))
    segments: list[dict[str, Any]] = []
    for group_name, values in ranked_groups[:max_segments]:
        segments.append(
            {
                "condition": f"{factor_name} = {group_name}",
                "observed_median": _round_number(median(values)),
                "sample_size": len(values),
            }
        )
    return segments


def _numeric_segments(
    pairs: list[tuple[float, float]],
    factor_name: str,
    num_bins: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    ordered = sorted(pairs, key=lambda item: item[0])
    n = len(ordered)
    # Ensure each bin gets at least 2 items so median is meaningful.
    num_bins = max(2, min(num_bins, n // 2))
    if num_bins < 2:
        return [], None

    # Equal-frequency split: divide sorted array into num_bins index-based slices.
    bin_edges = [int(round(i * n / num_bins)) for i in range(num_bins + 1)]
    slices = [ordered[bin_edges[i]:bin_edges[i + 1]] for i in range(num_bins)]
    slices = [s for s in slices if s]  # drop any empty edge slices

    if len(slices) < 2:
        return [], None

    # Boundary is the last factor value of each slice except the final one.
    boundaries = [s[-1][0] for s in slices[:-1]]

    segments: list[dict[str, Any]] = []
    for i, s in enumerate(slices):
        metric_values = [m for _, m in s]
        if i == 0:
            condition = f"{factor_name} <= {round(boundaries[0], 2)}"
        elif i == len(slices) - 1:
            condition = f"{factor_name} > {round(boundaries[-1], 2)}"
        else:
            condition = f"{factor_name} in ({round(boundaries[i - 1], 2)}, {round(boundaries[i], 2)}]"
        segments.append(
            {
                "condition": condition,
                "observed_median": _round_number(median(metric_values)),
                "sample_size": len(metric_values),
            }
        )

    threshold_info = {
        "split_boundaries": [_round_number(b, 2) for b in boundaries],
        "num_bins": len(slices),
    }
    return segments, threshold_info


def _epsilon_squared(statistic: float, total_sample_size: int, group_count: int) -> float | None:
    if total_sample_size <= group_count or group_count < 2:
        return None
    estimate = (statistic - group_count + 1) / (total_sample_size - group_count)
    return max(0.0, estimate)


def _base_relationship(
    *,
    factor_name: str,
    factor_scope: str,
    factor_type: str,
    metric_name: str,
    metric_scope: str,
    activity_name: str | None,
    provenance: dict[str, Any] | None,
    test_name: str,
) -> dict[str, Any]:
    return {
        "factor": factor_name,
        "factor_name": factor_name,
        "factor_scope": factor_scope,
        "factor_type": factor_type,
        "metric": metric_name,
        "metric_name": metric_name,
        "metric_scope": metric_scope,
        "activity": activity_name,
        "test": test_name,
        "test_name": test_name,
        "p_value": None,
        "raw_p_value": None,
        "adjusted_p_value": None,
        "statistic": None,
        "effect_size": None,
        "effect_size_type": None,
        "sample_size": 0,
        "segment_support": [],
        "segments": [],
        "summary": "",
        "is_significant": False,
        "accepted": False,
        "provenance": provenance or {},
        "inclusion_reason": None,
        "rejection_reason": None,
        "notes": [],
        "threshold_info": None,
    }


def _rejected_relationship(
    *,
    base: dict[str, Any],
    reason: str,
    note: str,
    sample_size: int = 0,
    segments: list[dict[str, Any]] | None = None,
    raw_p_value: float | None = None,
    statistic: float | None = None,
    effect_size: float | None = None,
    effect_size_type: str | None = None,
    threshold_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rejected = dict(base)
    rejected["rejection_reason"] = reason
    rejected["sample_size"] = sample_size
    rejected["segments"] = segments or []
    rejected["segment_support"] = _segment_support(rejected["segments"])
    rejected["raw_p_value"] = _round_number(raw_p_value) if raw_p_value is not None else None
    rejected["statistic"] = _round_number(statistic) if statistic is not None else None
    rejected["effect_size"] = _round_number(effect_size) if effect_size is not None else None
    rejected["effect_size_type"] = effect_size_type
    rejected["threshold_info"] = threshold_info
    rejected["notes"] = [note]
    return rejected


def _tested_relationship(
    *,
    base: dict[str, Any],
    raw_p_value: float,
    statistic: float,
    effect_size: float,
    effect_size_type: str,
    sample_size: int,
    segments: list[dict[str, Any]],
    summary: str,
    threshold_info: dict[str, Any] | None = None,
    comparison: dict[str, Any] | None = None,
    direction: str | None = None,
) -> dict[str, Any]:
    relationship = dict(base)
    relationship["raw_p_value"] = _round_number(raw_p_value)
    relationship["statistic"] = _round_number(statistic)
    relationship["effect_size"] = _round_number(effect_size)
    relationship["effect_size_type"] = effect_size_type
    relationship["sample_size"] = sample_size
    relationship["segments"] = segments
    relationship["segment_support"] = _segment_support(segments)
    relationship["summary"] = summary
    relationship["threshold_info"] = threshold_info
    if comparison is not None:
        relationship["comparison"] = comparison
    if direction is not None:
        relationship["direction"] = direction
    return relationship


def _analyze_categorical_relationship(
    *,
    factor_name: str,
    factor_scope: str,
    factor_type: str,
    metric_name: str,
    metric_scope: str,
    activity_name: str | None,
    observations: list[dict[str, Any]],
    provenance: dict[str, Any] | None,
    config: AssociationAnalysisConfig,
) -> dict[str, Any]:
    kruskal_fn, _ = _load_stats_backend()
    base = _base_relationship(
        factor_name=factor_name,
        factor_scope=factor_scope,
        factor_type=factor_type,
        metric_name=metric_name,
        metric_scope=metric_scope,
        activity_name=activity_name,
        provenance=provenance,
        test_name="kruskal_wallis",
    )
    if kruskal_fn is None:
        return _rejected_relationship(
            base=base,
            reason="statistics_backend_unavailable",
            note="SciPy is unavailable, so association screening cannot run.",
        )

    if _is_non_simulatable_temporal(factor_name):
        return _rejected_relationship(
            base=base,
            reason="non_simulatable_temporal_factor",
            note=(
                "Calendar month/quarter/year factors reflect historical seasonality "
                "and cannot be modelled as case attributes in a DES simulation."
            ),
        )

    grouped_values: dict[str, list[float]] = defaultdict(list)
    for observation in observations:
        metric_value = _safe_float(observation.get("metric_value"))
        factor_value = observation.get("factor_value")
        if metric_value is None or factor_value in (None, ""):
            continue
        grouped_values[str(factor_value)].append(metric_value)

    eligible_groups = {
        group_name: values
        for group_name, values in grouped_values.items()
        if len(values) >= config.min_group_size
    }
    effective_max_segments = _resolve_max_segments(
        factor_name, factor_scope, config.max_segments
    )
    segments = _categorical_segments(
        eligible_groups,
        factor_name,
        max_segments=effective_max_segments,
    ) if eligible_groups else []

    if len(eligible_groups) < 2:
        return _rejected_relationship(
            base=base,
            reason="insufficient_group_support",
            note=(
                f"At least two groups with at least {config.min_group_size} observations are required "
                "for categorical association screening."
            ),
            sample_size=sum(len(values) for values in grouped_values.values()),
            segments=segments,
        )

    total_sample_size = sum(len(values) for values in eligible_groups.values())
    if total_sample_size < config.min_test_sample_size:
        return _rejected_relationship(
            base=base,
            reason="insufficient_total_support",
            note=(
                f"At least {config.min_test_sample_size} observations are required for a stable association test."
            ),
            sample_size=total_sample_size,
            segments=segments,
        )

    all_metric_values = [value for values in eligible_groups.values() for value in values]
    if not _has_non_constant_values(all_metric_values):
        return _rejected_relationship(
            base=base,
            reason="constant_metric_values",
            note="The observed metric values are effectively constant, so no meaningful group difference can be tested.",
            sample_size=total_sample_size,
            segments=segments,
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            statistic, raw_p_value = kruskal_fn(*eligible_groups.values())
    except Exception:
        return _rejected_relationship(
            base=base,
            reason="statistical_test_failed",
            note="The Kruskal-Wallis association test could not be evaluated for this factor-metric pair.",
            sample_size=total_sample_size,
            segments=segments,
        )

    if raw_p_value is None or not isfinite(float(raw_p_value)):
        return _rejected_relationship(
            base=base,
            reason="invalid_p_value",
            note="The statistical test returned an invalid p-value.",
            sample_size=total_sample_size,
            segments=segments,
        )

    epsilon_squared = _epsilon_squared(float(statistic), total_sample_size, len(eligible_groups))
    if epsilon_squared is None:
        return _rejected_relationship(
            base=base,
            reason="effect_size_unavailable",
            note="The practical effect size could not be computed reliably for this categorical association.",
            sample_size=total_sample_size,
            segments=segments,
            raw_p_value=float(raw_p_value),
            statistic=float(statistic),
        )

    ranked_groups = sorted(eligible_groups.items(), key=lambda item: median(item[1]))
    lowest_group, highest_group = ranked_groups[0], ranked_groups[-1]
    lowest_median = median(lowest_group[1])
    highest_median = median(highest_group[1])
    metric_label = _metric_display_name(metric_name, activity_name)
    summary = (
        f"{factor_name} shows an evidence-supported difference in {metric_label} "
        f"(raw p={raw_p_value:.4f}); {highest_group[0]} has a median of {_round_number(highest_median)} "
        f"versus {_round_number(lowest_median)} for {lowest_group[0]}."
    )

    reportable_segments = [s for s in segments if s.get("sample_size", 0) >= config.min_segment_report_n]
    if len(reportable_segments) < 2:
        return _rejected_relationship(
            base=base,
            reason="insufficient_segment_support_for_reporting",
            note=(
                f"Fewer than 2 segments had >= {config.min_segment_report_n} cases after filtering; "
                "segmented KPI targets would not be reliably grounded in the evidence."
            ),
            sample_size=total_sample_size,
            segments=reportable_segments,
            raw_p_value=float(raw_p_value),
            statistic=float(statistic),
            effect_size=epsilon_squared,
            effect_size_type="epsilon_squared",
        )
    segments = reportable_segments

    return _tested_relationship(
        base=base,
        raw_p_value=float(raw_p_value),
        statistic=float(statistic),
        effect_size=epsilon_squared,
        effect_size_type="epsilon_squared",
        sample_size=total_sample_size,
        segments=segments,
        summary=summary,
        comparison={
            "lowest_group": lowest_group[0],
            "lowest_median": _round_number(lowest_median),
            "highest_group": highest_group[0],
            "highest_median": _round_number(highest_median),
        },
    )


def _analyze_numeric_relationship(
    *,
    factor_name: str,
    factor_scope: str,
    factor_type: str,
    metric_name: str,
    metric_scope: str,
    activity_name: str | None,
    observations: list[dict[str, Any]],
    provenance: dict[str, Any] | None,
    config: AssociationAnalysisConfig,
) -> dict[str, Any]:
    _, spearmanr_fn = _load_stats_backend()
    base = _base_relationship(
        factor_name=factor_name,
        factor_scope=factor_scope,
        factor_type=factor_type,
        metric_name=metric_name,
        metric_scope=metric_scope,
        activity_name=activity_name,
        provenance=provenance,
        test_name="spearman_correlation",
    )
    if spearmanr_fn is None:
        return _rejected_relationship(
            base=base,
            reason="statistics_backend_unavailable",
            note="SciPy is unavailable, so association screening cannot run.",
        )

    pairs: list[tuple[float, float]] = []
    for observation in observations:
        factor_value = _safe_float(observation.get("factor_value"))
        metric_value = _safe_float(observation.get("metric_value"))
        if factor_value is None or metric_value is None:
            continue
        pairs.append((factor_value, metric_value))

    segments, threshold_info = _numeric_segments(pairs, factor_name, num_bins=config.max_segments)
    if len(pairs) < max(config.min_numeric_samples, config.min_test_sample_size):
        return _rejected_relationship(
            base=base,
            reason="insufficient_total_support",
            note=(
                f"At least {max(config.min_numeric_samples, config.min_test_sample_size)} paired observations are required "
                "for numeric association screening."
            ),
            sample_size=len(pairs),
            segments=segments,
            threshold_info=threshold_info,
        )

    if len({factor for factor, _ in pairs}) < 4:
        return _rejected_relationship(
            base=base,
            reason="insufficient_factor_variability",
            note="The numeric factor does not vary enough to support a meaningful rank correlation test.",
            sample_size=len(pairs),
            segments=segments,
            threshold_info=threshold_info,
        )

    if not _has_non_constant_values([factor for factor, _ in pairs]) or not _has_non_constant_values([metric for _, metric in pairs]):
        return _rejected_relationship(
            base=base,
            reason="constant_values",
            note="Either the numeric factor or the metric values are effectively constant, so no meaningful association can be tested.",
            sample_size=len(pairs),
            segments=segments,
            threshold_info=threshold_info,
        )

    if any(sample_size < config.min_group_size for sample_size in _segment_support(segments)):
        return _rejected_relationship(
            base=base,
            reason="insufficient_segment_support",
            note=(
                f"Derived numeric segments must each contain at least {config.min_group_size} observations "
                "to support segmented KPI targets."
            ),
            sample_size=len(pairs),
            segments=segments,
            threshold_info=threshold_info,
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            correlation, raw_p_value = spearmanr_fn(
                [factor for factor, _ in pairs],
                [metric for _, metric in pairs],
            )
    except Exception:
        return _rejected_relationship(
            base=base,
            reason="statistical_test_failed",
            note="The Spearman association test could not be evaluated for this factor-metric pair.",
            sample_size=len(pairs),
            segments=segments,
            threshold_info=threshold_info,
        )

    if correlation is None or raw_p_value is None:
        return _rejected_relationship(
            base=base,
            reason="invalid_test_result",
            note="The Spearman association test returned an invalid result.",
            sample_size=len(pairs),
            segments=segments,
            threshold_info=threshold_info,
        )

    if not isfinite(float(correlation)) or not isfinite(float(raw_p_value)):
        return _rejected_relationship(
            base=base,
            reason="invalid_test_result",
            note="The Spearman association test returned a non-finite result.",
            sample_size=len(pairs),
            segments=segments,
            threshold_info=threshold_info,
        )

    direction = "positive" if float(correlation) > 0 else "negative"
    metric_label = _metric_display_name(metric_name, activity_name)
    summary = (
        f"{factor_name} shows an evidence-supported {direction} association with {metric_label} "
        f"(Spearman rho={float(correlation):.3f}, raw p={float(raw_p_value):.4f})."
    )

    return _tested_relationship(
        base=base,
        raw_p_value=float(raw_p_value),
        statistic=float(correlation),
        effect_size=abs(float(correlation)),
        effect_size_type="spearman_rho",
        sample_size=len(pairs),
        segments=segments,
        summary=summary,
        threshold_info=threshold_info,
        direction=direction,
    )


def _analyze_factor_metric(
    *,
    factor_definition: dict[str, Any],
    metric_name: str,
    metric_scope: str,
    activity_name: str | None,
    observations: list[dict[str, Any]],
    metric_metadata: dict[str, dict[str, Any]],
    config: AssociationAnalysisConfig,
) -> dict[str, Any]:
    provenance = metric_metadata.get(metric_name, {})
    if factor_definition["value_type"] == "numeric":
        return _analyze_numeric_relationship(
            factor_name=factor_definition["name"],
            factor_scope=factor_definition["scope"],
            factor_type=factor_definition["value_type"],
            metric_name=metric_name,
            metric_scope=metric_scope,
            activity_name=activity_name,
            observations=observations,
            provenance=provenance,
            config=config,
        )

    return _analyze_categorical_relationship(
        factor_name=factor_definition["name"],
        factor_scope=factor_definition["scope"],
        factor_type=factor_definition["value_type"],
        metric_name=metric_name,
        metric_scope=metric_scope,
        activity_name=activity_name,
        observations=observations,
        provenance=provenance,
        config=config,
    )


def _finalize_relationship_decisions(
    relationships: list[dict[str, Any]],
    *,
    config: AssociationAnalysisConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _apply_bh_fdr_correction(relationships)

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for relationship in relationships:
        raw_p_value = relationship.get("raw_p_value")
        adjusted_p_value = relationship.get("adjusted_p_value")
        effect_size = relationship.get("effect_size")
        effect_size_type = relationship.get("effect_size_type")

        if raw_p_value is None:
            rejected.append(relationship)
            continue

        if adjusted_p_value is None or float(adjusted_p_value) > config.alpha:
            relationship["rejection_reason"] = "adjusted_significance_threshold_not_met"
            relationship["notes"] = relationship.get("notes", []) + [
                f"The adjusted p-value exceeded the configured false-discovery threshold of {config.alpha}."
            ]
            rejected.append(relationship)
            continue

        if effect_size_type == "spearman_rho" and (
            effect_size is None or float(effect_size) < config.min_abs_spearman_rho
        ):
            relationship["rejection_reason"] = "practical_effect_too_small"
            relationship["notes"] = relationship.get("notes", []) + [
                f"The absolute Spearman rho did not reach the minimum practical threshold of {config.min_abs_spearman_rho}."
            ]
            rejected.append(relationship)
            continue

        if effect_size_type == "epsilon_squared" and (
            effect_size is None or float(effect_size) < config.min_kruskal_epsilon_squared
        ):
            relationship["rejection_reason"] = "practical_effect_too_small"
            relationship["notes"] = relationship.get("notes", []) + [
                f"The epsilon-squared estimate did not reach the minimum practical threshold of {config.min_kruskal_epsilon_squared}."
            ]
            rejected.append(relationship)
            continue

        relationship["accepted"] = True
        relationship["is_significant"] = True
        relationship["inclusion_reason"] = (
            "The relationship passed false-discovery correction, practical effect-size filtering, and minimum support thresholds."
        )
        accepted.append(relationship)

    accepted.sort(key=_relationship_sort_key)
    rejected.sort(key=_relationship_sort_key)
    return accepted[: config.max_relationships], rejected


def analyze_contextual_impact(
    *,
    factor_definitions: list[dict[str, Any]],
    case_observations: list[dict[str, Any]],
    activity_observations: list[dict[str, Any]],
    metric_metadata: dict[str, dict[str, Any]] | None = None,
    config: AssociationAnalysisConfig = DEFAULT_ASSOCIATION_ANALYSIS_CONFIG,
) -> dict[str, Any]:
    """Return a ranked view of context-performance associations with rigorous filtering."""

    kruskal_fn, spearmanr_fn = _load_stats_backend()
    if kruskal_fn is None or spearmanr_fn is None:
        notes = [
            "Statistical context association screening is unavailable because SciPy could not be imported."
        ]
        if _SCIPY_IMPORT_ERROR:
            notes.append(f"SciPy import error: {_SCIPY_IMPORT_ERROR}")
        return {
            "statistics_backend": "unavailable",
            "significance_threshold": config.alpha,
            "fdr_method": "benjamini_hochberg",
            "effect_thresholds": {
                "spearman_rho": config.min_abs_spearman_rho,
                "epsilon_squared": config.min_kruskal_epsilon_squared,
            },
            "support_thresholds": {
                "min_group_size": config.min_group_size,
                "min_test_sample_size": config.min_test_sample_size,
                "min_numeric_samples": config.min_numeric_samples,
            },
            "significant_relationships": [],
            "rejected_relationships": [],
            "tested_relationships": 0,
            "filtered_out_factors": [],
            "notes": notes,
        }

    metric_metadata = metric_metadata or {}
    candidate_results: list[dict[str, Any]] = []
    significant_factors: set[str] = set()
    factor_lookup = {definition["name"]: definition for definition in factor_definitions}

    case_metric_names = sorted(
        {
            metric_name
            for observation in case_observations
            for metric_name, metric_value in observation.get("metrics", {}).items()
            if _safe_float(metric_value) is not None
        }
    )
    for metric_name in case_metric_names:
        for factor_definition in factor_definitions:
            scoped_observations = [
                {
                    "metric_value": observation.get("metrics", {}).get(metric_name),
                    "factor_value": observation.get("factors", {}).get(factor_definition["name"]),
                }
                for observation in case_observations
            ]
            candidate_results.append(
                _analyze_factor_metric(
                    factor_definition=factor_definition,
                    metric_name=metric_name,
                    metric_scope="case_level",
                    activity_name=None,
                    observations=scoped_observations,
                    metric_metadata=metric_metadata,
                    config=config,
                )
            )

    activities = sorted(
        {
            observation.get("activity")
            for observation in activity_observations
            if observation.get("activity")
        }
    )
    for activity_name in activities:
        activity_rows = [
            observation
            for observation in activity_observations
            if observation.get("activity") == activity_name
        ]
        activity_metric_names = sorted(
            {
                metric_name
                for observation in activity_rows
                for metric_name, metric_value in observation.get("metrics", {}).items()
                if _safe_float(metric_value) is not None
            }
        )
        for metric_name in activity_metric_names:
            for factor_definition in factor_definitions:
                scoped_observations = [
                    {
                        "metric_value": observation.get("metrics", {}).get(metric_name),
                        "factor_value": observation.get("factors", {}).get(factor_definition["name"]),
                    }
                    for observation in activity_rows
                ]
                candidate_results.append(
                    _analyze_factor_metric(
                        factor_definition=factor_definition,
                        metric_name=metric_name,
                        metric_scope="activity_level",
                        activity_name=activity_name,
                        observations=scoped_observations,
                        metric_metadata=metric_metadata,
                        config=config,
                    )
                )

    accepted_relationships, rejected_relationships = _finalize_relationship_decisions(
        candidate_results,
        config=config,
    )
    for relationship in accepted_relationships:
        significant_factors.add(relationship["factor"])

    filtered_out_factors = [
        factor_name
        for factor_name in factor_lookup
        if factor_name not in significant_factors
    ]

    return {
        "statistics_backend": "scipy",
        "significance_threshold": config.alpha,
        "fdr_method": "benjamini_hochberg",
        "effect_thresholds": {
            "spearman_rho": config.min_abs_spearman_rho,
            "epsilon_squared": config.min_kruskal_epsilon_squared,
        },
        "support_thresholds": {
            "min_group_size": config.min_group_size,
            "min_test_sample_size": config.min_test_sample_size,
            "min_numeric_samples": config.min_numeric_samples,
        },
        "significant_relationships": accepted_relationships,
        "rejected_relationships": rejected_relationships,
        "tested_relationships": sum(
            1 for relationship in candidate_results if relationship.get("raw_p_value") is not None
        ),
        "filtered_out_factors": filtered_out_factors,
        "notes": [
            "Association filtering uses Benjamini-Hochberg correction and practical effect-size thresholds.",
            "Accepted relationships reflect evidence-supported associations rather than causal impact claims.",
        ],
    }


def export_association_analysis_thresholds() -> dict[str, Any]:
    """Expose hardening thresholds for developer docs and debug views."""

    return {
        "factor_screening": asdict(DEFAULT_CONTEXT_FACTOR_SCREENING_CONFIG),
        "association_analysis": asdict(DEFAULT_ASSOCIATION_ANALYSIS_CONFIG),
    }

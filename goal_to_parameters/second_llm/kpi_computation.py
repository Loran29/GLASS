"""KPI computation from simulated event logs.

Extracts standard BPS KPIs from a simulated event log DataFrame.
All computations are deterministic and code-based — no LLM involvement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


# -----------------------------------------------------------------------
# Data models
# -----------------------------------------------------------------------

@dataclass
class ComputedKPI:
    """A single KPI value computed from a simulation log."""

    name: str
    value: float
    unit: str
    category: str  # time, cost, utilization, throughput, quality, compliance
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class KPIComputationResult:
    """All KPIs computed from a single simulation run."""

    kpis: list[ComputedKPI] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    def get_kpi(self, name: str) -> ComputedKPI | None:
        """Look up a KPI by name (case-insensitive)."""
        name_lower = name.lower()
        for k in self.kpis:
            if k.name.lower() == name_lower:
                return k
        return None

    def to_dict(self) -> dict[str, float]:
        """Return a flat name → value mapping."""
        return {k.name: k.value for k in self.kpis}


# -----------------------------------------------------------------------
# Core KPI extractors
# -----------------------------------------------------------------------

def _ensure_timestamps(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure start_time and end_time are datetime columns."""
    df = df.copy()
    for col in ("start_time", "end_time"):
        if col in df.columns and not pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def _compute_cycle_time(df: pd.DataFrame) -> ComputedKPI | None:
    """Average case cycle time (first event start → last event end)."""
    if "case_id" not in df.columns:
        return None

    case_groups = df.groupby("case_id")
    case_start = case_groups["start_time"].min()
    case_end = case_groups["end_time"].max()
    cycle_times = (case_end - case_start).dt.total_seconds() / 3600.0

    valid = cycle_times.dropna()
    if valid.empty:
        return None

    return ComputedKPI(
        name="Average Cycle Time",
        value=round(float(valid.mean()), 2),
        unit="hours",
        category="time",
        details={
            "median": round(float(valid.median()), 2),
            "p90": round(float(valid.quantile(0.9)), 2),
            "std": round(float(valid.std()), 2),
            "n_cases": len(valid),
        },
    )


def _compute_waiting_time(df: pd.DataFrame) -> ComputedKPI | None:
    """Average waiting time per case (gaps between consecutive activities)."""
    if "case_id" not in df.columns:
        return None

    df_sorted = df.sort_values(["case_id", "start_time"])

    waiting_times: list[float] = []
    for _case_id, group in df_sorted.groupby("case_id"):
        events = group.sort_values("start_time")
        if len(events) < 2:
            continue

        end_times = events["end_time"].values[:-1]
        start_times = events["start_time"].values[1:]
        gaps = (start_times - end_times).astype("timedelta64[s]").astype(float)
        wait = float(gaps[gaps > 0].sum()) / 3600.0
        waiting_times.append(wait)

    if not waiting_times:
        return None

    avg_wait = float(np.mean(waiting_times))
    return ComputedKPI(
        name="Average Waiting Time",
        value=round(avg_wait, 2),
        unit="hours",
        category="time",
        details={
            "median": round(float(np.median(waiting_times)), 2),
            "p90": round(float(np.percentile(waiting_times, 90)), 2),
            "n_cases": len(waiting_times),
        },
    )


def _compute_processing_time(df: pd.DataFrame) -> ComputedKPI | None:
    """Average processing (service) time per case."""
    if "case_id" not in df.columns:
        return None

    df["_duration_h"] = (
        (df["end_time"] - df["start_time"]).dt.total_seconds() / 3600.0
    )

    case_proc = df.groupby("case_id")["_duration_h"].sum()
    valid = case_proc.dropna()
    if valid.empty:
        return None

    return ComputedKPI(
        name="Average Processing Time",
        value=round(float(valid.mean()), 2),
        unit="hours",
        category="time",
        details={
            "median": round(float(valid.median()), 2),
            "p90": round(float(valid.quantile(0.9)), 2),
            "n_cases": len(valid),
        },
    )


def _compute_throughput(df: pd.DataFrame) -> ComputedKPI | None:
    """Throughput: completed cases per day."""
    if "case_id" not in df.columns:
        return None

    case_end = df.groupby("case_id")["end_time"].max().dropna()
    if case_end.empty:
        return None

    total_cases = len(case_end)
    time_span = (case_end.max() - case_end.min()).total_seconds() / 86400.0

    if time_span <= 0:
        return None

    throughput = total_cases / time_span
    return ComputedKPI(
        name="Throughput",
        value=round(throughput, 2),
        unit="cases/day",
        category="throughput",
        details={
            "total_cases": total_cases,
            "simulation_days": round(time_span, 1),
        },
    )


def _compute_resource_utilization(df: pd.DataFrame) -> ComputedKPI | None:
    """Average resource utilization (busy time / available time)."""
    if "resource" not in df.columns or "case_id" not in df.columns:
        return None

    df["_duration_h"] = (
        (df["end_time"] - df["start_time"]).dt.total_seconds() / 3600.0
    )

    sim_start = df["start_time"].min()
    sim_end = df["end_time"].max()
    if pd.isna(sim_start) or pd.isna(sim_end):
        return None

    total_hours = (sim_end - sim_start).total_seconds() / 3600.0
    if total_hours <= 0:
        return None

    resource_busy = df.groupby("resource")["_duration_h"].sum()
    resource_utils = resource_busy / total_hours
    valid = resource_utils.dropna()

    if valid.empty:
        return None

    per_resource = {str(k): round(float(v), 3) for k, v in valid.items()}

    return ComputedKPI(
        name="Resource Utilization",
        value=round(float(valid.mean()), 3),
        unit="ratio",
        category="utilization",
        details={
            "max_utilization": round(float(valid.max()), 3),
            "min_utilization": round(float(valid.min()), 3),
            "n_resources": len(valid),
            "per_resource": per_resource,
        },
    )


def _compute_cost_per_case(
    df: pd.DataFrame,
    cost_per_hour: dict[str, float] | None = None,
) -> ComputedKPI | None:
    """Average cost per case based on resource hourly rates."""
    if cost_per_hour is None or not cost_per_hour:
        return None
    if "resource" not in df.columns or "case_id" not in df.columns:
        return None

    df["_duration_h"] = (
        (df["end_time"] - df["start_time"]).dt.total_seconds() / 3600.0
    )
    df["_cost"] = df.apply(
        lambda row: row["_duration_h"] * cost_per_hour.get(str(row["resource"]), 0),
        axis=1,
    )

    case_cost = df.groupby("case_id")["_cost"].sum()
    valid = case_cost.dropna()
    if valid.empty:
        return None

    return ComputedKPI(
        name="Cost per Case",
        value=round(float(valid.mean()), 2),
        unit="EUR",
        category="cost",
        details={
            "median": round(float(valid.median()), 2),
            "total_cost": round(float(valid.sum()), 2),
            "n_cases": len(valid),
        },
    )


def _compute_activity_waiting_times(df: pd.DataFrame) -> list[ComputedKPI]:
    """Per-activity average waiting time (time in queue before starting)."""
    if "case_id" not in df.columns or "activity" not in df.columns:
        return []

    df_sorted = df.sort_values(["case_id", "start_time"])
    results: list[ComputedKPI] = []

    activity_waits: dict[str, list[float]] = {}

    for _case_id, group in df_sorted.groupby("case_id"):
        events = group.sort_values("start_time").reset_index(drop=True)
        for i in range(1, len(events)):
            gap = (
                events.iloc[i]["start_time"] - events.iloc[i - 1]["end_time"]
            ).total_seconds() / 3600.0
            if gap > 0:
                act = str(events.iloc[i]["activity"])
                activity_waits.setdefault(act, []).append(gap)

    for act_name, waits in sorted(activity_waits.items()):
        if waits:
            results.append(ComputedKPI(
                name=f"{act_name} Waiting Time",
                value=round(float(np.mean(waits)), 2),
                unit="hours",
                category="time",
                details={
                    "median": round(float(np.median(waits)), 2),
                    "p90": round(float(np.percentile(waits, 90)), 2),
                    "n_observations": len(waits),
                },
            ))

    return results


# -----------------------------------------------------------------------
# Main computation entry point
# -----------------------------------------------------------------------

def compute_kpis(
    simulated_log: pd.DataFrame,
    *,
    cost_per_hour: dict[str, float] | None = None,
    include_activity_kpis: bool = True,
) -> KPIComputationResult:
    """Compute all standard KPIs from a simulated event log.

    Parameters
    ----------
    simulated_log : pd.DataFrame
        Event log with columns: case_id, activity, resource, start_time, end_time.
    cost_per_hour : dict, optional
        Mapping of resource name → hourly cost rate for cost KPI computation.
    include_activity_kpis : bool
        Whether to include per-activity waiting time KPIs.

    Returns
    -------
    KPIComputationResult
        All computed KPIs with metadata.
    """
    if simulated_log is None or simulated_log.empty:
        return KPIComputationResult(error="Empty or None simulation log provided.")

    df = _ensure_timestamps(simulated_log)

    result = KPIComputationResult()

    cycle_time = _compute_cycle_time(df)
    if cycle_time:
        result.kpis.append(cycle_time)

    waiting_time = _compute_waiting_time(df)
    if waiting_time:
        result.kpis.append(waiting_time)

    processing_time = _compute_processing_time(df)
    if processing_time:
        result.kpis.append(processing_time)

    throughput = _compute_throughput(df)
    if throughput:
        result.kpis.append(throughput)

    utilization = _compute_resource_utilization(df)
    if utilization:
        result.kpis.append(utilization)

    cost = _compute_cost_per_case(df, cost_per_hour)
    if cost:
        result.kpis.append(cost)

    if include_activity_kpis:
        activity_kpis = _compute_activity_waiting_times(df)
        result.kpis.extend(activity_kpis)

    if not result.kpis:
        result.notes.append("No KPIs could be computed from the simulation log.")

    return result

"""KPI computation from simulated event logs.

Extracts standard BPS KPIs from a simulated event log DataFrame.
All computations are deterministic and code-based — no LLM involvement.
"""

from __future__ import annotations

import bisect
import re
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


_WEEKDAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]


def _wall_time(values: Any, tz: Any = None) -> Any:
    """Timestamps as wall-clock time (the frame calendars are written in)."""
    if isinstance(values, pd.Series):
        return values.dt.tz_localize(None) if values.dt.tz is not None else values
    ts = pd.Timestamp(values)
    if ts.tzinfo is not None:
        ts = (ts.tz_convert(tz) if tz is not None else ts).tz_localize(None)
    return ts


def _seconds_of_day(text: Any) -> float:
    """'HH:MM:SS[.fff]' -> seconds; an end of 23:59:59 or later counts as the full day."""
    h, m, s = (str(text).split(":") + ["0", "0"])[:3]
    sec = int(h) * 3600 + int(m) * 60 + float(s)
    return 86400.0 if sec >= 86399 else sec


def _working_windows(periods: list[dict[str, Any]], t0: pd.Timestamp, t1: pd.Timestamp) -> tuple[list, list]:
    """Sorted working intervals of a Prosimos calendar (list of time periods) within [t0, t1]."""
    starts, ends = [], []
    day = t0.normalize()
    while day <= t1:
        wd = day.weekday()
        for p in periods:
            a = _WEEKDAYS.index(str(p.get("from", "")).upper())
            b = _WEEKDAYS.index(str(p.get("to") or p.get("from", "")).upper())
            if (a <= wd <= b) if a <= b else (wd >= a or wd <= b):
                s = max(day + pd.Timedelta(seconds=_seconds_of_day(p.get("beginTime", "00:00:00"))), t0)
                e = min(day + pd.Timedelta(seconds=_seconds_of_day(p.get("endTime", "23:59:59"))), t1)
                if e > s:
                    starts.append(s)
                    ends.append(e)
        day += pd.Timedelta(days=1)
    order = sorted(range(len(starts)), key=starts.__getitem__)
    return [starts[i] for i in order], [ends[i] for i in order]


def _resource_index(prosimos_params: dict[str, Any] | None) -> list[tuple[set[str], str, list | None]]:
    """(log names of a resource, its role, its calendar periods) from Prosimos parameters.

    Resources are indexed by both id and name, as simod_to_simubridge and patch_validator do,
    so a log that names resources by either matches.
    """
    if not prosimos_params:
        return []
    periods = {c.get("id"): c.get("time_periods") or [] for c in prosimos_params.get("resource_calendars") or []}
    out = []
    for prof in prosimos_params.get("resource_profiles") or []:
        role = str(prof.get("name") or prof.get("id"))
        for res in prof.get("resource_list") or []:
            keys = {str(k) for k in (res.get("id"), res.get("name")) if k}
            out.append((keys, role, periods.get(res.get("calendar"))))
    return out


def _compute_resource_utilization(
    df: pd.DataFrame,
    prosimos_params: dict[str, Any] | None = None,
    sim_start: Any = None,
) -> ComputedKPI | None:
    """Resource utilization: busy time / available working time, per resource.

    Evaluated period: from the simulation start (``sim_start``, else the first start in the log)
    to the last end in the log. Available time = the resource's calendar periods within the period;
    busy time = its activities clipped to those periods. A resource without a calendar falls back to
    busy time / log span and is listed in details["fallback_span_based"]. The value is the mean over
    all resources; per-resource and per-role values are kept in details (see resolve_role_utilization).
    """
    if "resource" not in df.columns or "case_id" not in df.columns:
        return None

    df["_duration_h"] = (
        (df["end_time"] - df["start_time"]).dt.total_seconds() / 3600.0
    )

    starts, ends = _wall_time(df["start_time"]), _wall_time(df["end_time"])
    log_start, log_end = starts.min(), ends.max()
    if pd.isna(log_start) or pd.isna(log_end):
        return None
    span_hours = (log_end - log_start).total_seconds() / 3600.0
    if span_hours <= 0:
        return None
    period_start = log_start
    if sim_start is not None:
        period_start = min(_wall_time(sim_start, df["start_time"].dt.tz), log_start)

    names = df["resource"].astype(str)
    index = _resource_index(prosimos_params)
    known = set().union(*(keys for keys, _, _ in index)) if index else set()
    per_resource: dict[str, float] = {}
    resource_role: dict[str, str] = {}
    fallback: list[str] = []
    for keys, role, periods in index:
        mask = names.isin(keys)
        label = next((k for k in sorted(keys) if k in set(names[mask])), sorted(keys)[0])
        if periods:
            w_start, w_end = _working_windows(periods, period_start, log_end)
            available = sum((e - s).total_seconds() for s, e in zip(w_start, w_end))
        else:
            available = 0.0
        if available > 0:
            busy = 0.0
            for s, e in zip(starts[mask], ends[mask]):
                i = bisect.bisect_right(w_end, s)
                while i < len(w_start) and w_start[i] < e:
                    busy += max(0.0, (min(e, w_end[i]) - max(s, w_start[i])).total_seconds())
                    i += 1
            per_resource[label] = busy / available
        else:
            per_resource[label] = float(df.loc[mask, "_duration_h"].sum()) / span_hours
            fallback.append(label)
        resource_role[label] = role
    for name in sorted(set(names) - known):          # resources without parameters
        per_resource[name] = float(df.loc[names == name, "_duration_h"].sum()) / span_hours
        fallback.append(name)

    valid = pd.Series(per_resource, dtype=float).dropna()
    if valid.empty:
        return None

    per_role: dict[str, float] = {}
    for role in dict.fromkeys(resource_role.values()):
        vals = [valid[r] for r, ro in resource_role.items() if ro == role and r in valid]
        if vals:
            per_role[role] = round(float(np.mean(vals)), 3)

    return ComputedKPI(
        name="Resource Utilization",
        value=round(float(valid.mean()), 3),
        unit="ratio",
        category="utilization",
        details={
            "max_utilization": round(float(valid.max()), 3),
            "min_utilization": round(float(valid.min()), 3),
            "n_resources": len(valid),
            "per_resource": {k: round(float(v), 3) for k, v in valid.items()},
            "per_role": per_role,
            "resource_role": resource_role,
            "fallback_span_based": fallback,
            "busy_clipped_to_calendar": True,
            "period_start": str(period_start),
            "period_end": str(log_end),
        },
    )


def _role_tokens(text: str) -> list[str]:
    """Lower-case words with a plural 's' dropped ("Field agents" -> field, agent)."""
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", text.lower())]


def _roles_named(text: str, roles: list[str]) -> list[str]:
    """Roles whose name occurs in ``text`` (case-insensitive, singular/plural tolerant); a role
    whose occurrence lies inside a longer matched role ("Manager" in "Account Manager") is dropped."""
    tokens = _role_tokens(text or "")
    hits = []
    for role in roles:
        rt = _role_tokens(role)
        spans = [(i, i + len(rt)) for i in range(len(tokens) - len(rt) + 1) if rt and tokens[i:i + len(rt)] == rt]
        if spans:
            hits.append((role, spans))
    hits.sort(key=lambda h: -(h[1][0][1] - h[1][0][0]))
    used: set[int] = set()
    matched = []
    for role, spans in hits:
        free = [sp for sp in spans if not used & set(range(*sp))]
        if free:
            matched.append(role)
            for sp in free:
                used |= set(range(*sp))
    return matched


def resolve_role_utilization(kpi: ComputedKPI, kpi_name: str, kpi_text: str = "") -> ComputedKPI:
    """Resource Utilization for the role(s) a KPI refers to.

    Roles (from the resource profiles) are looked up in the KPI name, then in its description /
    formula text. The value becomes the mean utilization of those roles' resources; the
    all-resource mean is kept in details. Without a match (or without role data) the all-resource
    mean is kept and details["role_resolved"] is False.
    """
    roles = list((kpi.details or {}).get("per_role") or {})
    matched = _roles_named(kpi_name, roles) or _roles_named(kpi_text, roles)
    if not matched:
        return ComputedKPI(kpi.name, kpi.value, kpi.unit, kpi.category,
                           {**kpi.details, "role_resolved": False})
    resource_role = kpi.details.get("resource_role") or {}
    values = [v for r, v in (kpi.details.get("per_resource") or {}).items() if resource_role.get(r) in matched]
    return ComputedKPI(kpi.name, round(float(np.mean(values)), 3), kpi.unit, kpi.category,
                       {**kpi.details, "role_resolved": True, "roles": matched, "all_resource_mean": kpi.value})


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
    prosimos_params: dict[str, Any] | None = None,
    sim_start: Any = None,
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
    prosimos_params : dict, optional
        The Prosimos parameters the log was simulated with (resource_profiles,
        resource_calendars): utilization then uses each resource's working time.
    sim_start : optional
        Simulation start (start of the evaluated period for utilization).

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

    utilization = _compute_resource_utilization(df, prosimos_params, sim_start)
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

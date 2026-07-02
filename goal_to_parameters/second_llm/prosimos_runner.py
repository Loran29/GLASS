"""Prosimos simulation runner — executes SimuBridge scenarios via Prosimos.

Converts a SimuBridgeScenario to the Prosimos JSON format (which mirrors
the SIMOD discovery output), writes temp files, invokes the Prosimos
simulation engine, and returns the simulated event log as a DataFrame.

Supports two backends:
  1. Python-native — requires ``pip install prosimos`` (Python 3.9–3.11).
  2. Docker — runs the ``prosimos`` image; no local Python constraints.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd

from second_llm.output_schema import (
    DistributionType,
    SimuBridgeScenario,
    TimeUnit,
)

logger = logging.getLogger(__name__)

DOCKER_IMAGE = "glass/prosimos"


class ProsimosBackend(str, Enum):
    PYTHON = "python"
    DOCKER = "docker"


# -----------------------------------------------------------------------
# Availability checks
# -----------------------------------------------------------------------

def is_prosimos_available() -> bool:
    """Return True if the prosimos Python package is importable."""
    try:
        import prosimos  # noqa: F401
        return True
    except ImportError:
        return False


def is_docker_available() -> bool:
    """Return True if Docker CLI is on PATH, daemon responds, and the Prosimos image exists."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False
        # Check if the prosimos image is built
        img_check = subprocess.run(
            ["docker", "image", "inspect", DOCKER_IMAGE],
            capture_output=True,
            timeout=10,
        )
        return img_check.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def get_available_backend() -> ProsimosBackend | None:
    """Return the best available backend, or None if neither works."""
    if is_prosimos_available():
        return ProsimosBackend.PYTHON
    if is_docker_available():
        return ProsimosBackend.DOCKER
    return None


# -----------------------------------------------------------------------
# Result container
# -----------------------------------------------------------------------

@dataclass
class ProsimosResult:
    """Outcome of a Prosimos simulation run."""

    simulated_log: pd.DataFrame | None = None
    stats: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.simulated_log is not None and self.error is None


# -----------------------------------------------------------------------
# SimuBridge → Prosimos JSON conversion
# -----------------------------------------------------------------------

_WEEKDAY_TO_SIMOD = {
    "Monday": "MONDAY",
    "Tuesday": "TUESDAY",
    "Wednesday": "WEDNESDAY",
    "Thursday": "THURSDAY",
    "Friday": "FRIDAY",
    "Saturday": "SATURDAY",
    "Sunday": "SUNDAY",
}

_DIST_TYPE_TO_SIMOD = {
    DistributionType.EXPONENTIAL: "expon",
    DistributionType.NORMAL: "norm",
    DistributionType.UNIFORM: "uniform",
    DistributionType.CONSTANT: "fix",
    DistributionType.ERLANG: "gamma",
    DistributionType.TRIANGULAR: "triang",
    DistributionType.BINOMIAL: "binomial",
}

_TIME_UNIT_MULTIPLIER_TO_SECONDS = {
    TimeUnit.SECONDS: 1.0,
    TimeUnit.MINUTES: 60.0,
    TimeUnit.HOURS: 3600.0,
}


def _distribution_to_prosimos(td: Any) -> dict[str, Any]:
    """Convert a SimuBridge TimeDistribution to Prosimos format.

    Prosimos expects distribution_params as a list of {"value": float} dicts.
    The number of params depends on the distribution type:
      - fix: [value]
      - expon: [mean, min, max]
      - norm: [mean, std, min, max]
      - uniform: [min, max]
      - gamma: [mean, var, min, max]
      - triang: [lower, peak, upper]
    """
    dist_name = _DIST_TYPE_TO_SIMOD.get(td.distributionType, "expon")

    params_map = {v.id: v.value for v in td.values}
    multiplier = _TIME_UNIT_MULTIPLIER_TO_SECONDS.get(td.timeUnit, 1.0)

    positional: list[dict[str, float]] = []

    if td.distributionType == DistributionType.CONSTANT:
        val = params_map.get("constantValue", 1.0) * multiplier
        positional = [{"value": val}]
    elif td.distributionType == DistributionType.EXPONENTIAL:
        mean = params_map.get("mean", 1.0) * multiplier
        mn = params_map.get("lower", 0.0) * multiplier
        raw_upper = params_map.get("upper", None)
        mx = raw_upper * multiplier if raw_upper is not None else mean * 10
        positional = [{"value": mean}, {"value": mn}, {"value": mx}]
    elif td.distributionType == DistributionType.NORMAL:
        mean = params_map.get("mean", 1.0) * multiplier
        std = (params_map.get("variance", 0.1) ** 0.5) * multiplier
        mn = params_map.get("lower", 0.0) * multiplier
        raw_upper = params_map.get("upper", None)
        mx = raw_upper * multiplier if raw_upper is not None else mean * 3
        positional = [{"value": mean}, {"value": std}, {"value": mn}, {"value": mx}]
    elif td.distributionType == DistributionType.UNIFORM:
        lo = params_map.get("lower", 0.0) * multiplier
        hi = params_map.get("upper", 1.0) * multiplier
        positional = [{"value": lo}, {"value": hi}]
    elif td.distributionType == DistributionType.ERLANG:
        mean = params_map.get("mean", 1.0) * multiplier
        var = params_map.get("variance", 0.1) * (multiplier ** 2)
        mn = params_map.get("lower", 0.0) * multiplier
        raw_upper = params_map.get("upper", None)
        mx = raw_upper * multiplier if raw_upper is not None else mean * 10
        positional = [{"value": mean}, {"value": var}, {"value": mn}, {"value": mx}]
    elif td.distributionType == DistributionType.TRIANGULAR:
        lo = params_map.get("lower", 0.0) * multiplier
        pk = params_map.get("peak", 0.5) * multiplier
        hi = params_map.get("upper", 1.0) * multiplier
        positional = [{"value": lo}, {"value": pk}, {"value": hi}]
    else:
        mean = params_map.get("mean", 1.0) * multiplier
        mn = 0.0
        mx = mean * 10
        positional = [{"value": mean}, {"value": mn}, {"value": mx}]

    return {
        "distribution_name": dist_name,
        "distribution_params": positional,
    }


def _extract_intermediate_event_ids(bpmn_xml: str) -> list[str]:
    """Parse BPMN XML and return IDs of intermediate catch events."""
    import xml.etree.ElementTree as ET

    if not bpmn_xml:
        return []

    try:
        root = ET.fromstring(bpmn_xml)
    except ET.ParseError:
        return []

    ns = {"bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
    event_ids: list[str] = []

    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "intermediateCatchEvent":
            eid = elem.get("id")
            if eid:
                event_ids.append(eid)

    return event_ids


def scenario_to_prosimos_json(
    scenario: SimuBridgeScenario,
    bpmn_xml: str = "",
) -> dict[str, Any]:
    """Convert a SimuBridgeScenario to the Prosimos/SIMOD JSON format."""
    model = scenario.models[0] if scenario.models else None
    if model is None:
        return {}

    rp = scenario.resourceParameters

    # --- Resource calendars ---
    # Prosimos's CalendarIterator crashes if a day has no work intervals.
    # Extend all calendars to cover Mon–Sun to prevent IndexError on weekends.
    _ALL_DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]

    resource_calendars: list[dict[str, Any]] = []
    for tt in rp.timeTables:
        periods: list[dict[str, Any]] = []
        covered_days: set[str] = set()
        default_begin = "09:00:00"
        default_end = "17:00:00"

        for item in tt.timeTableItems:
            from_day = _WEEKDAY_TO_SIMOD.get(item.startWeekday.value, item.startWeekday.value)
            to_day = _WEEKDAY_TO_SIMOD.get(item.endWeekday.value, item.endWeekday.value)
            begin_time = f"{item.startTime:02d}:00:00"
            end_time = f"{item.endTime:02d}:00:00"
            default_begin = begin_time
            default_end = end_time
            periods.append({
                "from": from_day,
                "to": to_day,
                "beginTime": begin_time,
                "endTime": end_time,
            })
            # Track which days are covered
            start_idx = _ALL_DAYS.index(from_day)
            end_idx = _ALL_DAYS.index(to_day)
            for i in range(start_idx, end_idx + 1):
                covered_days.add(_ALL_DAYS[i])

        # Add missing days with the same hours to avoid CalendarIterator crash
        for day in _ALL_DAYS:
            if day not in covered_days:
                periods.append({
                    "from": day,
                    "to": day,
                    "beginTime": default_begin,
                    "endTime": default_end,
                })

        resource_calendars.append({"id": tt.id, "time_periods": periods})

    # --- Resource profiles (each resource needs a "calendar" field) ---
    resource_profiles: list[dict[str, Any]] = []
    for role in rp.roles:
        res_list = []
        for r in role.resources:
            res_list.append({
                "id": r.id,
                "name": r.id,
                "cost_per_hour": role.costHour,
                "amount": 1,
                "calendar": role.schedule,
            })
        resource_profiles.append({
            "id": role.id,
            "name": role.id,
            "resource_list": res_list,
        })

    # --- Build resource_id → role mapping for task distribution ---
    role_resource_map: dict[str, list[str]] = {}
    for role in rp.roles:
        role_resource_map[role.id] = [r.id for r in role.resources]

    # --- Task-resource distribution ---
    task_resource_dist: list[dict[str, Any]] = []
    for act in model.modelParameter.activities:
        resources_entry: list[dict[str, Any]] = []
        for role_id in act.resources:
            resource_ids = role_resource_map.get(role_id, [role_id])
            for res_id in resource_ids:
                resources_entry.append({
                    "resource_id": res_id,
                    "distribution_name": _DIST_TYPE_TO_SIMOD.get(
                        act.duration.distributionType, "expon"
                    ),
                    "distribution_params": _distribution_to_prosimos(act.duration)[
                        "distribution_params"
                    ],
                })
        task_resource_dist.append({
            "task_id": act.id,
            "task_name": act.name or act.id,
            "resources": resources_entry,
        })

    # --- Gateway branching probabilities ---
    gateway_probs: list[dict[str, Any]] = []
    for gw in model.modelParameter.gateways:
        probs = []
        for flow_id, prob in gw.probabilities.items():
            probs.append({"path_id": flow_id, "value": prob})
        gateway_probs.append({
            "gateway_id": gw.id,
            "probabilities": probs,
        })

    # --- Arrival time distribution ---
    arrival_dist: dict[str, Any] = {}
    if model.modelParameter.events:
        arrival_dist = _distribution_to_prosimos(
            model.modelParameter.events[0].interArrivalTime
        )

    # Safeguard: cap extreme arrival distributions to prevent datetime overflow.
    # A mean inter-arrival > 1 week (604800s) with high variance can cause
    # individual samples to overflow Python's datetime. Replace with exponential.
    _MAX_MEAN_ARRIVAL_SECONDS = 604800.0
    if arrival_dist.get("distribution_params"):
        first_param = arrival_dist["distribution_params"][0].get("value", 0)
        if first_param > _MAX_MEAN_ARRIVAL_SECONDS:
            arrival_dist = {
                "distribution_name": "expon",
                "distribution_params": [
                    {"value": 3600.0},
                    {"value": 0.0},
                    {"value": 36000.0},
                ],
            }

    # --- Arrival time calendar (24/7 — cases can arrive any time) ---
    arrival_calendar: list[dict[str, Any]] = [
        {
            "from": "MONDAY",
            "to": "SUNDAY",
            "beginTime": "00:00:00",
            "endTime": "23:59:59",
        }
    ]

    # --- Intermediate event distributions (timer events from BPMN) ---
    event_distribution: list[dict[str, Any]] = []
    intermediate_event_ids = _extract_intermediate_event_ids(bpmn_xml)
    for evt_id in intermediate_event_ids:
        event_distribution.append({
            "event_id": evt_id,
            "distribution_name": "expon",
            "distribution_params": [
                {"value": 0.0},
                {"value": 0.0},
                {"value": 1.0},
            ],
        })

    prosimos_json: dict[str, Any] = {
        "resource_profiles": resource_profiles,
        "resource_calendars": resource_calendars,
        "task_resource_distribution": task_resource_dist,
        "gateway_branching_probabilities": gateway_probs,
        "arrival_time_distribution": arrival_dist,
        "arrival_time_calendar": arrival_calendar,
    }

    if event_distribution:
        prosimos_json["event_distribution"] = event_distribution

    return prosimos_json


# -----------------------------------------------------------------------
# Simulation execution
# -----------------------------------------------------------------------

def _run_python_backend(
    bpmn_path: Path,
    json_path: Path,
    log_path: Path,
    stats_path: Path,
    total_cases: int,
    start_time: str,
) -> str | None:
    """Run via the prosimos Python package. Returns error string or None."""
    try:
        from prosimos.simulation_engine import run_simulation

        run_simulation(
            bpmn_path=str(bpmn_path),
            json_path=str(json_path),
            total_cases=total_cases,
            log_out_path=str(log_path),
            stat_out_path=str(stats_path),
            starting_at=start_time,
        )
        return None
    except Exception as e:
        return f"Prosimos Python backend failed: {e}"


def _run_docker_backend(
    bpmn_path: Path,
    json_path: Path,
    log_path: Path,
    stats_path: Path,
    total_cases: int,
    start_time: str,
) -> str | None:
    """Run via Docker container. Returns error string or None."""
    import os
    import platform

    work_dir = bpmn_path.parent

    # On Windows with MSYS/Git Bash, /data/ gets mangled to C:/Program Files/Git/data/
    # Use //data/ (double leading slash) to prevent MSYS path translation
    container_prefix = "//data" if platform.system() == "Windows" else "/data"

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{work_dir}:/data",
        DOCKER_IMAGE,
        "start-simulation",
        "--bpmn_path", f"{container_prefix}/{bpmn_path.name}",
        "--json_path", f"{container_prefix}/{json_path.name}",
        "--total_cases", str(total_cases),
        "--log_out_path", f"{container_prefix}/{log_path.name}",
        "--stat_out_path", f"{container_prefix}/{stats_path.name}",
        "--starting_at", start_time,
    ]

    env = os.environ.copy()
    env["MSYS_NO_PATHCONV"] = "1"

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.strip() or proc.stdout.strip()
            return f"Prosimos Docker backend failed (exit {proc.returncode}): {stderr[-1000:]}"
        return None
    except subprocess.TimeoutExpired:
        return "Prosimos Docker backend timed out (300s)."
    except FileNotFoundError:
        return "Docker not found on PATH."
    except Exception as e:
        return f"Prosimos Docker backend error: {e}"


def run_prosimos_simulation(
    scenario: SimuBridgeScenario,
    bpmn_xml: str,
    *,
    total_cases: int | None = None,
    start_time: str = "2024-01-01 09:00:00.000000+00:00",
    seed: int = 42,
    backend: ProsimosBackend | None = None,
) -> ProsimosResult:
    """Run a simulation using the Prosimos engine.

    Parameters
    ----------
    scenario : SimuBridgeScenario
        The scenario to simulate (baseline or proposed).
    bpmn_xml : str
        BPMN 2.0 XML content for the process model.
    total_cases : int, optional
        Number of cases to simulate. Defaults to scenario.numberOfInstances.
    start_time : str
        Simulation start timestamp.
    seed : int
        Random seed for reproducibility.
    backend : ProsimosBackend, optional
        Force a specific backend. Auto-detects if None.

    Returns
    -------
    ProsimosResult
        Contains the simulated event log DataFrame or error information.
    """
    # Resolve backend
    if backend is None:
        backend = get_available_backend()
    if backend is None:
        return ProsimosResult(
            error=(
                "Neither Prosimos Python package nor Docker is available. "
                "Install prosimos (requires Python 3.9-3.11) or install Docker."
            )
        )

    if not bpmn_xml or not bpmn_xml.strip():
        return ProsimosResult(error="No BPMN XML provided for simulation.")

    # Prosimos requires timezone-aware timestamps
    if "+" not in start_time and "Z" not in start_time:
        start_time = start_time + "+00:00"

    num_cases = total_cases or scenario.numberOfInstances or 1000

    prosimos_json = scenario_to_prosimos_json(scenario, bpmn_xml)
    if not prosimos_json:
        return ProsimosResult(error="Failed to convert scenario to Prosimos format.")

    result = ProsimosResult()

    # Write debug files to a persistent location for troubleshooting
    debug_dir = Path(tempfile.gettempdir()) / "prosimos_debug"
    debug_dir.mkdir(exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="prosimos_") as tmpdir:
            tmp_path = Path(tmpdir)

            bpmn_path = tmp_path / "model.bpmn"
            bpmn_path.write_text(bpmn_xml, encoding="utf-8")

            json_path = tmp_path / "simulation_params.json"
            json_path.write_text(json.dumps(prosimos_json, indent=2), encoding="utf-8")

            # Save debug copies
            (debug_dir / "last_sim_params.json").write_text(
                json.dumps(prosimos_json, indent=2), encoding="utf-8"
            )
            (debug_dir / "last_model.bpmn").write_text(bpmn_xml, encoding="utf-8")

            log_path = tmp_path / "simulated_log.csv"
            stats_path = tmp_path / "simulation_stats.csv"

            if backend == ProsimosBackend.PYTHON:
                err = _run_python_backend(
                    bpmn_path, json_path, log_path, stats_path,
                    num_cases, start_time,
                )
            else:
                err = _run_docker_backend(
                    bpmn_path, json_path, log_path, stats_path,
                    num_cases, start_time,
                )

            if err:
                result.error = err
                (debug_dir / "last_error.txt").write_text(err, encoding="utf-8")
                logger.error("Prosimos execution error: %s", err)
                return result

            if log_path.exists():
                result.simulated_log = pd.read_csv(str(log_path))
                result.notes.append(
                    f"Simulated {num_cases} cases via {backend.value} backend."
                )
            else:
                result.error = "Prosimos did not produce an output log file."

            if stats_path.exists():
                try:
                    result.stats = pd.read_csv(str(stats_path)).to_dict()
                except Exception:
                    pass

    except Exception as e:
        result.error = f"Unexpected error during simulation: {e}"
        logger.exception("Prosimos runner unexpected error")

    return result


# -----------------------------------------------------------------------
# Alternative: load pre-computed simulation results
# -----------------------------------------------------------------------

def load_simulation_log(path: str | Path) -> ProsimosResult:
    """Load a pre-computed simulation event log from a CSV file.

    Expected columns: case_id, activity, resource, start_time, end_time
    (or common aliases thereof).
    """
    path = Path(path)
    if not path.exists():
        return ProsimosResult(error=f"File not found: {path}")

    try:
        df = pd.read_csv(str(path))
    except Exception as e:
        return ProsimosResult(error=f"Failed to read CSV: {e}")

    _COL_ALIASES = {
        "case_id": ["case:concept:name", "caseid", "case id", "case"],
        "activity": ["concept:name", "task", "event", "activity_name"],
        "resource": ["org:resource", "org:group", "resource_name"],
        "start_time": ["start_timestamp", "time:timestamp", "start"],
        "end_time": ["end_timestamp", "complete_timestamp", "end"],
    }

    for target, aliases in _COL_ALIASES.items():
        if target not in df.columns:
            for alias in aliases:
                if alias in df.columns:
                    df = df.rename(columns={alias: target})
                    break

    required = {"case_id", "activity", "start_time", "end_time"}
    missing = required - set(df.columns)
    if missing:
        return ProsimosResult(
            error=f"Missing required columns: {missing}. Found: {list(df.columns)}"
        )

    for col in ("start_time", "end_time"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    return ProsimosResult(
        simulated_log=df,
        notes=[f"Loaded {len(df)} events from {path.name}"],
    )

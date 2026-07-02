"""Integrated SIMOD runner with Python-native and Docker backends.

Supports two execution modes:

1. **Python-native** — calls the ``simod`` library directly.
   Requires ``pip install simod`` (Python 3.9-3.11) + Java 1.8.

2. **Docker** — runs the ``nokal/simod`` image.
   Requires only Docker Desktop / Docker Engine running on the host.
   No Python or Java version constraints.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from enum import Enum
from pathlib import Path

import pandas as pd
import yaml

from second_llm.models import RawSimodInput, SimodResult

logger = logging.getLogger(__name__)

DOCKER_IMAGE = "nokal/simod"
# The simod CLI lives inside a virtualenv in the container image.
_CONTAINER_SIMOD_BIN = "/usr/src/Simod/.venv/bin/simod"


class SimodBackend(str, Enum):
    PYTHON = "python"
    DOCKER = "docker"


# -----------------------------------------------------------------------
# Availability checks
# -----------------------------------------------------------------------

def is_python_simod_available() -> bool:
    """Return True if the simod Python package can be imported."""
    try:
        import simod  # noqa: F401
        return True
    except ImportError:
        return False


def is_docker_available() -> bool:
    """Return True if Docker CLI is on PATH and the daemon responds."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# -----------------------------------------------------------------------
# Column normalisation — SIMOD requires exact column names
# -----------------------------------------------------------------------

# SIMOD's --one-shot mode expects exactly these column names.
_SIMOD_COLUMNS = {"case_id", "activity", "resource", "start_time", "end_time"}

# Common aliases found in XES-derived or PM4Py-exported CSV files.
_COLUMN_ALIASES: dict[str, list[str]] = {
    "case_id": [
        "case:concept:name", "caseid", "case id", "case_id",
        "case", "traceid", "trace_id", "trace id",
    ],
    "activity": [
        "concept:name", "activity", "activity_name", "event",
        "task", "activity name",
    ],
    "resource": [
        "org:resource", "resource", "org:group", "resource_name",
        "resource name", "agent",
    ],
    "start_time": [
        "time:timestamp", "start_time", "start time", "starttime",
        "start_timestamp", "start timestamp", "timestamp",
    ],
    "end_time": [
        "end_time", "end time", "endtime", "end_timestamp",
        "end timestamp", "complete_timestamp", "time:end",
    ],
}


def _pair_lifecycle_events(df: pd.DataFrame, lc_col: str) -> pd.DataFrame:
    """Pair START→COMPLETE lifecycle rows into single rows with real durations.

    XES-derived CSVs have one row per event where start_time == end_time
    (the single XES timestamp is copied to both columns).  To get a real
    activity duration we need: start_time = START.timestamp,
    end_time = COMPLETE.timestamp.

    Activities that have no START counterpart (e.g. instant A_ events)
    fall back to start_time == end_time, which gives duration = 0 and is
    the correct representation for instantaneous activities.
    """
    lc = df[lc_col].str.lower()
    starts    = df[lc == "start"].copy()
    completes = df[lc == "complete"].copy()

    if starts.empty:
        return completes.drop(columns=[lc_col], errors="ignore")

    starts["_occ"]    = (starts.sort_values("start_time")
                         .groupby(["case_id", "activity"]).cumcount())
    completes["_occ"] = (completes.sort_values("end_time")
                         .groupby(["case_id", "activity"]).cumcount())

    start_ts = (starts[["case_id", "activity", "_occ", "start_time"]]
                .rename(columns={"start_time": "_real_start"}))

    merged = completes.merge(start_ts, on=["case_id", "activity", "_occ"], how="left")
    merged["start_time"] = merged["_real_start"].fillna(merged["end_time"])
    merged = merged.drop(columns=["_occ", "_real_start", lc_col], errors="ignore")

    # Clamp any row where start_time >= end_time (unpaired or mismatched)
    # to a 1-second floor so distribution fitting never sees zero/negative durations.
    invalid = merged["start_time"] >= merged["end_time"]
    if invalid.any():
        end_ts = pd.to_datetime(merged.loc[invalid, "end_time"], format="mixed", utc=True)
        merged.loc[invalid, "start_time"] = (
            (end_ts - pd.Timedelta(seconds=1))
            .dt.strftime("%Y-%m-%d %H:%M:%S.%f%z")
        )

    return merged


def fix_single_timestamp_log(csv_path: Path) -> tuple[Path, bool]:
    """Detect and fix XES-derived CSVs where every row has start_time == end_time.

    Returns the (possibly rewritten) path and a bool indicating whether
    the fix was applied.  If no lifecycle column is present or durations
    are already non-zero, the original path is returned unchanged.
    """
    df = pd.read_csv(csv_path)

    lc_col = next((c for c in df.columns if "lifecycle" in c.lower()), None)
    if not lc_col or "start_time" not in df.columns or "end_time" not in df.columns:
        return csv_path, False

    if not (df["start_time"] == df["end_time"]).all():
        return csv_path, False

    fixed = _pair_lifecycle_events(df, lc_col)
    out_path = csv_path.parent / f"paired_{csv_path.name}"
    fixed.to_csv(out_path, index=False)
    logger.info(
        "Single-timestamp log detected: paired START+COMPLETE events → %d activity instances",
        len(fixed),
    )
    return out_path, True


def normalise_csv_columns(csv_path: Path) -> tuple[Path, list[str]]:
    """Rename CSV columns to match SIMOD's expected names.

    Returns the (possibly rewritten) path and a list of rename notes.
    If columns already match, the original file is returned unchanged.
    """
    df = pd.read_csv(csv_path, nrows=0)  # headers only
    original_columns = list(df.columns)
    lower_map = {col.strip().lower(): col for col in original_columns}

    # Check if already correct
    if _SIMOD_COLUMNS.issubset(set(lower_map.keys())):
        return csv_path, []

    rename_map: dict[str, str] = {}
    notes: list[str] = []

    for target, aliases in _COLUMN_ALIASES.items():
        if target in lower_map:
            # Column exists with correct name (possibly different case)
            actual = lower_map[target]
            if actual != target:
                rename_map[actual] = target
                notes.append(f"  {actual} -> {target} (case fix)")
            continue

        # Try aliases
        matched = False
        for alias in aliases:
            if alias in lower_map:
                rename_map[lower_map[alias]] = target
                notes.append(f"  {lower_map[alias]} -> {target}")
                matched = True
                break

        if not matched:
            logger.warning(
                "Could not find a match for SIMOD column '%s' in CSV "
                "columns: %s", target, original_columns,
            )

    if not rename_map:
        return csv_path, []

    # Rewrite the CSV with renamed columns
    df_full = pd.read_csv(csv_path)
    df_full.rename(columns=rename_map, inplace=True)
    out_path = csv_path.parent / f"normalised_{csv_path.name}"
    df_full.to_csv(out_path, index=False)
    logger.info("Normalised CSV columns: %s", rename_map)
    return out_path, notes


# -----------------------------------------------------------------------
# Output collection (shared by both backends)
# -----------------------------------------------------------------------

def _is_simulation_params_json(path: Path) -> bool:
    """Return True if *path* looks like a SIMOD simulation parameters file.

    SIMOD writes several JSON files (settings, intermediate results).
    The simulation parameters file is the one containing keys like
    ``resource_profiles``, ``arrival_time_distribution``, or
    ``gateway_branching_probabilities``.  Settings files instead have
    keys like ``mining_algorithm``, ``optimization_metric``, etc.
    """
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read(2000)
        for marker in (
            "resource_profiles",
            "arrival_time_distribution",
            "gateway_branching_probabilities",
            "task_resource_distribution",
            "resource_calendars",
        ):
            if marker in text:
                return True
    except Exception:
        pass
    return False


def _collect_simod_output(output_dir: Path, process_name: str) -> RawSimodInput:
    """Read SIMOD output files from *output_dir* and return a model."""

    # SIMOD may nest output under a subfolder or place files at the root.
    # Try exact name first, then glob for any .bpmn / .json in the tree.
    bpmn_path = output_dir / f"{process_name}.bpmn"
    json_path = output_dir / f"{process_name}.json"

    if not bpmn_path.exists():
        candidates = glob.glob(str(output_dir / "**" / "*.bpmn"), recursive=True)
        if candidates:
            bpmn_path = Path(candidates[0])

    if not json_path.exists() or not _is_simulation_params_json(json_path):
        # Glob all .json files and pick the one that contains simulation
        # parameter markers, not settings/config files.
        candidates = glob.glob(str(output_dir / "**" / "*.json"), recursive=True)
        matched = [Path(c) for c in candidates if _is_simulation_params_json(Path(c))]
        if matched:
            json_path = matched[0]
        elif candidates:
            # Last resort: largest file is most likely the params
            json_path = max(
                (Path(c) for c in candidates),
                key=lambda p: p.stat().st_size,
            )
            logger.warning(
                "No JSON file with simulation parameter markers found. "
                "Using largest JSON file: %s", json_path,
            )

    bpmn_content = bpmn_path.read_text(encoding="utf-8") if bpmn_path.exists() else ""
    json_content = json_path.read_text(encoding="utf-8") if json_path.exists() else ""

    raw_parts: list[str] = []
    if bpmn_content:
        raw_parts.append("=== BPMN MODEL ===\n" + bpmn_content)
    if json_content:
        raw_parts.append("=== SIMULATION PARAMETERS (JSON) ===\n" + json_content)
    raw_text = "\n\n".join(raw_parts)

    simod_result = SimodResult(
        bpmn_path=str(bpmn_path),
        json_params_path=str(json_path),
        bpmn_content=bpmn_content,
        json_params_content=json_content,
        output_dir=str(output_dir),
        process_name=process_name,
    )

    lines = raw_text.splitlines() if raw_text else []
    return RawSimodInput(
        raw_text=raw_text,
        line_count=len(lines),
        is_non_empty=bool(raw_text.strip()),
        simod_result=simod_result,
    )


# -----------------------------------------------------------------------
# Python-native backend
# -----------------------------------------------------------------------

def _run_python(event_log_path: Path, output_dir: Path, one_shot: bool) -> RawSimodInput:
    """Run SIMOD via the installed Python package."""
    try:
        import simod  # noqa: F401
    except ImportError:
        raise ImportError(
            "The 'simod' package is not installed. "
            "Install it with:  pip install simod\n"
            "SIMOD requires Python 3.9-3.11 and Java 1.8 on PATH.\n\n"
            "Alternatively, switch to the Docker backend."
        ) from None

    from simod.event_log.event_log import EventLog
    from simod.runtime_meter import RuntimeMeter
    from simod.settings.simod_settings import SimodSettings
    from simod.simod import Simod

    settings = SimodSettings.one_shot() if one_shot else SimodSettings.default()
    settings.common.train_log_path = event_log_path
    settings.common.test_log_path = None

    runtimes = RuntimeMeter()
    runtimes.start(RuntimeMeter.PREPROCESSING)
    event_log = EventLog.from_path(
        log_ids=settings.common.log_ids,
        train_log_path=settings.common.train_log_path,
        test_log_path=settings.common.test_log_path,
        preprocessing_settings=settings.preprocessing,
        need_test_partition=settings.common.perform_final_evaluation,
    )
    runtimes.stop(RuntimeMeter.PREPROCESSING)

    simod_instance = Simod(settings, event_log=event_log, output_dir=output_dir)
    simod_instance.run(runtimes=runtimes)

    return _collect_simod_output(output_dir, event_log_path.stem)


# -----------------------------------------------------------------------
# Docker backend
# -----------------------------------------------------------------------

def _run_docker(event_log_path: Path, output_dir: Path, one_shot: bool) -> RawSimodInput:
    """Run SIMOD inside the ``nokal/simod`` Docker container.

    Mounts two directories into the container:
      - an *input* dir containing the event log CSV
      - an *output* dir where SIMOD writes results

    On Windows the temp directory may not be in a Docker-shared path,
    so we copy the event log into a sub-folder of *output_dir* (which
    the user controls or which defaults to a known temp location) and
    mount from there.
    """
    if not is_docker_available():
        raise RuntimeError(
            "Docker is not available. Make sure Docker Desktop (or Docker "
            "Engine) is installed and running, then try again."
        )

    event_log_abs = event_log_path.resolve()
    output_abs = output_dir.resolve()
    output_abs.mkdir(parents=True, exist_ok=True)

    # Copy the event log next to the output dir so both mounts live
    # under the same root — avoids Docker shared-folder issues on Windows.
    input_dir = output_abs / "_input"
    input_dir.mkdir(exist_ok=True)
    local_log = input_dir / event_log_abs.name
    shutil.copy2(event_log_abs, local_log)

    container_input = "/simod_input"
    container_output = "/simod_output"

    cmd: list[str] = [
        "docker", "run", "--rm",
        "-v", f"{input_dir}:{container_input}",
        "-v", f"{output_abs}:{container_output}",
        DOCKER_IMAGE,
        _CONTAINER_SIMOD_BIN,
    ]
    if one_shot:
        cmd += [
            "--one-shot",
            "--event-log", f"{container_input}/{local_log.name}",
            "--output", container_output,
        ]
    else:
        # Standard SIMOD mode requires a configuration file. We generate a
        # minimal config next to the copied event log and point SIMOD at it.
        config_path = input_dir / "configuration.yaml"
        config_payload = {
            "version": 5,
            "common": {
                "train_log_path": local_log.name,
            },
        }
        with config_path.open("w", encoding="utf-8") as config_file:
            yaml.safe_dump(config_payload, config_file, sort_keys=False)

        cmd += [
            "--configuration", f"{container_input}/{config_path.name}",
            "--output", container_output,
        ]

    # On Windows under Git Bash / MSYS, environment variable
    # MSYS_NO_PATHCONV prevents mangling of Linux-style paths.
    env = os.environ.copy()
    if sys.platform == "win32":
        env["MSYS_NO_PATHCONV"] = "1"

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=3600,  # 60-minute timeout
        env=env,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"SIMOD Docker container exited with code {result.returncode}.\n"
            f"Output:\n{stderr[:2000]}"
        )

    return _collect_simod_output(output_abs, event_log_abs.stem)


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------

class SimodRunner:
    """Run SIMOD on an event log and return structured results.

    Parameters
    ----------
    event_log_path:
        Path to a CSV event log with at least: case_id, activity,
        resource, start_time, end_time columns.
    output_dir:
        Where SIMOD writes its output. Defaults to a temporary directory.
    backend:
        ``SimodBackend.PYTHON`` for native Python or
        ``SimodBackend.DOCKER`` for the Docker container.
    """

    def __init__(
        self,
        event_log_path: Path,
        output_dir: Path | None = None,
        backend: SimodBackend = SimodBackend.DOCKER,
    ) -> None:
        self._event_log_path = Path(event_log_path)
        self._output_dir = (
            Path(output_dir) if output_dir else
            Path(tempfile.mkdtemp(prefix="simod_output_"))
        )
        self._backend = backend

    def run(self, one_shot: bool = True) -> RawSimodInput:
        """Execute SIMOD and return a populated ``RawSimodInput``.

        Automatically normalises CSV column names and fixes single-timestamp
        XES-derived logs before passing to SIMOD.
        """
        log_path, fixed = fix_single_timestamp_log(self._event_log_path)
        if fixed:
            logger.info("Applied START+COMPLETE lifecycle pairing to %s", self._event_log_path.name)

        log_path, rename_notes = normalise_csv_columns(log_path)
        if rename_notes:
            logger.info(
                "CSV columns were renamed for SIMOD compatibility:\n%s",
                "\n".join(rename_notes),
            )

        if self._backend == SimodBackend.PYTHON:
            return _run_python(log_path, self._output_dir, one_shot)
        return _run_docker(log_path, self._output_dir, one_shot)

"""
Run SIMOD on each Stage-2 event log to produce the baseline simulation models.

Outputs to evaluation/simod_outputs/<log_name>/
  - model.bpmn
  - simulation_parameters.json
  - simod_raw.json   (serialised RawSimodInput for later use in Stage 2)

Usage (from Thesis/goal_to_parameters/):
    python ../evaluation/run_simod_baselines.py

SIMOD needs either:
  - Docker Desktop running  (preferred, uses nokal/simod image)
  - OR: pip install simod + Java 1.8 on PATH
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# --- make sure the goal_to_parameters package is on sys.path ---
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "goal_to_parameters"))

from second_llm.simod_runner import SimodBackend, SimodRunner

# Logs to process for Stage 2 (Sepsis and BPIC 2019 excluded)
STAGE2_LOGS = ["bpic2017", "bpic2012", "sepsis"]

CSV_DIR    = REPO_ROOT / "evaluation" / "logs" / "csv"
OUTPUT_DIR = REPO_ROOT / "evaluation" / "simod_outputs"


import pandas as pd

# Max events to feed SIMOD — process discovery works well with ~5k cases.
# BPIC 2017 has 1.2M events which causes timeouts; cap at 100k rows (~4k cases).
SIMOD_MAX_ROWS = 100_000


def _pair_lifecycle_events(df: pd.DataFrame, lc_col: str) -> pd.DataFrame:
    """Pair START→COMPLETE events into single rows with real durations.

    XES-derived CSVs have one row per event with start_time == end_time.
    Real activity duration = COMPLETE.timestamp - START.timestamp.
    Activities without a START counterpart (e.g. A_ instant events) keep
    start_time == end_time (duration 0), which is correct for them.
    """
    lc = df[lc_col].str.lower()
    starts    = df[lc == "start"].copy()
    completes = df[lc == "complete"].copy()

    if starts.empty:
        return completes.drop(columns=[lc_col], errors="ignore")

    # Number occurrences within (case_id, activity) in timestamp order
    starts["_occ"]    = (starts.sort_values("start_time")
                         .groupby(["case_id", "activity"]).cumcount())
    completes["_occ"] = (completes.sort_values("end_time")
                         .groupby(["case_id", "activity"]).cumcount())

    start_ts = (starts[["case_id", "activity", "_occ", "start_time"]]
                .rename(columns={"start_time": "_real_start"}))

    merged = completes.merge(start_ts, on=["case_id", "activity", "_occ"], how="left")
    # Use real start time where paired; fall back to end_time (instantaneous)
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


def _prepare_csv(csv_path: Path, out_dir: Path) -> Path:
    """Return a (possibly sampled) CSV path suitable for SIMOD."""
    df = pd.read_csv(csv_path)

    lc_col = next((c for c in df.columns if "lifecycle" in c.lower()), None)
    if lc_col and "start_time" in df.columns and "end_time" in df.columns:
        # Detect XES-style single-timestamp logs where every row has
        # start_time == end_time.  These need START+COMPLETE pairing to
        # produce real duration intervals; filtering to COMPLETE only would
        # give 0-duration activities and degenerate resource profiles.
        is_single_ts = (df["start_time"] == df["end_time"]).all()
        if is_single_ts:
            df = _pair_lifecycle_events(df, lc_col)
            print(f"  Paired START+COMPLETE events: {len(df)} activity instances")
        else:
            # Already has real durations — keep COMPLETE events only
            complete = df[df[lc_col].str.lower() == "complete"]
            if len(complete) > 0:
                df = complete.copy()
                print(f"  Filtered to 'complete' lifecycle events: {len(df)} rows")
    elif lc_col:
        complete = df[df[lc_col].str.lower() == "complete"]
        if len(complete) > 0:
            df = complete.copy()
            print(f"  Filtered to 'complete' lifecycle events: {len(df)} rows")

    if len(df) <= SIMOD_MAX_ROWS:
        sampled_path = out_dir / f"{csv_path.stem}_simod.csv"
        df.to_csv(sampled_path, index=False)
        return sampled_path

    # Sample complete cases so SIMOD sees whole traces, not truncated ones
    case_ids = df["case_id"].unique()
    rng = __import__("numpy").random.default_rng(42)
    n_cases = int(SIMOD_MAX_ROWS / (len(df) / len(case_ids)))
    sampled_ids = rng.choice(case_ids, size=min(n_cases, len(case_ids)), replace=False)
    sampled = df[df["case_id"].isin(sampled_ids)]
    sampled_path = out_dir / f"{csv_path.stem}_simod.csv"
    sampled.to_csv(sampled_path, index=False)
    print(f"  Sampled {len(sampled_ids)} cases ({len(sampled)} events) for SIMOD")
    return sampled_path


def run_one(log_name: str, backend: SimodBackend) -> None:
    csv_path = CSV_DIR / f"{log_name}.csv"
    out_dir  = OUTPUT_DIR / log_name

    if not csv_path.exists():
        print(f"  SKIP — {csv_path} not found. Download and convert the log first.")
        return

    if (out_dir / "simod_raw.json").exists():
        print(f"  SKIP — {log_name} already has a SIMOD output. Delete to re-run.")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    simod_csv = _prepare_csv(csv_path, out_dir)

    print(f"  Running SIMOD on {log_name} ({backend.value} backend) ...")
    t0 = time.time()

    runner = SimodRunner(event_log_path=simod_csv, output_dir=out_dir, backend=backend)
    try:
        result = runner.run(one_shot=True)
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return

    elapsed = time.time() - t0
    print(f"  Done in {elapsed:.0f}s")

    # RawSimodInput wraps a SimodResult inside result.simod_result
    simod_result = result.simod_result
    bpmn_path    = simod_result.bpmn_path         if simod_result else ""
    json_path    = simod_result.json_params_path  if simod_result else ""
    bpmn_xml     = simod_result.bpmn_content      if simod_result else ""
    json_text    = simod_result.json_params_content if simod_result else ""
    json_content = None
    if json_text:
        try:
            json_content = json.loads(json_text)
        except Exception:
            pass

    print(f"    BPMN:   {bpmn_path}")
    print(f"    JSON:   {json_path}")

    raw = {
        "bpmn_xml":     bpmn_xml,
        "json_text":    json_text,
        "json_content": json_content,
        "bpmn_path":    bpmn_path,
        "json_path":    json_path,
    }
    with open(out_dir / "simod_raw.json", "w") as f:
        json.dump(raw, f, indent=2)
    print(f"    Saved simod_raw.json")


def main() -> None:
    # Prefer Docker; fall back to Python-native if Docker unavailable
    from second_llm.simod_runner import is_docker_available
    backend = SimodBackend.DOCKER if is_docker_available() else SimodBackend.PYTHON

    print(f"Using SIMOD backend: {backend.value}")
    print()

    for log_name in STAGE2_LOGS:
        print(f"[{log_name}]")
        run_one(log_name, backend)
        print()

    print("Done. Check evaluation/simod_outputs/ for results.")
    print("If a log failed, check that the CSV exists and SIMOD is available.")


if __name__ == "__main__":
    main()

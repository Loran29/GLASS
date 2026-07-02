"""
Convert XES event logs to CSV for use in the evaluation pipeline.

Usage:
    python convert_xes_to_csv.py <input.xes> <output.csv>
    python convert_xes_to_csv.py --all   (converts all XES files in logs/raw/)

Output columns: case_id, activity, resource, start_time, end_time
These match the column names expected by simod_runner.py and log_processing.py.
"""

import sys
import os
import argparse
from pathlib import Path

import pm4py
import pandas as pd


RAW_DIR = Path(__file__).parent / "logs" / "raw"
CSV_DIR = Path(__file__).parent / "logs" / "csv"


def _has_lifecycle(df: pd.DataFrame) -> bool:
    """Return True if the log contains lifecycle:transition events (start/complete pairs)."""
    lc_col = None
    for c in ("lifecycle:transition", "lifecycle_transition", "lifecycle"):
        if c in df.columns:
            lc_col = c
            break
    if lc_col is None:
        return False
    vals = set(df[lc_col].dropna().str.lower().unique())
    return bool(vals & {"start", "complete"})


def convert(xes_path: Path, csv_path: Path) -> None:
    print(f"Reading {xes_path.name} ...")
    log = pm4py.read_xes(str(xes_path))
    df = pm4py.convert_to_dataframe(log)

    print(f"  Raw columns: {list(df.columns)}")
    print(f"  Cases: {df['case:concept:name'].nunique()}, Events: {len(df)}")

    # If the log uses lifecycle start/complete pairs, convert to interval format
    # so each row has both a start_timestamp and a completion timestamp.
    if _has_lifecycle(df):
        print("  Lifecycle transitions detected — converting to interval format ...")
        try:
            from pm4py.objects.log.util import interval_lifecycle
            interval_log = interval_lifecycle.to_interval(log)
            df = pm4py.convert_to_dataframe(interval_log)
            print(f"  After interval conversion: {len(df)} activity instances")
        except Exception as exc:
            print(f"  Warning: interval conversion failed ({exc}), falling back to raw events")

    # Rename to the canonical names expected by simod_runner.py
    rename = {
        "case:concept:name": "case_id",
        "concept:name": "activity",
        "org:resource": "resource",
        "time:timestamp": "end_time",
    }
    # pm4py interval_lifecycle places the start timestamp here
    if "start_timestamp" in df.columns:
        rename["start_timestamp"] = "start_time"
    elif "time:start_timestamp" in df.columns:
        rename["time:start_timestamp"] = "start_time"

    df = df.rename(columns=rename)

    if "start_time" not in df.columns:
        df["start_time"] = df["end_time"]
        print("  Warning: no start timestamp found — start_time set equal to end_time")
    else:
        n_zero = (df["start_time"] == df["end_time"]).sum()
        pct = n_zero / len(df) * 100
        if pct < 5:
            print(f"  start_time != end_time for most rows — lifecycle conversion succeeded")
        else:
            print(f"  Warning: {pct:.0f}% of rows still have start_time == end_time")

    # Keep all columns (extra attributes become context factors for Stage 1)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False)
    print(f"  Saved {len(df)} rows to {csv_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?", help="Path to .xes or .xes.gz file")
    parser.add_argument("output", nargs="?", help="Path to output .csv file")
    parser.add_argument("--all", action="store_true",
                        help=f"Convert all XES files in {RAW_DIR}")
    args = parser.parse_args()

    if args.all:
        xes_files = list(RAW_DIR.glob("*.xes")) + list(RAW_DIR.glob("*.xes.gz"))
        if not xes_files:
            print(f"No XES files found in {RAW_DIR}")
            print("Download logs first and place them there.")
            return
        for xes in xes_files:
            csv = CSV_DIR / (xes.stem.replace(".xes", "") + ".csv")
            convert(xes, csv)
    elif args.input and args.output:
        convert(Path(args.input), Path(args.output))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

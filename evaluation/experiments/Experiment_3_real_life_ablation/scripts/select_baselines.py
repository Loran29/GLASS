"""
Select the baseline per real-life dataset from the Zenodo package of Chapela-Campa & Dumas (10.5281/zenodo.10435849):
the timer-enhanced variant whose 10 simulated logs have the lowest mean CTD to the test log.

Copies the test log, the chosen .bpmn/.json and *_timers.txt into data/<dataset>/ and writes
baselines/baseline_selection.csv and .json.

Usage (repo root):
  python evaluation/experiments/Experiment_3_real_life_ablation/scripts/select_baselines.py <extracted package folder>
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
DATASETS = ["AcademicCredentials", "BPIC_2012_W", "BPIC_2017_W"]
ORIENT = ["naive", "complex"]
METHOD = ["direct", "hyperopt_holdout"]
PLACE = ["before", "after"]
BIN = timedelta(hours=1)


def load(p: Path) -> pd.DataFrame:
    df = pd.read_csv(p)
    for c in ("start_time", "end_time"):
        df[c] = pd.to_datetime(df[c], utc=True, format="mixed")
    return df


def timers_file(folder: Path, variant: str) -> Path:
    stem = variant.replace("_enhanced", "")
    for suffix in ("_enhanced_timers.txt", "_enhancer_timers.txt"):     # the package uses both spellings
        if (folder / f"{stem}{suffix}").exists():
            return folder / f"{stem}{suffix}"
    raise FileNotFoundError(f"no timers file for {variant} in {folder}")


def main() -> None:
    from log_distance_measures.config import EventLogIDs
    from log_distance_measures.cycle_time_distribution import cycle_time_distribution_distance
    ids = EventLogIDs(case="case_id", activity="activity", start_time="start_time", end_time="end_time",
                      resource="resource")
    pkg = Path(sys.argv[1]).resolve()
    rows, chosen = [], {}
    for ds in DATASETS:
        test_path = pkg / "real-life" / f"{ds}_test.csv.gz"
        test = load(test_path)
        ev = pkg / "real-life-evaluation" / ds
        for o in ORIENT:
            for m in METHOD:
                for pl in PLACE:
                    variant = f"{o}_{m}_{pl}_enhanced"
                    logs = sorted(ev.glob(f"{ds}_sim_{variant}_*.csv"), key=lambda p: int(p.stem.rsplit("_", 1)[1]))
                    assert len(logs) == 10, (ds, variant, len(logs))
                    ctd = [float(cycle_time_distribution_distance(test, ids, load(p), ids, bin_size=BIN)) for p in logs]
                    rows.append({"dataset": ds, "variant": variant, "n_logs": len(logs),
                                 "ctd_mean": round(float(np.mean(ctd)), 4), "ctd_std": round(float(np.std(ctd, ddof=1)), 4),
                                 "ctd_per_log": json.dumps([round(x, 4) for x in ctd])})
                    print(f"{ds} {variant}: CTD {np.mean(ctd):.2f}", flush=True)
        best = min((r for r in rows if r["dataset"] == ds), key=lambda r: r["ctd_mean"])
        chosen[ds] = {"variant": best["variant"], "ctd_mean": best["ctd_mean"], "ctd_std": best["ctd_std"]}
        out = BASE / "data" / ds
        out.mkdir(parents=True, exist_ok=True)
        for src in (test_path, ev / f"{ds}_{best['variant']}.bpmn", ev / f"{ds}_{best['variant']}.json",
                    timers_file(ev, best["variant"])):
            shutil.copyfile(src, out / src.name)
            chosen[ds].setdefault("files", []).append(src.name)
    (BASE / "baselines").mkdir(exist_ok=True)
    with open(BASE / "baselines" / "baseline_selection.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (BASE / "baselines" / "baseline_selection.json").write_text(json.dumps(chosen, indent=1), encoding="utf-8")
    for ds, c in chosen.items():
        print(f"chosen {ds}: {c['variant']} (CTD {c['ctd_mean']:.2f} ± {c['ctd_std']:.2f}); files {c['files']}")


if __name__ == "__main__":
    main()

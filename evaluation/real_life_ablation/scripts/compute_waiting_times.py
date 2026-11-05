"""
Mean waiting time per activity on each dataset's test log, and X = the activity with the largest mean among the
activities that occur in at least MIN_CASE_SHARE (10 %) of the test log's cases (lead author's decision: an X
resting on a handful of events is not a stable target).

Waiting time of an event = its start minus the end of the preceding event in the same case (events ordered by
start, then end; the first event of a case has no predecessor and is left out). This is the rule used for X
(column mean_waiting_h). Two variants are reported for reference: clipped at 0 (an event that starts before
its predecessor has ended counts as 0), and the latest event that ended before the start as predecessor.
x_activity.json records whether X is the same under the two reference rules.

Writes setup/waiting_time_per_activity.csv and setup/x_activity.json.
Usage (repo root): python evaluation/real_life_ablation/scripts/compute_waiting_times.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
DATASETS = ["AcademicCredentials", "BPIC_2012_W", "BPIC_2017_W"]
MIN_CASE_SHARE = 0.10


def main() -> None:
    rows, x = [], {}
    for ds in DATASETS:
        df = pd.read_csv(BASE / "data" / ds / f"{ds}_test.csv.gz")
        for c in ("start_time", "end_time"):
            df[c] = pd.to_datetime(df[c], utc=True, format="mixed")
        df = df.sort_values(["case_id", "start_time", "end_time"]).reset_index(drop=True)
        df["prev_end"] = df.groupby("case_id")["end_time"].shift(1)
        df["w"] = (df["start_time"] - df["prev_end"]).dt.total_seconds()
        df["w_clip"] = df["w"].clip(lower=0)
        latest = []
        for _, g in df.groupby("case_id", sort=False):
            ends = g["end_time"].values
            for i, s in enumerate(g["start_time"].values):
                others = np.delete(ends, i)                    # the event's own end is not a predecessor
                prior = others[others <= s]
                latest.append((s - prior.max()) / np.timedelta64(1, "s") if len(prior) else np.nan)
        df["w_latest"] = latest
        n_cases = df["case_id"].nunique()
        t = df.groupby("activity").agg(events=("activity", "size"), cases=("case_id", "nunique"), with_predecessor=("w", "count"),
                                       mean_waiting_s=("w", "mean"), clipped_s=("w_clip", "mean"),
                                       latest_ended_s=("w_latest", "mean"), negative=("w", lambda s: int((s < 0).sum())))
        t = t.sort_values("mean_waiting_s", ascending=False)
        for act, r in t.iterrows():
            rows.append({"dataset": ds, "activity": act, "events": int(r.events), "cases_with_activity": int(r.cases),
                         "case_share": round(r.cases / n_cases, 4), "eligible_for_x": bool(r.cases >= MIN_CASE_SHARE * n_cases),
                         "events_with_predecessor": int(r.with_predecessor),
                         "mean_waiting_h": None if pd.isna(r.mean_waiting_s) else round(r.mean_waiting_s / 3600, 2),
                         "mean_waiting_clipped_h": None if pd.isna(r.clipped_s) else round(r.clipped_s / 3600, 2),
                         "mean_waiting_latest_ended_predecessor_h": None if pd.isna(r.latest_ended_s) else round(r.latest_ended_s / 3600, 2),
                         "negative_waits": r.negative})
        el = t[t["cases"] >= MIN_CASE_SHARE * n_cases]
        best = el["mean_waiting_s"].idxmax()
        x[ds] = {"activity": best, "mean_waiting_h": round(float(t.loc[best, "mean_waiting_s"]) / 3600, 2),
                 "events": int(t.loc[best, "events"]), "cases_with_activity": int(t.loc[best, "cases"]),
                 "test_log_cases": int(n_cases), "min_case_share": MIN_CASE_SHARE,
                 "largest_without_minimum": t["mean_waiting_s"].idxmax(),
                 "same_under_clipped_rule": el["clipped_s"].idxmax() == best,
                 "same_under_latest_ended_rule": el["latest_ended_s"].idxmax() == best}
    with open(BASE / "setup" / "waiting_time_per_activity.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (BASE / "setup" / "x_activity.json").write_text(json.dumps(x, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(x, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

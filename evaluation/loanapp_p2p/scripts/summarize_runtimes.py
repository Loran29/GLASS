"""
Run times of every evaluation run, from the run records (read-only), for all reported batches:
Stage 1 (LoanApp/P2P), the refinement pilot, Stage 2 v1, v2, v3, v3.1, the v3.2 RAG comparison (V_full, V_no_kb),
and the real-life ablation (Stage 1, Stage 2). The normalised Stage 1 folder re-processes the Stage 1 records
without new LLM calls and the aborted local batches are not reported, so neither is included.

Per run: start = the record's `timestamp` (taken when the run starts), end = start + `elapsed_s` (wall clock of
the run in its worker). LLM time = the sum of the recorded LLM calls' `elapsed_s` (Stage 2: `llm_calls`; Stage 1
and pilot: `attempts`), each including the batch's retries and back-offs. The rest of the run ("other") is
simulation, KPI and distance computation, log writing and the 1 s pause between LLM calls; the records do not
time the simulation on its own. Stage 2 also records `loop.total_time_seconds`, the refinement loop's share
(LLM calls plus its single-seed simulations), which is listed per run.

Standby: results/standby_windows.csv (sleep and Modern Standby windows from the Windows System log of the
machine that ran the batches: Power-Troubleshooter event 1, Kernel-Power events 506/507). A run whose
[start, end] overlaps a window is flagged and left out of the statistics; it is listed in runtimes.md.
Workers: from the batch READMEs and logs (see WORKERS below); the real-life ablation's second pass ran with
2 workers for its first 76 runs and 4 after the restart.

Writes results/runtimes.csv (per batch and dataset/variant), results/runtimes_runs.csv (per run) and
results/runtimes.md.
"""
from __future__ import annotations

import csv
import json
import re
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
EVAL = BASE.parent
RES = BASE / "results"
RL = EVAL / "real_life_ablation" / "results"
RL_RESTART = datetime(2026, 10, 28, 10, 25, 53, tzinfo=timezone.utc)   # second pass restarted with --workers 4

# (batch, glob of the run records, workers, source of the worker count)
BATCHES = [
    ("Stage 1 (LoanApp/P2P)", RES / "stage1_results" / "raw", "*/rep_*.json", 4, "README (thread pool)"),
    ("Refinement pilot", RES / "refinement_pilot" / "raw", "*.json", 1, "sequential script"),
    ("Stage 2 v1", RES / "stage2_results" / "raw", "*/rep_*.json", 2, "README (processes)"),
    ("Stage 2 v2", RES / "stage2_results_v2" / "raw", "*/rep_*.json", 2, "README (processes)"),
    ("Stage 2 v3", RES / "stage2_results_v3" / "raw", "*/rep_*.json", 2, "README (processes)"),
    ("Stage 2 v3.1", RES / "stage2_results_v3_1" / "raw", "*/rep_*.json", 2, "README (processes)"),
    ("Stage 2 v3.2 RAG comparison", RES / "stage2_results_v32_rag", "*/raw/*/rep_*.json", 4, "README (processes)"),
    ("Real-life ablation Stage 1", RL / "stage1" / "raw", "*/rep_*.json", 2, "records' worker field"),
    ("Real-life ablation Stage 2", RL / "stage2" / "raw", "*/rep_*.json", None, "README and batch log"),
]


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def load_windows() -> list[tuple[datetime, datetime, str]]:
    with open(RES / "standby_windows.csv", encoding="utf-8") as fh:
        return [(parse(r["start_utc"]), parse(r["end_utc"]), r["source"]) for r in csv.DictReader(fh)]


def overlap_s(start: datetime, end: datetime, windows) -> tuple[float, list[str]]:
    """Seconds of [start, end] inside a standby window (windows merged), and the windows touched."""
    spans, hits = [], []
    for a, b, src in windows:
        lo, hi = max(a, start), min(b, end)
        if lo < hi:
            spans.append((lo, hi))
            hits.append(f"{a:%Y-%m-%d %H:%M:%S}–{b:%H:%M:%S} UTC ({src})")
    total, cur = 0.0, None
    for lo, hi in sorted(spans):
        if cur and lo <= cur[1]:
            cur = (cur[0], max(cur[1], hi))
        else:
            if cur:
                total += (cur[1] - cur[0]).total_seconds()
            cur = (lo, hi)
    if cur:
        total += (cur[1] - cur[0]).total_seconds()
    return total, hits


def identify(batch: str, path: Path, rec: dict) -> tuple[str, str, str, str]:
    """dataset, variant, configuration, repetition."""
    if batch == "Refinement pilot":
        m = re.match(r"(.+)_rep(\d+)_round(\d+)$", path.stem)
        return rec["process"], f"round {m.group(3)}", m.group(1), m.group(2)
    if batch.startswith("Real-life"):
        return rec["dataset"], rec["variant"], path.parent.name.split("__")[0], str(rec["rep"])
    variant = rec.get("variant") or ""
    return rec["config_id"].split("_G")[0], variant, rec["config_id"], str(rec["rep"])


def runs() -> list[dict]:
    windows = load_windows()
    out = []
    for batch, root, pattern, workers, workers_src in BATCHES:
        for path in sorted(root.glob(pattern)):
            rec = json.loads(path.read_text(encoding="utf-8"))
            dataset, variant, config, rep = identify(batch, path, rec)
            start = parse(rec["timestamp"])
            elapsed = float(rec["elapsed_s"])
            end = start + timedelta(seconds=elapsed)
            calls = rec.get("llm_calls") if "llm_calls" in rec else rec.get("attempts") or []
            llm = sum(c.get("elapsed_s") or 0 for c in calls)
            w = workers
            if w is None:   # real-life ablation Stage 2
                w = 4 if rec["rep"] in (4, 5) and start >= RL_RESTART else 2
            ov, hits = overlap_s(start, end, windows)
            loop = (rec.get("loop") or {}).get("total_time_seconds")
            out.append({
                "batch": batch, "dataset": dataset, "variant": variant, "config": config, "rep": rep,
                "status": rec.get("status"), "start_utc": f"{start:%Y-%m-%dT%H:%M:%SZ}",
                "end_utc": f"{end:%Y-%m-%dT%H:%M:%SZ}", "elapsed_s": round(elapsed, 1),
                "elapsed_min": round(elapsed / 60, 2), "workers": w, "workers_source": workers_src,
                "llm_calls": len(calls), "llm_s": round(llm, 1), "other_s": round(elapsed - llm, 1),
                "loop_s": loop if loop is not None else "",
                "standby_overlap": ov > 0, "standby_overlap_s": round(ov), "standby_windows": "; ".join(hits),
                "completed_later": bool(rec.get("completed_by")),
                "record": path.relative_to(EVAL).as_posix()})
    return out


def stats(rows: list[dict]) -> dict:
    kept = [r for r in rows if not r["standby_overlap"]]
    m = [r["elapsed_s"] / 60 for r in kept]
    llm = [r["llm_s"] / 60 for r in kept]
    oth = [r["other_s"] / 60 for r in kept]
    f = lambda xs, fn: round(fn(xs), 2) if xs else ""
    return {"runs": len(rows), "runs_standby_excluded": len(rows) - len(kept), "runs_used": len(kept),
            "workers": "/".join(str(w) for w in sorted({r["workers"] for r in rows})),
            "median_min": f(m, statistics.median), "mean_min": f(m, statistics.mean), "min_min": f(m, min),
            "max_min": f(m, max), "median_all_runs_min": f([r["elapsed_s"] / 60 for r in rows], statistics.median),
            "median_llm_min": f(llm, statistics.median),
            "median_other_min": f(oth, statistics.median),
            "llm_share_of_total": round(sum(llm) / sum(m), 3) if m and sum(m) else ""}


def summary(all_runs: list[dict]) -> list[dict]:
    rows = []
    for batch, *_ in BATCHES:
        br = [r for r in all_runs if r["batch"] == batch]
        groups = sorted({(r["dataset"], r["variant"]) for r in br}, key=lambda g: (g[1], g[0]))
        for ds, var in groups:
            rows.append({"batch": batch, "dataset": ds, "variant": var or "-",
                         **stats([r for r in br if (r["dataset"], r["variant"]) == (ds, var)])})
        variants = sorted({r["variant"] for r in br})
        if len(variants) > 1:
            for var in variants:
                rows.append({"batch": batch, "dataset": "all", "variant": var,
                             **stats([r for r in br if r["variant"] == var])})
        rows.append({"batch": batch, "dataset": "all", "variant": "all", **stats(br)})
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def md_table(rows: list[dict], cols: list[tuple[str, str]]) -> list[str]:
    out = ["| " + " | ".join(h for _, h in cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(r[k]) for k, _ in cols) + " |" for r in rows]
    return out


def write_md(all_runs: list[dict], summ: list[dict]) -> None:
    cols = [("batch", "Batch"), ("dataset", "Dataset"), ("variant", "Variant"), ("workers", "Workers"),
            ("runs", "Runs"), ("runs_standby_excluded", "Standby, excluded"), ("median_min", "Median"),
            ("mean_min", "Mean"), ("min_min", "Min"), ("max_min", "Max"),
            ("median_all_runs_min", "Median, all runs"), ("median_llm_min", "Median LLM"),
            ("median_other_min", "Median other"), ("llm_share_of_total", "LLM share")]
    totals = [r for r in summ if r["dataset"] == "all"]
    excluded = [r for r in all_runs if r["standby_overlap"]]
    L = ["# Run times of the evaluation batches", "",
         "Run time of every run from its record (`timestamp` = start, `elapsed_s` = wall clock in its worker), in "
         "minutes. Per-run values: `runtimes_runs.csv`; per batch and dataset/variant: `runtimes.csv` "
         "(`scripts/summarize_runtimes.py`).", "",
         "- **Statistics** (median, mean, min, max) leave out the runs that overlapped a standby window of the "
         "machine; they are listed below. `Runs` counts all runs. Any overlap counts, also with the short Modern "
         "Standby windows. Not every window paused the runs (Stage 2 v2's Procure2Pay runs inside the 93-minute "
         "window of 2026-10-15 took their usual 7–16 min); those that did show run times of hours "
         "(up to 1,222 min). `Median, all runs` keeps every run: per batch the exclusion moves the median by at "
         "most 0.66 min (Stage 2 v3.1); per dataset by up to 3.07 min (Stage 2 v2 Procure2Pay, 15 of 35 runs "
         "left out).",
         "- **LLM vs. other time.** LLM time is the sum of the recorded LLM calls (`elapsed_s` per call, with "
         "retries and back-offs). The records do not time the simulation separately: \"other\" is everything else "
         "in the run, i.e. Prosimos simulation (Stage 2: the first proposal and the final on 10 seeds each, plus "
         "one seed per loop iteration), KPI and distance computation, log writing and the 1 s pause between LLM "
         "calls. Stage 1 and the pilot make LLM calls only, so their \"other\" is parsing and validation. LLM share "
         "= LLM time / run time over the runs used.",
         "- **Workers** run in parallel, so run times include contention for the CPU (simulation) and the API; "
         "a batch's wall clock is shorter than the sum of its runs.",
         "- **Stage 2 completion step.** Runs whose iteration 1 had no scenario were completed later with their "
         "final evaluation (`completed_by` in the record); that evaluation is not part of `elapsed_s` and was not "
         f"timed ({sum(r['completed_later'] for r in all_runs)} runs, column `completed_later`).",
         "- **Not included:** `stage1_results_normalised` (Stage 1 records re-processed without LLM calls) and the "
         "aborted local batches.", "",
         "## Per batch", ""]
    L += md_table(totals, cols)
    L += ["", "## Per batch and dataset/variant", ""]
    L += md_table(summ, cols)
    L += ["", f"## Runs left out: overlap with a standby window ({len(excluded)})", "",
          "Windows from `standby_windows.csv` (Windows System log: sleep, Power-Troubleshooter event 1; Modern "
          "Standby, Kernel-Power 506/507). Overlap = seconds of the run inside a window.", ""]
    L += md_table(excluded, [("batch", "Batch"), ("config", "Configuration"), ("variant", "Variant"), ("rep", "Rep"),
                             ("start_utc", "Start (UTC)"), ("elapsed_min", "Run time (min)"),
                             ("standby_overlap_s", "Overlap (s)"), ("standby_windows", "Windows")])
    L += ["", "## Workers", "",
          "- Stage 1 (LoanApp/P2P): 4 (thread pool; `stage1_results/README.md`).",
          "- Refinement pilot: 1 (the script makes its calls one after another).",
          "- Stage 2 v1, v2, v3, v3.1: 2 processes; v3.2 RAG comparison: 4 processes (the batch READMEs).",
          "- Real-life ablation Stage 1: 2 (the records' `worker` field: worker_0, worker_1).",
          "- Real-life ablation Stage 2: 2 for repetitions 1–3 and for the first 76 runs of repetitions 4–5; 4 for "
          f"the last 32 runs, which started after the restart at {RL_RESTART:%Y-%m-%d %H:%M:%S} UTC "
          "(`real_life_ablation/README.md`)."]
    (RES / "runtimes.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main() -> None:
    all_runs = runs()
    summ = summary(all_runs)
    write_csv(RES / "runtimes_runs.csv", all_runs)
    write_csv(RES / "runtimes.csv", summ)
    write_md(all_runs, summ)
    print(f"{len(all_runs)} runs; {sum(r['standby_overlap'] for r in all_runs)} overlap a standby window")


if __name__ == "__main__":
    main()

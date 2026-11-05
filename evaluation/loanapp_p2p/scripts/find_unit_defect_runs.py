"""
Find Stage-2 runs in which a duration was applied in the wrong unit (report only; records are not changed).

Before the baseline-mismatch rejection (GLASS 33cf3cb), the merger applied a proposed duration as a number in
the element's own time unit (seconds in these baselines), whatever unit the LLM used. A proposal quoted in
hours against a baseline quoted in hours ("1.1667 hours" -> "0.5 hours") therefore became 0.5 seconds.

A modification (activity_duration, event_duration, inter_arrival_time) is listed when the merged scenario holds
the proposed number itself (within 1 %), i.e. unconverted, and either
  class "unit_factor": its quoted baseline_value differs from the model's actual value by a unit factor (60,
    3600 or 86400, within 5 %), or its proposed_value names a unit (min / hour / day) other than the element's;
  class "scale_only": the actual value is at least 50 times the quoted one without matching a unit factor (an
    hour-scale quote with a wrong number, e.g. "2.5833" for 2100 s), and the applied value is below 1/50 of
    the actual value.
For each run the script reports every iteration with such a modification and whether the run's initial
(iteration 1) or final (best iteration) scenario contains it.

Writes results/unit_defect_runs_v1_v2.csv (one row per modification) and prints a per-run summary. When
results/stage2_results_v3/ (or _v3_1/) exists, the same search runs over it and writes
results/unit_defect_runs_v3.csv (or _v3_1.csv); v3 and later reject mismatched modifications, so these are
expected to be empty.
Usage (repo root): python evaluation/loanapp_p2p/scripts/find_unit_defect_runs.py
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
RES = BASE / "results"
FOLDERS = {"v1": RES / "stage2_results", "v2": RES / "stage2_results_v2"}
LATER = {"v3": (RES / "stage2_results_v3", "unit_defect_runs_v3.csv"),
         "v3.1": (RES / "stage2_results_v3_1", "unit_defect_runs_v3_1.csv")}
FACTORS = {60: "minutes", 3600: "hours", 86400: "days"}
UNIT_WORDS = {"minutes": r"\bmin(ute)?s?\b", "hours": r"\b(h|hrs?|hours?)\b", "days": r"\bdays?\b"}
NUM = re.compile(r"[-+]?\d*\.?\d+")


def number(text: str) -> float | None:
    m = NUM.search(str(text))
    return float(m.group()) if m else None


def dist_value(dist: dict) -> float | None:
    p = {v["id"]: v["value"] for v in dist.get("values", [])}
    for key in ("mean", "constantValue"):
        if key in p:
            return p[key]
    if "lower" in p and "upper" in p:
        return (p["lower"] + p["upper"]) / 2
    return p.get("peak")


def element(scn: dict, mod: dict) -> dict | None:
    t = mod["target_element"].lower()
    mp = [m["modelParameter"] for m in scn["models"]]
    if mod["parameter_type"] == "activity_duration":
        pool, key = [a for x in mp for a in x["activities"]], "duration"
    elif mod["parameter_type"] == "event_duration":
        pool, key = [e for x in mp for e in x.get("intermediateEvents", [])], "duration"
    elif mod["parameter_type"] == "inter_arrival_time":
        pool, key = [e for x in mp for e in x["events"]], "interArrivalTime"
        hit = next((e for e in pool if t in (e["id"].lower(), str(e.get("name", "")).lower())), pool[0] if pool else None)
        return hit[key] if hit else None
    else:
        return None
    hit = next((e for e in pool if t in (e["id"].lower(), str(e.get("name", "")).lower())), None)
    return hit[key] if hit else None


def close(a: float, b: float, rel: float) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-9)


def scan(folders: dict[str, Path]) -> list[dict]:
    rows = []
    for version, folder in folders.items():
        for path in sorted((folder / "raw").glob("*/rep_*.json")):
            rec = json.loads(path.read_text(encoding="utf-8"))
            loop = rec.get("loop") or {}
            iterations = loop.get("iterations") or []
            base = iterations and json.loads(json.dumps(rec.get("sim_params_with_costs")))
            for it in iterations:
                merged, patch = it.get("merged_scenario"), it.get("patch") or {}
                if not merged:
                    continue
                for mod in patch.get("modifications", []):
                    if mod["parameter_type"] not in ("activity_duration", "event_duration", "inter_arrival_time"):
                        continue
                    applied_dist = element(merged, mod)
                    prop = number(mod["proposed_value"])
                    if applied_dist is None or prop is None:
                        continue
                    applied = dist_value(applied_dist)
                    unit = applied_dist.get("timeUnit", "")
                    if applied is None or not close(applied, prop, 0.01):
                        continue                    # converted, or not applied
                    quoted = number(mod["baseline_value"])
                    actual = None
                    # the model's actual value before the patch: the same element in the first iteration's
                    # baseline is not stored separately; the merger's diagnostic quotes it
                    for d in it.get("merge_diagnostics") or []:
                        if d.get("category") in ("baseline_value_mismatch", "baseline_mismatch")                                 and d.get("element") == mod["target_element"]:
                            m = re.search(r"actual (?:baseline|value) ([-\d.e+]+)", d["message"])
                            actual = float(m.group(1)) if m else None
                    factor = None
                    if quoted and actual:
                        factor = next((f for f in FACTORS if close(actual / quoted, f, 0.05)), None)
                    named = next((u for u, rx in UNIT_WORDS.items()
                                  if re.search(rx, mod["proposed_value"], re.I)), None)
                    if factor is not None or (named is not None and named != unit):
                        kind = "unit_factor"
                    elif quoted and actual and actual / quoted >= 50 and applied < actual / 50:
                        kind = "scale_only"
                    else:
                        continue
                    best = loop.get("best_iteration")
                    rows.append({
                        "version": version, "class": kind, "config_id": rec["config_id"], "rep": rec["rep"],
                        "iteration": it["iteration"],
                        "in_initial": it["iteration"] == 1, "in_final": it["iteration"] == best,
                        "parameter_type": mod["parameter_type"], "target_element": mod["target_element"],
                        "baseline_value_quoted": mod["baseline_value"], "baseline_actual": actual,
                        "proposed_value_quoted": mod["proposed_value"], "applied_value": applied,
                        "applied_unit": unit, "unit_factor": factor or "",
                        "intended_unit": FACTORS.get(factor) or named,
                    })
    return rows


def write(rows: list[dict], out: Path) -> None:
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["version"])
        w.writeheader()
        w.writerows(rows)
    runs = {}
    for r in rows:
        k = (r["version"], r["config_id"], r["rep"])
        s = runs.setdefault(k, {"iterations": set(), "initial": False, "final": False, "classes": set()})
        s["classes"].add(r["class"])
        s["iterations"].add(r["iteration"])
        s["initial"] |= r["in_initial"]
        s["final"] |= r["in_final"]
    for (v, cid, rep), s in sorted(runs.items()):
        print(f"{v} {cid} rep {rep}: {'+'.join(sorted(s['classes']))}; iterations {sorted(s['iterations'])}; "
              f"initial {s['initial']}; final {s['final']}")
    print(f"{len(rows)} modification(s) in {len(runs)} run(s); written {out.relative_to(BASE.parent.parent).as_posix()}")


def main() -> None:
    write(scan(FOLDERS), RES / "unit_defect_runs_v1_v2.csv")
    for version, (folder, name) in LATER.items():
        if (folder / "raw").exists():
            write(scan({version: folder}), RES / name)


if __name__ == "__main__":
    main()

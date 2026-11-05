"""
Reported column only (no re-scoring): did any Stage-2 run of the LoanApp/P2P batches change customer demand?

Rule (decided for the real-life ablation study before its runs, applied here retroactively): a scenario whose
arrival differs from the baseline's is invalid (demand_changed), because the arrival rate is customer demand, not
something the company can change. Only applied changes count: an inter_arrival_time modification the merger
skipped leaves the demand unchanged (reported as demand_proposed). The batches' own validity scores are not
changed; this script only reports whether the rule would ever have fired.

Per run and phase (initial = iteration 1, final = the loop's best iteration):
  demand_changed            the simulated scenario's start-event inter-arrival time or arrival calendar differs
                            from the baseline's (the rule)
  demand_proposed           the accepted patch has an inter_arrival_time modification (applied or skipped)
  any_attempt_inter_arrival some patch attempt of any iteration (accepted or not) proposed inter_arrival_time

Writes results/demand_changed_runs.csv and prints the counts per batch.
Usage (repo root): python evaluation/loanapp_p2p/scripts/report_demand_changed.py
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_stage2_batch as s2  # noqa: E402

BATCHES = {"v1": "stage2_results", "v2": "stage2_results_v2", "v3": "stage2_results_v3", "v3.1": "stage2_results_v3_1"}


def arrivals(scn: dict) -> list:
    return [(e.get("interArrivalTime"), e.get("arrivalCalendar")) for m in scn["models"]
            for e in m["modelParameter"].get("events", [])]


def main() -> None:
    config = json.loads(s2.CONFIG_PATH.read_text(encoding="utf-8"))
    baseline_dir = {c["config_id"]: c["baseline_dir"] for c in config["configurations"]}
    base_arrivals: dict[tuple, list] = {}
    rows, counts = [], defaultdict(lambda: defaultdict(int))
    for version, folder in BATCHES.items():
        for path in sorted((BASE / "results" / folder / "raw").glob("*/rep_*.json")):
            rec = json.loads(path.read_text(encoding="utf-8"))
            cid = rec["config_id"]
            key = (cid, json.dumps(rec.get("sim_params_with_costs"), sort_keys=True))
            if key not in base_arrivals:
                bdir = BASE / baseline_dir[cid]
                scn = s2.load_scenario(json.dumps(rec["sim_params_with_costs"]),
                                       (bdir / "model.bpmn").read_text(encoding="utf-8"))
                base_arrivals[key] = arrivals(scn.model_dump(mode="json"))
            its = (rec.get("loop") or {}).get("iterations") or []
            any_attempt = any(m.get("parameter_type") == "inter_arrival_time"
                              for it in its for a in it.get("all_patch_attempts") or []
                              for m in (a.get("patch") or {}).get("modifications", []))
            row = {"batch": version, "config_id": cid, "rep": rec["rep"], "status": rec["status"],
                   "any_attempt_inter_arrival": any_attempt}
            for phase, it_no in (("initial", 1), ("final", (rec.get("loop") or {}).get("best_iteration"))):
                it = its[it_no - 1] if it_no and it_no <= len(its) else None
                mods = ((it or {}).get("patch") or {}).get("modifications", [])
                scn = (it or {}).get("simulated_scenario")
                row[f"{phase}_demand_changed"] = (arrivals(scn) != base_arrivals[key]) if scn else ""
                row[f"{phase}_demand_proposed"] = any(m.get("parameter_type") == "inter_arrival_time" for m in mods) \
                    if it else ""
            rows.append(row)
            c = counts[version]
            c["runs"] += 1
            for k in ("initial_demand_changed", "final_demand_changed", "initial_demand_proposed",
                      "final_demand_proposed", "any_attempt_inter_arrival"):
                c[k] += row[k] is True
    out = BASE / "results" / "demand_changed_runs.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for version, c in counts.items():
        print(version, dict(c))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

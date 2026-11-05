"""
Reported only (no re-scoring): which KPI did the refinement loop measure in the LoanApp/P2P Stage-2 batches?

Until GLASS commit "Refinement loop: pass measurable_as to the loop's KPI targets", the loop's targets
(ui/second_llm_panel._get_evaluation_targets) carried no measurable_as, so the loop resolved each KPI by its name,
while the multi-seed evaluation resolved it through measurable_as. Per run and KPI target this script resolves
both ways (scenario_evaluation._find_kpi on the run's single-run baseline KPIs, as GLASS did) and reports whether
they reach the same computed KPI. Utilization KPIs resolve to "Resource Utilization" either way (the role is
picked afterwards from the same text), so they count as the same.

Writes results/loop_target_gap.csv (one row per batch x run x KPI) and prints the counts per batch.
Usage (repo root): python evaluation/loanapp_p2p/scripts/report_loop_target_gap.py
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE.parent.parent / "goal_to_parameters"))
from second_llm.kpi_computation import ComputedKPI, KPIComputationResult  # noqa: E402
from second_llm.scenario_evaluation import _find_kpi  # noqa: E402

BATCHES = {"v1": "stage2_results", "v2": "stage2_results_v2", "v3": "stage2_results_v3", "v3.1": "stage2_results_v3_1"}


def main() -> None:
    rows = []
    for version, folder in BATCHES.items():
        for path in sorted((BASE / "results" / folder / "raw").glob("*/rep_*.json")):
            rec = json.loads(path.read_text(encoding="utf-8"))
            base = (rec.get("loop") or {}).get("baseline_kpis_single_run")
            if not base:
                continue
            computed = KPIComputationResult(kpis=[ComputedKPI(k["name"], k["value"], k.get("unit", ""),
                                                              k.get("category", "")) for k in base["kpis"]])
            for t in rec.get("eval_targets") or []:
                by_name = _find_kpi(t["name"], computed, t.get("category", ""), None)
                by_metric = _find_kpi(t["name"], computed, t.get("category", ""), t.get("measurable_as"))
                rows.append({"batch": version, "config_id": rec["config_id"], "rep": rec["rep"], "kpi": t["name"],
                             "measurable_as": t.get("measurable_as"),
                             "loop_resolved": by_name.name if by_name else "",
                             "evaluation_resolved": by_metric.name if by_metric else "",
                             "same": (by_name.name if by_name else None) == (by_metric.name if by_metric else None)})
    out = BASE / "results" / "loop_target_gap.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for version in BATCHES:
        rr = [r for r in rows if r["batch"] == version]
        diff = [r for r in rr if not r["same"]]
        runs = {(r["config_id"], r["rep"]) for r in diff}
        print(f"{version}: {len(diff)} of {len(rr)} KPI targets resolved differently, in {len(runs)} runs; "
              + "; ".join(f"{k}" for k in Counter(f"{r['measurable_as']} -> loop {r['loop_resolved'] or 'none'}"
                                                  for r in diff).most_common(6)))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

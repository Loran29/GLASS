"""
Build setup/ablation_config.json from the lead author's decisions, setup/x_activity.json, the test logs and
setup/process_descriptions.md (description texts copied verbatim from the markdown sections).

Usage (repo root): python evaluation/experiments/experiment_4_real_life_ablation/scripts/build_config.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
VARIANT = "complex_hyperopt_holdout_after_enhanced"
DATASETS = {
    # dataset: (domain noun, plural for sentence start, X as a manager would say it)
    "AcademicCredentials": ("credential requests", "Credential requests", "the student is notified of the cancellation"),
    "BPIC_2012_W": ("loan applications", "Loan applications", "the follow-up calls on offers"),
    "BPIC_2017_W": ("loan applications", "Loan applications", "the application validation"),
}
CONTEXT = {
    # Both answers were reworded after the smoke runs, before the batch (history in the README):
    # staffing  1. "Each resource group may be increased by up to 20 % of its current size, at least one person; ..."
    #              -> fractional headcounts (1 -> 1.2) on the one-person profiles, skipped by the merger;
    #           2. "Any person's role may be reinforced by one additional person ...; overall, staff may grow by at
    #              most 20 %." -> GLASS's compliance check read the 20 % as a per-role cap (1 -> 2 = +100 %); GLASS's
    #              constraint model has per-role maxima and a budget only, so the overall cap was dropped;
    #           3. "Each role may be reinforced by at most one additional person ..." -> the compliance check still
    #              judged 1 -> 2 a violation.
    # immutable "The order of the steps and the activities themselves must stay; who does what, how long steps take
    #           and how long cases wait between steps can be improved." -> GLASS's feasibility check treated the
    #           activities' durations as immutable.
    "staffing": "Each role may grow by one additional person with the same skills (for example, a role with one "
                "person may have two). Working hours may be extended by up to two hours per day.",
    "budget": "Staff cost may increase by at most 35 %; hourly cost 20 EUR for everyone.",
    "immutable": "The order of the steps must stay, and no activity may be removed or added. Activity durations, "
                 "staffing, working hours and waiting times between steps may be changed.",
}


def descriptions() -> dict[str, str]:
    text = (BASE / "setup" / "process_descriptions.md").read_text(encoding="utf-8")
    out = {}
    for ds in DATASETS:
        m = re.search(rf"^## {ds}\n\n(.+?)(?:\n\n## |\Z)", text, re.S | re.M)
        out[ds] = m.group(1).strip()
    return out


def main() -> None:
    x = json.loads((BASE / "setup" / "x_activity.json").read_text(encoding="utf-8"))
    desc = descriptions()
    datasets, configurations = {}, []
    for ds, (noun, Noun, x_spoken) in DATASETS.items():
        test = pd.read_csv(BASE / "data" / ds / f"{ds}_test.csv.gz")
        start = pd.to_datetime(test["start_time"], utc=True, format="mixed").min()
        datasets[ds] = {
            "test_log": f"data/{ds}/{ds}_test.csv.gz",
            "baseline_bpmn": f"data/{ds}/{ds}_{VARIANT}.bpmn",
            "baseline_json": f"data/{ds}/{ds}_{VARIANT}.json",
            "baseline_variant": VARIANT,
            "timers": f"data/{ds}/complex_hyperopt_holdout_after_enhancer_timers.txt",
            "cases": int(test["case_id"].nunique()),
            "start": start.isoformat(),
            "domain_noun": noun,
            "x_activity": x[ds]["activity"],
            "x_spoken": x_spoken,
            "x_mean_waiting_h": x[ds]["mean_waiting_h"],
            "process_description": desc[ds],
        }
        g1 = f"We want {noun} to be completed sooner."
        g2 = f"{Noun} should not sit waiting before {x_spoken}."
        x_kpi = f"{x[ds]['activity']} Waiting Time"
        for goal, text, kpis in (("G1", g1, ["Average Cycle Time"]), ("G2", g2, [x_kpi]),
                                 ("G1G2", f"{g1} {g2}", ["Average Cycle Time", x_kpi])):
            configurations.append({"config_id": f"{ds}_{goal}", "dataset": ds, "goal": goal, "simulation_goal": text,
                                   "expected_kpis": kpis, "expected_measurable_as": kpis})
    config = {
        "experiment": "GLASS real-life ablation study (Zenodo 10.5281/zenodo.10435849)",
        "settings": {
            "model": "gpt-4o-mini",
            "stage1_temperature": "GLASS app (0.2, read from app.py as in the Step 1 batch)",
            "stage2_temperature": "GLASS loop schedule min(0.3 + 0.1 x (iteration - 1), 0.7); context summary and "
                                  "compliance calls 0.1",
            "max_iterations": 4,
            "seeds": 10,
            "seed_values": list(range(42, 52)),
            "repetitions": 5,
            "repetitions_first_pass": [1, 2, 3],
            "repetitions_second_pass": [4, 5],
            "cases": "per dataset: the test log's case count",
            "start": "per dataset: the test log's first start",
        },
        "business_context": {**CONTEXT, "cost_per_hour_all_resources": 20},
        "datasets": datasets,
        "configurations": configurations,
    }
    (BASE / "setup" / "ablation_config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                                                         encoding="utf-8", newline="\n")
    print(json.dumps({c["config_id"]: (c["simulation_goal"], c["expected_kpis"]) for c in configurations}, indent=1,
                     ensure_ascii=False))
    print({ds: (d["cases"], d["start"]) for ds, d in datasets.items()})


if __name__ == "__main__":
    main()

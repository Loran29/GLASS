"""Resource Utilization: calendar-based availability and role-aware evaluation.

Per resource, utilization = busy time clipped to the resource's calendar / its available working time
within the evaluated period (simulation start to last end). A Resource Utilization KPI is evaluated for
the role(s) it names; without a named role it stays the all-resource mean.

The unit tests use synthetic logs and generic role names. The last test uses the stored LoanApp_G1G3
rep 1 logs of the Stage 2 evaluation; the simulated logs are not in git, so it is skipped without them.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "goal_to_parameters"))

from second_llm.kpi_computation import compute_kpis  # noqa: E402
from second_llm.scenario_evaluation import KPITarget, TargetDirection, _match_kpi_value  # noqa: E402

WEEKDAYS_9_17 = [{"from": "MONDAY", "to": "FRIDAY", "beginTime": "09:00:00", "endTime": "17:00:00"}]
ALWAYS = [{"from": "MONDAY", "to": "SUNDAY", "beginTime": "00:00:00", "endTime": "23:59:59"}]
MONDAY = pd.Timestamp("2023-02-06")          # a Monday


def log(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    """rows: (resource, start, end) with times relative to MONDAY ("0 09:00" = Monday 09:00)."""
    def at(text):
        day, hhmm = text.split()
        return MONDAY + pd.Timedelta(days=int(day)) + pd.Timedelta(hours=int(hhmm[:2]), minutes=int(hhmm[3:]))
    return pd.DataFrame([{"case_id": i, "activity": "Task", "resource": r, "start_time": at(s), "end_time": at(e)}
                         for i, (r, s, e) in enumerate(rows)])


def params(roles: dict[str, list[str]], periods=WEEKDAYS_9_17) -> dict:
    """Prosimos parameters: one calendar per resource; profile name = role."""
    return {"resource_profiles": [{"id": f"p{k}", "name": role, "resource_list": [
                {"id": rid, "name": rid, "calendar": f"{rid}_cal"} for rid in rids]}
                for k, (role, rids) in enumerate(roles.items())],
            "resource_calendars": [{"id": f"{rid}_cal", "time_periods": periods}
                                   for rids in roles.values() for rid in rids]}


def utilization(df, prosimos_params=None, sim_start=None):
    return compute_kpis(df, prosimos_params=prosimos_params, sim_start=sim_start,
                        include_activity_kpis=False).get_kpi("Resource Utilization")


def target(name: str, description: str = "") -> KPITarget:
    return KPITarget(name=name, direction=TargetDirection.MINIMIZE, category="utilization",
                     measurable_as="Resource Utilization", description=description)


class CalendarAvailabilityTests(unittest.TestCase):
    def test_six_busy_hours_on_five_working_days(self):
        rows = [(f"R1", f"{d} 09:00", f"{d} 12:00") for d in range(5)] + \
               [(f"R1", f"{d} 14:00", f"{d} 17:00") for d in range(5)]
        kpi = utilization(log(rows), params({"Role A": ["R1"]}), sim_start=MONDAY + pd.Timedelta(hours=9))
        self.assertAlmostEqual(kpi.value, 0.75)
        self.assertEqual(kpi.details["fallback_span_based"], [])

    def test_always_open_calendar_equals_span_based_value(self):
        rows = [("R1", "0 08:00", "0 20:00"), ("R1", "1 22:00", "2 03:00"), ("R1", "3 10:00", "3 11:30")]
        df = log(rows)
        old = ((df["end_time"] - df["start_time"]).dt.total_seconds().sum()
               / (df["end_time"].max() - df["start_time"].min()).total_seconds())
        kpi = utilization(df, params({"Role A": ["R1"]}, ALWAYS))
        self.assertAlmostEqual(kpi.value, round(old, 3))

    def test_activity_over_night_counts_only_working_time(self):
        rows = [("R1", "0 16:00", "1 11:00"),      # 1 h on Monday + 2 h on Tuesday inside 09-17
                ("R1", "1 15:00", "1 17:00")]      # 2 h
        kpi = utilization(log(rows), params({"Role A": ["R1"]}), sim_start=MONDAY + pd.Timedelta(hours=9))
        self.assertAlmostEqual(kpi.value, round(5 / 16, 3))   # 5 busy hours of 16 available

    def test_resources_without_calendar_fall_back_and_are_flagged(self):
        rows = [("R1", "0 09:00", "0 13:00"), ("R2", "0 09:00", "0 11:00")]
        df = log(rows)
        no_params = utilization(df)
        self.assertEqual(sorted(no_params.details["fallback_span_based"]), ["R1", "R2"])
        self.assertAlmostEqual(no_params.details["per_resource"]["R1"], 1.0)      # 4 h of a 4 h span
        partial = utilization(df, params({"Role A": ["R1"]}), sim_start=MONDAY + pd.Timedelta(hours=9))
        self.assertEqual(partial.details["fallback_span_based"], ["R2"])


class RoleResolutionTests(unittest.TestCase):
    def setUp(self):
        rows = [("LO_1", "0 09:00", "0 17:00"), ("LO_2", "0 09:00", "0 13:00"),
                ("CL_1", "0 09:00", "0 11:00"), ("PA_1", "0 09:00", "0 10:00")]
        p = params({"Loan Officer": ["LO_1", "LO_2"], "Clerk": ["CL_1"], "Purchasing Agent": ["PA_1"]})
        self.kpis = compute_kpis(log(rows), prosimos_params=p, sim_start=MONDAY + pd.Timedelta(hours=9),
                                 include_activity_kpis=False)

    def match(self, t: KPITarget):
        return _match_kpi_value(t.name, self.kpis, t.category, t.measurable_as, t.description)

    def test_role_named_in_kpi_name(self):
        kpi = self.match(target("Loan Officer Workload Distribution"))
        self.assertEqual(kpi.details["roles"], ["Loan Officer"])
        self.assertTrue(kpi.details["role_resolved"])
        self.assertAlmostEqual(kpi.value, round((8 / 8 + 4 / 8) / 2, 3))

    def test_plural_role_name(self):
        kpi = self.match(target("Purchasing agents utilization"))
        self.assertEqual(kpi.details["roles"], ["Purchasing Agent"])
        self.assertAlmostEqual(kpi.value, round(1 / 8, 3))

    def test_role_named_only_in_description(self):
        kpi = self.match(target("Workload Utilization", "Busy time of the clerks over their working hours."))
        self.assertEqual(kpi.details["roles"], ["Clerk"])

    def test_no_role_named_keeps_all_resource_mean(self):
        kpi = self.match(target("Average Resource Utilization", "Mean busy share of all staff."))
        self.assertFalse(kpi.details["role_resolved"])
        self.assertAlmostEqual(kpi.value, round((1 + 0.5 + 0.25 + 0.125) / 4, 3))


R = ROOT / "evaluation" / "loanapp_p2p" / "results" / "stage2_results"
LOGS = [R / "baseline_logs" / "LoanApp_G1G3" / "seed_42.csv.gz",
        R / "logs" / "LoanApp_G1G3" / "rep_1" / "initial_seed_42.csv.gz"]


@unittest.skipUnless(all(p.exists() for p in LOGS), "stored Stage 2 logs not present (not in git)")
class StoredLogsTest(unittest.TestCase):
    def test_role_resolved_utilization_falls_while_all_resource_mean_rises(self):
        rec = json.loads((R / "raw" / "LoanApp_G1G3" / "rep_1.json").read_text(encoding="utf-8"))
        start = rec["settings"]["start"]
        kpi_obj = next(k for k in rec["stage1_result_passed"]["kpis"] if k["category"] == "utilization")
        t = target(kpi_obj["name"], f"{kpi_obj['description']} {kpi_obj['suggested_formula']}")
        values = []
        for path, prosimos in zip(LOGS, (rec["sim_params_with_costs"],
                                         rec["loop"]["iterations"][0]["simulated_scenario_prosimos"])):
            kpis = compute_kpis(pd.read_csv(path), prosimos_params=prosimos, sim_start=start,
                                include_activity_kpis=False)
            values.append(_match_kpi_value(t.name, kpis, t.category, t.measurable_as, t.description))
        base, init = values
        self.assertEqual(base.details["roles"], ["Loan Officer"])
        self.assertAlmostEqual(base.value, 0.997, delta=0.01)
        self.assertAlmostEqual(init.value, 0.850, delta=0.01)
        self.assertLess(init.value, base.value)                                                   # role: falls
        self.assertGreater(init.details["all_resource_mean"], base.details["all_resource_mean"])  # all: rises


if __name__ == "__main__":
    unittest.main()

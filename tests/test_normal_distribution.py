"""Normal-distribution conversion between Prosimos and the SimuBridge scenario.

Prosimos ``norm`` parameters are [mean, std, min, max]; SimuBridge's normal is
(mean, variance). Import squares the std and export takes the square root, so the
std round-trips unchanged for activities and timers alike. ``fix`` and ``expon``
must be unaffected (fingerprints from the code before this fix, commit 7c93a1c).

Reuses the synthetic process from ``test_event_duration``.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from test_event_duration import TIMER_ID, bpmn_with_timer, simod_json  # noqa: E402

from second_llm.output_schema import DistributionType  # noqa: E402
from second_llm.prosimos_runner import scenario_to_prosimos_json  # noqa: E402
from second_llm.simod_to_simubridge import build_baseline_scenario  # noqa: E402

MEAN_S = 120 * 60.0  # 120 min
STD_S = 10 * 60.0    # 10 min
NORM = {"distribution_name": "norm",
        "distribution_params": [{"value": MEAN_S}, {"value": STD_S}, {"value": 0}, {"value": 3 * MEAN_S}]}

# sha256 of json.dumps(export, sort_keys=True), computed with the code before this fix.
PRE_FIX_EXPORT = {
    "fix": "9d5753838d6beb86371239662b0ac158edb7a54dc9169edcca48ac0f997b0102",
    "expon": "e3cedf0b16fcc569396978395f6ba29fad16c2fa9b572449517592f615203bcc",
}


def _round_trip(data: dict):
    scenario = build_baseline_scenario(data, bpmn_xml=bpmn_with_timer()).scenario
    return scenario, scenario_to_prosimos_json(scenario, bpmn_with_timer())


def _params(entry: dict) -> list[float]:
    return [p["value"] for p in entry["distribution_params"]]


class NormalDistributionRoundTripTests(unittest.TestCase):
    def test_activity_norm_std_round_trips(self):
        data = simod_json(with_timer=True)
        data["task_resource_distribution"][0]["resources"][0].update(NORM)
        scenario, out = _round_trip(data)

        act = next(a for a in scenario.models[0].modelParameter.activities if a.id == "Task_A")
        self.assertEqual(act.duration.distributionType, DistributionType.NORMAL)
        stored = {v.id: v.value for v in act.duration.values}
        self.assertEqual(stored, {"mean": MEAN_S, "variance": STD_S ** 2})

        task = next(t for t in out["task_resource_distribution"] if t["task_id"] == "Task_A")
        for res in task["resources"]:
            self.assertEqual(res["distribution_name"], "norm")
            self.assertEqual(_params(res)[:2], [MEAN_S, STD_S])

    def test_timer_norm_std_round_trips(self):
        data = simod_json(with_timer=True)
        data["event_distribution"][0].update(NORM)
        scenario, out = _round_trip(data)

        evt = scenario.models[0].modelParameter.intermediateEvents[0]
        stored = {v.id: v.value for v in evt.duration.values}
        self.assertEqual(stored, {"mean": MEAN_S, "variance": STD_S ** 2})

        entry = next(e for e in out["event_distribution"] if e["event_id"] == TIMER_ID)
        self.assertEqual(entry["distribution_name"], "norm")
        self.assertEqual(_params(entry)[:2], [MEAN_S, STD_S])

    def test_named_std_field_is_squared_and_variance_field_kept(self):
        from second_llm.simod_to_simubridge import _parse_distribution

        by_std = _parse_distribution({"dname": "norm", "mean": MEAN_S, "std": STD_S})
        by_var = _parse_distribution({"dname": "norm", "mean": MEAN_S, "variance": STD_S ** 2})
        for td in (by_std, by_var):
            self.assertEqual({v.id: v.value for v in td.values}, {"mean": MEAN_S, "variance": STD_S ** 2})


class ProposedNormalDistributionTests(unittest.TestCase):
    """LLM side: a proposed std alias is squared into the stored variance."""

    def _export_patched_task_a(self, values: list[dict]) -> list[float]:
        from test_event_duration import patch_of

        from second_llm.output_schema import ModificationDirection
        from second_llm.output_schema_patch import PatchModification, PatchParameterType
        from second_llm.scenario_merger import apply_patch

        mod = PatchModification(
            parameter_type=PatchParameterType.ACTIVITY_DURATION, target_element="Task A",
            direction=ModificationDirection.INCREASE, baseline_value="600", proposed_value=str(MEAN_S),
            proposed_structured={"distributionType": "normal", "timeUnit": "secs", "values": values},
            kpi_reference="Task B Waiting Time", rationale="Normal duration for Task A.",
            evidence_source="baseline",
        )
        baseline = build_baseline_scenario(simod_json(with_timer=True), bpmn_xml=bpmn_with_timer()).scenario
        merged = apply_patch(baseline, patch_of(mod))
        self.assertIsNotNone(merged.scenario, merged.diagnostics)
        out = scenario_to_prosimos_json(merged.scenario, bpmn_with_timer())
        task = next(t for t in out["task_resource_distribution"] if t["task_id"] == "Task_A")
        self.assertEqual({r["distribution_name"] for r in task["resources"]}, {"norm"})
        return _params(task["resources"][0])[:2]

    def test_proposed_std_exports_unchanged(self):
        exported = self._export_patched_task_a([{"id": "mean", "value": MEAN_S}, {"id": "std", "value": STD_S}])
        self.assertEqual(exported, [MEAN_S, STD_S])

    def test_proposed_variance_exports_the_same(self):
        exported = self._export_patched_task_a(
            [{"id": "mean", "value": MEAN_S}, {"id": "variance", "value": STD_S ** 2}])
        self.assertEqual(exported, [MEAN_S, STD_S])

    def test_every_std_alias_is_squared_and_variance_aliases_kept(self):
        from second_llm.output_schema import DistributionParameter

        for alias in ("std", "sigma", "std_dev", "stdev", "standard_deviation", "std_hours", "std_minutes"):
            with self.subTest(alias=alias):
                p = DistributionParameter(id=alias, value=STD_S)
                self.assertEqual((p.id, p.value), ("variance", STD_S ** 2))
        for alias in ("variance", "var"):
            with self.subTest(alias=alias):
                p = DistributionParameter(id=alias, value=STD_S ** 2)
                self.assertEqual((p.id, p.value), ("variance", STD_S ** 2))
        # Re-validating a stored scenario does not square twice.
        p = DistributionParameter(id="std", value=STD_S)
        self.assertEqual(DistributionParameter.model_validate(p.model_dump()).value, STD_S ** 2)


class FixAndExponUnaffectedTests(unittest.TestCase):
    def test_fix_and_expon_export_byte_identical(self):
        timers = {
            "fix": {"distribution_name": "fix", "distribution_params": [{"value": 7200}]},
            "expon": {"distribution_name": "expon",
                      "distribution_params": [{"value": 3600}, {"value": 0}, {"value": 36000}]},
        }
        for name, timer in timers.items():
            with self.subTest(timer=name):
                data = simod_json(with_timer=True)  # activities: fix and expon; arrival: expon
                data["event_distribution"][0].update(timer)
                _, out = _round_trip(data)
                digest = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()
                self.assertEqual(digest, PRE_FIX_EXPORT[name])


if __name__ == "__main__":
    unittest.main()

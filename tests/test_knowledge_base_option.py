"""The knowledge base (RAG) is optional and off by default.

``knowledge.switch`` holds the setting (``config.yaml`` key ``use_knowledge_base``, default false; the Scenario
Studio toggle and ``--use-knowledge-base`` / ``--no-knowledge-base`` override it).

- **Off:** ``build_second_llm_evidence`` makes no call to ``retrieve_for_second_llm`` and returns no
  ``kb_json``, so the scenario prompt has no knowledge-base section; the refinement feedback retrieves no
  alternative interventions and has no knowledge-base section. The rest of the evidence and of the prompt is
  the same as with the knowledge base on.
- **On:** the knowledge base is retrieved and rendered as before: one retrieval per evidence build, its
  ``to_prompt_json()`` as ``kb_json``, the "Literature-Backed Parameter Recommendations" section in the prompt
  and the "Literature-Backed Alternative Interventions" section in the feedback.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

import knowledge.retrieval as kr  # noqa: E402
import second_llm.iterative_evaluator as ie  # noqa: E402
from knowledge import switch  # noqa: E402
from prompts.scenario_patch_prompt import build_scenario_patch_prompt  # noqa: E402
from second_llm.kpi_computation import ComputedKPI, KPIComputationResult  # noqa: E402
from second_llm.scenario_evaluation import KPITarget, TargetDirection, compare_kpis  # noqa: E402
from tests.test_step4_integration import CONTEXT_PROFILE, FIRST_LLM_PARSED, LOG_PROFILE, SIMOD_JSON  # noqa: E402

KB_PROMPT_SECTION = "Literature-Backed Parameter Recommendations"
KB_FEEDBACK_SECTION = "Literature-Backed Alternative Interventions"


def evidence():
    return kr.build_second_llm_evidence(
        goal_structured=FIRST_LLM_PARSED["simulation_goal_structured"], kpis=FIRST_LLM_PARSED["kpis"],
        simod_json=SIMOD_JSON, log_profile=LOG_PROFILE, context_profile=CONTEXT_PROFILE)


def prompt(ev) -> str:
    system, _, user = build_scenario_patch_prompt(json.dumps(FIRST_LLM_PARSED), evidence=ev)
    return system + "\n" + user


def feedback() -> str:
    """Refinement feedback for a utilization KPI that got worse."""
    t = [KPITarget(name="Pool Utilization", direction=TargetDirection.MINIMIZE, category="utilization",
                   measurable_as="Resource Utilization", threshold=0.85)]
    base = KPIComputationResult(kpis=[ComputedKPI("Resource Utilization", 0.83, "ratio", "utilization")])
    prop = KPIComputationResult(kpis=[ComputedKPI("Resource Utilization", 0.86, "ratio", "utilization")])
    return ie._build_evaluation_feedback(compare_kpis(base, prop, t), None, 1, 4)


class CountingRetrieval:
    """Stands in for retrieve_for_second_llm and counts the calls (the original does the work)."""

    def __init__(self):
        self.calls, self.original = 0, kr.retrieve_for_second_llm

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.original(*args, **kwargs)

    def __enter__(self):
        kr.retrieve_for_second_llm = self
        return self

    def __exit__(self, *exc):
        kr.retrieve_for_second_llm = self.original


class Default(unittest.TestCase):
    def tearDown(self):
        switch.set_knowledge_base(None)

    def test_off_by_default(self):
        switch.set_knowledge_base(None)
        self.assertFalse(switch.config_default())
        self.assertFalse(switch.knowledge_base_enabled())


class Off(unittest.TestCase):
    def setUp(self):
        switch.set_knowledge_base(False)

    def tearDown(self):
        switch.set_knowledge_base(None)

    def test_no_retrieval_and_no_kb_section_in_the_prompt(self):
        before = switch.skipped["retrieval"]
        with CountingRetrieval() as retrieval:
            ev = evidence()
        self.assertEqual(retrieval.calls, 0)
        self.assertEqual(switch.skipped["retrieval"], before + 1)
        self.assertEqual(ev.kb_json, "")
        self.assertNotIn(KB_PROMPT_SECTION, prompt(ev))
        self.assertTrue(ev.simod_json and ev.log_json and ev.context_json)   # the other evidence is kept

    def test_no_retrieval_and_no_kb_section_in_the_feedback(self):
        before = switch.skipped["feedback"]
        with CountingRetrieval() as retrieval:
            text = feedback()
        self.assertEqual(retrieval.calls, 0)
        self.assertEqual(switch.skipped["feedback"], before + 1)
        self.assertNotIn(KB_FEEDBACK_SECTION, text)

    def test_only_the_kb_section_differs_from_on(self):
        off = evidence()
        switch.set_knowledge_base(True)
        on = evidence()
        self.assertEqual(off.model_dump(exclude={"kb_json", "retrieval_notes"}),
                         on.model_dump(exclude={"kb_json", "retrieval_notes"}))
        self.assertEqual(prompt(off), prompt(on.model_copy(update={"kb_json": ""})))


class On(unittest.TestCase):
    def setUp(self):
        switch.set_knowledge_base(True)

    def tearDown(self):
        switch.set_knowledge_base(None)

    def test_retrieval_as_before(self):
        with CountingRetrieval() as retrieval:
            ev = evidence()
        self.assertEqual(retrieval.calls, 1)
        expected = kr.retrieve_for_second_llm(FIRST_LLM_PARSED["simulation_goal_structured"],
                                              FIRST_LLM_PARSED["kpis"], CONTEXT_PROFILE).to_prompt_json()
        self.assertEqual(ev.kb_json, expected)
        self.assertIn(KB_PROMPT_SECTION, prompt(ev))

    def test_feedback_as_before(self):
        with CountingRetrieval() as retrieval:
            text = feedback()
        self.assertEqual(retrieval.calls, 1)
        self.assertIn(KB_FEEDBACK_SECTION, text)


if __name__ == "__main__":
    unittest.main()

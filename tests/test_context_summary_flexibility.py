"""Clarification-context summary: staffing and overtime flexibility stated for a group of roles.

Regression for a summary that turned "for each internal role we could add up to three people,
and working hours could be extended by up to two hours per day" into "staffing is FIXED, NO
overtime" for every role named in a later cost sentence. Causes: the summariser schema had no
"not stated" value for staffing_flexible / overtime_available (so roles that were only named got
false), no rule for statements about a group of roles, no fields for hours per day, external
parties, a relative budget or hourly costs; and role names in the summary ("loan_officer") were
compared verbatim with model role names ("Loan Officer").

The answers below are used verbatim. The live test calls the configured LLM and runs only when
GLASS_LIVE_LLM_TESTS=1 and an API key is set.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from prompts.scenario_patch_prompt import _build_constraints_block  # noqa: E402
from second_llm.context_summary import OperationalContextSummary, build_context_summary  # noqa: E402

ANSWERS = [
    "Staffing is flexible within limits: for each internal role we could add up to three people, and working "
    "hours could be extended by up to two hours per day if needed. The applicant is an external party; we cannot "
    "add applicants, but we can send reminders and simplify what we ask from them.",
    "We can accept up to 35% higher staff cost overall. Hourly costs: clerk 25 EUR, loan officer 50 EUR, "
    "appraiser 45 EUR, AML investigator 45 EUR, senior officer 70 EUR.",
    "The AML check is a regulatory step and the senior officer's approval of the offer is our four-eyes rule; both "
    "must stay in the process in their current order. Everything else – who does what, how long steps take, how "
    "long files wait between steps – can be improved.",
]
MESSAGES = [{"role": "user", "content": a} for a in ANSWERS]
INTERNAL = {"clerk": 25, "loan officer": 50, "appraiser": 45, "AML investigator": 45, "senior officer": 70}


def role(summary: OperationalContextSummary, name: str) -> dict:
    norm = lambda s: " ".join(s.replace("_", " ").lower().split())
    return next((v for k, v in summary.resource_constraints.items() if norm(k) == norm(name)), {})


def expected_summary() -> dict:
    """What a correct summary of ANSWERS looks like under the summariser schema."""
    rc = {r: {"staffing_flexible": True, "max_additional": 3, "overtime_available": True,
              "max_overtime_hours_per_day": 2, "cost_per_hour": c} for r, c in INTERNAL.items()}
    rc["applicant"] = {"staffing_flexible": False, "external_party": True}
    return {"resource_constraints": rc, "budget": {"max_increase_pct": 35, "currency": "EUR"},
            "calendar_constraints": {"shift_extension_possible": True},
            "immutable_parameters": [{"element": "AML check"}, {"element": "senior officer's approval of the offer"}]}


class _StubProvider:
    def __init__(self, answer: dict):
        self.answer, self.calls = answer, []

    def generate(self, system_prompt, user_prompt, temperature=0.3, **kwargs):
        self.calls.append((system_prompt, user_prompt))
        return json.dumps(self.answer)


class SummaryParsingAndRenderingTests(unittest.TestCase):
    def test_prompt_carries_the_answers_and_the_group_rules(self):
        stub = _StubProvider(expected_summary())
        build_context_summary(MESSAGES, stub)
        system_prompt, user_prompt = stub.calls[0]
        for answer in ANSWERS:
            self.assertIn(answer, user_prompt)
        for rule in ("<true|false|null>", "applies to a group of roles", "external_party",
                     "max_overtime_hours_per_day", "max_increase_pct", "cost_per_hour"):
            self.assertIn(rule, system_prompt)

    def test_rendered_block_reflects_the_flexibility(self):
        block = _build_constraints_block(build_context_summary(MESSAGES, _StubProvider(expected_summary())))
        for r in INTERNAL:
            self.assertIn(f"- **{r}**: max additional = 3; overtime up to 2 h/day.", block)
        self.assertIn("- **applicant**: staffing is FIXED (no headcount changes allowed); "
                      "external party (headcount cannot be changed).", block)
        self.assertIn("total staff cost may increase by at most 35 %", block)
        self.assertIn("aml check", block)
        self.assertIn("senior officer's approval of the offer", block)
        self.assertNotIn("NO overtime", block)

    def test_roles_only_named_render_no_restriction(self):
        summary = OperationalContextSummary({"resource_constraints": {
            "clerk": {"staffing_flexible": None, "overtime_available": None, "cost_per_hour": 25}}})
        self.assertEqual(_build_constraints_block(summary), "")
        self.assertFalse(summary.is_role_fixed("Clerk"))

    def test_role_names_match_model_roles(self):
        summary = OperationalContextSummary({"resource_constraints": {
            "loan_officer": {"staffing_flexible": True, "max_additional": 3, "overtime_available": True},
            "applicant": {"staffing_flexible": False, "external_party": True}}})
        self.assertEqual(summary.get_max_additional("Loan Officer"), 3)
        self.assertEqual(summary.get_effective_max_headcount("Loan Officer", current_count=1), 4)
        self.assertTrue(summary.is_role_overtime_available("Loan Officer"))
        self.assertTrue(summary.is_role_fixed("Applicant"))
        plural = OperationalContextSummary({"resource_constraints": {"purchasing agents": {"max_additional": 3}}})
        self.assertEqual(plural.get_max_additional("Purchasing Agent"), 3)
        self.assertIsNone(plural.get_max_additional("Purchasing"))


@unittest.skipUnless(os.getenv("GLASS_LIVE_LLM_TESTS") == "1", "live LLM test: set GLASS_LIVE_LLM_TESTS=1")
class LiveSummariserTest(unittest.TestCase):
    def test_summary_of_exactly_these_answers(self):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / "goal_to_parameters" / ".env")
        if os.getenv("OPENAI_API_KEY"):
            from llm.openai_provider import OpenAIProvider
            provider = OpenAIProvider(api_key=os.environ["OPENAI_API_KEY"], model="gpt-4o-mini")
        elif os.getenv("OPENROUTER_API_KEY"):
            from llm.openrouter_provider import OpenRouterProvider
            provider = OpenRouterProvider(api_key=os.environ["OPENROUTER_API_KEY"], model="openai/gpt-4o-mini")
        else:
            self.skipTest("no API key")
        s = build_context_summary(MESSAGES, provider)
        for r, cost in INTERNAL.items():
            info = role(s, r)
            with self.subTest(role=r):
                self.assertIs(info.get("staffing_flexible"), True)
                self.assertEqual(info.get("max_additional"), 3)
                self.assertIs(info.get("overtime_available"), True)
                self.assertEqual(info.get("max_overtime_hours_per_day"), 2)
                self.assertEqual(info.get("cost_per_hour"), cost)
        applicant = role(s, "applicant")
        self.assertIs(applicant.get("external_party"), True)
        self.assertIs(applicant.get("staffing_flexible"), False)
        self.assertEqual(s.budget.get("max_increase_pct"), 35)
        self.assertIsNone(s.get_global_max_additional())        # "+3" is per role, not a total
        immutable = " | ".join(s.get_immutable_elements())
        self.assertIn("aml check", immutable)
        self.assertIn("approval", immutable)
        block = _build_constraints_block(s)
        for r in INTERNAL:
            self.assertFalse(s.is_role_fixed(r), r)
            line = next(l for l in block.splitlines() if l.lower().startswith(f"- **{r.lower()}"))
            self.assertNotIn("FIXED", line)
            self.assertNotIn("NO overtime", line)


if __name__ == "__main__":
    unittest.main()

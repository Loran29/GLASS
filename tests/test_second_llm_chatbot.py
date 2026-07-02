from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "goal_to_parameters"))

from second_llm.chatbot import ClarificationChatbot  # noqa: E402
from second_llm.models import ClarificationSession, FirstLLMInput, RawSimodInput, SimodResult  # noqa: E402


def _first_llm_input() -> FirstLLMInput:
    return FirstLLMInput(
        raw_json_text='{"simulation_goal_structured":"Reduce cycle time","kpis":[{"name":"Cycle Time"},{"name":"Waiting Time"}]}',
        parsed={
            "simulation_goal_structured": "Reduce cycle time",
            "kpis": [
                {"name": "Cycle Time"},
                {"name": "Waiting Time"},
            ],
        },
    )


def _simod_input() -> RawSimodInput:
    return RawSimodInput(
        raw_text="=== BPMN MODEL ===\n<bpmn />",
        line_count=2,
        is_non_empty=True,
        simod_result=SimodResult(
            process_name="loan_application",
            bpmn_content="<bpmn />",
            json_params_content='{"resources":[]}',
        ),
    )


class _FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self._responses.pop(0)

    def get_model_name(self) -> str:
        return "fake-model"


class SecondLLMChatbotTests(unittest.TestCase):
    def test_greet_with_ready_inputs_includes_loaded_context(self) -> None:
        session = ClarificationSession()
        chatbot = ClarificationChatbot(
            session=session,
            first_llm=_first_llm_input(),
            simod=_simod_input(),
        )

        greeting = chatbot.greet()

        self.assertIn("loaded your data", greeting.lower())
        self.assertIn("Reduce cycle time", greeting)
        self.assertIn("loan_application", greeting)
        self.assertTrue(session.last_context_signature)

    def test_sync_context_message_announces_new_context_once(self) -> None:
        session = ClarificationSession()
        chatbot = ClarificationChatbot(
            session=session,
            first_llm=FirstLLMInput(),
            simod=RawSimodInput(),
        )

        chatbot.greet()
        chatbot._first_llm = _first_llm_input()
        chatbot._simod = _simod_input()

        first_sync = chatbot.sync_context_message()
        second_sync = chatbot.sync_context_message()

        self.assertIsNotNone(first_sync)
        self.assertIn("Cycle Time", first_sync or "")
        self.assertIsNone(second_sync)
        self.assertEqual(len(session.messages), 2)

    def test_has_required_inputs_false_when_first_llm_schema_invalid(self) -> None:
        session = ClarificationSession()
        chatbot = ClarificationChatbot(
            session=session,
            first_llm=FirstLLMInput(
                raw_json_text='{"foo":"bar"}',
                validation_error="missing required fields",
            ),
            simod=_simod_input(),
        )

        self.assertFalse(chatbot.has_required_inputs())

    def test_reply_blocks_until_first_llm_schema_is_valid(self) -> None:
        session = ClarificationSession()
        chatbot = ClarificationChatbot(
            session=session,
            first_llm=FirstLLMInput(
                raw_json_text='{"foo":"bar"}',
                validation_error="missing required fields",
            ),
            simod=_simod_input(),
        )

        reply = chatbot.generate_assistant_reply("Can we continue?")

        self.assertIn("does not match the verified KPI schema", reply)


    def test_is_ready_to_generate_false_initially(self) -> None:
        session = ClarificationSession()
        chatbot = ClarificationChatbot(
            session=session,
            first_llm=_first_llm_input(),
            simod=_simod_input(),
        )
        chatbot.greet()
        self.assertFalse(chatbot.is_ready_to_generate())

    def test_is_ready_blocked_below_minimum_turns(self) -> None:
        """Signal present but only 1 user message — readiness must be False."""
        from second_llm.models import ChatRole

        session = ClarificationSession()
        chatbot = ClarificationChatbot(
            session=session,
            first_llm=_first_llm_input(),
            simod=_simod_input(),
        )
        chatbot.greet()
        session.append(ChatRole.USER, "We have a flexible overtime budget.")
        session.append(
            ChatRole.ASSISTANT,
            "I have enough context now. You can click **Generate and Optimize** "
            "whenever you're ready.",
        )
        # Only 1 user message — minimum is 3, so must be False
        self.assertFalse(chatbot.is_ready_to_generate())

    def test_is_ready_true_after_minimum_turns_and_signal(self) -> None:
        """Signal present AND enough user messages — readiness must be True."""
        from second_llm.models import ChatRole

        session = ClarificationSession()
        chatbot = ClarificationChatbot(
            session=session,
            first_llm=_first_llm_input(),
            simod=_simod_input(),
        )
        chatbot.greet()
        # Simulate 3 Q&A exchanges covering mandatory categories
        session.append(ChatRole.USER, "Staffing is fixed for Analysts, flexible for Clerks.")
        session.append(ChatRole.ASSISTANT, "Got it. What about budget for overtime or extra staff?")
        session.append(ChatRole.USER, "We have 5000 EUR/month for overtime.")
        session.append(ChatRole.ASSISTANT, "Understood. Are there any activities that must not change due to regulations?")
        session.append(ChatRole.USER, "Final Approval duration is regulatory, cannot change.")
        session.append(
            ChatRole.ASSISTANT,
            "I have enough context now. You can click **Generate and Optimize** "
            "whenever you're ready.",
        )
        self.assertEqual(chatbot.user_message_count, 3)
        self.assertTrue(chatbot.is_ready_to_generate())

    def test_greet_repairs_ambiguous_gateway_opening(self) -> None:
        session = ClarificationSession()
        provider = _FakeProvider(
            [
                (
                    "Let me ask a few questions to understand what you're looking "
                    "for before we generate the scenario.\n\n"
                    "The SIMOD baseline indicates a 42.3% probability of taking "
                    "one path at a specific gateway in the onboarding process. "
                    "How flexible is the staffing for this pathway?"
                ),
                (
                    "Gateway 'Eligibility check' routes 42.3% of cases to "
                    "'Manual review'. Is staffing on that branch fixed, or could "
                    "you add temporary capacity during peak periods?"
                ),
            ]
        )
        chatbot = ClarificationChatbot(
            session=session,
            first_llm=_first_llm_input(),
            simod=_simod_input(),
            provider=provider,
        )

        greeting = chatbot.greet()

        self.assertIn("Gateway 'Eligibility check' routes 42.3% of cases", greeting)
        self.assertNotIn("specific gateway", greeting)
        self.assertEqual(len(provider.calls), 2)
        self.assertIn(
            "ALWAYS name the gateway AND the",
            str(provider.calls[0]["system_prompt"]),
        )

    def test_prompt_version_updated_to_v5(self) -> None:
        session = ClarificationSession()
        chatbot = ClarificationChatbot(
            session=session,
            first_llm=_first_llm_input(),
            simod=_simod_input(),
        )
        chatbot.greet()
        self.assertEqual(session.prompt_version, "operational_context_v5")


if __name__ == "__main__":
    unittest.main()

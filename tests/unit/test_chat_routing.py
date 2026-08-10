"""Tests for ChatInterface keyword routing.

Verifies that documented and advertised prompts reach the correct intent
handler rather than falling through to the general fallback.
"""

import pytest

from ad_seller.interfaces.chat.main import ChatInterface


@pytest.fixture
def chat():
    return ChatInterface()


class TestChatIntentRouting:
    """Each prompt should map to the expected intent type."""

    @pytest.mark.parametrize(
        "prompt, expected_type",
        [
            ("list products", "availability"),
            ("show me your product catalog", "availability"),
            ("what CTV inventory do you have?", "availability"),
            ("available impressions for display", "availability"),
            ("how much does video inventory cost?", "pricing"),
            ("what is the CPM for CTV?", "pricing"),
            ("create deal for 5M impressions", "deal"),
            ("I want to create a deal for 5M display impressions", "deal"),
            ("book a campaign for next quarter", "deal"),
            ("make a deal for sports inventory", "deal"),
            ("that's too expensive, can you do $30?", "negotiation"),
            ("how about $25 CPM instead?", "negotiation"),
        ],
    )
    def test_prompt_routes_to_expected_intent(self, chat, prompt, expected_type):
        result = chat.process_message(prompt)
        assert result["type"] == expected_type, (
            f"'{prompt}' routed to '{result['type']}', expected '{expected_type}'"
        )

    def test_general_handler_example_prompts_do_not_self_refer(self, chat):
        """Example prompts printed by the general handler should not route to general."""
        general_response = chat.process_message("hello")
        assert general_response["type"] == "general"

        example_lines = [
            line.strip().lstrip("- ").strip('"')
            for line in general_response["text"].splitlines()
            if line.strip().startswith("- ")
        ]
        for example in example_lines:
            result = chat.process_message(example)
            assert result["type"] != "general", (
                f"General handler's own example '{example}' routes back to general"
            )

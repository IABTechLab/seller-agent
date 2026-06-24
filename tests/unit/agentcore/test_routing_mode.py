"""Tests for ROUTING_MODE selection logic in the AgentCore entrypoint.

Verifies that:
- ROUTING_MODE=chat uses ChatInterface (existing behavior)
- ROUTING_MODE=crew uses PublisherCrew
- Default mode is chat
- Invalid mode falls back to chat
- Payload routing_mode overrides env var
"""

import asyncio
import os
import re

# We need to mock bedrock_agentcore before importing the entrypoint,
# since it's imported at module level.
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_mock_agentcore = MagicMock()
_mock_app = MagicMock()
# Make the entrypoint decorator a passthrough
_mock_app.entrypoint = lambda fn: fn
_mock_agentcore.BedrockAgentCoreApp.return_value = _mock_app
sys.modules["bedrock_agentcore"] = MagicMock()
sys.modules["bedrock_agentcore.runtime"] = _mock_agentcore

from ad_seller.interfaces.agentcore.http_main import (  # noqa: E402
    _DEFAULT_ROUTING_MODE,
    _VALID_ROUTING_MODES,
    _format_crew_output,
    _get_routing_mode,
    _handle_invocation,
    _is_deal_request,
)

# ---------------------------------------------------------------------------
# _get_routing_mode tests
# ---------------------------------------------------------------------------


class TestGetRoutingMode:
    """Tests for the _get_routing_mode function."""

    def test_default_mode_is_chat(self):
        """Default routing mode should be 'chat' when nothing is set."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ROUTING_MODE", None)
            assert _get_routing_mode({}) == "chat"

    def test_env_var_chat(self):
        """ROUTING_MODE=chat should return 'chat'."""
        with patch.dict(os.environ, {"ROUTING_MODE": "chat"}):
            assert _get_routing_mode({}) == "chat"

    def test_env_var_crew(self):
        """ROUTING_MODE=crew should return 'crew'."""
        with patch.dict(os.environ, {"ROUTING_MODE": "crew"}):
            assert _get_routing_mode({}) == "crew"

    def test_invalid_env_var_falls_back_to_chat(self):
        """Invalid ROUTING_MODE value should fall back to 'chat'."""
        with patch.dict(os.environ, {"ROUTING_MODE": "invalid_mode"}):
            assert _get_routing_mode({}) == "chat"

    def test_payload_overrides_env_var(self):
        """Payload routing_mode should take priority over env var."""
        with patch.dict(os.environ, {"ROUTING_MODE": "chat"}):
            assert _get_routing_mode({"routing_mode": "crew"}) == "crew"

    def test_payload_crew_without_env_var(self):
        """Payload routing_mode=crew should work without env var."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ROUTING_MODE", None)
            assert _get_routing_mode({"routing_mode": "crew"}) == "crew"

    def test_invalid_payload_falls_back_to_chat(self):
        """Invalid payload routing_mode should fall back to 'chat'."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ROUTING_MODE", None)
            assert _get_routing_mode({"routing_mode": "bogus"}) == "chat"

    def test_case_insensitive(self):
        """Routing mode should be case-insensitive."""
        with patch.dict(os.environ, {"ROUTING_MODE": "CREW"}):
            assert _get_routing_mode({}) == "crew"

    def test_whitespace_stripped(self):
        """Whitespace around routing mode should be stripped."""
        with patch.dict(os.environ, {"ROUTING_MODE": "  crew  "}):
            assert _get_routing_mode({}) == "crew"

    def test_empty_string_env_var_uses_default(self):
        """Empty string ROUTING_MODE should fall back to default."""
        # Empty string is falsy, so os.environ.get returns it but
        # payload.get("routing_mode") with empty string is falsy → falls to env
        with patch.dict(os.environ, {"ROUTING_MODE": ""}):
            # Empty string stripped → empty → not in valid modes → fallback
            assert _get_routing_mode({}) == "chat"


# ---------------------------------------------------------------------------
# _handle_invocation routing tests
# ---------------------------------------------------------------------------


class TestHandleInvocationRouting:
    """Tests that _handle_invocation dispatches to the correct handler."""

    @pytest.mark.asyncio
    async def test_chat_mode_uses_chat_interface(self):
        """ROUTING_MODE=chat should route through ChatInterface."""
        with patch.dict(os.environ, {"ROUTING_MODE": "chat"}):
            mock_chat = MagicMock()
            mock_chat.process_message.return_value = "chat response"

            with patch(
                "ad_seller.interfaces.agentcore.http_main._get_chat",
                new_callable=AsyncMock,
                return_value=mock_chat,
            ):
                result = await _handle_invocation({"prompt": "list products"})

                assert result["response"] == "chat response"
                assert result["metadata"]["type"] == "seller_response"

    @pytest.mark.asyncio
    async def test_crew_mode_uses_publisher_crew(self):
        """ROUTING_MODE=crew should route through CrewAI crew."""
        with patch.dict(os.environ, {"ROUTING_MODE": "crew"}):
            with (
                patch(
                    "ad_seller.interfaces.agentcore.http_main._start_fastapi_background",
                ),
                patch(
                    "ad_seller.interfaces.agentcore.http_main._run_crew_with_crewai",
                    new_callable=AsyncMock,
                ) as mock_crew,
            ):
                mock_crew.return_value = {
                    "response": "crew response",
                    "metadata": {"routing_mode": "crew"},
                }

                result = await _handle_invocation({"prompt": "list products"})

                mock_crew.assert_awaited_once()
                assert result["response"] == "crew response"
                assert result["metadata"]["routing_mode"] == "crew"

    @pytest.mark.asyncio
    async def test_default_mode_uses_chat(self):
        """Default (no env var) should route through ChatInterface."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ROUTING_MODE", None)
            mock_chat = MagicMock()
            mock_chat.process_message.return_value = "default chat"

            with patch(
                "ad_seller.interfaces.agentcore.http_main._get_chat",
                new_callable=AsyncMock,
                return_value=mock_chat,
            ):
                result = await _handle_invocation({"prompt": "hello"})

                assert result["response"] == "default chat"

    @pytest.mark.asyncio
    async def test_payload_routing_mode_overrides_env(self):
        """Payload routing_mode=crew should override ROUTING_MODE=chat env var."""
        with patch.dict(os.environ, {"ROUTING_MODE": "chat"}):
            with (
                patch(
                    "ad_seller.interfaces.agentcore.http_main._start_fastapi_background",
                ),
                patch(
                    "ad_seller.interfaces.agentcore.http_main._run_crew_with_crewai",
                    new_callable=AsyncMock,
                ) as mock_crew,
            ):
                mock_crew.return_value = {
                    "response": "crew via payload",
                    "metadata": {"routing_mode": "crew"},
                }

                await _handle_invocation({"prompt": "list products", "routing_mode": "crew"})

                mock_crew.assert_awaited_once()


# ---------------------------------------------------------------------------
# _format_crew_output tests
# ---------------------------------------------------------------------------


class TestFormatCrewOutput:
    """Tests for structured output formatting of CrewAI responses."""

    def test_plain_text_output(self):
        """Plain text without structured data should return raw text."""
        mock_output = MagicMock()
        mock_output.raw = "Here is some inventory information."
        mock_output.json_dict = None
        mock_output.pydantic = None

        result = _format_crew_output(mock_output)

        assert result["response"] == "Here is some inventory information."
        assert result["metadata"]["routing_mode"] == "crew"

    def test_deal_id_extraction(self):
        """Deal IDs should be extracted and included in metadata."""
        mock_output = MagicMock()
        mock_output.raw = "Deal created: DEAL-2026-WBD-001 at $45 CPM"
        mock_output.json_dict = None
        mock_output.pydantic = None

        result = _format_crew_output(mock_output)

        assert "DEAL-2026-WBD-001" in result["metadata"]["deal_ids"]
        assert "<visualization-data>" in result["response"]

    def test_cpm_extraction(self):
        """CPM values should be extracted into visualization data."""
        mock_output = MagicMock()
        mock_output.raw = "Base rate: $45 CPM for CTV, $12 CPM for display"
        mock_output.json_dict = None
        mock_output.pydantic = None

        result = _format_crew_output(mock_output)

        assert "<visualization-data>" in result["response"]
        # Parse the viz data from the response
        import json
        import re

        viz_match = re.search(r"<visualization-data>(.*?)</visualization-data>", result["response"])
        assert viz_match is not None
        viz_data = json.loads(viz_match.group(1))
        assert 45.0 in viz_data["cpm_values"]
        assert 12.0 in viz_data["cpm_values"]

    def test_json_dict_output(self):
        """CrewOutput with json_dict should include structured_output in viz data."""
        mock_output = MagicMock()
        mock_output.raw = "Inventory list with $18 CPM"
        mock_output.json_dict = {"products": [{"id": "CTV-001", "cpm": 18}]}
        mock_output.pydantic = None

        result = _format_crew_output(mock_output)

        assert "<visualization-data>" in result["response"]

    def test_empty_raw_text(self):
        """Empty raw text should still return a valid response dict."""
        mock_output = MagicMock()
        mock_output.raw = ""
        mock_output.json_dict = None
        mock_output.pydantic = None

        result = _format_crew_output(mock_output)

        assert result["response"] == ""
        assert result["metadata"]["type"] == "seller_response"
        assert result["metadata"]["routing_mode"] == "crew"

    def test_budget_extraction(self):
        """Budget values should be extracted from crew output text."""
        mock_output = MagicMock()
        mock_output.raw = "Campaign with $500,000 budget at $35 CPM"
        mock_output.json_dict = None
        mock_output.pydantic = None

        result = _format_crew_output(mock_output)

        assert "<visualization-data>" in result["response"]


# ---------------------------------------------------------------------------
# CrewAI crew path tests
# ---------------------------------------------------------------------------


class TestCrewPath:
    """Tests for the CrewAI crew routing path."""

    @pytest.mark.asyncio
    async def test_crew_mode_routes_to_crewai(self):
        """Crew mode should route through _run_crew_with_crewai."""
        with patch.dict(os.environ, {"ROUTING_MODE": "crew"}):
            with (
                patch(
                    "ad_seller.interfaces.agentcore.http_main._start_fastapi_background",
                ),
                patch(
                    "ad_seller.interfaces.agentcore.http_main._run_crew_with_crewai",
                    new_callable=AsyncMock,
                ) as mock_crew,
            ):
                mock_crew.return_value = {
                    "response": "crew response",
                    "metadata": {"routing_mode": "crew"},
                }

                result = await _handle_invocation({"prompt": "list products"})

                mock_crew.assert_awaited_once()
                assert result["response"] == "crew response"

    @pytest.mark.asyncio
    async def test_crew_missing_prompt_returns_error(self):
        """Missing prompt in crew mode should return error."""
        with patch.dict(os.environ, {"ROUTING_MODE": "crew"}):
            with patch(
                "ad_seller.interfaces.agentcore.http_main._start_fastapi_background",
            ):
                result = await _handle_invocation({})
                assert "error" in result


# ---------------------------------------------------------------------------
# Constants validation
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify module-level constants are correct."""

    def test_valid_routing_modes(self):
        assert _VALID_ROUTING_MODES == {"chat", "crew"}

    def test_default_routing_mode(self):
        assert _DEFAULT_ROUTING_MODE == "chat"


# ---------------------------------------------------------------------------
# _is_deal_request tests
# ---------------------------------------------------------------------------


class TestIsDealRequest:
    """Tests for the _is_deal_request function (deal intent detection)."""

    def test_create_deal_with_product_id(self):
        assert _is_deal_request("Create a deal for inv-ctv-apex-sports-nba at $55 CPM")

    def test_book_deal_with_product_id(self):
        assert _is_deal_request(
            "Book a deal for inv-ctv-apex-sports-nhl at $42 CPM for 3M impressions"
        )

    def test_preferred_deal_with_product_id(self):
        assert _is_deal_request("Create a preferred deal for inv-ctv-apex-sports-nba")

    def test_multiple_deals(self):
        assert _is_deal_request(
            "Create both deals: inv-ctv-apex-sports-nba at $55 CPM and "
            "inv-ctv-apex-sports-nhl at $50 CPM"
        )

    def test_generate_deal_id(self):
        assert _is_deal_request("Generate deal ID for inv-digital-gnn-news at $30 CPM")

    def test_no_product_id_returns_false(self):
        assert not _is_deal_request("Create a deal for NBA basketball")

    def test_no_deal_keyword_returns_false(self):
        assert not _is_deal_request("Show me pricing for inv-ctv-apex-sports-nba")

    def test_inventory_query_returns_false(self):
        assert not _is_deal_request("Show me available CTV inventory with pricing")

    def test_empty_prompt_returns_false(self):
        assert not _is_deal_request("")

    def test_case_insensitive(self):
        assert _is_deal_request("CREATE A DEAL for INV-CTV-APEX-SPORTS-NBA at $55 CPM")


# ---------------------------------------------------------------------------
# Property-based tests (hypothesis)
# ---------------------------------------------------------------------------

from hypothesis import given  # noqa: E402
from hypothesis import settings as hyp_settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402


class TestGetRoutingModeProperty:
    """Property-based tests for _get_routing_mode.

    **Validates: Requirements 1.5**
    """

    @given(
        routing_mode=st.one_of(
            st.none(),
            st.text(min_size=0, max_size=50),
            st.integers(),
            st.just(""),
            st.just("  "),
            st.just("CREW"),
            st.just("Chat"),
            st.just("mcp"),
            st.just("invalid"),
            st.just("crew"),
            st.just("chat"),
        )
    )
    @hyp_settings(max_examples=200)
    def test_always_returns_valid_mode(self, routing_mode):
        """_get_routing_mode always returns a member of {"crew", "chat"} regardless of input."""
        # Build a payload with the generated routing_mode
        if routing_mode is None:
            payload = {}
        else:
            payload = {"routing_mode": routing_mode}

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ROUTING_MODE", None)
            result = _get_routing_mode(payload)
            assert result in {"crew", "chat"}, (
                f"_get_routing_mode returned {result!r} for input {routing_mode!r}"
            )

    @given(
        env_mode=st.one_of(
            st.none(),
            st.text(min_size=0, max_size=50).filter(lambda s: "\x00" not in s),
            st.just("crew"),
            st.just("chat"),
            st.just("CREW"),
            st.just("  chat  "),
            st.just("bogus"),
        ),
        payload_mode=st.one_of(
            st.none(),
            st.text(min_size=0, max_size=50).filter(lambda s: "\x00" not in s),
            st.just("crew"),
            st.just("chat"),
        ),
    )
    @hyp_settings(max_examples=200)
    def test_env_and_payload_always_returns_valid_mode(self, env_mode, payload_mode):
        """With any combination of env var and payload, result is always in {"crew", "chat"}."""
        env = {}
        if env_mode is not None:
            env["ROUTING_MODE"] = str(env_mode)

        payload = {}
        if payload_mode is not None:
            payload["routing_mode"] = payload_mode

        with patch.dict(os.environ, env, clear=False):
            if env_mode is None:
                os.environ.pop("ROUTING_MODE", None)
            result = _get_routing_mode(payload)
            assert result in {"crew", "chat"}, (
                f"_get_routing_mode returned {result!r} for env={env_mode!r}, payload={payload_mode!r}"
            )


class TestFormatCrewOutputErrorProperty:
    """Property-based tests for _format_crew_output error handling and
    crew invocation error path.

    **Validates: Requirements 6.4**

    Requirement 6.4: IF a tool invocation fails during CrewAI processing,
    THEN THE Seller_Agent SHALL include the tool name and error detail
    in the response metadata.
    """

    @given(
        error_message=st.text(min_size=1, max_size=200).filter(lambda s: s.strip()),
    )
    @hyp_settings(max_examples=100)
    def test_crew_invocation_error_propagates(self, error_message):
        """Crew invocation errors propagate as RuntimeError."""
        with (
            patch("ad_seller.interfaces.agentcore.http_main._start_fastapi_background"),
            patch(
                "ad_seller.interfaces.agentcore.http_main._run_crew_with_crewai",
                new_callable=AsyncMock,
                side_effect=RuntimeError(error_message),
            ),
        ):
            with pytest.raises(RuntimeError, match=re.escape(error_message)):
                asyncio.run(_handle_invocation({"prompt": "test", "routing_mode": "crew"}))

    @given(
        raw_text=st.text(min_size=0, max_size=500),
    )
    @hyp_settings(max_examples=100)
    def test_format_crew_output_always_returns_valid_structure(self, raw_text):
        """_format_crew_output always returns dict with 'response' and 'metadata' keys."""
        mock_output = MagicMock()
        mock_output.raw = raw_text
        mock_output.json_dict = None
        mock_output.pydantic = None

        result = _format_crew_output(mock_output)

        assert "response" in result, "Result must contain 'response' key"
        assert "metadata" in result, "Result must contain 'metadata' key"
        assert result["metadata"]["routing_mode"] == "crew"
        assert result["metadata"]["type"] == "seller_response"

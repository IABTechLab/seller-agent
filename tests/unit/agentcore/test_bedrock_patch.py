"""Tests for the CrewAI Bedrock Converse API patch.

Validates that _sanitize_tool_blocks correctly handles:
- Matched toolUse/toolResult pairs (preserved)
- Orphaned toolUse without toolResult (stripped)
- Orphaned toolResult without toolUse (stripped)
- Messages with only text (preserved)
- Empty messages (preserved)
- Mixed content (text + toolUse — text preserved, toolUse stripped if orphaned)
"""

import sys
from unittest.mock import MagicMock

# Mock bedrock_agentcore before any imports
sys.modules.setdefault("bedrock_agentcore", MagicMock())
sys.modules.setdefault("bedrock_agentcore.runtime", MagicMock())

from patches.crewai_bedrock_fix import _sanitize_tool_blocks  # noqa: E402


class TestSanitizeToolBlocks:
    """Tests for _sanitize_tool_blocks message sanitizer."""

    def test_empty_messages(self):
        assert _sanitize_tool_blocks([]) == []

    def test_text_only_messages_preserved(self):
        msgs = [
            {"role": "user", "content": [{"text": "hello"}]},
            {"role": "assistant", "content": [{"text": "hi"}]},
        ]
        assert _sanitize_tool_blocks(msgs) == msgs

    def test_matched_pair_preserved(self):
        """Matched toolUse + toolResult pair should be kept."""
        msgs = [
            {"role": "user", "content": [{"text": "list products"}]},
            {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": "tu-1", "name": "list_products", "input": {}}}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"toolResult": {"toolUseId": "tu-1", "content": [{"text": "products"}]}}
                ],
            },
            {"role": "assistant", "content": [{"text": "Here are the products"}]},
        ]
        result = _sanitize_tool_blocks(msgs)
        assert len(result) == 4
        assert "toolUse" in result[1]["content"][0]
        assert "toolResult" in result[2]["content"][0]

    def test_orphaned_tool_use_stripped(self):
        """toolUse without matching toolResult should be stripped."""
        msgs = [
            {"role": "user", "content": [{"text": "list products"}]},
            {
                "role": "assistant",
                "content": [
                    {"text": "Let me check"},
                    {"toolUse": {"toolUseId": "tu-orphan", "name": "list_products", "input": {}}},
                ],
            },
            {"role": "user", "content": [{"text": "what happened?"}]},
        ]
        result = _sanitize_tool_blocks(msgs)
        # The assistant message should keep text but strip toolUse
        assert len(result) == 3
        assert result[1]["content"] == [{"text": "Let me check"}]

    def test_orphaned_tool_use_only_dropped(self):
        """Assistant message with ONLY orphaned toolUse (no text) should be dropped."""
        msgs = [
            {"role": "user", "content": [{"text": "list products"}]},
            {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": "tu-orphan", "name": "list_products", "input": {}}},
                ],
            },
            {"role": "user", "content": [{"text": "what happened?"}]},
        ]
        result = _sanitize_tool_blocks(msgs)
        # The assistant message should be dropped entirely
        assert len(result) == 2

    def test_orphaned_tool_result_stripped(self):
        """toolResult without matching toolUse should be stripped."""
        msgs = [
            {"role": "user", "content": [{"text": "hello"}]},
            {"role": "assistant", "content": [{"text": "hi"}]},
            {
                "role": "user",
                "content": [
                    {"text": "here's the data"},
                    {"toolResult": {"toolUseId": "tu-orphan", "content": [{"text": "data"}]}},
                ],
            },
        ]
        result = _sanitize_tool_blocks(msgs)
        assert len(result) == 3
        # User message should keep text but strip toolResult
        assert result[2]["content"] == [{"text": "here's the data"}]

    def test_multiple_tool_uses_all_matched(self):
        """Multiple toolUse blocks all matched by toolResults."""
        msgs = [
            {"role": "user", "content": [{"text": "get pricing for both"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "tu-1",
                            "name": "get_pricing",
                            "input": {"product_id": "p1"},
                        }
                    },
                    {
                        "toolUse": {
                            "toolUseId": "tu-2",
                            "name": "get_pricing",
                            "input": {"product_id": "p2"},
                        }
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {"toolResult": {"toolUseId": "tu-1", "content": [{"text": "$42"}]}},
                    {"toolResult": {"toolUseId": "tu-2", "content": [{"text": "$48"}]}},
                ],
            },
        ]
        result = _sanitize_tool_blocks(msgs)
        assert len(result) == 3
        assert len(result[1]["content"]) == 2  # Both toolUse preserved
        assert len(result[2]["content"]) == 2  # Both toolResult preserved

    def test_partial_match_strips_all(self):
        """If not ALL toolUse IDs are matched, strip the entire toolUse message."""
        msgs = [
            {"role": "user", "content": [{"text": "get pricing"}]},
            {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": "tu-1", "name": "get_pricing", "input": {}}},
                    {"toolUse": {"toolUseId": "tu-2", "name": "list_products", "input": {}}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"toolResult": {"toolUseId": "tu-1", "content": [{"text": "$42"}]}},
                    # tu-2 missing!
                ],
            },
        ]
        result = _sanitize_tool_blocks(msgs)
        # Assistant message stripped (partial match = not matched)
        assert len(result) <= 2

    def test_non_dict_messages_preserved(self):
        """Non-dict messages should pass through unchanged."""
        msgs = ["raw string", {"role": "user", "content": [{"text": "hi"}]}]
        result = _sanitize_tool_blocks(msgs)
        assert result[0] == "raw string"

    def test_string_content_preserved(self):
        """Messages with string content (not list) should pass through."""
        msgs = [{"role": "user", "content": "hello"}]
        result = _sanitize_tool_blocks(msgs)
        assert result == msgs


class TestApplyPatches:
    """Test that apply_patches is idempotent and handles missing imports."""

    def test_apply_patches_idempotent(self):
        from patches.crewai_bedrock_fix import apply_patches

        # Should not raise on repeated calls
        apply_patches()
        apply_patches()


class TestParseNativeToolCallPatch:
    """Tests for the _parse_native_tool_call arg extraction fix."""

    def test_bedrock_tool_use_dict_args_preserved(self):
        """Bedrock toolUse dict should have input args extracted correctly."""
        from patches.crewai_bedrock_fix import _patch_parse_native_tool_call

        _patch_parse_native_tool_call()

        from crewai.agents.crew_agent_executor import CrewAgentExecutor

        # Create a minimal executor (won't actually run)
        executor = CrewAgentExecutor.__new__(CrewAgentExecutor)

        tool_call = {
            "toolUseId": "tooluse_abc123",
            "name": "get_pricing",
            "input": {
                "product_id": "inv-ctv-apex-sports-nba",
                "buyer_tier": "preferred",
                "volume": 5000000,
            },
        }

        result = executor._parse_native_tool_call(tool_call)
        assert result is not None
        call_id, func_name, func_args = result
        assert call_id == "tooluse_abc123"
        assert func_name == "get_pricing"
        assert func_args == {
            "product_id": "inv-ctv-apex-sports-nba",
            "buyer_tier": "preferred",
            "volume": 5000000,
        }

    def test_bedrock_tool_use_empty_input_returns_empty_dict(self):
        """Bedrock toolUse with empty input should return {} not '{}'."""
        from patches.crewai_bedrock_fix import _patch_parse_native_tool_call

        _patch_parse_native_tool_call()

        from crewai.agents.crew_agent_executor import CrewAgentExecutor

        executor = CrewAgentExecutor.__new__(CrewAgentExecutor)

        tool_call = {
            "toolUseId": "tooluse_xyz",
            "name": "list_products",
            "input": {},
        }

        result = executor._parse_native_tool_call(tool_call)
        assert result is not None
        call_id, func_name, func_args = result
        assert func_args == {}
        assert isinstance(func_args, dict)  # Not a string "{}"

    def test_openai_format_still_works(self):
        """OpenAI-format tool calls should still be handled by original parser."""
        from patches.crewai_bedrock_fix import _patch_parse_native_tool_call

        _patch_parse_native_tool_call()

        from crewai.agents.crew_agent_executor import CrewAgentExecutor

        executor = CrewAgentExecutor.__new__(CrewAgentExecutor)

        # OpenAI format — no toolUseId, has function.name
        tool_call = {
            "id": "call_abc",
            "function": {"name": "get_pricing", "arguments": '{"product_id": "p1"}'},
        }

        result = executor._parse_native_tool_call(tool_call)
        assert result is not None
        call_id, func_name, func_args = result
        assert func_name == "get_pricing"

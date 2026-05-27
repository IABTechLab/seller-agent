# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for FreeWheel MCP client transport resolution."""

from ad_seller.clients.freewheel_mcp_client import _build_transport_attempts


class TestBuildTransportAttempts:
    def test_prefers_streamable_then_sse_for_base_url(self):
        attempts = _build_transport_attempts("https://shmcp.freewheel.com")

        assert attempts == [
            ("streamable_http", "https://shmcp.freewheel.com"),
            ("sse", "https://shmcp.freewheel.com/sse"),
            ("sse", "https://shmcp.freewheel.com/mcp-sse/sse"),
            ("sse", "https://shmcp.freewheel.com"),
        ]

    def test_keeps_explicit_sse_first_and_tries_root_streamable(self):
        attempts = _build_transport_attempts("https://example.com/sse")

        assert attempts == [
            ("sse", "https://example.com/sse"),
            ("streamable_http", "https://example.com"),
        ]

    def test_deduplicates_trailing_slash_variants(self):
        attempts = _build_transport_attempts("https://example.com/")

        assert attempts == [
            ("streamable_http", "https://example.com"),
            ("sse", "https://example.com/sse"),
            ("sse", "https://example.com/mcp-sse/sse"),
            ("sse", "https://example.com"),
        ]

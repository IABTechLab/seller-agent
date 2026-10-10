# Donated to IAB Tech Lab

"""Tests for the seller runtime-registration helper (Req 6.1/6.2/6.5)."""

import pytest

from ad_seller.registry.runtime_registration import (
    agentcore_invocations_url,
    build_runtime_card,
    print_connection_info,
    register_runtimes,
)

_ARN = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/seller_mcp-abc"
_TOKEN_URL = "https://issuer.example/oauth2/token"
_SCOPE = "seller-agent/invoke"


def test_invocations_url_encoded_shape():
    url = agentcore_invocations_url(_ARN)
    assert url == (
        "https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/"
        "arn%3Aaws%3Abedrock-agentcore%3Aus-west-2%3A123456789012%3Aruntime"
        "%2Fseller_mcp-abc/invocations?qualifier=DEFAULT"
    )


class TestBuildRuntimeCard:
    def test_card_advertises_remote_auth_and_oauth(self):
        card = build_runtime_card(
            agent_name="aamp_seller_mcp",
            primary_domain="seller.example",
            runtime_arn=_ARN,
            protocol_type="mcp",
            token_endpoint=_TOKEN_URL,
            scope=_SCOPE,
        )
        assert card["agent_name"] == "aamp_seller_mcp"
        assert card["primary_domain"] == "seller.example"
        assert card["type"] == "remote"
        assert card["protocol_type"] == "mcp"
        assert card["auth_required"] is True
        assert card["endpoint_url"].endswith("/invocations?qualifier=DEFAULT")
        auth = card["authentication"]
        assert auth["type"] == "oauth2"
        assert auth["grant_type"] == "client_credentials"
        assert auth["token_endpoint"] == _TOKEN_URL
        assert auth["scope"] == _SCOPE

    def test_no_secret_in_card(self):
        card = build_runtime_card(
            agent_name="s", primary_domain="d", runtime_arn=_ARN,
            protocol_type="a2a", token_endpoint=_TOKEN_URL, scope=_SCOPE,
        )
        # The registry never carries a client secret.
        blob = str(card).lower()
        assert "secret" not in blob


class _FakeRegistryClient:
    def __init__(self, fail_names=()):
        self.registered = []
        self._fail = set(fail_names)

    async def register_self(self, agent: dict):
        if agent["agent_name"] in self._fail:
            return None
        stored = {**agent, "id": len(self.registered) + 1}
        self.registered.append(stored)
        return stored


@pytest.mark.asyncio
async def test_register_runtimes_registers_each():
    client = _FakeRegistryClient()
    runtimes = {
        "aamp_seller_mcp": {"arn": _ARN, "protocol_type": "mcp"},
        "aamp_seller_a2a": {"arn": _ARN.replace("_mcp-", "_a2a-"), "protocol_type": "a2a"},
    }
    stored = await register_runtimes(
        client, primary_domain="seller.example",
        token_endpoint=_TOKEN_URL, scope=_SCOPE, runtimes=runtimes,
    )
    assert len(stored) == 2
    assert {s["protocol_type"] for s in stored} == {"mcp", "a2a"}


@pytest.mark.asyncio
async def test_register_runtimes_skips_failures():
    client = _FakeRegistryClient(fail_names=["aamp_seller_a2a"])
    runtimes = {
        "aamp_seller_mcp": {"arn": _ARN, "protocol_type": "mcp"},
        "aamp_seller_a2a": {"arn": _ARN, "protocol_type": "a2a"},
    }
    stored = await register_runtimes(
        client, primary_domain="d", token_endpoint=_TOKEN_URL, scope=_SCOPE, runtimes=runtimes,
    )
    assert len(stored) == 1  # the failed one is skipped, no raise


def test_print_connection_info_has_urls_token_and_curl():
    runtimes = {"aamp_seller_mcp": {"arn": _ARN, "protocol_type": "mcp"}}
    out = print_connection_info(token_endpoint=_TOKEN_URL, scope=_SCOPE, runtimes=runtimes)
    assert _TOKEN_URL in out
    assert _SCOPE in out
    assert "/invocations?qualifier=DEFAULT" in out
    assert "grant_type=client_credentials" in out
    # Placeholder creds only — no real secret printed.
    assert "<BUYER_CLIENT_SECRET>" in out

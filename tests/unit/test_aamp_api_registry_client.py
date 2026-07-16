# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Tests for the real-API AAMP registry client (EP-5.1).

AampApiRegistryClient wires the seller's registry-facing surface
(verify_registration / lookup_agent / search_agents, plus self card
publication) to the REAL IAB agent registry API — ``/api/agents`` with the
``{"success": true, "data": ...}`` envelope and Bearer JWT auth — via the
shared contract library's RegistryClient.

These tests drive the library's faithful in-process test double of the
real registry over an httpx ASGITransport — no network, no sockets.
"""

import httpx
import pytest
from iab_agentic_primitives.sandbox_registry.real_double import create_registry_double

from ad_seller.clients.agent_registry_client import (
    AampApiRegistryClient,
    AAMPRegistryClient,
    build_registry_clients,
)

BASE_URL = "http://registry.test"


def _seed_agents(app) -> None:
    store = app.state.store
    store.agents[1] = {
        "id": 1,
        "agent_name": "Acme Buyer",
        "primary_domain": "acme-buyer.example.com",
        "type": "remote",
        "endpoint_url": "https://buyer.acme.example.com",
        "protocol_type": "a2a",
        "capabilities": ["deal_negotiation"],
        "industry_roles": ["buyer"],
        "verification_status": "active",
        "domain_verified": True,
    }
    store.agents[2] = {
        "id": 2,
        "agent_name": "Beta Seller",
        "primary_domain": "beta-seller.example.com",
        "type": "remote",
        "endpoint_url": "https://seller.beta.example.com",
        "protocol_type": "mcp",
        "capabilities": ["ctv"],
        "industry_roles": ["seller"],
        "verification_status": "pending",
        # The hosted UAT registry serializes unset list columns as null;
        # the wiring must tolerate that (lib gap, see module docstring).
        "endorsements": None,
        "iab_capabilities": None,
        "iab_subcategories": None,
    }
    store.next_id = 3


@pytest.fixture
def double_app():
    app = create_registry_double()
    _seed_agents(app)
    return app


@pytest.fixture
def client(double_app):
    return AampApiRegistryClient(
        base_url=BASE_URL,
        auth_token="user-token",
        transport=httpx.ASGITransport(app=double_app),
    )


class TestVerifyRegistration:
    async def test_known_endpoint_url_is_registered(self, client):
        registered, ext_id = await client.verify_registration(
            "https://buyer.acme.example.com/"
        )
        assert registered is True
        assert ext_id == "1"

    async def test_unknown_agent_is_not_registered(self, client):
        registered, ext_id = await client.verify_registration("https://nobody.example.com")
        assert registered is False
        assert ext_id is None

    async def test_unauthorized_degrades_to_not_registered(self, double_app):
        client = AampApiRegistryClient(
            base_url=BASE_URL,
            auth_token=None,
            transport=httpx.ASGITransport(app=double_app),
        )
        registered, ext_id = await client.verify_registration(
            "https://buyer.acme.example.com"
        )
        assert registered is False
        assert ext_id is None


class TestLookupAgent:
    async def test_returns_agent_record(self, client):
        record = await client.lookup_agent("1")
        assert record is not None
        assert record["agent_name"] == "Acme Buyer"
        assert record["id"] == 1

    async def test_missing_agent_returns_none(self, client):
        assert await client.lookup_agent("999") is None

    async def test_tolerates_null_list_fields(self, client):
        record = await client.lookup_agent("2")
        assert record is not None
        assert record["agent_name"] == "Beta Seller"


class TestSearchAgents:
    async def test_lists_all_agents(self, client):
        agents = await client.search_agents()
        assert {a["agent_name"] for a in agents} == {"Acme Buyer", "Beta Seller"}

    async def test_filters_by_agent_type_via_industry_roles(self, client):
        agents = await client.search_agents(agent_type="seller")
        assert [a["agent_name"] for a in agents] == ["Beta Seller"]

    async def test_filters_by_inventory_types_via_capabilities(self, client):
        agents = await client.search_agents(inventory_types=["ctv"])
        assert [a["agent_name"] for a in agents] == ["Beta Seller"]

    async def test_error_degrades_to_empty_list(self, double_app):
        client = AampApiRegistryClient(
            base_url=BASE_URL,
            auth_token=None,
            transport=httpx.ASGITransport(app=double_app),
        )
        assert await client.search_agents() == []


class TestRegisterSelf:
    async def test_publishes_seller_card(self, client):
        record = await client.register_self(
            {
                "agent_name": "Our Seller",
                # The double scopes registration to the token's company domain.
                "primary_domain": "example.com",
                "type": "remote",
                "endpoint_url": "https://seller.example.com",
                "protocol_type": "a2a",
            }
        )
        assert record is not None
        assert record["id"] == 3
        assert record["agent_name"] == "Our Seller"

    async def test_domain_mismatch_returns_none(self, client):
        record = await client.register_self(
            {
                "agent_name": "Rogue Seller",
                "primary_domain": "not-ours.example.org",
                "type": "remote",
                "endpoint_url": "https://rogue.example.org",
            }
        )
        assert record is None


class TestBuildRegistryClients:
    def test_returns_real_api_client_when_configured(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("AAMP_REGISTRY_URL", BASE_URL)
        monkeypatch.setenv("AAMP_REGISTRY_AUTH_TOKEN", "user-token")
        from ad_seller.config.settings import Settings

        clients = build_registry_clients(Settings())
        assert len(clients) == 1
        assert isinstance(clients[0], AampApiRegistryClient)
        assert clients[0].registry_url == BASE_URL

    def test_defaults_to_legacy_stub_clients(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.delenv("AAMP_REGISTRY_URL", raising=False)
        monkeypatch.delenv("AAMP_REGISTRY_AUTH_TOKEN", raising=False)
        from ad_seller.config.settings import Settings

        settings = Settings(aamp_registry_url="", agent_registry_extra_urls="")
        clients = build_registry_clients(settings)
        assert len(clients) == 1
        assert isinstance(clients[0], AAMPRegistryClient)

    def test_extra_urls_preserved_on_legacy_path(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.delenv("AAMP_REGISTRY_URL", raising=False)
        from ad_seller.config.settings import Settings

        settings = Settings(
            aamp_registry_url="",
            agent_registry_extra_urls="http://extra-1.test, http://extra-2.test",
        )
        clients = build_registry_clients(settings)
        assert len(clients) == 3

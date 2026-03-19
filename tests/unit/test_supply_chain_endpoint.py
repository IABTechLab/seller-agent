# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for GET /api/v1/supply-chain endpoint.

Tests the seller supply chain self-description endpoint per the
DealJockey seller API contract (buyer-te6b.1.1).

This endpoint is unauthenticated — public transparency data analogous
to IAB sellers.json.
"""

import sys
from types import ModuleType

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version mismatch)
# before any import of ad_seller.flows triggers __init__.py.
_broken_flows = [
    "ad_seller.flows.discovery_inquiry_flow",
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

import pytest
import httpx
from httpx import ASGITransport

from ad_seller.interfaces.api.main import app


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def client():
    """httpx AsyncClient — no auth overrides needed (endpoint is unauthenticated)."""
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c


# =============================================================================
# GET /api/v1/supply-chain
# =============================================================================


class TestGetSupplyChain:
    """Tests for the supply chain self-description endpoint."""

    async def test_returns_200(self, client):
        """Endpoint returns HTTP 200 OK."""
        resp = await client.get("/api/v1/supply-chain")
        assert resp.status_code == 200

    async def test_content_type_is_json(self, client):
        """Response has application/json content type."""
        resp = await client.get("/api/v1/supply-chain")
        assert "application/json" in resp.headers["content-type"]

    async def test_response_has_all_required_top_level_fields(self, client):
        """Response includes all required fields per the API contract."""
        resp = await client.get("/api/v1/supply-chain")
        data = resp.json()

        required_fields = [
            "seller_id",
            "seller_domain",
            "seller_name",
            "seller_type",
            "is_direct",
            "schain_node",
            "supported_deal_types",
            "supported_media_types",
        ]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"

    async def test_seller_type_is_valid_enum(self, client):
        """seller_type is one of PUBLISHER, SSP, DSP, INTERMEDIARY."""
        resp = await client.get("/api/v1/supply-chain")
        data = resp.json()
        valid_types = {"PUBLISHER", "SSP", "DSP", "INTERMEDIARY"}
        assert data["seller_type"] in valid_types

    async def test_is_direct_is_boolean(self, client):
        """is_direct is a boolean value."""
        resp = await client.get("/api/v1/supply-chain")
        data = resp.json()
        assert isinstance(data["is_direct"], bool)

    async def test_schain_node_has_required_fields(self, client):
        """schain_node contains required OpenRTB 2.6 fields: asi, sid, hp."""
        resp = await client.get("/api/v1/supply-chain")
        data = resp.json()
        schain_node = data["schain_node"]

        assert "asi" in schain_node, "schain_node missing 'asi'"
        assert "sid" in schain_node, "schain_node missing 'sid'"
        assert "hp" in schain_node, "schain_node missing 'hp'"

    async def test_schain_node_hp_is_integer(self, client):
        """schain_node.hp is an integer (0 or 1)."""
        resp = await client.get("/api/v1/supply-chain")
        data = resp.json()
        hp = data["schain_node"]["hp"]
        assert isinstance(hp, int)
        assert hp in (0, 1)

    async def test_schain_node_asi_matches_seller_domain(self, client):
        """For a direct publisher, schain_node.asi should match seller_domain."""
        resp = await client.get("/api/v1/supply-chain")
        data = resp.json()
        # Per contract example: asi matches the seller's domain
        assert data["schain_node"]["asi"] == data["seller_domain"]

    async def test_schain_node_sid_matches_seller_id(self, client):
        """schain_node.sid should match seller_id."""
        resp = await client.get("/api/v1/supply-chain")
        data = resp.json()
        assert data["schain_node"]["sid"] == data["seller_id"]

    async def test_supported_deal_types_are_valid(self, client):
        """supported_deal_types contains only valid values: PG, PD, PA."""
        resp = await client.get("/api/v1/supply-chain")
        data = resp.json()
        valid_deal_types = {"PG", "PD", "PA"}
        assert isinstance(data["supported_deal_types"], list)
        assert len(data["supported_deal_types"]) > 0
        for dt in data["supported_deal_types"]:
            assert dt in valid_deal_types, f"Invalid deal type: {dt}"

    async def test_supported_media_types_are_valid(self, client):
        """supported_media_types contains only valid values."""
        resp = await client.get("/api/v1/supply-chain")
        data = resp.json()
        valid_media_types = {"DIGITAL", "CTV", "LINEAR_TV", "AUDIO", "DOOH"}
        assert isinstance(data["supported_media_types"], list)
        assert len(data["supported_media_types"]) > 0
        for mt in data["supported_media_types"]:
            assert mt in valid_media_types, f"Invalid media type: {mt}"

    async def test_optional_fields_present_or_null(self, client):
        """Optional fields (sellers_json_url, contact) may be null but present."""
        resp = await client.get("/api/v1/supply-chain")
        data = resp.json()
        # These are optional per the contract — they can be null or present
        # Just verify the response parses cleanly
        if "sellers_json_url" in data:
            assert data["sellers_json_url"] is None or isinstance(data["sellers_json_url"], str)
        if "contact" in data:
            if data["contact"] is not None:
                assert isinstance(data["contact"], dict)

    async def test_contact_object_shape(self, client):
        """If contact is present and not null, it has the right fields."""
        resp = await client.get("/api/v1/supply-chain")
        data = resp.json()
        if data.get("contact") is not None:
            contact = data["contact"]
            # Only check fields that are present — all are optional
            for key in contact:
                assert key in {"programmatic_email", "sales_url"}

    async def test_response_matches_expected_json_shape(self, client):
        """Full response matches the expected JSON shape from the API contract."""
        resp = await client.get("/api/v1/supply-chain")
        data = resp.json()

        # Verify top-level types
        assert isinstance(data["seller_id"], str)
        assert isinstance(data["seller_domain"], str)
        assert isinstance(data["seller_name"], str)
        assert isinstance(data["seller_type"], str)
        assert isinstance(data["is_direct"], bool)
        assert isinstance(data["schain_node"], dict)
        assert isinstance(data["supported_deal_types"], list)
        assert isinstance(data["supported_media_types"], list)

        # Verify schain_node types
        node = data["schain_node"]
        assert isinstance(node["asi"], str)
        assert isinstance(node["sid"], str)
        assert isinstance(node["hp"], int)

    async def test_no_auth_required(self, client):
        """Endpoint does not require authentication — no API key needed."""
        # Just call without any auth headers and expect 200
        resp = await client.get("/api/v1/supply-chain")
        assert resp.status_code == 200

    async def test_get_method_only(self, client):
        """Only GET is allowed; POST should return 405."""
        resp = await client.post("/api/v1/supply-chain")
        assert resp.status_code == 405

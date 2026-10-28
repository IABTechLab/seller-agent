# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Buyer-facing catalog surfaces must not disclose the seller's floor.

The floor is the seller's negotiation reserve: one of the guardrails the
shared ``Negotiation`` primitive keeps off the wire (see
``test_counter_and_chat_floor_disclosure.py`` for the negotiation paths).
The catalog surfaces still published it:

1. ``GET /products`` and ``GET /products/{id}`` (anonymous) carried
   ``ext.floor_cpm`` on every product, via ``internal_product_to_shared``.
2. The ungated MCP ``list_products`` tool returned ``floor_cpm``.
3. The ungated MCP ``get_config`` / ``get_setup_status`` tools returned the
   default floor and the PG/PD pricing knobs to any HTTP caller.
4. ``AuthenticatedPackageView`` carried the package ``floor_price``. It is
   served to any non-public *effective* tier, which on
   ``/media-kit/search`` includes a tier merely claimed in the body.
5. AgentCore ``create_deal_direct`` stated the floor-derived minimum in its
   rejection and returned ``floor_cpm`` on the booked deal.
6. ``POST /discovery`` pricing answers (``DiscoveryInquiryFlow``) returned
   every product's floor under the bare key ``floor`` to any tier that sees
   exact prices, including a tier claimed in the body.

With real ad-server floors served (AI-6) and quotes clamped to the floor,
any of these told a buyer the lowest price the seller would accept.

Pricing still reads floors internally (quotes, the clamp, negotiation);
only what crosses the wire changes. Deal-level auction bid floors
(``bidfloor``) are untouched: a PA buyer needs them to bid.
"""

import json
import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ad_seller.engines.media_kit_service import MediaKitService
from ad_seller.engines.pricing_rules_engine import PricingRulesEngine
from ad_seller.interfaces.api import contract_mappers as cm
from ad_seller.models.core import DealType
from ad_seller.models.flow_state import ProductDefinition
from ad_seller.models.media_kit import (
    AuthenticatedPackageView,
    Package,
    PackageLayer,
    PackageStatus,
)
from ad_seller.models.pricing_tiers import TieredPricingConfig

FLOOR_CPM = 21.25
#: Field names that would disclose the floor or the pricing guardrails.
#: Includes the bare ``floor`` (the /discovery spelling), which a search for
#: ``floor_cpm`` / ``floor_price`` misses.
FLOOR_FIELDS = {"floor", "floor_cpm", "floor_price", "pg_floor_multiplier", "pd_discount_max"}


def _all_keys(obj: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            keys |= _all_keys(v)
    return keys


def _assert_no_floor_fields(payload: Any, where: str) -> None:
    leaked = FLOOR_FIELDS & _all_keys(payload)
    assert not leaked, f"{where} discloses {sorted(leaked)}"


def _product() -> ProductDefinition:
    return ProductDefinition(
        product_id="prod-floor-1",
        name="Premium CTV",
        inventory_type="ctv",
        base_cpm=25.0,
        floor_cpm=FLOOR_CPM,
        supported_deal_types=[DealType.PREFERRED_DEAL],
    )


# =============================================================================
# 1. Shared Product mapper (GET /products, GET /products/{id})
# =============================================================================


class TestSharedProductCarriesNoFloor:
    def test_ext_has_no_floor_cpm(self):
        shared = cm.internal_product_to_shared(_product())
        assert "floor_cpm" not in (shared.ext or {})

    def test_wire_payload_has_no_floor_field_at_any_depth(self):
        shared = cm.internal_product_to_shared(_product())
        _assert_no_floor_fields(shared.model_dump(mode="json"), "the shared Product")

    def test_list_response_has_no_floor_field(self):
        response = cm.products_to_list_response([_product()], limit=50, offset=0)
        _assert_no_floor_fields(response.model_dump(mode="json"), "the product list")

    def test_rest_of_ext_is_unchanged(self):
        ext = cm.internal_product_to_shared(_product()).ext
        assert ext["inventory_type"] == "ctv"
        assert ext["deal_types"] == ["PD"]


# =============================================================================
# 2 + 3. MCP tools
# =============================================================================


def _fake_http_context(headers: dict) -> MagicMock:
    ctx = MagicMock()
    request = MagicMock()
    request.headers = headers
    ctx.request_context.request = request
    return ctx


class TestMcpToolsCarryNoFloor:
    async def test_list_products_has_no_floor_cpm(self):
        from ad_seller.interfaces import mcp_server

        catalog = {"products": {"prod-floor-1": _product()}}
        with patch(
            "ad_seller.services.catalog_service.get_static_product_catalog",
            AsyncMock(return_value=catalog),
        ):
            result = json.loads(await mcp_server.list_products())
        assert result["products"][0]["base_cpm"] == 25.0
        _assert_no_floor_fields(result, "MCP list_products")

    @pytest.mark.parametrize("tool", ["get_config", "get_setup_status"])
    async def test_http_caller_without_operator_key_gets_currency_only(self, tool):
        from ad_seller.interfaces import mcp_server

        with (
            patch.object(mcp_server.mcp, "get_context", return_value=_fake_http_context({})),
            patch.object(mcp_server, "_get_storage", AsyncMock()),
            patch.object(mcp_server, "_media_kit_service", AsyncMock(side_effect=RuntimeError)),
        ):
            result = json.loads(await getattr(mcp_server, tool)())
        assert set(result["pricing"]) == {"currency"}
        _assert_no_floor_fields(result, f"MCP {tool} over HTTP without an operator key")

    async def test_local_operator_still_sees_pricing_policy(self):
        """stdio / in-process is operator access (same model as the CLI)."""
        from ad_seller.interfaces import mcp_server

        with patch.object(mcp_server.mcp, "get_context", side_effect=LookupError):
            result = json.loads(await mcp_server.get_config())
        assert {"floor_cpm", "pg_floor_multiplier", "pd_discount_max"} <= set(result["pricing"])


# =============================================================================
# 4. Authenticated package view (media kit)
# =============================================================================


class TestAuthenticatedPackageViewCarriesNoFloor:
    def test_model_has_no_floor_field(self):
        assert "floor_price" not in AuthenticatedPackageView.model_fields

    async def test_search_with_claimed_tier_returns_no_floor(self):
        """A body-claimed agency tier (no key) gets the authenticated view;
        it must still not carry the package floor."""
        package = Package(
            package_id="pkg-floor-1",
            name="Video Bundle",
            description="Premium video",
            layer=PackageLayer.CURATED,
            status=PackageStatus.ACTIVE,
            base_price=30.0,
            floor_price=FLOOR_CPM,
        )
        storage = AsyncMock()
        storage.list_packages.return_value = [package.model_dump(mode="json")]
        service = MediaKitService(
            storage=storage,
            pricing_engine=PricingRulesEngine(
                config=TieredPricingConfig(seller_organization_id="test-seller")
            ),
        )
        # Built exactly as POST /media-kit/search builds it for a caller
        # with no key who claims a tier in the body.
        from ad_seller.interfaces.api import deps

        claimed = deps._build_buyer_context(buyer_tier="agency", agency_id="ag-1")

        results = await service.search_packages("video", buyer_context=claimed)

        assert results and isinstance(results[0], AuthenticatedPackageView)
        assert results[0].exact_price > 0
        _assert_no_floor_fields([r.model_dump(mode="json") for r in results], "media-kit search")


# =============================================================================
# 5. AgentCore create_deal_direct
# =============================================================================


def _agentcore_stub() -> ModuleType:
    """Stub http_main (the real module needs the AgentCore runtime) holding
    one product with a known floor."""
    stub = ModuleType("ad_seller.interfaces.agentcore.http_main")
    stub._chat = SimpleNamespace(
        _products={
            "prod-floor-1": SimpleNamespace(
                name="Premium CTV", base_cpm=25.0, floor_cpm=FLOOR_CPM, inventory_type="ctv"
            )
        }
    )
    stub._get_chat = AsyncMock()
    return stub


class TestAgentCoreDirectDealCarriesNoFloor:
    @pytest.fixture(autouse=True)
    def _agentcore_runtime(self, monkeypatch):
        # setitem restores only these keys; patch.dict(sys.modules) would also
        # evict every module first imported during the test.
        monkeypatch.setitem(sys.modules, "bedrock_agentcore", MagicMock())
        monkeypatch.setitem(sys.modules, "bedrock_agentcore.runtime", MagicMock())
        monkeypatch.setitem(
            sys.modules, "ad_seller.interfaces.agentcore.http_main", _agentcore_stub()
        )

    def test_rejection_does_not_state_the_minimum(self):
        from ad_seller.interfaces.agentcore.crew_tools import CreateDealTool

        result = json.loads(CreateDealTool()._create_deal_direct("prod-floor-1", "PD", 1.0, 0))

        assert result["error"] == "price_below_floor"
        assert "seller_minimum_cpm" not in result
        threshold = round(FLOOR_CPM * 0.85, 2)
        assert f"{threshold:.2f}" not in result["message"]
        assert f"{FLOOR_CPM:.2f}" not in result["message"]

    def test_booked_deal_has_no_floor_cpm(self):
        from ad_seller.interfaces.agentcore.crew_tools import CreateDealTool

        result = json.loads(
            CreateDealTool()._create_deal_direct("prod-floor-1", "PD", 30.0, 100_000)
        )

        assert result["status"] == "booked"
        assert result["cpm"] == 30.0
        _assert_no_floor_fields(result, "AgentCore booked deal")


# =============================================================================
# 6. POST /discovery pricing answers
# =============================================================================


class TestDiscoveryPricingCarriesNoFloor:
    @pytest.mark.parametrize("query", ["what is the price for ctv?", "how much does ctv cost?"])
    def test_exact_price_tier_gets_price_but_no_floor(self, query):
        """A tier that sees exact prices (built as POST /discovery builds a
        body-claimed agency tier) gets its price, never the floor."""
        from ad_seller.flows.discovery_inquiry_flow import DiscoveryInquiryFlow
        from ad_seller.interfaces.api import deps

        context = deps._build_buyer_context(buyer_tier="agency", agency_id="ag-1")
        flow = DiscoveryInquiryFlow()
        response = flow.query(query=query, buyer_context=context, products={"p": _product()})

        assert flow.state.response_type == "pricing"
        products = response["pricing"]["products"]
        assert products[0]["price"] == pytest.approx(25.0 * 0.9)
        _assert_no_floor_fields(response, "POST /discovery pricing answer")

    def test_catalog_answer_carries_no_floor(self):
        from ad_seller.flows.discovery_inquiry_flow import DiscoveryInquiryFlow
        from ad_seller.interfaces.api import deps

        context = deps._build_buyer_context(buyer_tier="agency", agency_id="ag-1")
        response = DiscoveryInquiryFlow().query(
            query="what do you have?", buyer_context=context, products={"p": _product()}
        )
        _assert_no_floor_fields(response, "POST /discovery catalog answer")

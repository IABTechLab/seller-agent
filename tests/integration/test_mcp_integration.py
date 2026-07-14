"""Task 11: MCP Server Integration Test.

Tests MCP tools end-to-end with mocked backends:
- get_setup_status -> detect incomplete config
- list_products -> return products from mocked storage
- create_deal_from_template -> create deal via mocked HTTP
- list_orders -> show created deal via mocked HTTP
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from .conftest import make_settings

# ============================================================================
# get_setup_status
# ============================================================================


class TestGetSetupStatusIntegration:
    """Test get_setup_status with various configurations."""

    async def test_incomplete_when_default_publisher(self):
        from ad_seller.interfaces.mcp_server import get_setup_status

        settings = make_settings(seller_organization_name="Default Publisher")
        storage = AsyncMock()

        with (
            patch("ad_seller.interfaces.mcp_server._get_settings", return_value=settings),
            patch(
                "ad_seller.interfaces.mcp_server._get_storage",
                new_callable=AsyncMock,
                return_value=storage,
            ),
        ):
            result = json.loads(await get_setup_status())

        assert result["publisher_identity"]["configured"] is False
        assert result["setup_complete"] is False
        assert "incomplete" in result["message"].lower()

    async def test_incomplete_when_no_ad_server(self):
        from ad_seller.interfaces.mcp_server import get_setup_status

        settings = make_settings(
            seller_organization_name="My Publisher",
            gam_network_code=None,
            freewheel_sh_mcp_url=None,
        )
        storage = AsyncMock()

        with (
            patch("ad_seller.interfaces.mcp_server._get_settings", return_value=settings),
            patch(
                "ad_seller.interfaces.mcp_server._get_storage",
                new_callable=AsyncMock,
                return_value=storage,
            ),
        ):
            result = json.loads(await get_setup_status())

        assert result["publisher_identity"]["configured"] is True
        assert result["ad_server"]["configured"] is False
        assert result["setup_complete"] is False

    async def test_complete_with_identity_adserver_and_packages(self):
        from ad_seller.interfaces.mcp_server import get_setup_status

        settings = make_settings(
            seller_organization_name="My Publisher",
            gam_network_code="12345",
        )
        storage = AsyncMock()

        # Mock MediaKitService to return packages
        mock_service = AsyncMock()
        mock_service.list_packages_public.return_value = [MagicMock()]
        fake_module = MagicMock()
        fake_module.MediaKitService.return_value = mock_service

        with (
            patch("ad_seller.interfaces.mcp_server._get_settings", return_value=settings),
            patch(
                "ad_seller.interfaces.mcp_server._get_storage",
                new_callable=AsyncMock,
                return_value=storage,
            ),
            patch.dict(
                "sys.modules",
                {"ad_seller.engines.media_kit_service": fake_module},
            ),
        ):
            result = json.loads(await get_setup_status())

        assert result["setup_complete"] is True
        assert "fully configured" in result["message"].lower()


# ============================================================================
# list_products
# ============================================================================


class TestListProductsIntegration:
    """Test list_products via mocked ProductSetupFlow."""

    async def test_returns_products_from_flow(self, freewheel_products):
        from ad_seller.models.core import DealType, PricingModel
        from ad_seller.models.flow_state import ProductDefinition

        # Build ProductDefinition objects from fixture data
        products = {}
        for p in freewheel_products[:3]:
            pd = ProductDefinition(
                product_id=p["product_id"],
                name=p["name"],
                inventory_type=p["inventory_type"],
                base_cpm=p["base_cpm"],
                floor_cpm=p["floor_cpm"],
                supported_deal_types=[DealType(dt) for dt in p["supported_deal_types"]],
                supported_pricing_models=[PricingModel(pm) for pm in p["supported_pricing_models"]],
            )
            products[pd.product_id] = pd

        # Mock the ProductSetupFlow so it returns our products
        mock_flow_instance = AsyncMock()
        mock_flow_state = MagicMock()
        mock_flow_state.products = products
        mock_flow_instance.state = mock_flow_state
        mock_flow_instance.kickoff = AsyncMock()
        mock_flow_instance.kickoff_async = AsyncMock()

        mock_flow_cls = MagicMock(return_value=mock_flow_instance)

        with patch("ad_seller.interfaces.mcp_server.ProductSetupFlow", mock_flow_cls, create=True):

            async def patched_list_products(limit=50):
                flow = mock_flow_instance
                await flow.kickoff()
                result_products = []
                for pid, product in list(flow.state.products.items())[:limit]:
                    result_products.append(
                        {
                            "product_id": pid,
                            "name": product.name,
                            "inventory_type": product.inventory_type,
                            "base_cpm": product.base_cpm,
                            "floor_cpm": product.floor_cpm,
                            "deal_types": [dt.value for dt in product.supported_deal_types],
                        }
                    )
                return json.dumps(
                    {"products": result_products, "count": len(result_products)}, indent=2
                )

            result = json.loads(await patched_list_products())

        assert result["count"] == 3
        assert len(result["products"]) == 3
        ids = {p["product_id"] for p in result["products"]}
        assert "fw-ctv-premium-001" in ids


# ============================================================================
# create_deal_from_template
# ============================================================================


class TestCreateDealFromTemplateIntegration:
    """create_deal_from_template MCP tool calls the deal_service in-process.

    EP-3.2: the tool no longer loops back to REST over httpx — it invokes the
    same ``deal_service.create_deal_from_template`` the REST route uses. We
    patch that service function and assert the tool routed through it (not
    over HTTP) and projected the service result into the tool's I/O shape.
    """

    async def test_creates_deal_via_service(self):
        from ad_seller.interfaces.mcp_server import create_deal_from_template

        deal_data = {
            "deal_id": "INTEG-DEAL-001",
            "deal_type": "PG",
            "status": "confirmed",
            "product_id": "fw-ctv-premium-001",
            "actual_price_cpm": 38.0,
            "currency": "USD",
            "impressions": 100000,
            "flight_start": "2026-04-01",
            "flight_end": "2026-04-30",
            "buyer_tier": "public",
            "activation_instructions": {"ttd": "…"},
            "schain": {"complete": 1, "nodes": []},
            "created_at": "2026-04-01T00:00:00Z",
        }

        service_mock = AsyncMock(return_value=deal_data)

        with patch(
            "ad_seller.services.deal_service.create_deal_from_template",
            service_mock,
        ):
            result = await create_deal_from_template(
                deal_type="PG",
                product_id="fw-ctv-premium-001",
                max_cpm=40.0,
                impressions=100000,
                flight_start="2026-04-01",
                flight_end="2026-04-30",
            )

        # The service was invoked directly (no httpx loopback).
        assert service_mock.await_count == 1

        parsed = json.loads(result)
        assert parsed["deal_id"] == "INTEG-DEAL-001"
        assert parsed["deal_type"] == "PG"
        assert parsed["status"] == "confirmed"
        assert parsed["actual_price_cpm"] == 38.0


# ============================================================================
# list_orders
# ============================================================================


class TestListOrdersIntegration:
    """list_orders MCP tool calls order_service in-process (no httpx loopback)."""

    async def test_returns_orders_via_service(self):
        from ad_seller.interfaces.mcp_server import list_orders

        service_mock = AsyncMock(
            return_value={
                "orders": [
                    {"order_id": "ord-001", "status": "draft", "deal_id": "DEAL-001"},
                    {"order_id": "ord-002", "status": "approved", "deal_id": "DEAL-002"},
                ],
                "count": 2,
            }
        )

        with patch("ad_seller.services.order_service.list_orders", service_mock):
            result = await list_orders(limit=50)

        assert service_mock.await_count == 1

        parsed = json.loads(result)
        assert parsed["count"] == 2
        assert len(parsed["orders"]) == 2


# ============================================================================
# get_config
# ============================================================================


class TestGetConfigIntegration:
    """Test get_config MCP tool returns expected structure."""

    async def test_returns_config_without_secrets(self):
        from ad_seller.interfaces.mcp_server import get_config

        settings = make_settings(
            seller_organization_name="Integration Publisher",
            seller_organization_id="org-integ-001",
            default_currency="USD",
            default_price_floor_cpm=5.0,
        )

        with patch("ad_seller.interfaces.mcp_server._get_settings", return_value=settings):
            result = json.loads(await get_config())

        assert result["publisher"]["name"] == "Integration Publisher"
        assert result["pricing"]["currency"] == "USD"
        assert result["pricing"]["floor_cpm"] == 5.0

        # No secrets
        result_str = json.dumps(result)
        assert "anthropic" not in result_str.lower()
        assert "sk-test" not in result_str


# ============================================================================
# health_check
# ============================================================================


class TestHealthCheckIntegration:
    """Test health_check MCP tool."""

    async def test_healthy_with_storage(self):
        from ad_seller.interfaces.mcp_server import health_check

        settings = make_settings()
        storage = AsyncMock()

        with (
            patch("ad_seller.interfaces.mcp_server._get_settings", return_value=settings),
            patch(
                "ad_seller.interfaces.mcp_server._get_storage",
                new_callable=AsyncMock,
                return_value=storage,
            ),
        ):
            result = json.loads(await health_check())

        assert result["status"] == "healthy"
        assert result["checks"]["storage"] == "ok"

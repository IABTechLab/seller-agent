# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Internal deal statuses must map onto the shared DealStatus wire enum.

Regression tests for issue #73: the from-template, bulk-create, curated,
and migrate paths (and the MCP ``create_deal_from_template`` tool on top
of them) store deals with internal status ``"confirmed"``, which is not a
member of the shared ``iab-agentic-primitives`` ``DealStatus`` enum. The
wire mapper fed the stored string straight into ``DealStatus(...)``, so
``GET /api/v1/deals/{deal_id}`` raised a bare ``ValueError`` and 500'd on
every deal created outside the quote->book path. Same failure class for
``"deprecated"`` (written by migrate/deprecate).

The fix lives at the anti-corruption boundary
(``contract_mappers.internal_deal_status_to_wire``): storage keeps the
internal statuses, the wire gets shared enum values, and an internal
status with no translation fails loudly, naming the offending status.
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

# Stub execution_activation_flow (cancel-scope leak on ad-server
# connection failure, unresolved -- issue #60 part 2).
_broken_flows = [
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

from datetime import datetime  # noqa: E402

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402
from iab_agentic_primitives.primitives import DealStatus  # noqa: E402

from ad_seller.interfaces.api import contract_mappers as cm  # noqa: E402
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402
from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity  # noqa: E402
from ad_seller.models.core import DealType, PricingModel  # noqa: E402
from ad_seller.models.flow_state import ProductDefinition  # noqa: E402
from ad_seller.services import deal_service  # noqa: E402

# =============================================================================
# Helpers / fixtures
# =============================================================================


@pytest.fixture
def mock_storage():
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.get_quote = AsyncMock(side_effect=lambda qid: store.get(f"quote:{qid}"))
    storage.set_quote = AsyncMock(
        side_effect=lambda qid, data, ttl=86400: store.__setitem__(f"quote:{qid}", data)
    )
    storage.get_deal = AsyncMock(side_effect=lambda did: store.get(f"deal:{did}"))
    storage.set_deal = AsyncMock(
        side_effect=lambda did, data: store.__setitem__(f"deal:{did}", data)
    )
    storage._store = store
    return storage


@pytest.fixture
def client(mock_storage):
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


def _make_product(**overrides) -> ProductDefinition:
    defaults = dict(
        product_id="ctv-premium-sports",
        name="Premium CTV - Sports",
        description="Premium CTV sports inventory",
        inventory_type="ctv",
        supported_deal_types=[DealType.PREFERRED_DEAL, DealType.PROGRAMMATIC_GUARANTEED],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=35.0,
        floor_cpm=28.0,
        minimum_impressions=100000,
    )
    defaults.update(overrides)
    return ProductDefinition(**defaults)


def _mock_catalog():
    products = {"ctv-premium-sports": _make_product()}
    return {"products": products, "inventory_types": ["ctv"]}


def _public_context() -> BuyerContext:
    return BuyerContext(identity=BuyerIdentity(), is_authenticated=False)


def _template_request(**overrides) -> SimpleNamespace:
    """Request shape create_deal_from_template reads (same one the MCP
    create_deal_from_template tool builds)."""
    defaults = dict(
        deal_type="PD",
        product_id="ctv-premium-sports",
        impressions=1_000_000,
        max_cpm=None,
        flight_start=None,
        flight_end=None,
        buyer_identity=None,
        notes=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _available_quote(**overrides):
    defaults = {
        "quote_id": "qt-bulk1234567",
        "status": "available",
        "deal_type": "PD",
        "product": {
            "product_id": "ctv-premium-sports",
            "name": "Premium CTV - Sports",
            "inventory_type": "ctv",
        },
        "pricing": {"base_cpm": 35.0, "final_cpm": 28.26, "currency": "USD"},
        "terms": {
            "impressions": 5000000,
            "flight_start": "2026-04-01",
            "flight_end": "2026-04-30",
            "guaranteed": False,
        },
        "buyer_tier": "advertiser",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    defaults.update(overrides)
    return defaults


# =============================================================================
# Mapper unit tests
# =============================================================================


class TestInternalDealStatusToWire:
    def test_confirmed_maps_to_booked(self):
        assert cm.internal_deal_status_to_wire("confirmed") is DealStatus.BOOKED

    def test_deprecated_maps_to_cancelled(self):
        assert cm.internal_deal_status_to_wire("deprecated") is DealStatus.CANCELLED

    @pytest.mark.parametrize("status", [s.value for s in DealStatus])
    def test_shared_values_pass_through(self, status):
        assert cm.internal_deal_status_to_wire(status) is DealStatus(status)

    def test_unknown_internal_status_fails_loudly_naming_it(self):
        with pytest.raises(ValueError, match=r"'frobnicated'.*INTERNAL_TO_WIRE_STATUS"):
            cm.internal_deal_status_to_wire("frobnicated")

    def test_confirmed_deal_dict_maps_to_shared_deal(self):
        """The exact stored shape of a from-template deal must build the
        shared Deal primitive (this used to raise ValueError)."""
        deal = cm.internal_deal_to_shared_deal(
            {
                "deal_id": "DEMO-TPL123456789",
                "deal_type": "PD",
                "status": "confirmed",
                "product_id": "ctv-premium-sports",
                "actual_price_cpm": 28.0,
                "currency": "USD",
                "impressions": 1_000_000,
                "flight_start": "2026-10-01",
                "flight_end": "2026-10-31",
                "buyer_tier": "public",
                "created_at": datetime.utcnow().isoformat() + "Z",
            }
        )
        assert deal.status is DealStatus.BOOKED


# =============================================================================
# GET /api/v1/deals/{deal_id} round-trips (used to 500)
# =============================================================================


class TestGetDealCreatedOutsideQuotePath:
    async def test_from_template_deal_is_readable_as_booked(self, client, mock_storage):
        """Create via the from-template service path (what the REST route
        and the MCP tool both call), then GET through the wire mapper."""
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            deal_data = await deal_service.create_deal_from_template(
                _template_request(), _public_context(), _mock_catalog()
            )
            assert deal_data["status"] == "confirmed"  # storage untouched

            resp = await client.get(f"/api/v1/deals/{deal_data['deal_id']}")

        assert resp.status_code == 200, resp.text
        assert resp.json()["deal"]["status"] == "booked"

    async def test_bulk_created_deal_is_readable_as_booked(self, client, mock_storage):
        quote = _available_quote()
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            bulk = await client.post(
                "/api/v1/deals/bulk",
                json={"operations": [{"action": "create", "quote_id": quote["quote_id"]}]},
            )
            assert bulk.status_code == 200, bulk.text
            result = bulk.json()["results"][0]
            assert result["success"] is True

            resp = await client.get(f"/api/v1/deals/{result['deal_id']}")

        assert resp.status_code == 200, resp.text
        deal = resp.json()["deal"]
        assert deal["status"] == "booked"
        # The bulk path now carries the quote's booked terms so the shared
        # Deal primitive (which requires deal_type) can be built at all.
        assert deal["deal_type"] == "PD"
        assert deal["product"]["product_id"] == "ctv-premium-sports"

    async def test_curated_deal_is_readable_as_booked(self, client, mock_storage):
        request = SimpleNamespace(
            curator_id="agent-range",
            deal_type="PD",
            product_id="ctv-premium-sports",
            max_cpm=None,
            impressions=1_000_000,
            flight_start=None,
            flight_end=None,
            buyer_seat_ids=[],
            audience_segments=[],
            content_categories=[],
        )
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            result = await deal_service.create_curated_deal(request, _mock_catalog())
            assert result["status"] == "confirmed"  # storage untouched

            resp = await client.get(f"/api/v1/deals/{result['deal_id']}")

        assert resp.status_code == 200, resp.text
        assert resp.json()["deal"]["status"] == "booked"

    async def test_deprecated_deal_is_readable_as_cancelled(self, client, mock_storage):
        """Migrate/deprecate leave the superseded deal stored as
        'deprecated' -- also absent from the shared enum, same 500."""
        mock_storage._store["deal:DEMO-OLD123456789"] = {
            "deal_id": "DEMO-OLD123456789",
            "deal_type": "PD",
            "status": "deprecated",
            "deprecated_at": datetime.utcnow().isoformat() + "Z",
            "deprecated_reason": "Replaced by migration",
            "replacement_deal_id": "DEMO-NEW123456789",
            "created_at": datetime.utcnow().isoformat() + "Z",
        }

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/deals/DEMO-OLD123456789")

        assert resp.status_code == 200, resp.text
        assert resp.json()["deal"]["status"] == "cancelled"

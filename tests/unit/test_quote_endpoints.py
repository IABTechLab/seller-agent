# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for IAB Deals API v1.0 — Quote endpoints.

Tests POST /api/v1/quotes and GET /api/v1/quotes/{quote_id}.

EP-12.2: the wire edge now speaks the shared ``iab-agentic-primitives``
contract. Requests are the shared ``QuoteRequest`` (required
``idempotency_key``; carries ``media_type``/``linear_tv``/``agent_url``)
and responses are the shared ``QuoteResponse`` (``{"quote": {...}}`` with
``Money`` pricing in micros).
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version mismatch)
# before any import of ad_seller.flows triggers __init__.py.
_broken_flows = [
    "ad_seller.flows.discovery_inquiry_flow",
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        # Add the class name that __init__.py expects to import
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

from datetime import datetime, timedelta  # noqa: E402

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402

# =============================================================================
# Helpers
# =============================================================================


def _mock_product_setup_flow(products_dict):
    """Return a mock ProductSetupFlow whose state has the given products.

    Kept for backward compatibility with TestGetQuote and other call sites.
    """
    mock_flow = MagicMock()
    mock_flow.state = MagicMock()
    mock_flow.state.products = products_dict
    mock_flow.kickoff = AsyncMock()
    mock_flow.kickoff_async = AsyncMock()
    return mock_flow


def _mock_catalog(products_dict):
    """Build the dict shape returned by `_get_static_product_catalog`.

    Quote endpoint switched from `ProductSetupFlow.kickoff()` to the cached
    static catalog (ar-uwad / ar-0vtg). Tests patch the catalog accessor
    rather than the flow class.
    """
    inventory_types = sorted({p.inventory_type for p in products_dict.values()})
    return {"products": products_dict, "inventory_types": inventory_types}


def _make_product(**overrides):
    from ad_seller.models.core import DealType, PricingModel
    from ad_seller.models.flow_state import ProductDefinition

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


def _products():
    return {"ctv-premium-sports": _make_product()}


def _body(**fields):
    """A valid shared QuoteRequest body (auto-supplies idempotency_key)."""
    fields.setdefault("idempotency_key", "idem-quote-1")
    return fields


@pytest.fixture
def mock_storage():
    """In-memory dict-backed mock storage."""
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.get_quote = AsyncMock(side_effect=lambda qid: store.get(f"quote:{qid}"))
    storage.set_quote = AsyncMock(
        side_effect=lambda qid, data, ttl=86400: store.__setitem__(f"quote:{qid}", data)
    )
    storage._store = store
    return storage


@pytest.fixture
def client(mock_storage):
    """httpx AsyncClient with FastAPI dependency overrides."""
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# =============================================================================
# POST /api/v1/quotes
# =============================================================================


class TestCreateQuote:
    async def test_happy_path_pd_quote(self, client, mock_storage):
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(_products()),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json=_body(
                    product_id="ctv-premium-sports",
                    deal_type="PD",
                    impressions=5000000,
                    flight_start="2026-04-01",
                    flight_end="2026-04-30",
                ),
            )

        assert resp.status_code == 200
        # Shared QuoteResponse envelope wraps the Quote primitive.
        quote = resp.json()["quote"]
        assert quote["quote_id"].startswith("qt-")
        assert quote["status"] == "available"
        assert quote["deal_type"] == "PD"
        assert quote["product"]["product_id"] == "ctv-premium-sports"
        # Money in micros, not a bare float.
        assert quote["pricing"]["base_cpm"]["amount_micros"] == 35_000_000
        assert quote["pricing"]["base_cpm"]["currency"] == "USD"
        assert quote["pricing"]["final_cpm"]["amount_micros"] > 0
        assert quote["terms"]["flight_start"] == "2026-04-01"
        assert quote["terms"]["impressions"] == 5000000
        assert quote["terms"]["guaranteed"] is False
        assert quote["expires_at"] is not None
        assert quote["media_type"] == "digital"

    async def test_pg_quote_sets_guaranteed_true(self, client, mock_storage):
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(_products()),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json=_body(
                    product_id="ctv-premium-sports",
                    deal_type="PG",
                    impressions=5000000,
                ),
            )

        assert resp.status_code == 200
        assert resp.json()["quote"]["terms"]["guaranteed"] is True

    async def test_ctv_media_type_carried_through(self, client, mock_storage):
        """media_type is no longer silently dropped — it round-trips (FD-6)."""
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(_products()),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json=_body(
                    product_id="ctv-premium-sports",
                    deal_type="PD",
                    impressions=1000000,
                    media_type="ctv",
                    agent_url="https://buyer.example/agent",
                ),
            )

        assert resp.status_code == 200
        assert resp.json()["quote"]["media_type"] == "ctv"

    async def test_linear_tv_structurally_rejected(self, client, mock_storage):
        """FD-6: unsupported media_type gets the shared structured rejection."""
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(_products()),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json=_body(
                    product_id="ctv-premium-sports",
                    deal_type="PD",
                    impressions=1000000,
                    media_type="linear_tv",
                    linear_tv={"target_demo": "A18-49"},
                ),
            )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error"] == "unsupported_capability"
        assert detail["unsupported"][0]["capability"] == "linear_tv"

    async def test_target_cpm_accepted_when_above_floor(self, client, mock_storage):
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(_products()),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json=_body(
                    product_id="ctv-premium-sports",
                    deal_type="PD",
                    impressions=1000000,
                    target_cpm={"amount_micros": 32_000_000, "currency": "USD"},
                ),
            )

        assert resp.status_code == 200
        assert resp.json()["quote"]["pricing"]["final_cpm"]["amount_micros"] == 32_000_000

    async def test_target_cpm_rejected_below_floor(self, client, mock_storage):
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(_products()),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json=_body(
                    product_id="ctv-premium-sports",
                    deal_type="PD",
                    impressions=1000000,
                    target_cpm={"amount_micros": 500_000, "currency": "USD"},
                ),
            )

        assert resp.status_code == 200
        final = resp.json()["quote"]["pricing"]["final_cpm"]["amount_micros"]
        assert final != 500_000
        assert final > 0

    async def test_buyer_identity_affects_tier(self, client, mock_storage):
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(_products()),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json=_body(
                    product_id="ctv-premium-sports",
                    deal_type="PD",
                    buyer_identity={
                        "seat_id": "seat-ttd-12345",
                        "agency_id": "agency-groupm-001",
                        "advertiser_id": "adv-nike-001",
                        "dsp_platform": "ttd",
                    },
                ),
            )

        assert resp.status_code == 200
        quote = resp.json()["quote"]
        assert quote["buyer_tier"] == "advertiser"
        assert quote["pricing"]["tier_discount_pct"] == 15.0

    # Error cases

    async def test_product_not_found(self, client, mock_storage):
        with patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            return_value=_mock_catalog(_products()),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json=_body(product_id="nonexistent", deal_type="PD"),
            )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "product_not_found"

    async def test_invalid_deal_type_rejected_at_wire(self, client, mock_storage):
        """Shared QuoteRequest types deal_type as an enum: bad values 422 at the edge."""
        with patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            return_value=_mock_catalog(_products()),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json=_body(product_id="ctv-premium-sports", deal_type="INVALID"),
            )
        assert resp.status_code == 422

    async def test_missing_idempotency_key_rejected(self, client, mock_storage):
        """FD-12: idempotency_key is required on the shared QuoteRequest."""
        with patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            return_value=_mock_catalog(_products()),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json={"product_id": "ctv-premium-sports", "deal_type": "PD"},
            )
        assert resp.status_code == 422

    async def test_pg_without_impressions(self, client, mock_storage):
        with patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            return_value=_mock_catalog(_products()),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json=_body(product_id="ctv-premium-sports", deal_type="PG"),
            )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "pg_requires_impressions"

    async def test_below_minimum_impressions(self, client, mock_storage):
        with patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            return_value=_mock_catalog(_products()),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json=_body(
                    product_id="ctv-premium-sports",
                    deal_type="PD",
                    impressions=50,
                ),
            )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "below_minimum_impressions"

    async def test_quote_recorded_in_history(self, client, mock_storage):
        """Layer 4: quote creation should persist a quote_history record."""
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(_products()),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            resp = await client.post(
                "/api/v1/quotes",
                json=_body(
                    product_id="ctv-premium-sports",
                    deal_type="PD",
                    impressions=5000000,
                ),
            )

        assert resp.status_code == 200
        quote_id = resp.json()["quote"]["quote_id"]

        # Verify quote_history record was created
        history_key = f"quote_history:{quote_id}"
        assert history_key in mock_storage._store
        record = mock_storage._store[history_key]
        assert record["product_id"] == "ctv-premium-sports"
        assert record["quoted_cpm"] > 0
        assert "buyer_id" in record
        assert "quoted_at" in record


# =============================================================================
# GET /api/v1/quotes/{quote_id}
# =============================================================================


def _full_quote_dict(**overrides):
    """A complete internal quote dict (as persisted by create_quote)."""
    data = {
        "quote_id": "qt-abc123",
        "status": "available",
        "deal_type": "PD",
        "product": {
            "product_id": "ctv-premium-sports",
            "name": "CTV",
            "inventory_type": "ctv",
        },
        "pricing": {
            "base_cpm": 35.0,
            "final_cpm": 29.75,
            "currency": "USD",
            "pricing_model": "cpm",
            "rationale": "",
        },
        "terms": {
            "flight_start": "2026-04-01",
            "flight_end": "2026-04-30",
            "guaranteed": False,
        },
        "buyer_tier": "public",
        "expires_at": (datetime.utcnow() + timedelta(hours=23)).isoformat() + "Z",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    data.update(overrides)
    return data


class TestGetQuote:
    async def test_retrieve_stored_quote(self, client, mock_storage):
        quote_data = _full_quote_dict()
        mock_storage._store["quote:qt-abc123"] = quote_data

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/quotes/qt-abc123")

        assert resp.status_code == 200
        quote = resp.json()["quote"]
        assert quote["quote_id"] == "qt-abc123"
        assert quote["pricing"]["final_cpm"]["amount_micros"] == 29_750_000

    async def test_quote_not_found(self, client, mock_storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/quotes/qt-nonexistent")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "quote_not_found"

    async def test_expired_quote_returns_410(self, client, mock_storage):
        quote_data = {
            "quote_id": "qt-expired1",
            "status": "available",
            "expires_at": (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z",
        }
        mock_storage._store["quote:qt-expired1"] = quote_data

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            resp = await client.get("/api/v1/quotes/qt-expired1")

        assert resp.status_code == 410
        assert resp.json()["detail"]["error"] == "quote_expired"
        stored = mock_storage._store["quote:qt-expired1"]
        assert stored["status"] == "expired"

# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for auth header binding on API endpoints.

Regression tests for the silent auth-header binding bug: the
``_get_optional_api_key_record`` dependency in
``ad_seller.interfaces.api.main`` must bind ``Authorization`` and
``X-Api-Key`` as HTTP *headers* (not query parameters). If they bind
as query params, real credentials never reach the validator: every
buyer is treated as anonymous (PUBLIC-tier pricing) and invalid or
revoked keys are silently accepted as anonymous instead of rejected
with 401.

These tests hit the real app (no dependency override for the auth
dependency) through httpx.ASGITransport, seeding an in-memory mock
storage with API key records so ``ApiKeyService.validate_key`` runs
for real.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

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

from ad_seller.interfaces.api.main import app  # noqa: E402
from ad_seller.models.api_key import (  # noqa: E402
    API_KEY_STORAGE_PREFIX,
    ApiKeyRecord,
    generate_api_key,
    hash_api_key,
)
from ad_seller.models.buyer_identity import BuyerIdentity  # noqa: E402
from ad_seller.models.core import DealType, PricingModel  # noqa: E402
from ad_seller.models.flow_state import ProductDefinition  # noqa: E402

# =============================================================================
# Helpers
# =============================================================================


def _make_product(**overrides):
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
    inventory_types = sorted({p.inventory_type for p in products.values()})
    return {"products": products, "inventory_types": inventory_types}


def _seed_key(store, *, revoked=False, expired=False):
    """Create an API key, store its record like ApiKeyService does, return the raw key."""
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    record = ApiKeyRecord(
        key_id="key-authbind",
        key_hash=key_hash,
        key_prefix_hint=raw_key[:12] + "...",
        identity=BuyerIdentity(agency_id="agency-001", agency_name="Acme Agency"),
        label="Auth binding test key",
        revoked=revoked,
        revoked_at=datetime.utcnow() - timedelta(hours=1) if revoked else None,
        expires_at=datetime.utcnow() - timedelta(hours=1) if expired else None,
    )
    store[f"{API_KEY_STORAGE_PREFIX}{key_hash}"] = record.model_dump(mode="json")
    return raw_key


QUOTE_BODY = {
    "product_id": "ctv-premium-sports",
    "deal_type": "PD",
    "impressions": 5000000,
}


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_storage():
    """In-memory dict-backed mock storage (also serves API key lookups)."""
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
def client():
    """httpx AsyncClient against the real app.

    Deliberately does NOT override _get_optional_api_key_record: the point
    of this suite is to exercise the real header-binding behavior.
    """
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c


def _patches(mock_storage):
    return (
        patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            return_value=_mock_catalog(),
        ),
        patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
    )


# =============================================================================
# Header binding through a real auth-aware endpoint (POST /api/v1/quotes)
# =============================================================================


class TestAuthHeaderBinding:
    async def test_x_api_key_header_resolves_key_record(self, client, mock_storage):
        """A valid X-Api-Key header must authenticate the buyer.

        The seeded key carries an agency identity, so the quote must come
        back at the agency tier — not the anonymous public tier.
        """
        raw_key = _seed_key(mock_storage._store)
        catalog_patch, storage_patch = _patches(mock_storage)
        with catalog_patch, storage_patch:
            resp = await client.post(
                "/api/v1/quotes",
                json=QUOTE_BODY,
                headers={"X-Api-Key": raw_key},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["buyer_tier"] == "agency", (
            "X-Api-Key header did not bind: buyer was treated as anonymous "
            f"(got tier {data['buyer_tier']!r})"
        )

    async def test_authorization_bearer_header_resolves_key_record(self, client, mock_storage):
        """A valid `Authorization: Bearer <key>` header must authenticate the buyer."""
        raw_key = _seed_key(mock_storage._store)
        catalog_patch, storage_patch = _patches(mock_storage)
        with catalog_patch, storage_patch:
            resp = await client.post(
                "/api/v1/quotes",
                json=QUOTE_BODY,
                headers={"Authorization": f"Bearer {raw_key}"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["buyer_tier"] == "agency", (
            "Authorization header did not bind: buyer was treated as anonymous "
            f"(got tier {data['buyer_tier']!r})"
        )

    async def test_invalid_key_rejected_with_401(self, client, mock_storage):
        """An unknown key must be rejected, not silently downgraded to anonymous."""
        catalog_patch, storage_patch = _patches(mock_storage)
        with catalog_patch, storage_patch:
            resp = await client.post(
                "/api/v1/quotes",
                json=QUOTE_BODY,
                headers={"X-Api-Key": "ask_live_definitely-not-a-real-key"},
            )

        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid API key"

    async def test_revoked_key_rejected_with_401(self, client, mock_storage):
        """A revoked key must be rejected per auth.dependencies semantics."""
        raw_key = _seed_key(mock_storage._store, revoked=True)
        catalog_patch, storage_patch = _patches(mock_storage)
        with catalog_patch, storage_patch:
            resp = await client.post(
                "/api/v1/quotes",
                json=QUOTE_BODY,
                headers={"X-Api-Key": raw_key},
            )

        assert resp.status_code == 401
        assert "revoked" in resp.json()["detail"]

    async def test_expired_key_rejected_with_401(self, client, mock_storage):
        """An expired key must be rejected per auth.dependencies semantics."""
        raw_key = _seed_key(mock_storage._store, expired=True)
        catalog_patch, storage_patch = _patches(mock_storage)
        with catalog_patch, storage_patch:
            resp = await client.post(
                "/api/v1/quotes",
                json=QUOTE_BODY,
                headers={"X-Api-Key": raw_key},
            )

        assert resp.status_code == 401
        assert "expired" in resp.json()["detail"]

    async def test_no_credentials_still_anonymous_public(self, client, mock_storage):
        """Requests without credentials keep the anonymous/PUBLIC path."""
        catalog_patch, storage_patch = _patches(mock_storage)
        with catalog_patch, storage_patch:
            resp = await client.post("/api/v1/quotes", json=QUOTE_BODY)

        assert resp.status_code == 200
        assert resp.json()["buyer_tier"] == "public"

    def test_auth_params_are_not_query_parameters(self):
        """The auth dependency must not expose authorization/x_api_key as query params."""
        from fastapi.routing import APIRoute

        for route in app.routes:
            if isinstance(route, APIRoute) and route.path == "/api/v1/quotes" and (
                "POST" in route.methods
            ):
                query_param_names = {p.name for p in route.dependant.query_params}
                for dep in route.dependant.dependencies:
                    query_param_names |= {p.name for p in dep.query_params}
                assert "authorization" not in query_param_names
                assert "x_api_key" not in query_param_names
                return
        pytest.fail("POST /api/v1/quotes route not found")

"""Package read views depend on validated credentials, not claimed query tiers."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from ad_seller.engines.media_kit_service import MediaKitService
from ad_seller.engines.pricing_rules_engine import PricingRulesEngine
from ad_seller.interfaces.api import deps
from ad_seller.interfaces.api.main import app
from ad_seller.models.api_key import (
    API_KEY_STORAGE_PREFIX,
    ApiKeyRecord,
    ApiKeyRole,
    generate_api_key,
    hash_api_key,
)
from ad_seller.models.audience_capabilities import AudienceCapabilities
from ad_seller.models.buyer_identity import BuyerIdentity
from ad_seller.models.media_kit import Package, PackageLayer, PackagePlacement, PackageStatus
from ad_seller.models.pricing_tiers import TieredPricingConfig

_PATHS = ["/packages", "/packages/pkg-auth"]
_CLAIMS = [
    {},
    {"buyer_tier": "public", "agency_id": "claimed-agency", "advertiser_id": "claimed-brand"},
    {"buyer_tier": "agency", "agency_id": "claimed-agency"},
    {"buyer_tier": "seat"},
    {"buyer_tier": "advertiser", "advertiser_id": "claimed-brand"},
    {"buyer_tier": "operator"},
    {"buyer_tier": "unknown"},
    {"buyer_tier": "Public"},
]


@pytest.fixture
async def package_client(monkeypatch):
    package = Package(
        package_id="pkg-auth",
        name="Package auth regression",
        layer=PackageLayer.CURATED,
        status=PackageStatus.ACTIVE,
        base_price=45.0,
        floor_price=38.25,
        placements=[PackagePlacement(product_id="product-1", product_name="Placement")],
        audience_capabilities=AudienceCapabilities(standard_segment_ids=["3-7"]),
    ).model_dump(mode="json")
    records = {}
    storage = AsyncMock()
    storage.get.side_effect = lambda key: records.get(key)
    storage.set.side_effect = lambda key, value, ttl=None: records.__setitem__(key, value)
    storage.list_packages.return_value = [package]
    storage.get_package.side_effect = lambda key: package if key == "pkg-auth" else None
    service = MediaKitService(
        storage=storage,
        pricing_engine=PricingRulesEngine(
            config=TieredPricingConfig(seller_organization_id="auth-test-seller")
        ),
    )
    monkeypatch.setattr("ad_seller.storage.factory.get_storage", AsyncMock(return_value=storage))
    monkeypatch.setattr(deps, "_get_media_kit_service", AsyncMock(return_value=service))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, records


def _seed_key(records, *, role=ApiKeyRole.BUYER, state="active"):
    raw = generate_api_key()
    key_hash = hash_api_key(raw)
    record = ApiKeyRecord(
        key_id="package-auth-key",
        key_hash=key_hash,
        key_prefix_hint="test-key",
        identity=BuyerIdentity(agency_id="verified-agency")
        if role == ApiKeyRole.BUYER
        else BuyerIdentity(),
        role=role,
        revoked=state == "revoked",
        expires_at=datetime.utcnow() - timedelta(hours=1) if state == "expired" else None,
    )
    records[f"{API_KEY_STORAGE_PREFIX}{key_hash}"] = record.model_dump(mode="json")
    return raw


def _package(response, path):
    assert response.status_code == 200
    body = response.json()
    return body["packages"][0] if path == "/packages" else body


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize("claims", _CLAIMS)
async def test_anonymous_claims_keep_the_public_view(package_client, path, claims):
    client, _ = package_client
    package = _package(await client.get(path, params=claims), path)
    assert package["price_range"] == "$36-$54 CPM"
    assert "exact_price" not in package
    assert "floor_price" not in package
    assert "placements" not in package
    assert "standard_segment_ids" not in package["audience_capabilities"]
    assert package["audience_capabilities"]["supports_standard"] is True


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize("header", ["X-Api-Key", "Authorization"])
@pytest.mark.parametrize("role", [ApiKeyRole.BUYER, ApiKeyRole.OPERATOR])
async def test_valid_keys_keep_authenticated_view(package_client, path, header, role):
    client, records = package_client
    raw = _seed_key(records, role=role)
    value = f"Bearer {raw}" if header == "Authorization" else raw
    package = _package(
        await client.get(
            path,
            params={"buyer_tier": "advertiser", "advertiser_id": "unverified-brand"},
            headers={header: value},
        ),
        path,
    )
    assert package["exact_price"] == (40.5 if role == ApiKeyRole.BUYER else 45.0)
    assert package["floor_price"] == 38.25
    assert package["placements"][0]["product_id"] == "product-1"
    assert package["audience_capabilities"]["standard_segment_ids"] == ["3-7"]


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize("state", ["invalid", "revoked", "expired"])
@pytest.mark.parametrize("header", ["X-Api-Key", "Authorization"])
async def test_rejected_keys_never_fall_back_to_public(package_client, path, state, header):
    client, records = package_client
    raw = "invalid-test-key" if state == "invalid" else _seed_key(records, state=state)
    value = f"Bearer {raw}" if header == "Authorization" else raw
    response = await client.get(path, params={"buyer_tier": "agency"}, headers={header: value})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("authenticated", [False, True])
async def test_layer_and_audience_filters_still_apply(package_client, authenticated):
    client, records = package_client
    headers = {"X-Api-Key": _seed_key(records)} if authenticated else {}
    for params, expected_count in [
        ({"layer": "curated", "audience_type": "standard", "audience_id": "3-7"}, 1),
        ({"audience_type": "standard", "audience_id": "no-match"}, 0),
    ]:
        response = await client.get(
            "/packages", params={"buyer_tier": "agency", **params}, headers=headers
        )
        assert response.status_code == 200
        assert len(response.json()["packages"]) == expected_count
    for params in [{"layer": "invalid"}, {"audience_type": "invalid"}]:
        response = await client.get("/packages", params=params, headers=headers)
        assert response.status_code == 400
    response = await client.get("/packages/missing", headers=headers)
    assert response.status_code == 404

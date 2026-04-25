# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Integration tests for `GET /packages` audience filter and `POST /media-kit/search`
audience corpus (proposal §5.7 + §6 row 10, bead ar-2wxa).

Drives the FastAPI app via httpx + ASGITransport, with the global storage
patched to an in-memory backend that we seed per-test. Mirrors the pattern
in `test_quote_endpoints.py` and `test_capability_audience_block.py`.

Coverage:

1. `GET /packages?audience_type=standard&audience_id=3-7` returns only
   packages whose AudienceCapabilities include "3-7".
2. `GET /packages?audience_type=contextual&audience_id=IAB1-2` returns only
   matching packages.
3. `GET /packages?audience_type=agentic` returns only packages whose
   `audience_capabilities.agentic_capabilities` is non-null (empty list when
   none exist -- not 404).
4. `GET /packages` (no audience params) returns all packages (backward
   compat).
5. `POST /media-kit/search` ranks audience-matching packages higher when
   the query mentions a known IAB segment ID.
6. `POST /media-kit/search` with no audience hint still works.
7. `POST /media-kit/search` with an `audience_filter` body field restricts
   to matching packages (additive; backward compat preserved by tests above).
"""

from __future__ import annotations

import sys
from types import ModuleType

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version
# mismatch) before importing main, mirroring test_quote_endpoints.py.
_broken_flows = [
    "ad_seller.flows.discovery_inquiry_flow",
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = (
            _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        )
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

from unittest.mock import AsyncMock, patch  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.interfaces.api.main import (  # noqa: E402
    _get_optional_api_key_record,
    app,
)
from ad_seller.models.audience_capabilities import (  # noqa: E402
    AgenticCapabilities,
    AudienceCapabilities,
)
from ad_seller.models.media_kit import (  # noqa: E402
    Package,
    PackageLayer,
    PackageStatus,
)


# =============================================================================
# Fixtures
# =============================================================================


def _make_package_dict(
    *,
    package_id: str,
    name: str = "Test Package",
    standard_segment_ids: list[str] | None = None,
    contextual_segment_ids: list[str] | None = None,
    standard_taxonomy_version: str = "1.1",
    contextual_taxonomy_version: str = "3.1",
    agentic_capabilities: AgenticCapabilities | None = None,
    tags: list[str] | None = None,
    cat: list[str] | None = None,
    is_featured: bool = False,
    base_price: float = 20.0,
    floor_price: float = 10.0,
    description: str | None = None,
) -> dict:
    pkg = Package(
        package_id=package_id,
        name=name,
        description=description or f"Description for {name}",
        layer=PackageLayer.CURATED,
        status=PackageStatus.ACTIVE,
        base_price=base_price,
        floor_price=floor_price,
        is_featured=is_featured,
        tags=tags or [],
        cat=cat or [],
        audience_capabilities=AudienceCapabilities(
            standard_segment_ids=standard_segment_ids or [],
            standard_taxonomy_version=standard_taxonomy_version,
            contextual_segment_ids=contextual_segment_ids or [],
            contextual_taxonomy_version=contextual_taxonomy_version,
            agentic_capabilities=agentic_capabilities,
        ),
    )
    return pkg.model_dump(mode="json")


@pytest.fixture
def seed_packages():
    """Four packages spanning the three types + one with no audience caps."""
    return [
        _make_package_dict(
            package_id="pkg-std",
            name="Standard Auto Intenders",
            standard_segment_ids=["3-7", "3-12"],
        ),
        _make_package_dict(
            package_id="pkg-ctx",
            name="Contextual Automotive",
            contextual_segment_ids=["IAB1-2"],
        ),
        _make_package_dict(
            package_id="pkg-agt",
            name="Agentic Premium",
            agentic_capabilities=AgenticCapabilities(
                supported_signal_types=["identity", "contextual"]
            ),
        ),
        _make_package_dict(
            package_id="pkg-none",
            name="Legacy Direct Response",
            tags=["direct response"],
        ),
    ]


@pytest.fixture
def mock_storage(seed_packages):
    """In-memory storage stub that returns the seed packages from list_packages()."""
    storage = AsyncMock()
    storage.list_packages = AsyncMock(return_value=seed_packages)
    return storage


@pytest.fixture
def patched_get_storage(mock_storage):
    """Patch `get_storage` so the API's MediaKitService talks to our stub."""
    with patch(
        "ad_seller.storage.factory.get_storage",
        new=AsyncMock(return_value=mock_storage),
    ):
        yield mock_storage


@pytest.fixture
def client(patched_get_storage):
    """httpx AsyncClient with FastAPI dependency overrides + storage patched."""
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# =============================================================================
# GET /packages — audience filter
# =============================================================================


class TestPackagesAudienceFilter:
    """Verify audience query params filter the result set."""

    @pytest.mark.asyncio
    async def test_filter_by_standard_id_returns_only_matching(self, client):
        async with client as c:
            resp = await c.get(
                "/packages",
                params={"audience_type": "standard", "audience_id": "3-7"},
            )
        assert resp.status_code == 200
        ids = [p["package_id"] for p in resp.json()["packages"]]
        assert ids == ["pkg-std"]

    @pytest.mark.asyncio
    async def test_filter_by_contextual_id(self, client):
        async with client as c:
            resp = await c.get(
                "/packages",
                params={
                    "audience_type": "contextual",
                    "audience_id": "IAB1-2",
                },
            )
        assert resp.status_code == 200
        ids = [p["package_id"] for p in resp.json()["packages"]]
        assert ids == ["pkg-ctx"]

    @pytest.mark.asyncio
    async def test_filter_agentic_type_only(self, client):
        async with client as c:
            resp = await c.get(
                "/packages", params={"audience_type": "agentic"}
            )
        assert resp.status_code == 200
        ids = [p["package_id"] for p in resp.json()["packages"]]
        assert ids == ["pkg-agt"]

    @pytest.mark.asyncio
    async def test_no_match_returns_empty_list_not_404(self, client):
        async with client as c:
            resp = await c.get(
                "/packages",
                params={
                    "audience_type": "standard",
                    "audience_id": "never-exists",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["packages"] == []

    @pytest.mark.asyncio
    async def test_backward_compat_no_audience_params_returns_all(self, client):
        async with client as c:
            resp = await c.get("/packages")
        assert resp.status_code == 200
        ids = {p["package_id"] for p in resp.json()["packages"]}
        assert ids == {"pkg-std", "pkg-ctx", "pkg-agt", "pkg-none"}

    @pytest.mark.asyncio
    async def test_invalid_audience_type_returns_400(self, client):
        async with client as c:
            resp = await c.get(
                "/packages", params={"audience_type": "bogus"}
            )
        assert resp.status_code == 400
        assert "audience_type" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_audience_id_without_type_returns_400(self, client):
        async with client as c:
            resp = await c.get(
                "/packages", params={"audience_id": "3-7"}
            )
        assert resp.status_code == 400
        assert "audience_type" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_taxonomy_version_constraint(self, client):
        """Mismatched version should drop the package even if ID matches."""
        async with client as c:
            resp = await c.get(
                "/packages",
                params={
                    "audience_type": "standard",
                    "audience_id": "3-7",
                    "audience_taxonomy_version": "9.9",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["packages"] == []


# =============================================================================
# GET /media-kit/packages — same audience filter (public surface)
# =============================================================================


class TestMediaKitPackagesAudienceFilter:
    """The public media-kit listing accepts the same audience triple."""

    @pytest.mark.asyncio
    async def test_public_listing_filtered_by_standard(self, client):
        async with client as c:
            resp = await c.get(
                "/media-kit/packages",
                params={"audience_type": "standard", "audience_id": "3-7"},
            )
        assert resp.status_code == 200
        ids = [p["package_id"] for p in resp.json()["packages"]]
        assert ids == ["pkg-std"]


# =============================================================================
# POST /media-kit/search — audience corpus + optional audience_filter
# =============================================================================


class TestSearchAudienceCorpus:
    """Search now scores against audience capability segment IDs."""

    @pytest.mark.asyncio
    async def test_query_with_segment_id_ranks_matching_first(
        self, client, patched_get_storage
    ):
        # Override seed: two packages share keyword "premium"; only one declares "3-7"
        patched_get_storage.list_packages.return_value = [
            _make_package_dict(
                package_id="pkg-with",
                name="Premium Auto",
                standard_segment_ids=["3-7"],
                tags=["premium"],
            ),
            _make_package_dict(
                package_id="pkg-without",
                name="Premium News",
                tags=["premium"],
            ),
        ]
        async with client as c:
            resp = await c.post(
                "/media-kit/search", json={"query": "premium 3-7"}
            )
        assert resp.status_code == 200
        ids = [p["package_id"] for p in resp.json()["results"]]
        # pkg-with hits both tokens; pkg-without hits only "premium".
        assert ids[0] == "pkg-with"
        assert "pkg-without" in ids

    @pytest.mark.asyncio
    async def test_keyword_only_search_backward_compat(
        self, client, patched_get_storage
    ):
        patched_get_storage.list_packages.return_value = [
            _make_package_dict(
                package_id="pkg-sports",
                name="Sports Bundle",
                tags=["sports", "live events"],
            ),
            _make_package_dict(
                package_id="pkg-news",
                name="News Bundle",
                tags=["news"],
            ),
        ]
        async with client as c:
            resp = await c.post("/media-kit/search", json={"query": "sports"})
        assert resp.status_code == 200
        ids = [p["package_id"] for p in resp.json()["results"]]
        assert ids == ["pkg-sports"]

    @pytest.mark.asyncio
    async def test_search_request_without_audience_filter_still_works(
        self, client, patched_get_storage
    ):
        """Existing buyer code that doesn't ship `audience_filter` keeps working."""
        patched_get_storage.list_packages.return_value = [
            _make_package_dict(
                package_id="pkg-a", name="Alpha Bundle", tags=["alpha"]
            ),
        ]
        async with client as c:
            resp = await c.post(
                "/media-kit/search",
                json={"query": "alpha", "buyer_tier": "public"},
            )
        assert resp.status_code == 200
        assert [p["package_id"] for p in resp.json()["results"]] == ["pkg-a"]

    @pytest.mark.asyncio
    async def test_search_with_audience_filter_restricts_results(
        self, client, patched_get_storage
    ):
        patched_get_storage.list_packages.return_value = [
            _make_package_dict(
                package_id="pkg-std",
                name="Premium Standard",
                standard_segment_ids=["3-7"],
                tags=["premium"],
            ),
            _make_package_dict(
                package_id="pkg-ctx",
                name="Premium Contextual",
                contextual_segment_ids=["IAB1-2"],
                tags=["premium"],
            ),
        ]
        async with client as c:
            # Without filter: both match "premium"
            resp_all = await c.post(
                "/media-kit/search", json={"query": "premium"}
            )
            assert {p["package_id"] for p in resp_all.json()["results"]} == {
                "pkg-std",
                "pkg-ctx",
            }

            # With filter restricted to contextual: only pkg-ctx
            resp_filtered = await c.post(
                "/media-kit/search",
                json={
                    "query": "premium",
                    "audience_filter": {"audience_type": "contextual"},
                },
            )
            assert resp_filtered.status_code == 200
            ids = [p["package_id"] for p in resp_filtered.json()["results"]]
            assert ids == ["pkg-ctx"]

    @pytest.mark.asyncio
    async def test_search_invalid_audience_filter_type_returns_400(
        self, client
    ):
        async with client as c:
            resp = await c.post(
                "/media-kit/search",
                json={
                    "query": "premium",
                    "audience_filter": {"audience_type": "bogus"},
                },
            )
        assert resp.status_code == 400

# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for the audience filter on `/packages` and the audience corpus
in `/media-kit/search` scoring (proposal §5.7 + §6 row 10, bead ar-2wxa).

Service-level coverage (no HTTP):

- `MediaKitService.list_packages_public/authenticated` accepts an optional
  `AudienceFilter` and returns only matching packages.
- `MediaKitService.search_packages` ranks audience-matching packages higher
  via the expanded corpus.
- Backward compat: callers that don't pass `audience_filter` see the same
  behavior they did before this bead.

HTTP-level coverage for the endpoint surface lives in
`test_packages_audience_filter_endpoint.py` (integration) -- this file
exercises the service directly so service regressions are caught even if
endpoint plumbing breaks.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ad_seller.engines.media_kit_service import (
    AudienceFilter,
    MediaKitService,
)
from ad_seller.engines.pricing_rules_engine import PricingRulesEngine
from ad_seller.models.audience_capabilities import (
    AgenticCapabilities,
    AudienceCapabilities,
)
from ad_seller.models.media_kit import (
    Package,
    PackageLayer,
    PackageStatus,
)
from ad_seller.models.pricing_tiers import TieredPricingConfig

# =============================================================================
# Fixtures
# =============================================================================


def _make_package(
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
) -> dict:
    """Build a Package as a storage-shape dict (matches what list_packages returns)."""
    pkg = Package(
        package_id=package_id,
        name=name,
        description=f"Description for {name}",
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
def pricing_engine():
    config = TieredPricingConfig(seller_organization_id="test-seller")
    return PricingRulesEngine(config=config)


@pytest.fixture
def mock_storage():
    return AsyncMock()


@pytest.fixture
def service(mock_storage, pricing_engine):
    return MediaKitService(storage=mock_storage, pricing_engine=pricing_engine)


@pytest.fixture
def mixed_packages():
    """Three packages spanning the three audience types.

    - pkg-std: standard segments only ("3-7", "3-12")
    - pkg-ctx: contextual segments only ("IAB1-2", "IAB1-3")
    - pkg-agt: agentic capabilities populated
    - pkg-none: no audience capabilities (legacy / direct response, etc.)
    """
    return [
        _make_package(
            package_id="pkg-std",
            name="Standard Auto Intenders",
            standard_segment_ids=["3-7", "3-12"],
        ),
        _make_package(
            package_id="pkg-ctx",
            name="Contextual Automotive",
            contextual_segment_ids=["IAB1-2", "IAB1-3"],
        ),
        _make_package(
            package_id="pkg-agt",
            name="Agentic Premium",
            agentic_capabilities=AgenticCapabilities(
                supported_signal_types=["identity", "contextual"],
                spec_version="draft-2026-01",
            ),
        ),
        _make_package(
            package_id="pkg-none",
            name="Legacy Direct Response",
            tags=["direct response"],
        ),
    ]


# =============================================================================
# AudienceFilter.matches() — predicate semantics
# =============================================================================


class TestAudienceFilterPredicate:
    """Cover the matching predicate without going through storage."""

    def test_empty_filter_matches_everything(self):
        f = AudienceFilter()
        assert f.is_empty()
        # Build a Package via dict round-trip (we only care about the predicate)
        pkg = Package(**_make_package(package_id="pkg-x"))
        assert f.matches(pkg) is True

    def test_standard_id_match(self):
        f = AudienceFilter(audience_type="standard", audience_id="3-7")
        pkg_match = Package(
            **_make_package(package_id="p1", standard_segment_ids=["3-7"])
        )
        pkg_no_match = Package(
            **_make_package(package_id="p2", standard_segment_ids=["3-12"])
        )
        assert f.matches(pkg_match) is True
        assert f.matches(pkg_no_match) is False

    def test_standard_type_only_requires_any_segment(self):
        """Type-only filter passes any package with non-empty standard list."""
        f = AudienceFilter(audience_type="standard")
        pkg_with = Package(
            **_make_package(package_id="p1", standard_segment_ids=["3-7"])
        )
        pkg_without = Package(**_make_package(package_id="p2"))
        assert f.matches(pkg_with) is True
        assert f.matches(pkg_without) is False

    def test_contextual_id_match(self):
        f = AudienceFilter(audience_type="contextual", audience_id="IAB1-2")
        pkg_match = Package(
            **_make_package(package_id="p1", contextual_segment_ids=["IAB1-2"])
        )
        pkg_no_match = Package(
            **_make_package(package_id="p2", contextual_segment_ids=["IAB2-3"])
        )
        assert f.matches(pkg_match) is True
        assert f.matches(pkg_no_match) is False

    def test_agentic_supported_predicate(self):
        """For agentic, presence of agentic_capabilities is the gate."""
        f = AudienceFilter(audience_type="agentic")
        pkg_with = Package(
            **_make_package(
                package_id="p1",
                agentic_capabilities=AgenticCapabilities(
                    supported_signal_types=["identity"]
                ),
            )
        )
        pkg_without = Package(**_make_package(package_id="p2"))
        assert f.matches(pkg_with) is True
        assert f.matches(pkg_without) is False

    def test_agentic_with_id_still_supported_predicate(self):
        """Per ar-2wxa: agentic per-segment matching is §11; type-only gate."""
        f = AudienceFilter(
            audience_type="agentic", audience_id="emb://example.com/x"
        )
        pkg_with = Package(
            **_make_package(
                package_id="p1",
                agentic_capabilities=AgenticCapabilities(),
            )
        )
        assert f.matches(pkg_with) is True

    def test_taxonomy_version_constraint_standard(self):
        f = AudienceFilter(
            audience_type="standard",
            audience_id="3-7",
            taxonomy_version="1.1",
        )
        pkg_match = Package(
            **_make_package(
                package_id="p1",
                standard_segment_ids=["3-7"],
                standard_taxonomy_version="1.1",
            )
        )
        pkg_wrong_version = Package(
            **_make_package(
                package_id="p2",
                standard_segment_ids=["3-7"],
                standard_taxonomy_version="2.0",
            )
        )
        assert f.matches(pkg_match) is True
        assert f.matches(pkg_wrong_version) is False

    def test_id_without_type_returns_false(self):
        """Defense-in-depth: ID without type can't disambiguate corpus."""
        f = AudienceFilter(audience_id="3-7")
        pkg = Package(**_make_package(package_id="p1", standard_segment_ids=["3-7"]))
        assert f.matches(pkg) is False


# =============================================================================
# Service-level: list_packages_public/authenticated with audience_filter
# =============================================================================


class TestListPackagesAudienceFilter:
    """Exercise the service path that the endpoints call."""

    @pytest.mark.asyncio
    async def test_filter_by_standard_id(
        self, service, mock_storage, mixed_packages
    ):
        mock_storage.list_packages.return_value = mixed_packages
        filt = AudienceFilter(audience_type="standard", audience_id="3-7")
        results = await service.list_packages_public(audience_filter=filt)
        ids = [r.package_id for r in results]
        assert ids == ["pkg-std"]

    @pytest.mark.asyncio
    async def test_filter_by_contextual_id(
        self, service, mock_storage, mixed_packages
    ):
        mock_storage.list_packages.return_value = mixed_packages
        filt = AudienceFilter(audience_type="contextual", audience_id="IAB1-2")
        results = await service.list_packages_public(audience_filter=filt)
        ids = [r.package_id for r in results]
        assert ids == ["pkg-ctx"]

    @pytest.mark.asyncio
    async def test_filter_by_agentic_type_only(
        self, service, mock_storage, mixed_packages
    ):
        mock_storage.list_packages.return_value = mixed_packages
        filt = AudienceFilter(audience_type="agentic")
        results = await service.list_packages_public(audience_filter=filt)
        ids = [r.package_id for r in results]
        assert ids == ["pkg-agt"]

    @pytest.mark.asyncio
    async def test_filter_with_no_match_returns_empty_list(
        self, service, mock_storage, mixed_packages
    ):
        mock_storage.list_packages.return_value = mixed_packages
        filt = AudienceFilter(
            audience_type="standard", audience_id="never-exists"
        )
        results = await service.list_packages_public(audience_filter=filt)
        assert results == []

    @pytest.mark.asyncio
    async def test_no_filter_returns_all_active_packages(
        self, service, mock_storage, mixed_packages
    ):
        """Backward compat: no filter -> every package is returned."""
        mock_storage.list_packages.return_value = mixed_packages
        results = await service.list_packages_public()
        assert {r.package_id for r in results} == {
            "pkg-std",
            "pkg-ctx",
            "pkg-agt",
            "pkg-none",
        }

    @pytest.mark.asyncio
    async def test_filter_combined_with_layer(
        self, service, mock_storage, mixed_packages
    ):
        """Audience filter composes with the existing layer filter."""
        mock_storage.list_packages.return_value = mixed_packages
        filt = AudienceFilter(audience_type="standard")
        results = await service.list_packages_public(
            layer=PackageLayer.CURATED, audience_filter=filt
        )
        assert [r.package_id for r in results] == ["pkg-std"]

    @pytest.mark.asyncio
    async def test_authenticated_view_respects_filter(
        self, service, mock_storage, mixed_packages
    ):
        from ad_seller.models.buyer_identity import (
            BuyerContext,
            BuyerIdentity,
        )

        mock_storage.list_packages.return_value = mixed_packages
        ctx = BuyerContext(
            identity=BuyerIdentity(agency_id="a1", agency_name="A"),
            is_authenticated=True,
        )
        filt = AudienceFilter(audience_type="contextual", audience_id="IAB1-2")
        results = await service.list_packages_authenticated(
            ctx, audience_filter=filt
        )
        assert [r.package_id for r in results] == ["pkg-ctx"]


# =============================================================================
# Service-level: search_packages corpus + audience_filter
# =============================================================================


class TestSearchAudienceCorpus:
    """The audience corpus is part of `_score_package`'s text bag."""

    @pytest.mark.asyncio
    async def test_search_ranks_audience_match_higher(
        self, service, mock_storage
    ):
        """A query mentioning a segment ID prefers packages declaring that ID."""
        # pkg-with declares "3-7" in its standard segments; pkg-without doesn't.
        # Both share the keyword "premium" so neither scores zero.
        mock_storage.list_packages.return_value = [
            _make_package(
                package_id="pkg-with",
                name="Premium Auto",
                standard_segment_ids=["3-7"],
                tags=["premium"],
            ),
            _make_package(
                package_id="pkg-without",
                name="Premium News",
                tags=["premium"],
            ),
        ]
        # Query that mentions both the keyword and the segment ID.
        results = await service.search_packages("premium 3-7")
        ids = [r.package_id for r in results]
        # pkg-with should rank first because it matches both tokens
        # ("premium" via tag, "3-7" via standard_segment_ids).
        assert ids[0] == "pkg-with"
        assert "pkg-without" in ids

    @pytest.mark.asyncio
    async def test_search_finds_by_contextual_segment_id(
        self, service, mock_storage
    ):
        """A query that's ONLY a contextual ID still matches a package that declares it."""
        mock_storage.list_packages.return_value = [
            _make_package(
                package_id="pkg-ctx",
                name="Ctx Pkg",
                contextual_segment_ids=["IAB1-2"],
            ),
            _make_package(
                package_id="pkg-other",
                name="Other Pkg",
                tags=["sports"],
            ),
        ]
        results = await service.search_packages("iab1-2")
        ids = [r.package_id for r in results]
        assert ids == ["pkg-ctx"]

    @pytest.mark.asyncio
    async def test_keyword_only_search_still_works(self, service, mock_storage):
        """Backward compat: a keyword-only query against keyword-only data
        returns the right package."""
        mock_storage.list_packages.return_value = [
            _make_package(
                package_id="pkg-sports",
                name="Sports Bundle",
                tags=["sports", "live events"],
            ),
            _make_package(
                package_id="pkg-news",
                name="News Bundle",
                tags=["news"],
            ),
        ]
        results = await service.search_packages("sports")
        ids = [r.package_id for r in results]
        assert ids == ["pkg-sports"]

    @pytest.mark.asyncio
    async def test_audience_filter_restricts_search_results(
        self, service, mock_storage
    ):
        """Optional audience_filter narrows search even if keyword would match."""
        mock_storage.list_packages.return_value = [
            _make_package(
                package_id="pkg-std",
                name="Premium Auto",
                standard_segment_ids=["3-7"],
                tags=["premium"],
            ),
            _make_package(
                package_id="pkg-ctx",
                name="Premium Auto Content",
                contextual_segment_ids=["IAB1-2"],
                tags=["premium"],
            ),
        ]
        # Without filter: both packages match "premium"
        all_results = await service.search_packages("premium")
        assert {r.package_id for r in all_results} == {"pkg-std", "pkg-ctx"}

        # With filter restricted to contextual: only pkg-ctx returns
        filt = AudienceFilter(audience_type="contextual")
        filtered = await service.search_packages("premium", audience_filter=filt)
        assert [r.package_id for r in filtered] == ["pkg-ctx"]

    @pytest.mark.asyncio
    async def test_search_without_filter_is_unchanged(self, service, mock_storage):
        """Backward compat: search() accepts no audience_filter param."""
        mock_storage.list_packages.return_value = [
            _make_package(package_id="pkg-a", name="Alpha", tags=["alpha"]),
        ]
        results = await service.search_packages("alpha")
        assert [r.package_id for r in results] == ["pkg-a"]

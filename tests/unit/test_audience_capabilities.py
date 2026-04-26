# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for typed `AudienceCapabilities` on `Package` (proposal §5.7).

Covers:
- AudienceCapabilities/AgenticCapabilities default construction
- AudienceRef + ComplianceContext validators (agentic requires
  compliance_context; explicit must not carry confidence)
- Legacy `Package(audience_segment_ids=[...])` migration to typed shape
- New typed-shape input passthrough
- PublicPackageView excludes segment lists (capability metadata only)
- AuthenticatedPackageView includes the full capability object
- Round-trip via model_dump -> Package(**data)

Bead: ar-roi5.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from ad_seller.engines.media_kit_service import MediaKitService
from ad_seller.engines.pricing_rules_engine import PricingRulesEngine
from ad_seller.models.audience_capabilities import (
    AgenticCapabilities,
    AudienceCapabilities,
)
from ad_seller.models.audience_ref import AudienceRef, ComplianceContext
from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity
from ad_seller.models.media_kit import (
    AudienceCapabilityPublicSummary,
    AuthenticatedPackageView,
    Package,
    PackageLayer,
    PackageStatus,
    PublicPackageView,
)
from ad_seller.models.pricing_tiers import TieredPricingConfig


# =============================================================================
# AudienceCapabilities / AgenticCapabilities construction
# =============================================================================


class TestAudienceCapabilitiesConstruction:
    """Default construction and field defaults."""

    def test_default_construction(self):
        """AudienceCapabilities() builds cleanly with field defaults."""
        caps = AudienceCapabilities()
        assert caps.standard_segment_ids == []
        assert caps.standard_taxonomy_version == "1.1"
        assert caps.contextual_segment_ids == []
        assert caps.contextual_taxonomy_version == "3.1"
        assert caps.agentic_capabilities is None

    def test_with_standard_segments(self):
        caps = AudienceCapabilities(standard_segment_ids=["3", "4"])
        assert caps.standard_segment_ids == ["3", "4"]
        assert caps.standard_taxonomy_version == "1.1"

    def test_with_contextual_segments(self):
        caps = AudienceCapabilities(contextual_segment_ids=["IAB1-2"])
        assert caps.contextual_segment_ids == ["IAB1-2"]
        assert caps.contextual_taxonomy_version == "3.1"

    def test_with_agentic(self):
        caps = AudienceCapabilities(
            agentic_capabilities=AgenticCapabilities(
                supported_signal_types=["identity", "contextual"],
                consent_modes=["IAB-TCFv2"],
            )
        )
        assert caps.agentic_capabilities is not None
        assert caps.agentic_capabilities.spec_version == "draft-2026-01"
        assert caps.agentic_capabilities.embedding_dim_range == (256, 1024)

    def test_agentic_defaults(self):
        """AgenticCapabilities() agentic-only fields default sensibly."""
        a = AgenticCapabilities()
        assert a.supported_signal_types == []
        assert a.embedding_dim_range == (256, 1024)
        assert a.spec_version == "draft-2026-01"
        assert a.consent_modes == []


# =============================================================================
# AudienceRef + ComplianceContext validators
# =============================================================================


class TestAudienceRef:
    """Wire-format validators on AudienceRef."""

    def test_standard_explicit(self):
        ref = AudienceRef(
            type="standard",
            identifier="3-7",
            taxonomy="iab-audience",
            version="1.1",
            source="explicit",
        )
        assert ref.confidence is None
        assert ref.compliance_context is None

    def test_resolved_with_confidence(self):
        ref = AudienceRef(
            type="contextual",
            identifier="IAB1-2",
            taxonomy="iab-content",
            version="3.1",
            source="resolved",
            confidence=0.92,
        )
        assert ref.confidence == 0.92

    def test_explicit_with_confidence_rejected(self):
        """confidence MUST be None when source='explicit' (wire spec rule)."""
        with pytest.raises(ValueError, match="confidence must be None"):
            AudienceRef(
                type="standard",
                identifier="3-7",
                taxonomy="iab-audience",
                version="1.1",
                source="explicit",
                confidence=0.9,
            )

    def test_agentic_requires_compliance_context(self):
        """type='agentic' MUST carry a compliance_context (wire spec rule)."""
        with pytest.raises(ValueError, match="compliance_context is required"):
            AudienceRef(
                type="agentic",
                identifier="emb://buyer.example.com/auds/x",
                taxonomy="agentic-audiences",
                version="draft-2026-01",
                source="explicit",
            )

    def test_agentic_with_compliance_context(self):
        ref = AudienceRef(
            type="agentic",
            identifier="emb://buyer.example.com/auds/x",
            taxonomy="agentic-audiences",
            version="draft-2026-01",
            source="explicit",
            compliance_context=ComplianceContext(
                jurisdiction="US",
                consent_framework="IAB-TCFv2",
            ),
        )
        assert ref.compliance_context is not None
        assert ref.compliance_context.jurisdiction == "US"

    def test_compliance_context_required_fields(self):
        cc = ComplianceContext(jurisdiction="EU", consent_framework="GPP")
        assert cc.consent_string_ref is None
        assert cc.attestation is None


# =============================================================================
# Package legacy migration shim
# =============================================================================


def _basic_package_kwargs(**overrides):
    """Minimum-required kwargs for a Package, with overrides."""
    base = {
        "package_id": "pkg-test",
        "name": "Test Pkg",
        "layer": PackageLayer.CURATED,
        "base_price": 20.0,
        "floor_price": 10.0,
    }
    base.update(overrides)
    return base


class TestLegacyMigrationShim:
    """`audience_segment_ids: list[str]` -> typed AudienceCapabilities."""

    def test_legacy_input_migrates(self, caplog):
        """Legacy flat field rewrites to capabilities, AT 1.1, INFO log."""
        with caplog.at_level(logging.INFO, logger="ad_seller.audience.migration"):
            pkg = Package(
                **_basic_package_kwargs(audience_segment_ids=["3", "4"])
            )

        assert isinstance(pkg.audience_capabilities, AudienceCapabilities)
        assert pkg.audience_capabilities.standard_segment_ids == ["3", "4"]
        assert pkg.audience_capabilities.standard_taxonomy_version == "1.1"
        # Legacy field is gone -- no shadow attribute survives migration.
        assert not hasattr(pkg, "audience_segment_ids")
        # Migration was logged at INFO on the migration logger.
        assert any(
            "Migrated legacy audience_segment_ids" in r.message
            for r in caplog.records
        )

    def test_legacy_empty_list_migrates(self):
        """Empty legacy list still migrates -- preserves emptiness."""
        pkg = Package(**_basic_package_kwargs(audience_segment_ids=[]))
        assert pkg.audience_capabilities.standard_segment_ids == []
        assert pkg.audience_capabilities.standard_taxonomy_version == "1.1"

    def test_new_shape_passthrough(self):
        """New typed input is preserved byte-for-byte."""
        caps = AudienceCapabilities(
            standard_segment_ids=["5", "6"],
            contextual_segment_ids=["IAB19"],
            agentic_capabilities=AgenticCapabilities(
                supported_signal_types=["identity"]
            ),
        )
        pkg = Package(**_basic_package_kwargs(audience_capabilities=caps))
        assert pkg.audience_capabilities.standard_segment_ids == ["5", "6"]
        assert pkg.audience_capabilities.contextual_segment_ids == ["IAB19"]
        assert pkg.audience_capabilities.agentic_capabilities is not None
        assert (
            pkg.audience_capabilities.agentic_capabilities.supported_signal_types
            == ["identity"]
        )

    def test_both_fields_caps_wins(self, caplog):
        """If caller sends both, audience_capabilities wins; legacy dropped."""
        explicit = AudienceCapabilities(standard_segment_ids=["99"])
        with caplog.at_level(logging.INFO, logger="ad_seller.audience.migration"):
            pkg = Package(
                **_basic_package_kwargs(
                    audience_segment_ids=["3", "4"],
                    audience_capabilities=explicit,
                )
            )
        assert pkg.audience_capabilities.standard_segment_ids == ["99"]
        # Drop is logged.
        assert any("Dropping legacy" in r.message for r in caplog.records)

    def test_default_no_audience_input(self):
        """Package with no audience input gets default AudienceCapabilities()."""
        pkg = Package(**_basic_package_kwargs())
        assert isinstance(pkg.audience_capabilities, AudienceCapabilities)
        assert pkg.audience_capabilities.standard_segment_ids == []

    def test_dict_input_legacy_migrates(self):
        """Dict input (e.g., from SQLite row) with legacy field migrates."""
        data = {
            "package_id": "pkg-from-db",
            "name": "Old Row",
            "layer": "curated",
            "status": "active",
            "base_price": 15.0,
            "floor_price": 8.0,
            "audience_segment_ids": ["3", "4", "5"],
        }
        pkg = Package(**data)
        assert pkg.audience_capabilities.standard_segment_ids == ["3", "4", "5"]


# =============================================================================
# Round-trip via model_dump
# =============================================================================


class TestRoundTrip:
    """`Package` survives serialize/deserialize cycle."""

    def test_roundtrip_legacy_then_dump(self):
        """Legacy in -> typed out -> dump -> reconstruct -> still typed."""
        pkg = Package(
            **_basic_package_kwargs(audience_segment_ids=["3", "4"])
        )
        data = pkg.model_dump(mode="json")
        # Dumped form is the new typed shape, not the legacy field.
        assert "audience_capabilities" in data
        assert "audience_segment_ids" not in data
        assert data["audience_capabilities"]["standard_segment_ids"] == ["3", "4"]
        # Reconstruct.
        pkg2 = Package(**data)
        assert pkg2.audience_capabilities.standard_segment_ids == ["3", "4"]

    def test_roundtrip_with_agentic(self):
        caps = AudienceCapabilities(
            standard_segment_ids=["10"],
            agentic_capabilities=AgenticCapabilities(
                supported_signal_types=["identity", "reinforcement"],
                consent_modes=["IAB-TCFv2", "advertiser-1p"],
            ),
        )
        pkg = Package(**_basic_package_kwargs(audience_capabilities=caps))
        data = pkg.model_dump(mode="json")
        pkg2 = Package(**data)
        assert pkg2.audience_capabilities.agentic_capabilities is not None
        assert pkg2.audience_capabilities.agentic_capabilities.supported_signal_types == [
            "identity",
            "reinforcement",
        ]


# =============================================================================
# Public vs Authenticated views
# =============================================================================


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


def _authenticated_buyer_context() -> BuyerContext:
    """Build a buyer context whose effective_tier resolves to authenticated."""
    return BuyerContext(
        identity=BuyerIdentity(api_key="test", agency_id="agcy-1"),
    )


class TestPublicViewExcludesSegmentLists:
    """`PublicPackageView` is capability metadata only -- no segment lists."""

    def test_public_view_no_segment_lists(self, service):
        caps = AudienceCapabilities(
            standard_segment_ids=["3", "4"],
            contextual_segment_ids=["IAB19"],
            agentic_capabilities=AgenticCapabilities(
                supported_signal_types=["identity"]
            ),
        )
        pkg = Package(
            **_basic_package_kwargs(
                status=PackageStatus.ACTIVE,
                audience_capabilities=caps,
            )
        )
        view = service._to_public_view(pkg)
        assert isinstance(view, PublicPackageView)
        # The audience_capabilities on the public view is the public summary
        # type -- versions + supports flags only.
        assert isinstance(view.audience_capabilities, AudienceCapabilityPublicSummary)
        # Most importantly: no segment lists exposed at this tier.
        dumped = view.model_dump()
        assert "standard_segment_ids" not in dumped["audience_capabilities"]
        assert "contextual_segment_ids" not in dumped["audience_capabilities"]
        # Capability metadata is exposed.
        assert dumped["audience_capabilities"]["supports_standard"] is True
        assert dumped["audience_capabilities"]["supports_contextual"] is True
        assert dumped["audience_capabilities"]["supports_agentic"] is True
        assert dumped["audience_capabilities"]["standard_taxonomy_version"] == "1.1"
        assert dumped["audience_capabilities"]["contextual_taxonomy_version"] == "3.1"
        assert (
            dumped["audience_capabilities"]["agentic_spec_version"] == "draft-2026-01"
        )

    def test_public_view_empty_capabilities(self, service):
        """A package with no audience config still produces a clean summary."""
        pkg = Package(**_basic_package_kwargs(status=PackageStatus.ACTIVE))
        view = service._to_public_view(pkg)
        assert view.audience_capabilities.supports_standard is False
        assert view.audience_capabilities.supports_contextual is False
        assert view.audience_capabilities.supports_agentic is False
        assert view.audience_capabilities.agentic_spec_version is None


class TestAuthenticatedViewIncludesCapabilities:
    """`AuthenticatedPackageView` exposes the full typed capability object."""

    def test_authenticated_view_includes_full_capability(self, service):
        caps = AudienceCapabilities(
            standard_segment_ids=["3", "4", "5"],
            contextual_segment_ids=["IAB1-2"],
            agentic_capabilities=AgenticCapabilities(
                supported_signal_types=["identity"]
            ),
        )
        pkg = Package(
            **_basic_package_kwargs(
                status=PackageStatus.ACTIVE,
                audience_capabilities=caps,
            )
        )
        view = service._to_authenticated_view(pkg, _authenticated_buyer_context())

        assert isinstance(view, AuthenticatedPackageView)
        # The authenticated view carries the full AudienceCapabilities object.
        assert isinstance(view.audience_capabilities, AudienceCapabilities)
        assert view.audience_capabilities.standard_segment_ids == ["3", "4", "5"]
        assert view.audience_capabilities.contextual_segment_ids == ["IAB1-2"]
        assert view.audience_capabilities.agentic_capabilities is not None

    def test_authenticated_view_dump_has_segment_lists(self, service):
        caps = AudienceCapabilities(standard_segment_ids=["7", "8"])
        pkg = Package(
            **_basic_package_kwargs(
                status=PackageStatus.ACTIVE,
                audience_capabilities=caps,
            )
        )
        view = service._to_authenticated_view(pkg, _authenticated_buyer_context())
        dumped = view.model_dump()
        # Segment lists ARE exposed at this tier.
        assert dumped["audience_capabilities"]["standard_segment_ids"] == ["7", "8"]

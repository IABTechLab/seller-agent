"""E2-6: modern_default() agentic capability declaration tests."""

import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")

from ad_seller.models.audience_capabilities import (
    AgenticCapabilities,
    AudienceCapabilities,
)


class TestModernDefault:
    def test_modern_default_returns_expected_shape(self):
        caps = AgenticCapabilities.modern_default()
        assert caps.supported_signal_types == [
            "identity",
            "contextual",
            "reinforcement",
        ]
        assert caps.embedding_dim_range == (256, 1024)
        assert caps.spec_version == "draft-2026-01"
        assert "IAB-TCFv2" in caps.consent_modes

    def test_modern_default_covers_buyer_local_dim(self):
        # Buyer's local sentence-transformers model is 384-dim. Seller's
        # modern_default should accept it.
        caps = AgenticCapabilities.modern_default()
        lo, hi = caps.embedding_dim_range
        assert lo <= 384 <= hi

    def test_default_audience_capabilities_has_no_agentic(self):
        # Backward compat: AudienceCapabilities() default leaves agentic null.
        ac = AudienceCapabilities()
        assert ac.agentic_capabilities is None

    def test_audience_capabilities_with_modern_agentic(self):
        # Sellers opt in by setting modern_default on the package.
        ac = AudienceCapabilities(
            agentic_capabilities=AgenticCapabilities.modern_default(),
        )
        assert ac.agentic_capabilities is not None
        assert ac.agentic_capabilities.spec_version == "draft-2026-01"
        assert "identity" in ac.agentic_capabilities.supported_signal_types

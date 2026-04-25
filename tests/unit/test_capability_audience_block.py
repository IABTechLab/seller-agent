# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for the `audience_capabilities` block on capability discovery.

Covers proposal §5.7 layer 1 + §6 row 9 (bead ar-2sip):

- The capability discovery response (`GET /.well-known/agent.json`) carries
  the new `audience_capabilities` block.
- `taxonomy_lock_hashes` is loaded DYNAMICALLY from
  `data/taxonomies/taxonomies.lock.json` -- never hard-coded. Verified by
  pointing the loader at a fixture file with patched hashes.
- Demo defaults match the locked-in decisions: `schema_version="1"`,
  `agentic.supported=False`, `supports_extensions=False`,
  `supports_exclusions=False`, `supports_constraints=True`,
  `max_refs_per_role=(1/3/0/0)`.
- The block is itself a valid `CapabilityAudienceBlock` (Pydantic-roundtrips).
- Existing `AgentCard` callers that don't ship the block still validate
  (backward-compatible additive field).

Tests use the loader's `lock_path=` injection rather than the cached default
path so they don't conflict with concurrent tests touching the same cache.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version
# mismatch) before importing main, mirroring the pattern in
# test_quote_endpoints.py.
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

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.interfaces.api.main import (  # noqa: E402
    _get_optional_api_key_record,
    app,
)
from ad_seller.models.agent_registry import AgentCard  # noqa: E402
from ad_seller.models.audience_capabilities import (  # noqa: E402
    AgenticCapabilityFlag,
    CapabilityAudienceBlock,
    MaxRefsPerRole,
    TaxonomyLockHashes,
    build_capability_audience_block,
    load_taxonomy_lock_hashes,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def real_lock_path() -> Path:
    """Absolute path to the vendored taxonomy lock file."""

    # tests/unit/test_capability_audience_block.py -> project root
    return (
        Path(__file__).resolve().parents[2]
        / "data"
        / "taxonomies"
        / "taxonomies.lock.json"
    )


@pytest.fixture
def real_lock_data(real_lock_path: Path) -> dict:
    """Parsed contents of the real lock file (read once per test)."""

    return json.loads(real_lock_path.read_text())


@pytest.fixture
def fake_lock_path(tmp_path: Path) -> Path:
    """Build a synthetic lock file with known hashes for drift-detection tests.

    Uses distinctive marker hashes so we can prove the block was sourced from
    THIS file and not the real lock file (or any hard-coded constant).
    """

    payload = {
        "schema_version": "1",
        "audience": {
            "version": "1.1",
            "source": "test://audience",
            "path": "audience-1.1/x.tsv",
            "sha256": "deadbeef" * 8,  # 64-char marker
            "fetched_at": "2026-04-25T00:00:00Z",
            "license": "CC-BY-3.0",
            "license_url": "https://creativecommons.org/licenses/by/3.0/",
            "format": "tsv",
        },
        "content": {
            "version": "3.1",
            "source": "test://content",
            "path": "content-3.1/y.tsv",
            "sha256": "cafef00d" * 8,
            "fetched_at": "2026-04-25T00:00:00Z",
            "license": "CC-BY-3.0",
            "license_url": "https://creativecommons.org/licenses/by/3.0/",
            "format": "tsv",
        },
        "agentic": {
            "version": "draft-2026-01",
            "spec_url": "test://agentic",
            "source": "test://agentic",
            "path": "agentic-audiences-draft-2026-01/spec",
            "sha256": "0" * 64,
            "fetched_at": "2026-04-25T00:00:00Z",
            "license": "CC-BY-4.0",
            "license_url_spec": "https://creativecommons.org/licenses/by/4.0/",
            "license_url_impl": "https://www.apache.org/licenses/LICENSE-2.0",
            "format": "directory",
            "files": {},
        },
    }
    path = tmp_path / "taxonomies.lock.json"
    path.write_text(json.dumps(payload))
    return path


@pytest.fixture
def client():
    """httpx AsyncClient with FastAPI dependency overrides."""

    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# =============================================================================
# CapabilityAudienceBlock model construction & defaults
# =============================================================================


class TestCapabilityAudienceBlockDefaults:
    """Demo / MVP defaults match the locked-in decisions in the proposal."""

    def test_schema_version_is_one(self, real_lock_path):
        block = build_capability_audience_block(lock_path=real_lock_path)
        assert block.schema_version == "1"

    def test_agentic_unsupported_by_default(self, real_lock_path):
        """The seller hasn't gained match support yet -- that's §11."""
        block = build_capability_audience_block(lock_path=real_lock_path)
        assert block.agentic.supported is False

    def test_supports_constraints_true(self, real_lock_path):
        """Seller can filter (filter implementation lands in §10)."""
        block = build_capability_audience_block(lock_path=real_lock_path)
        assert block.supports_constraints is True

    def test_supports_extensions_false(self, real_lock_path):
        block = build_capability_audience_block(lock_path=real_lock_path)
        assert block.supports_extensions is False

    def test_supports_exclusions_false(self, real_lock_path):
        block = build_capability_audience_block(lock_path=real_lock_path)
        assert block.supports_exclusions is False

    def test_max_refs_per_role_demo_defaults(self, real_lock_path):
        block = build_capability_audience_block(lock_path=real_lock_path)
        assert block.max_refs_per_role.primary == 1
        assert block.max_refs_per_role.constraints == 3
        assert block.max_refs_per_role.extensions == 0
        assert block.max_refs_per_role.exclusions == 0

    def test_taxonomy_versions_from_lock_file(
        self, real_lock_path, real_lock_data
    ):
        """Taxonomy versions in the block match the lock file -- not hard-coded."""
        block = build_capability_audience_block(lock_path=real_lock_path)
        assert block.standard_taxonomy_versions == [
            real_lock_data["audience"]["version"]
        ]
        assert block.contextual_taxonomy_versions == [
            real_lock_data["content"]["version"]
        ]


# =============================================================================
# taxonomy_lock_hashes are loaded dynamically (NOT hard-coded)
# =============================================================================


class TestTaxonomyLockHashesAreDynamic:
    """taxonomy_lock_hashes must be sourced from the lock file at call time."""

    def test_real_lock_file_hashes_match(self, real_lock_path, real_lock_data):
        """Hashes in the block match what's in the real lock file."""
        block = build_capability_audience_block(lock_path=real_lock_path)
        expected_audience = f"sha256:{real_lock_data['audience']['sha256']}"
        expected_content = f"sha256:{real_lock_data['content']['sha256']}"
        assert block.taxonomy_lock_hashes.audience == expected_audience
        assert block.taxonomy_lock_hashes.content == expected_content

    def test_fake_lock_file_hashes_propagate(self, fake_lock_path):
        """Pointing the loader at a different lock file changes the hashes.

        This is the proof that hashes are NOT hard-coded -- if they were,
        the block would always emit the real-file hashes regardless of
        which file we pointed the loader at.
        """
        block = build_capability_audience_block(lock_path=fake_lock_path)
        assert block.taxonomy_lock_hashes.audience == f"sha256:{'deadbeef' * 8}"
        assert block.taxonomy_lock_hashes.content == f"sha256:{'cafef00d' * 8}"

    def test_load_taxonomy_lock_hashes_standalone(self, fake_lock_path):
        """`load_taxonomy_lock_hashes()` is usable on its own and dynamic."""
        hashes = load_taxonomy_lock_hashes(fake_lock_path)
        assert isinstance(hashes, TaxonomyLockHashes)
        assert hashes.audience == f"sha256:{'deadbeef' * 8}"
        assert hashes.content == f"sha256:{'cafef00d' * 8}"

    def test_lock_file_rewrite_picked_up(self, fake_lock_path):
        """Rewriting the lock file invalidates the mtime-keyed cache."""
        first = load_taxonomy_lock_hashes(fake_lock_path)
        assert first.audience == f"sha256:{'deadbeef' * 8}"

        # Rewrite with new hashes; bump mtime to force cache invalidation.
        new_payload = json.loads(fake_lock_path.read_text())
        new_payload["audience"]["sha256"] = "ab" * 32
        new_payload["content"]["sha256"] = "cd" * 32
        fake_lock_path.write_text(json.dumps(new_payload))
        # On some filesystems mtime granularity is coarse -- bump explicitly.
        import os
        import time

        future = time.time() + 5
        os.utime(fake_lock_path, (future, future))

        second = load_taxonomy_lock_hashes(fake_lock_path)
        assert second.audience == f"sha256:{'ab' * 32}"
        assert second.content == f"sha256:{'cd' * 32}"


# =============================================================================
# Pydantic round-trip (model_dump -> model_validate)
# =============================================================================


class TestCapabilityAudienceBlockRoundTrip:
    """The block survives serialize/deserialize via Pydantic."""

    def test_roundtrip_preserves_all_fields(self, real_lock_path):
        block = build_capability_audience_block(lock_path=real_lock_path)
        dumped = block.model_dump()
        rehydrated = CapabilityAudienceBlock.model_validate(dumped)
        assert rehydrated.schema_version == block.schema_version
        assert (
            rehydrated.standard_taxonomy_versions == block.standard_taxonomy_versions
        )
        assert (
            rehydrated.contextual_taxonomy_versions
            == block.contextual_taxonomy_versions
        )
        assert rehydrated.agentic.supported == block.agentic.supported
        assert rehydrated.supports_constraints == block.supports_constraints
        assert rehydrated.supports_extensions == block.supports_extensions
        assert rehydrated.supports_exclusions == block.supports_exclusions
        assert rehydrated.max_refs_per_role == block.max_refs_per_role
        assert (
            rehydrated.taxonomy_lock_hashes.audience
            == block.taxonomy_lock_hashes.audience
        )
        assert (
            rehydrated.taxonomy_lock_hashes.content
            == block.taxonomy_lock_hashes.content
        )

    def test_block_validates_from_wire_shape(self, real_lock_path, real_lock_data):
        """Construct from the JSON wire shape -- proves wire-format conformance."""
        wire = {
            "schema_version": "1",
            "standard_taxonomy_versions": [real_lock_data["audience"]["version"]],
            "contextual_taxonomy_versions": [real_lock_data["content"]["version"]],
            "agentic": {"supported": False},
            "supports_constraints": True,
            "supports_extensions": False,
            "supports_exclusions": False,
            "max_refs_per_role": {
                "primary": 1,
                "constraints": 3,
                "extensions": 0,
                "exclusions": 0,
            },
            "taxonomy_lock_hashes": {
                "audience": f"sha256:{real_lock_data['audience']['sha256']}",
                "content": f"sha256:{real_lock_data['content']['sha256']}",
            },
        }
        block = CapabilityAudienceBlock.model_validate(wire)
        assert block.schema_version == "1"
        assert block.agentic.supported is False
        assert isinstance(block.max_refs_per_role, MaxRefsPerRole)
        assert isinstance(block.agentic, AgenticCapabilityFlag)


# =============================================================================
# AgentCard backward-compatibility (block is optional)
# =============================================================================


class TestAgentCardBackwardCompatibility:
    """A missing `audience_capabilities` block is treated as legacy.

    The wire spec says the block is optional; older sellers that don't ship
    it should still validate as AgentCards (the buyer treats them as legacy
    when the field is missing). This test pins that contract.
    """

    def test_agent_card_without_block_validates(self):
        from ad_seller.models.agent_registry import (
            AgentAuthentication,
            AgentCard,
            AgentProvider,
        )

        card = AgentCard(
            name="Legacy Seller",
            description="Pre-§9 seller",
            url="https://legacy.example.com",
            provider=AgentProvider(name="Legacy Co", url="https://legacy.example.com"),
            authentication=AgentAuthentication(schemes=["api_key"]),
        )
        # Block is None on a legacy card.
        assert card.audience_capabilities is None
        # And the dump survives a round-trip.
        rehydrated = AgentCard.model_validate(card.model_dump())
        assert rehydrated.audience_capabilities is None


# =============================================================================
# Live capability discovery endpoint (GET /.well-known/agent.json)
# =============================================================================


def _mock_product_setup_flow(products_dict):
    """Mock ProductSetupFlow whose state has the given products."""

    mock_flow = MagicMock()
    mock_flow.state = MagicMock()
    mock_flow.state.products = products_dict
    mock_flow.kickoff = AsyncMock()
    return mock_flow


class TestAgentCardEndpointEmitsBlock:
    """`GET /.well-known/agent.json` carries the new audience_capabilities block."""

    async def test_endpoint_includes_audience_capabilities(self, client):
        with patch(
            "ad_seller.flows.ProductSetupFlow",
            return_value=_mock_product_setup_flow({}),
        ):
            resp = await client.get("/.well-known/agent.json")
        assert resp.status_code == 200
        body = resp.json()
        assert "audience_capabilities" in body
        ac = body["audience_capabilities"]
        # Demo defaults end-to-end through the live endpoint.
        assert ac["schema_version"] == "1"
        assert ac["agentic"]["supported"] is False
        assert ac["supports_constraints"] is True
        assert ac["supports_extensions"] is False
        assert ac["supports_exclusions"] is False
        assert ac["max_refs_per_role"] == {
            "primary": 1,
            "constraints": 3,
            "extensions": 0,
            "exclusions": 0,
        }
        # Taxonomy_lock_hashes are present and shaped correctly.
        assert ac["taxonomy_lock_hashes"]["audience"].startswith("sha256:")
        assert ac["taxonomy_lock_hashes"]["content"].startswith("sha256:")
        # And they match what's on disk in the real lock file (i.e., not
        # hard-coded somewhere upstream of the endpoint).
        from pathlib import Path as _Path

        lock_path = (
            _Path(__file__).resolve().parents[2]
            / "data"
            / "taxonomies"
            / "taxonomies.lock.json"
        )
        lock = json.loads(lock_path.read_text())
        assert (
            ac["taxonomy_lock_hashes"]["audience"]
            == f"sha256:{lock['audience']['sha256']}"
        )
        assert (
            ac["taxonomy_lock_hashes"]["content"]
            == f"sha256:{lock['content']['sha256']}"
        )

    async def test_endpoint_response_validates_as_agent_card(self, client):
        """The full response still validates as an AgentCard -- no breakage."""
        with patch(
            "ad_seller.flows.ProductSetupFlow",
            return_value=_mock_product_setup_flow({}),
        ):
            resp = await client.get("/.well-known/agent.json")
        assert resp.status_code == 200
        # Round-trip: response JSON -> AgentCard -> dump -> matches.
        card = AgentCard.model_validate(resp.json())
        assert card.audience_capabilities is not None
        assert isinstance(card.audience_capabilities, CapabilityAudienceBlock)

    async def test_endpoint_existing_fields_preserved(self, client):
        """Pre-existing AgentCard fields are still emitted (no regression)."""
        with patch(
            "ad_seller.flows.ProductSetupFlow",
            return_value=_mock_product_setup_flow({}),
        ):
            resp = await client.get("/.well-known/agent.json")
        body = resp.json()
        # Shape sanity -- these existed before this bead.
        assert "name" in body
        assert "url" in body
        assert "version" in body
        assert "provider" in body
        assert "skills" in body
        assert "authentication" in body
        assert "capabilities" in body  # the unrelated AgentCapabilities (protocols/streaming)
        assert "inventory_types" in body
        assert "supported_deal_types" in body

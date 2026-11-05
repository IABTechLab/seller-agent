# Donated to IAB Tech Lab

"""Entrypoint registry-verification tests (Req 8 / task 7.5).

Parity with the FastAPI path via the SHARED core
(``ad_seller.auth.verification``): the AgentCore entrypoint caps a self-declared
buyer tier at the registry-verified ceiling — claimed tier capped for a known
agent, unknown agents floored to PUBLIC, blocked agents rejected.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ad_seller.interfaces.agentcore import http_main  # noqa: E402
from ad_seller.interfaces.agentcore.verification import BlockedAgentError  # noqa: E402
from ad_seller.models.buyer_identity import AccessTier  # noqa: E402


class _Agent:
    def __init__(self, blocked=False):
        self.is_blocked = blocked


def _patch_core(agent, tier, verdict=None):
    """Patch the shared core's registry + storage so verify_buyer_context runs offline."""
    svc = AsyncMock()
    svc.verify_buyer_trust = AsyncMock(return_value=(agent, tier, verdict or {}))
    return (
        patch(
            "ad_seller.interfaces.api.deps._get_registry_service",
            new=AsyncMock(return_value=svc),
        ),
        patch("ad_seller.storage.factory.get_storage", new=AsyncMock(return_value=object())),
        patch(
            "ad_seller.storage.trust_verifications.TrustVerificationStore",
            return_value=AsyncMock(record_verification=AsyncMock()),
        ),
        patch("ad_seller.clients.agent_registry_client.build_registry_clients", return_value=[]),
    )


@pytest.mark.asyncio
async def test_claimed_tier_capped_to_registry_ceiling():
    payload = {"buyer_tier": "strategic_advertiser", "agent_url": "https://buyer.example/agent"}
    p = _patch_core(_Agent(), AccessTier.SEAT)
    with p[0], p[1], p[2], p[3]:
        ctx = await http_main._verified_buyer_context_from_payload(payload)
    assert ctx is not None
    assert ctx.max_access_tier == AccessTier.SEAT


@pytest.mark.asyncio
async def test_unknown_agent_floored_to_public():
    payload = {"buyer_tier": "strategic_advertiser", "agent_url": "https://unknown.example/agent"}
    p = _patch_core(None, None)
    with p[0], p[1], p[2], p[3]:
        ctx = await http_main._verified_buyer_context_from_payload(payload)
    assert ctx.max_access_tier == AccessTier.PUBLIC


@pytest.mark.asyncio
async def test_blocked_agent_rejected():
    payload = {"buyer_tier": "registered_buyer", "agent_url": "https://blocked.example/agent"}
    p = _patch_core(_Agent(blocked=True), AccessTier.SEAT)
    with p[0], p[1], p[2], p[3]:
        with pytest.raises(BlockedAgentError):
            await http_main._verified_buyer_context_from_payload(payload)


@pytest.mark.asyncio
async def test_no_agent_url_floors_to_public():
    payload = {"buyer_tier": "strategic_advertiser"}  # no agent_url, no key
    ctx = await http_main._verified_buyer_context_from_payload(payload)
    assert ctx.max_access_tier == AccessTier.PUBLIC


@pytest.mark.asyncio
async def test_public_tier_returns_none():
    ctx = await http_main._verified_buyer_context_from_payload({"buyer_tier": "public"})
    assert ctx is None

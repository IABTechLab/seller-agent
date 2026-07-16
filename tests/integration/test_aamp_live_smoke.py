# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Opt-in live smoke test against the hosted IAB agent registry (EP-5.1).

Skipped unless BOTH ``AAMP_REGISTRY_URL`` and ``AAMP_REGISTRY_AUTH_TOKEN``
are set in the environment. Never prints or asserts on credentials.

Run:
    AAMP_REGISTRY_URL=... AAMP_REGISTRY_AUTH_TOKEN=... \
        uv run pytest tests/integration/test_aamp_live_smoke.py -rA
"""

import os

import pytest

_URL = os.environ.get("AAMP_REGISTRY_URL")
_TOKEN = os.environ.get("AAMP_REGISTRY_AUTH_TOKEN")

pytestmark = pytest.mark.skipif(
    not (_URL and _TOKEN),
    reason="live AAMP registry smoke requires AAMP_REGISTRY_URL and "
    "AAMP_REGISTRY_AUTH_TOKEN",
)


async def test_live_search_agents_and_lookup_card():
    """List agents from the hosted registry and fetch one agent card."""
    from ad_seller.clients.agent_registry_client import (
        AampApiRegistryClient,
        _TolerantLibClient,
    )

    # Strict listing through the (null-tolerant) library client — raises on
    # any protocol/auth error rather than degrading to [].
    async with _TolerantLibClient(backend="IAB_SANDBOX", base_url=_URL) as lib_client:
        agents = await lib_client.list_agents()
    print(f"live registry agent count: {len(agents)}")
    assert isinstance(agents, list)

    # The seller's wired registry surface over the same registry.
    client = AampApiRegistryClient(base_url=_URL, auth_token=_TOKEN)
    records = await client.search_agents()
    assert len(records) == len(agents)

    if agents:
        first_id = next((a.id for a in agents if a.id is not None), None)
        assert first_id is not None
        record = await client.lookup_agent(str(first_id))
        assert record is not None
        assert record.get("agent_name")

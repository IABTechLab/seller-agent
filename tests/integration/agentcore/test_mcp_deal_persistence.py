# Donated to IAB Tech Lab

"""Live durable-persistence proof for the seller MCP runtime's pluggable backend.

The other MCP suite (``test_mcp_runtime.py``) proves auth, transport, catalog
serving and the tier wrapper — but every one of those is a READ. This suite
proves the ``--storage postgres`` runtime actually PERSISTS: a deal written on
one MCP session is still there when read back on a SEPARATE session.

Why this is the honest Postgres probe (not the crew path):
  * ``check_avails`` / ``list_products`` / discovery are read-only (in-memory
    catalog) — a discovery crew flow writes nothing to persist.
  * ``create_deal_from_template`` is operator-gated — a buyer cannot trigger it.
  * ``create_curated_deal`` is buyer-reachable (no operator gate) and writes a
    ``CUR-<uuid>`` deal via ``deal_service.create_curated_deal`` ->
    ``storage.set_deal(...)``. On the in-memory SQLite runtime that row dies with
    the container; on the Postgres VPC runtime it survives. Reading it back on a
    fresh MCP session (a new ``streamablehttp_client`` connection, i.e. a new
    server-side request context) via ``get_deal_performance`` ->
    ``storage.get_deal`` is the durable round-trip: ``deal_not_found`` would mean
    the write never reached a shared durable store.

Resolves the runtime ARN STALE-PROOF via ``resolve_live_runtime_arn`` (live
ListAgentRuntimes by name), so a delete+recreate can't point it at a dead ARN.
Skips cleanly when no runtime/token is available (safe pre-deploy).
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ad_seller.registry.runtime_registration import agentcore_invocations_url  # noqa: E402
from tests.integration.agentcore.conftest import resolve_live_runtime_arn  # noqa: E402
from tests.integration.agentcore.test_mcp_runtime import (  # noqa: E402
    _call_tool,
    _result_text,
)
from tests.integration.agentcore.test_runtime import _mint_bearer_token  # noqa: E402

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def mcp_url() -> str:
    arn = resolve_live_runtime_arn("aamp_seller_mcp", "SELLER_MCP_RUNTIME_ARN")
    if not arn:
        pytest.skip("No MCP runtime ARN — deploy the mcp runtime or set SELLER_MCP_RUNTIME_ARN")
    return agentcore_invocations_url(arn)


@pytest.fixture(scope="module")
def token() -> Optional[str]:
    region = os.environ.get("AWS_REGION", "us-west-2")
    tok = _mint_bearer_token(region)
    if not tok:
        pytest.skip("No bearer token — auth stack absent or secret unreadable")
    return tok


def _extract_deal_id(text: str) -> str:
    """Pull the created deal_id (CUR-...) out of the create_curated_deal JSON."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ""
    # Tolerate a couple of envelope shapes: top-level deal_id, or nested deal.
    return (
        payload.get("deal_id")
        or (payload.get("deal") or {}).get("deal_id")
        or (payload.get("data") or {}).get("deal_id")
        or ""
    )


async def test_curated_deal_persists_across_sessions(mcp_url, token):
    """create_curated_deal on one session -> get_deal_performance on a NEW session.

    The read-back opens a fresh streamablehttp connection (new server request
    context), so a returned deal that is NOT ``deal_not_found`` proves the write
    landed in the shared durable (Postgres) backend, not per-request memory.
    """
    # 0) Resolve a REAL product_id from the live catalog (stale-proof — no
    #    hardcoded inv-* id that could drift out of the catalog).
    listing = await _call_tool(mcp_url, token, "list_products", {"limit": 50})
    listing_text = _result_text(listing)
    assert not listing.isError, f"list_products errored: {listing_text}"
    catalog = json.loads(listing_text)
    product_ids = [
        p["product_id"] for p in catalog.get("products", []) if p.get("product_id")
    ]
    assert product_ids, f"empty catalog, cannot pick a product: {listing_text[:400]}"
    product_id = product_ids[0]
    logger.info("using live catalog product %s for persistence probe", product_id)

    # 1) WRITE — create a curated deal (buyer-reachable, persists via set_deal).
    create = await _call_tool(
        mcp_url,
        token,
        "create_curated_deal",
        {
            "curator_id": "agent-range",
            "deal_type": "PMP",
            "product_id": product_id,
            "max_cpm": 250.0,  # well above any curated floor (base + curator fee)
            "impressions": 5_000_000,
        },
    )
    create_text = _result_text(create)
    assert not create.isError, f"create_curated_deal errored: {create_text}"
    deal_id = _extract_deal_id(create_text)
    assert deal_id.startswith("CUR-"), f"no CUR- deal_id in create response: {create_text[:400]}"
    logger.info("created curated deal %s", deal_id)

    # 2) READ-BACK — a SEPARATE MCP session fetches the same deal.
    read = await _call_tool(mcp_url, token, "get_deal_performance", {"deal_id": deal_id})
    read_text = _result_text(read)
    assert not read.isError, f"get_deal_performance errored: {read_text}"
    assert "deal_not_found" not in read_text, (
        f"deal {deal_id} vanished between sessions — backend is not durable "
        f"(in-memory, not Postgres): {read_text[:400]}"
    )
    assert deal_id in read_text, (
        f"read-back did not echo {deal_id}; persistence unproven: {read_text[:400]}"
    )
    logger.info("deal %s survived a fresh MCP session — durable Postgres confirmed", deal_id)

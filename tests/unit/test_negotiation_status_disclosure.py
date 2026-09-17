# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""``GET /proposals/{proposal_id}/negotiation`` must not leak the seller's
internal negotiation guardrails, and must not answer anonymous callers.

The route previously carried no auth dependency and no ``response_model``,
returning whatever ``negotiation_service.get_negotiation_status`` produced —
including ``strategy``, ``base_price``, ``floor_price`` and
``limits.max_rounds``. Any caller who knew or guessed a proposal id learned
the seller's floor and its remaining concession budget. The shared
``Negotiation`` primitive excludes those same four fields deliberately:
they are the seller's internal guardrails and must never cross the wire.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

# Stub execution_activation_flow (cancel-scope leak on ad-server
# connection failure, unresolved -- issue #60 part 2).
_broken_flows = [
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

import httpx  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402
from ad_seller.models.buyer_identity import AccessTier, BuyerIdentity  # noqa: E402
from ad_seller.models.negotiation import (  # noqa: E402
    STRATEGY_LIMITS,
    NegotiationAction,
    NegotiationHistory,
    NegotiationRound,
    NegotiationStrategy,
)

PROPOSAL_ID = "prop-leak-1"

#: The seller-internal values the response must never carry. Distinctive
#: numbers so a leak is unambiguous in the serialized body.
SELLER_BASE_PRICE = 41.77
SELLER_FLOOR_PRICE = 23.11


def _stored_negotiation() -> dict:
    """A persisted ``NegotiationHistory`` carrying the seller's guardrails.

    Storage legitimately holds strategy/base_price/floor_price/limits — the
    engine needs them. The question under test is what reaches the wire.
    """
    history = NegotiationHistory(
        negotiation_id="neg-leak-1",
        proposal_id=PROPOSAL_ID,
        product_id="prod-ctv-1",
        buyer_tier=AccessTier.AGENCY,
        strategy=NegotiationStrategy.COLLABORATIVE,
        limits=STRATEGY_LIMITS[NegotiationStrategy.COLLABORATIVE],
        base_price=SELLER_BASE_PRICE,
        floor_price=SELLER_FLOOR_PRICE,
        rounds=[
            NegotiationRound(
                round_number=1,
                buyer_price=22.0,
                seller_price=38.5,
                action=NegotiationAction.COUNTER,
                rationale="Counter at $38.50 CPM.",
            )
        ],
    )
    return history.model_dump(mode="json")


@pytest.fixture
def mock_storage():
    store = {f"negotiation:{PROPOSAL_ID}": _stored_negotiation()}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.get_negotiation = AsyncMock(side_effect=lambda pid: store.get(f"negotiation:{pid}"))
    storage.set_negotiation = AsyncMock(
        side_effect=lambda pid, data: store.__setitem__(f"negotiation:{pid}", data)
    )
    storage._store = store
    return storage


@pytest.fixture
def anonymous_client():
    """No credential at all — the pre-fix caller."""
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


@pytest.fixture
def authenticated_client():
    """A seller-issued buyer credential, resolved the same way the sibling
    negotiation routes resolve one."""
    app.dependency_overrides[_get_optional_api_key_record] = lambda: MagicMock(
        identity=BuyerIdentity(agency_id="agency-leak-1")
    )
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


class TestNegotiationStatusDoesNotLeakGuardrails:
    async def test_response_omits_seller_guardrail_fields(self, authenticated_client, mock_storage):
        """strategy / base_price / floor_price / max_rounds must not appear.

        Asserted against the raw response text as well as the parsed body:
        the guardrails must be absent from the payload entirely, not merely
        absent from the top level.
        """
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            async with authenticated_client as c:
                resp = await c.get(f"/proposals/{PROPOSAL_ID}/negotiation")

        assert resp.status_code == 200, resp.text
        body = resp.json()

        for leaked in ("strategy", "base_price", "floor_price", "max_rounds"):
            assert leaked not in body, f"{leaked} leaked in negotiation status: {body}"
            assert leaked not in resp.text, f"{leaked} leaked somewhere in payload: {resp.text}"

        # The seller's actual numbers must not appear under any other key.
        assert str(SELLER_FLOOR_PRICE) not in resp.text
        assert str(SELLER_BASE_PRICE) not in resp.text

        # The buyer-visible facts are still served.
        assert body["negotiation_id"] == "neg-leak-1"
        assert body["proposal_id"] == PROPOSAL_ID
        assert body["status"] == "active"
        assert body["total_rounds"] == 1
        assert body["rounds"][0]["seller_price"] == 38.5

    async def test_service_projection_omits_guardrails(self, mock_storage):
        """The service projection itself drops them, so no future consumer
        of ``get_negotiation_status`` can re-expose them."""
        from ad_seller.services import negotiation_service

        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            status = await negotiation_service.get_negotiation_status(PROPOSAL_ID)

        for leaked in ("strategy", "base_price", "floor_price", "max_rounds", "limits"):
            assert leaked not in status, f"{leaked} still projected by the service: {status}"


class TestNegotiationStatusRequiresAuth:
    async def test_anonymous_caller_is_rejected(self, anonymous_client, mock_storage):
        """An unauthenticated caller who guesses a proposal id gets 401, not
        another buyer's negotiation state."""
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            async with anonymous_client as c:
                resp = await c.get(f"/proposals/{PROPOSAL_ID}/negotiation")

        assert resp.status_code == 401, resp.text
        assert "neg-leak-1" not in resp.text
        assert str(SELLER_FLOOR_PRICE) not in resp.text

    async def test_authenticated_caller_is_allowed(self, authenticated_client, mock_storage):
        """The gate is authentication, not a blanket denial."""
        with patch("ad_seller.storage.factory.get_storage", return_value=mock_storage):
            async with authenticated_client as c:
                resp = await c.get(f"/proposals/{PROPOSAL_ID}/negotiation")

        assert resp.status_code == 200, resp.text

# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Seller-side dual content-type acceptance + frozen plan snapshot at booking.

Implements bead ar-y6ki (proposal §5.1 Step 2 + §5.6 + §6 row 14b).

Coverage:
- POST /api/v1/deals accepts the legacy UCP content-type
  (`application/vnd.ucp.embedding+json; v=1`) on requests carrying an
  `audience_plan`.
- Same endpoint accepts the new IAB Agentic Audiences alias
  (`application/vnd.iab.agentic-audiences+json; v=1`) on the same shape.
- Successful bookings carrying an `audience_plan` return a body with
  `audience_plan_snapshot` + `audience_match_summary` per wire-format §6.5.
- The seller logs `audience_plan_id` at INFO via
  `ad_seller.audience.booking` so the buyer-side log can be correlated.
- The minted deal_id corresponds to a stored deal record carrying the
  frozen snapshot (proposal §5.1 Step 2 honor policy).
- Bookings without an `audience_plan` keep the legacy response shape
  (no regression on the non-audience path).
"""

import logging
import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version
# mismatch). Mirrors the pattern in tests/unit/test_deal_booking_endpoints.py.
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

from datetime import datetime, timedelta  # noqa: E402

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402

# Wire-format media types per docs/api/audience_plan_wire_format.md §8.
_UCP = "application/vnd.ucp.embedding+json; v=1"
_AGENTIC = "application/vnd.iab.agentic-audiences+json; v=1"

# Hash matching the buyer's `AudiencePlan.compute_id()` output for the
# fixture below. The seller's snapshot persists whatever hash the buyer
# sends -- the hash content is treated as opaque on the seller side, so the
# specific value here does not need to match the canonical algorithm.
_FIXTURE_PLAN_ID = "sha256:fixture-plan-id-for-snapshot-test"


def _make_available_quote(**overrides):
    defaults = {
        "quote_id": "qt-snap-1",
        "status": "available",
        "deal_type": "PD",
        "product": {
            "product_id": "ctv-premium-sports",
            "name": "Premium CTV - Sports",
            "inventory_type": "ctv",
        },
        "pricing": {
            "base_cpm": 35.0,
            "tier_discount_pct": 15.0,
            "volume_discount_pct": 5.0,
            "final_cpm": 28.26,
            "currency": "USD",
            "pricing_model": "cpm",
            "rationale": "Base $35.00 | -15% | $28.26",
        },
        "terms": {
            "impressions": 5000000,
            "flight_start": "2026-04-01",
            "flight_end": "2026-04-30",
            "guaranteed": False,
        },
        "buyer_tier": "advertiser",
        "expires_at": (datetime.utcnow() + timedelta(hours=23)).isoformat() + "Z",
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    defaults.update(overrides)
    return defaults


def _make_audience_plan(plan_id: str = _FIXTURE_PLAN_ID) -> dict:
    """A spec-shaped AudiencePlan with refs the default seller supports.

    Standard primary + contextual constraint + (empty) extensions /
    exclusions. Default seller capabilities (`build_capability_audience_block()`
    with no overrides) are `agentic_supported=False, supports_extensions=False,
    supports_exclusions=False`, so a plan that includes extensions / agentic
    refs would be rejected with `audience_plan_unsupported` before it reaches
    the snapshot logic. The Path-A demo (proposal §5.3) targets the basic
    standard+contextual mix; agentic-extension paths are exercised against
    explicitly-overridden seller fixtures in §11's tests.

    Versions are pinned to the seller's taxonomy lock defaults (audience
    1.1, content 3.1) -- changing the lock would require updating these.
    """

    return {
        "schema_version": "1",
        "audience_plan_id": plan_id,
        "primary": {
            "type": "standard",
            "identifier": "3-7",
            "taxonomy": "iab-audience",
            "version": "1.1",
            "source": "explicit",
            "confidence": None,
            "compliance_context": None,
        },
        "constraints": [
            {
                "type": "contextual",
                "identifier": "IAB1-2",
                "taxonomy": "iab-content",
                "version": "3.1",
                "source": "resolved",
                "confidence": 0.92,
                "compliance_context": None,
            }
        ],
        "extensions": [],
        "exclusions": [],
        "rationale": "Snapshot fixture",
    }


@pytest.fixture
def mock_storage():
    store: dict = {}
    storage = AsyncMock()
    storage.get_quote = AsyncMock(side_effect=lambda qid: store.get(f"quote:{qid}"))
    storage.set_quote = AsyncMock(
        side_effect=lambda qid, data, ttl=86400: store.__setitem__(
            f"quote:{qid}", data
        )
    )
    storage.get_deal = AsyncMock(side_effect=lambda did: store.get(f"deal:{did}"))
    storage.set_deal = AsyncMock(
        side_effect=lambda did, data: store.__setitem__(f"deal:{did}", data)
    )
    storage._store = store
    return storage


@pytest.fixture
def client(mock_storage):
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Dual content-type acceptance (proposal §5.6)
# ---------------------------------------------------------------------------


class TestDualContentTypeAcceptance:
    """Seller MUST accept both wire-format media types on /api/v1/deals."""

    async def test_accepts_legacy_ucp_content_type(self, client, mock_storage):
        quote = _make_available_quote(quote_id="qt-ucp")
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote

        with patch(
            "ad_seller.storage.factory.get_storage", return_value=mock_storage
        ):
            resp = await client.post(
                "/api/v1/deals",
                json={
                    "quote_id": quote["quote_id"],
                    "audience_plan": _make_audience_plan(),
                },
                headers={"Content-Type": _UCP},
            )

        assert resp.status_code == 200
        assert resp.json()["deal_id"].startswith("DEMO-")

    async def test_accepts_new_agentic_audiences_alias(self, client, mock_storage):
        quote = _make_available_quote(quote_id="qt-agentic")
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote

        with patch(
            "ad_seller.storage.factory.get_storage", return_value=mock_storage
        ):
            resp = await client.post(
                "/api/v1/deals",
                json={
                    "quote_id": quote["quote_id"],
                    "audience_plan": _make_audience_plan(),
                },
                headers={"Content-Type": _AGENTIC},
            )

        assert resp.status_code == 200
        assert resp.json()["deal_id"].startswith("DEMO-")


# ---------------------------------------------------------------------------
# Frozen snapshot + audience_match_summary in the response (wire-format §6.5)
# ---------------------------------------------------------------------------


class TestSnapshotResponseShape:
    """Successful audience-bearing booking returns the §6.5 shape."""

    async def test_response_contains_audience_plan_snapshot(
        self, client, mock_storage
    ):
        quote = _make_available_quote(quote_id="qt-snap-2")
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote
        plan = _make_audience_plan()

        with patch(
            "ad_seller.storage.factory.get_storage", return_value=mock_storage
        ):
            resp = await client.post(
                "/api/v1/deals",
                json={"quote_id": quote["quote_id"], "audience_plan": plan},
                headers={"Content-Type": _UCP},
            )

        assert resp.status_code == 200
        body = resp.json()
        # Snapshot is the buyer-supplied plan verbatim (proposal §5.1 Step 2).
        assert body["audience_plan_snapshot"] == plan
        # Hash echoed back so the buyer can verify cross-side parity.
        assert body["audience_plan_snapshot"]["audience_plan_id"] == plan[
            "audience_plan_id"
        ]

    async def test_response_contains_audience_match_summary(
        self, client, mock_storage
    ):
        quote = _make_available_quote(quote_id="qt-snap-3")
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote

        with patch(
            "ad_seller.storage.factory.get_storage", return_value=mock_storage
        ):
            resp = await client.post(
                "/api/v1/deals",
                json={
                    "quote_id": quote["quote_id"],
                    "audience_plan": _make_audience_plan(),
                },
                headers={"Content-Type": _UCP},
            )

        body = resp.json()
        summary = body["audience_match_summary"]
        # Per-role keys all present, even when a list is empty (wire-format
        # §6.5 receivers MUST treat absent arrays as empty; emitting them
        # keeps the buyer's typed parser stable).
        assert "primary" in summary
        assert summary["primary"]["match"] in {"STRONG", "MODERATE", "WEAK", "NONE"}
        assert 0.0 <= summary["primary"]["score"] <= 1.0
        assert isinstance(summary["constraints"], list)
        assert isinstance(summary["extensions"], list)
        assert isinstance(summary["exclusions"], list)
        # Single constraint -> single MatchEntry.
        assert len(summary["constraints"]) == 1
        assert summary["constraints"][0]["match"] in {
            "STRONG",
            "MODERATE",
            "WEAK",
            "NONE",
        }
        # Empty extensions / exclusions still emit empty arrays.
        assert summary["extensions"] == []
        assert summary["exclusions"] == []

    async def test_no_audience_plan_keeps_legacy_response_shape(
        self, client, mock_storage
    ):
        """Bookings without an audience_plan still parse, no snapshot fields."""

        quote = _make_available_quote(quote_id="qt-no-audience")
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote

        with patch(
            "ad_seller.storage.factory.get_storage", return_value=mock_storage
        ):
            resp = await client.post(
                "/api/v1/deals",
                json={"quote_id": quote["quote_id"]},
            )

        assert resp.status_code == 200
        body = resp.json()
        # No audience plan -> legacy shape (no snapshot fields land on the
        # response or on the persisted record).
        assert "audience_plan_snapshot" not in body
        assert "audience_match_summary" not in body


# ---------------------------------------------------------------------------
# Forensic logging (proposal §5.1 Step 2)
# ---------------------------------------------------------------------------


class TestPlanIdLogging:
    """Seller logs audience_plan_id at booking for cross-side correlation."""

    async def test_logs_audience_plan_id_at_info(self, client, mock_storage, caplog):
        quote = _make_available_quote(quote_id="qt-log-1")
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote
        plan = _make_audience_plan(plan_id="sha256:test-fixture-hash-abc")

        with patch(
            "ad_seller.storage.factory.get_storage", return_value=mock_storage
        ):
            with caplog.at_level(
                logging.INFO, logger="ad_seller.audience.booking"
            ):
                resp = await client.post(
                    "/api/v1/deals",
                    json={"quote_id": quote["quote_id"], "audience_plan": plan},
                )

        assert resp.status_code == 200
        records = [
            r for r in caplog.records if r.name == "ad_seller.audience.booking"
        ]
        assert len(records) == 1
        msg = records[0].getMessage()
        # Plan id, deal id, and quote id all surface for end-to-end correlation.
        assert plan["audience_plan_id"] in msg
        assert resp.json()["deal_id"] in msg
        assert quote["quote_id"] in msg

    async def test_no_audience_plan_does_not_log(
        self, client, mock_storage, caplog
    ):
        quote = _make_available_quote(quote_id="qt-log-2")
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote

        with patch(
            "ad_seller.storage.factory.get_storage", return_value=mock_storage
        ):
            with caplog.at_level(
                logging.INFO, logger="ad_seller.audience.booking"
            ):
                await client.post(
                    "/api/v1/deals", json={"quote_id": quote["quote_id"]}
                )

        assert [
            r for r in caplog.records if r.name == "ad_seller.audience.booking"
        ] == []


# ---------------------------------------------------------------------------
# Persisted snapshot is honored at fulfillment time
# ---------------------------------------------------------------------------


class TestSnapshotPersistence:
    """The minted deal_id must point to a deal record carrying the snapshot."""

    async def test_minted_deal_record_carries_snapshot(
        self, client, mock_storage
    ):
        """Snapshot lands on the persisted deal record so
        `honor_audience_plan_snapshot()` can read it post-booking."""

        quote = _make_available_quote(quote_id="qt-persist")
        mock_storage._store[f"quote:{quote['quote_id']}"] = quote
        plan = _make_audience_plan()

        with patch(
            "ad_seller.storage.factory.get_storage", return_value=mock_storage
        ):
            resp = await client.post(
                "/api/v1/deals",
                json={"quote_id": quote["quote_id"], "audience_plan": plan},
            )

        deal_id = resp.json()["deal_id"]
        stored = mock_storage._store[f"deal:{deal_id}"]
        # Frozen snapshot is verbatim on the persisted record.
        assert stored["audience_plan_snapshot"] == plan
        # Match summary is also persisted alongside (single source of truth
        # for fulfillment-time inspection).
        assert "audience_match_summary" in stored
        # Deal id is the cheap handle (per §5.1 Step 2).
        assert stored["deal_id"] == deal_id

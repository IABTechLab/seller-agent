# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for fail-closed delivery of audit-class events.

Audit-class events (AUDIT_EVENT_TYPES) must never be silently dropped:
- bus failure -> event lands in the durable fallback JSONL, caller proceeds
- bus failure + fallback failure -> exception propagates (fail-closed)
- non-audit events keep the existing fail-open behavior
- happy path is unchanged
"""

import json
from unittest.mock import patch

import pytest

import ad_seller.events.bus as bus_mod
from ad_seller.events.bus import InMemoryEventBus
from ad_seller.events.helpers import emit_event
from ad_seller.events.models import AUDIT_EVENT_TYPES, Event, EventType


class FailingBus(InMemoryEventBus):
    """Event bus whose publish always raises."""

    async def publish(self, event: Event) -> None:
        raise RuntimeError("bus down")


@pytest.fixture(autouse=True)
def reset_bus_singleton():
    """Isolate the global event bus singleton per test."""
    bus_mod._event_bus_instance = None
    yield
    bus_mod._event_bus_instance = None


@pytest.fixture
def fallback_path(tmp_path, monkeypatch):
    """Point the audit fallback JSONL at a temp file via settings."""
    from ad_seller.config.settings import get_settings

    path = tmp_path / "audit_fallback.jsonl"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("AUDIT_FALLBACK_PATH", str(path))
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


# =========================================================================
# AUDIT_EVENT_TYPES selection
# =========================================================================


class TestAuditEventTypes:
    def test_money_decision_types_are_audit_class(self):
        expected = {
            EventType.PROPOSAL_ACCEPTED,
            EventType.PROPOSAL_REJECTED,
            EventType.PROPOSAL_COUNTERED,
            EventType.DEAL_CREATED,
            EventType.DEAL_REGISTERED,
            EventType.DEAL_SYNCED,
            EventType.EXECUTION_COMPLETED,
            EventType.APPROVAL_REQUESTED,
            EventType.APPROVAL_GRANTED,
            EventType.APPROVAL_DENIED,
            EventType.APPROVAL_TIMED_OUT,
            EventType.NEGOTIATION_STARTED,
            EventType.NEGOTIATION_ROUND,
            EventType.NEGOTIATION_CONCLUDED,
        }
        assert expected <= AUDIT_EVENT_TYPES

    def test_observability_types_are_not_audit_class(self):
        for et in (
            EventType.PROPOSAL_RECEIVED,
            EventType.SESSION_CREATED,
            EventType.PACKAGE_UPDATED,
        ):
            assert et not in AUDIT_EVENT_TYPES


# =========================================================================
# (a) audit event + failing bus -> fallback JSONL, transaction proceeds
# =========================================================================


class TestAuditFallbackWrite:
    @pytest.mark.asyncio
    async def test_publish_failure_writes_fallback(self, fallback_path):
        bus_mod._event_bus_instance = FailingBus()

        result = await emit_event(
            event_type=EventType.DEAL_CREATED,
            flow_id="f1",
            proposal_id="p1",
            deal_id="d1",
            payload={"price": 15.0},
        )

        # Transaction proceeds (no raise); emit reports failure via None
        assert result is None

        records = _read_jsonl(fallback_path)
        assert len(records) == 1
        assert records[0]["event_type"] == "deal.created"
        assert records[0]["deal_id"] == "d1"
        assert records[0]["proposal_id"] == "p1"
        assert records[0]["payload"] == {"price": 15.0}
        assert "bus down" in records[0]["emit_error"]
        assert records[0]["event_id"]  # full event was captured

    @pytest.mark.asyncio
    async def test_bus_factory_failure_writes_fallback(self, fallback_path):
        """Even if the bus factory fails before Event construction, the
        record is reconstructed from the emit arguments."""
        with patch(
            "ad_seller.events.bus.get_event_bus",
            side_effect=RuntimeError("no bus"),
        ):
            result = await emit_event(
                event_type=EventType.APPROVAL_GRANTED,
                flow_id="f2",
                payload={"decided_by": "human:ops"},
            )

        assert result is None
        records = _read_jsonl(fallback_path)
        assert len(records) == 1
        assert records[0]["event_type"] == "approval.granted"
        assert records[0]["flow_id"] == "f2"
        assert records[0]["payload"] == {"decided_by": "human:ops"}

    @pytest.mark.asyncio
    async def test_fallback_appends_multiple_records(self, fallback_path):
        bus_mod._event_bus_instance = FailingBus()

        await emit_event(event_type=EventType.DEAL_CREATED, deal_id="d1")
        await emit_event(event_type=EventType.DEAL_REGISTERED, deal_id="d1")

        records = _read_jsonl(fallback_path)
        assert [r["event_type"] for r in records] == ["deal.created", "deal.registered"]


# =========================================================================
# (b) audit event + failing bus + failing fallback -> raises (fail-closed)
# =========================================================================


class TestAuditFailClosed:
    @pytest.mark.asyncio
    async def test_fallback_failure_raises(self, fallback_path):
        bus_mod._event_bus_instance = FailingBus()

        with patch(
            "ad_seller.events.helpers.write_audit_fallback",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(OSError, match="disk full"):
                await emit_event(event_type=EventType.DEAL_CREATED, deal_id="d1")


# =========================================================================
# (c) non-audit event + failing bus -> swallowed (unchanged fail-open)
# =========================================================================


class TestNonAuditUnchanged:
    @pytest.mark.asyncio
    async def test_non_audit_failure_swallowed(self, fallback_path):
        bus_mod._event_bus_instance = FailingBus()

        result = await emit_event(event_type=EventType.PROPOSAL_RECEIVED, flow_id="f1")

        assert result is None
        assert not fallback_path.exists()  # no fallback write for non-audit

    @pytest.mark.asyncio
    async def test_non_audit_failure_swallowed_even_if_fallback_broken(self, fallback_path):
        """Non-audit events never touch the fallback writer at all."""
        bus_mod._event_bus_instance = FailingBus()

        with patch(
            "ad_seller.events.helpers.write_audit_fallback",
            side_effect=OSError("disk full"),
        ):
            result = await emit_event(event_type=EventType.SESSION_CREATED)

        assert result is None


# =========================================================================
# (d) happy path unchanged
# =========================================================================


class TestHappyPathUnchanged:
    @pytest.mark.asyncio
    async def test_audit_event_happy_path(self, fallback_path):
        bus = InMemoryEventBus()
        bus_mod._event_bus_instance = bus

        event = await emit_event(
            event_type=EventType.DEAL_CREATED,
            flow_id="f1",
            deal_id="d1",
            payload={"price": 15.0},
        )

        assert event is not None
        assert event.event_type == EventType.DEAL_CREATED
        assert not fallback_path.exists()  # no fallback on success

        stored = await bus.get_event(event.event_id)
        assert stored is not None

    @pytest.mark.asyncio
    async def test_non_audit_event_happy_path(self, fallback_path):
        bus_mod._event_bus_instance = InMemoryEventBus()

        event = await emit_event(event_type=EventType.PROPOSAL_RECEIVED, flow_id="f1")
        assert event is not None
        assert not fallback_path.exists()

    @pytest.mark.asyncio
    async def test_subscriber_error_still_isolated_for_audit_events(self, fallback_path):
        """Subscriber failures are not emission failures: the event is already
        stored on the bus, so no fallback write and no raise."""
        bus = InMemoryEventBus()
        bus_mod._event_bus_instance = bus

        def bad_subscriber(e):
            raise RuntimeError("subscriber boom")

        await bus.subscribe(EventType.DEAL_CREATED.value, bad_subscriber)

        event = await emit_event(event_type=EventType.DEAL_CREATED, deal_id="d1")
        assert event is not None
        assert not fallback_path.exists()


# =========================================================================
# Fallback writer details
# =========================================================================


class TestFallbackWriter:
    def test_default_path_from_settings(self, fallback_path):
        from ad_seller.events.audit_fallback import get_audit_fallback_path

        assert get_audit_fallback_path() == fallback_path

    def test_creates_parent_directories(self, tmp_path, monkeypatch):
        from ad_seller.config.settings import get_settings
        from ad_seller.events.audit_fallback import write_audit_fallback

        nested = tmp_path / "deep" / "nested" / "audit.jsonl"
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("AUDIT_FALLBACK_PATH", str(nested))
        get_settings.cache_clear()
        try:
            write_audit_fallback({"event_type": "deal.created"})
        finally:
            get_settings.cache_clear()

        assert nested.exists()
        assert json.loads(nested.read_text())["event_type"] == "deal.created"

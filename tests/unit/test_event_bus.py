# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for Event Bus and Approval Gates.

Covers:
- Event published is received by subscriber (InMemoryEventBus)
- Subscriber filtering by event_type works
- Wildcard subscriber ("*") receives all events
- ApprovalGate.request_approval creates pending request
- ApprovalGate.list_pending returns only pending items
- Approve/reject updates status correctly
"""

import pytest
from unittest.mock import AsyncMock, patch

from ad_seller.events.bus import InMemoryEventBus
from ad_seller.events.models import (
    ApprovalStatus,
    Event,
    EventType,
)


# =========================================================================
# InMemoryEventBus
# =========================================================================


class TestInMemoryEventBus:
    """Tests for the InMemoryEventBus."""

    @pytest.mark.asyncio
    async def test_publish_and_receive(self):
        bus = InMemoryEventBus()
        received = []

        await bus.subscribe(EventType.DEAL_CREATED.value, lambda e: received.append(e))

        event = Event(event_type=EventType.DEAL_CREATED, flow_id="f1")
        await bus.publish(event)

        assert len(received) == 1
        assert received[0].event_type == EventType.DEAL_CREATED

    @pytest.mark.asyncio
    async def test_subscriber_filtering_by_type(self):
        bus = InMemoryEventBus()
        deal_events = []
        proposal_events = []

        await bus.subscribe(EventType.DEAL_CREATED.value, lambda e: deal_events.append(e))
        await bus.subscribe(EventType.PROPOSAL_RECEIVED.value, lambda e: proposal_events.append(e))

        await bus.publish(Event(event_type=EventType.DEAL_CREATED))
        await bus.publish(Event(event_type=EventType.PROPOSAL_RECEIVED))
        await bus.publish(Event(event_type=EventType.DEAL_CREATED))

        assert len(deal_events) == 2
        assert len(proposal_events) == 1

    @pytest.mark.asyncio
    async def test_wildcard_subscriber_receives_all(self):
        bus = InMemoryEventBus()
        all_events = []

        await bus.subscribe("*", lambda e: all_events.append(e))

        await bus.publish(Event(event_type=EventType.DEAL_CREATED))
        await bus.publish(Event(event_type=EventType.PROPOSAL_RECEIVED))
        await bus.publish(Event(event_type=EventType.APPROVAL_REQUESTED))

        assert len(all_events) == 3

    @pytest.mark.asyncio
    async def test_get_event_by_id(self):
        bus = InMemoryEventBus()
        event = Event(event_type=EventType.DEAL_CREATED, flow_id="f1")
        await bus.publish(event)

        found = await bus.get_event(event.event_id)
        assert found is not None
        assert found.event_id == event.event_id

    @pytest.mark.asyncio
    async def test_get_nonexistent_event_returns_none(self):
        bus = InMemoryEventBus()
        found = await bus.get_event("does-not-exist")
        assert found is None

    @pytest.mark.asyncio
    async def test_list_events_with_filter(self):
        bus = InMemoryEventBus()
        await bus.publish(Event(event_type=EventType.DEAL_CREATED, flow_id="f1"))
        await bus.publish(Event(event_type=EventType.PROPOSAL_RECEIVED, flow_id="f1"))
        await bus.publish(Event(event_type=EventType.DEAL_CREATED, flow_id="f2"))

        # Filter by flow_id
        f1_events = await bus.list_events(flow_id="f1")
        assert len(f1_events) == 2

        # Filter by event_type
        deal_events = await bus.list_events(event_type=EventType.DEAL_CREATED.value)
        assert len(deal_events) == 2

    @pytest.mark.asyncio
    async def test_subscriber_error_does_not_break_others(self):
        bus = InMemoryEventBus()
        good_events = []

        def bad_subscriber(e):
            raise ValueError("boom")

        await bus.subscribe(EventType.DEAL_CREATED.value, bad_subscriber)
        await bus.subscribe(EventType.DEAL_CREATED.value, lambda e: good_events.append(e))

        # Should not raise even though first subscriber errors
        await bus.publish(Event(event_type=EventType.DEAL_CREATED))
        assert len(good_events) == 1


# =========================================================================
# ApprovalGate
# =========================================================================


class _InMemoryStorage:
    """Simple dict-based async storage for testing ApprovalGate."""

    def __init__(self):
        self._data = {}

    async def get(self, key):
        return self._data.get(key)

    async def set(self, key, value, ttl=None):
        self._data[key] = value

    async def delete(self, key):
        return self._data.pop(key, None) is not None


class TestApprovalGate:
    """Tests for the ApprovalGate."""

    @pytest.fixture
    def storage(self):
        return _InMemoryStorage()

    @pytest.fixture
    def gate(self, storage):
        from ad_seller.events.approval import ApprovalGate
        return ApprovalGate(storage)

    @pytest.mark.asyncio
    async def test_request_approval_creates_pending(self, gate, storage):
        # Patch get_event_bus to use a local InMemoryEventBus
        bus = InMemoryEventBus()
        with patch("ad_seller.events.bus.get_event_bus", new_callable=AsyncMock, return_value=bus):
            req = await gate.request_approval(
                flow_id="flow-1",
                flow_type="proposal_handling",
                gate_name="proposal_decision",
                context={"proposal": "some data"},
                flow_state_snapshot={"state": "snapshot"},
            )

        assert req.status == ApprovalStatus.PENDING
        assert req.gate_name == "proposal_decision"
        assert req.flow_id == "flow-1"

        # Verify persisted
        stored = await storage.get(f"approval:{req.approval_id}")
        assert stored is not None
        assert stored["status"] == "pending"

    @pytest.mark.asyncio
    async def test_list_pending_returns_only_pending(self, gate, storage):
        bus = InMemoryEventBus()
        with patch("ad_seller.events.bus.get_event_bus", new_callable=AsyncMock, return_value=bus):
            req1 = await gate.request_approval(
                flow_id="f1", flow_type="test", gate_name="g1",
                context={}, flow_state_snapshot={},
            )
            req2 = await gate.request_approval(
                flow_id="f2", flow_type="test", gate_name="g2",
                context={}, flow_state_snapshot={},
            )

        # Approve one
        with patch("ad_seller.events.bus.get_event_bus", new_callable=AsyncMock, return_value=bus):
            await gate.submit_decision(req1.approval_id, "approve", decided_by="human:ops")

        pending = await gate.list_pending()
        assert len(pending) == 1
        assert pending[0].approval_id == req2.approval_id

    @pytest.mark.asyncio
    async def test_approve_updates_status(self, gate, storage):
        bus = InMemoryEventBus()
        with patch("ad_seller.events.bus.get_event_bus", new_callable=AsyncMock, return_value=bus):
            req = await gate.request_approval(
                flow_id="f1", flow_type="test", gate_name="g1",
                context={}, flow_state_snapshot={},
            )
            response = await gate.submit_decision(
                req.approval_id, "approve", decided_by="human:ops", reason="Looks good"
            )

        assert response.decision == "approve"
        assert response.decided_by == "human:ops"

        updated_req = await gate.get_request(req.approval_id)
        assert updated_req.status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_reject_updates_status(self, gate, storage):
        bus = InMemoryEventBus()
        with patch("ad_seller.events.bus.get_event_bus", new_callable=AsyncMock, return_value=bus):
            req = await gate.request_approval(
                flow_id="f1", flow_type="test", gate_name="g1",
                context={}, flow_state_snapshot={},
            )
            response = await gate.submit_decision(
                req.approval_id, "reject", decided_by="human:ops", reason="Too risky"
            )

        assert response.decision == "reject"

        updated_req = await gate.get_request(req.approval_id)
        assert updated_req.status == ApprovalStatus.REJECTED

    @pytest.mark.asyncio
    async def test_double_decision_raises(self, gate, storage):
        bus = InMemoryEventBus()
        with patch("ad_seller.events.bus.get_event_bus", new_callable=AsyncMock, return_value=bus):
            req = await gate.request_approval(
                flow_id="f1", flow_type="test", gate_name="g1",
                context={}, flow_state_snapshot={},
            )
            await gate.submit_decision(req.approval_id, "approve")

            with pytest.raises(ValueError, match="already resolved"):
                await gate.submit_decision(req.approval_id, "reject")

    @pytest.mark.asyncio
    async def test_nonexistent_approval_raises(self, gate):
        with pytest.raises(ValueError, match="not found"):
            await gate.submit_decision("nonexistent-id", "approve")

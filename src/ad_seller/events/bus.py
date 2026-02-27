# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Event bus implementations.

Provides abstract EventBus interface with two concrete backends:
- InMemoryEventBus: for development and testing
- StorageEventBus: backed by existing StorageBackend (SQLite or Redis)
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

from .models import Event

logger = logging.getLogger(__name__)

Subscriber = Callable[[Event], None]


class EventBus(ABC):
    """Abstract event bus interface."""

    @abstractmethod
    async def publish(self, event: Event) -> None:
        """Publish an event."""

    @abstractmethod
    async def subscribe(self, event_type: str, callback: Subscriber) -> None:
        """Subscribe to events of a given type.

        Args:
            event_type: EventType value or "*" for all events.
            callback: Function called when matching event arrives.
        """

    @abstractmethod
    async def get_event(self, event_id: str) -> Optional[Event]:
        """Retrieve a persisted event by ID."""

    @abstractmethod
    async def list_events(
        self,
        flow_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[Event]:
        """List persisted events, optionally filtered."""


class InMemoryEventBus(EventBus):
    """In-memory event bus for development and testing.

    Events stored in a list. Subscribers called synchronously.
    No persistence across restarts.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._subscribers: dict[str, list[Subscriber]] = {}

    async def publish(self, event: Event) -> None:
        self._events.append(event)
        logger.info("Event published: %s (id=%s)", event.event_type, event.event_id)
        for cb in self._subscribers.get(event.event_type.value, []):
            try:
                cb(event)
            except Exception as e:
                logger.error("Subscriber error for %s: %s", event.event_type, e)
        for cb in self._subscribers.get("*", []):
            try:
                cb(event)
            except Exception as e:
                logger.error("Subscriber error (wildcard): %s", e)

    async def subscribe(self, event_type: str, callback: Subscriber) -> None:
        self._subscribers.setdefault(event_type, []).append(callback)

    async def get_event(self, event_id: str) -> Optional[Event]:
        for ev in self._events:
            if ev.event_id == event_id:
                return ev
        return None

    async def list_events(
        self,
        flow_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[Event]:
        results = self._events
        if flow_id:
            results = [e for e in results if e.flow_id == flow_id]
        if event_type:
            results = [e for e in results if e.event_type.value == event_type]
        return results[-limit:]


class StorageEventBus(EventBus):
    """Event bus backed by the existing StorageBackend (SQLite or Redis).

    Persists events using key prefix ``event:``, following the same
    pattern as ``product:``, ``deal:``, etc.
    """

    def __init__(self, storage_backend: Any) -> None:
        self._storage = storage_backend
        self._subscribers: dict[str, list[Subscriber]] = {}

    async def publish(self, event: Event) -> None:
        event_data = event.model_dump(mode="json")
        await self._storage.set(f"event:{event.event_id}", event_data)

        # Index by flow_id
        if event.flow_id:
            index_key = f"event_index:flow:{event.flow_id}"
            existing = await self._storage.get(index_key) or []
            existing.append(event.event_id)
            await self._storage.set(index_key, existing)

        # Index by type
        type_key = f"event_index:type:{event.event_type.value}"
        existing = await self._storage.get(type_key) or []
        existing.append(event.event_id)
        await self._storage.set(type_key, existing)

        logger.info("Event persisted: %s (id=%s)", event.event_type, event.event_id)

        # Notify in-process subscribers
        for cb in self._subscribers.get(event.event_type.value, []):
            try:
                cb(event)
            except Exception as e:
                logger.error("Subscriber error for %s: %s", event.event_type, e)
        for cb in self._subscribers.get("*", []):
            try:
                cb(event)
            except Exception as e:
                logger.error("Subscriber error (wildcard): %s", e)

        # Redis pub/sub fanout if available
        if hasattr(self._storage, "publish"):
            try:
                await self._storage.publish(
                    f"events:{event.event_type.value}",
                    event_data,
                )
            except Exception as e:
                logger.warning("Redis pub/sub fanout failed: %s", e)

    async def subscribe(self, event_type: str, callback: Subscriber) -> None:
        self._subscribers.setdefault(event_type, []).append(callback)

    async def get_event(self, event_id: str) -> Optional[Event]:
        data = await self._storage.get(f"event:{event_id}")
        if data:
            return Event(**data)
        return None

    async def list_events(
        self,
        flow_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[Event]:
        if flow_id:
            ids = await self._storage.get(f"event_index:flow:{flow_id}") or []
        elif event_type:
            ids = await self._storage.get(f"event_index:type:{event_type}") or []
        else:
            keys = await self._storage.keys("event:*")
            ids = [
                k.replace("event:", "", 1)
                for k in keys
                if not k.startswith("event_index:")
            ]

        events = []
        for eid in ids[-limit:]:
            ev = await self.get_event(eid)
            if ev:
                if event_type and ev.event_type.value != event_type:
                    continue
                events.append(ev)
        return events


# ---------------------------------------------------------------------------
# Factory / singleton
# ---------------------------------------------------------------------------

_event_bus_instance: Optional[EventBus] = None


async def get_event_bus() -> EventBus:
    """Get or create the global event bus instance.

    Uses the same storage backend as the rest of the application.
    Falls back to InMemoryEventBus if storage is unavailable.
    """
    global _event_bus_instance

    if _event_bus_instance is not None:
        return _event_bus_instance

    try:
        from ..config import get_settings
        settings = get_settings()
        if not settings.event_bus_enabled:
            _event_bus_instance = InMemoryEventBus()
            return _event_bus_instance
    except Exception:
        pass

    try:
        from ..storage.factory import get_storage
        storage = await get_storage()
        _event_bus_instance = StorageEventBus(storage)
    except Exception as e:
        logger.warning("Failed to create StorageEventBus, falling back to InMemory: %s", e)
        _event_bus_instance = InMemoryEventBus()

    return _event_bus_instance


async def close_event_bus() -> None:
    """Reset the global event bus instance."""
    global _event_bus_instance
    _event_bus_instance = None

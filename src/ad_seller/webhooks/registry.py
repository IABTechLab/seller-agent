# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Webhook registry for managing webhook subscriptions.

Stores webhook subscriptions in the storage backend and provides
query methods for retrieving subscribers by event type.
"""

import logging
from datetime import datetime
from typing import Any, Optional

from ..models.webhooks import (
    WebhookDelivery,
    WebhookStatus,
    WebhookSubscription,
    WebhookSubscriptionUpdate,
)

logger = logging.getLogger(__name__)


class WebhookRegistry:
    """Manages webhook subscriptions using the storage backend."""

    def __init__(self, storage_backend: Any) -> None:
        """Initialize webhook registry.

        Args:
            storage_backend: Storage backend instance (SQLite, Redis, etc.)
        """
        self._storage = storage_backend

    async def register(self, subscription: WebhookSubscription) -> str:
        """Register a new webhook subscription.

        Args:
            subscription: Webhook subscription to register

        Returns:
            webhook_id of the registered subscription
        """
        await self._storage.set(
            f"webhook:{subscription.webhook_id}",
            subscription.model_dump(mode="json"),
        )

        # Index by event type for fast lookups
        for event_type in subscription.events:
            index_key = f"webhook_index:event:{event_type}"
            existing = (await self._storage.get(index_key)) or []
            if subscription.webhook_id not in existing:
                existing.append(subscription.webhook_id)
                await self._storage.set(index_key, existing)

        # Wildcard index for "*" subscriptions
        if "*" in subscription.events:
            wildcard_key = "webhook_index:event:*"
            existing = (await self._storage.get(wildcard_key)) or []
            if subscription.webhook_id not in existing:
                existing.append(subscription.webhook_id)
                await self._storage.set(wildcard_key, existing)

        logger.info("Webhook registered: %s for events %s", subscription.webhook_id, subscription.events)
        return subscription.webhook_id

    async def get(self, webhook_id: str) -> Optional[WebhookSubscription]:
        """Get a webhook subscription by ID.

        Args:
            webhook_id: Webhook ID to retrieve

        Returns:
            WebhookSubscription if found, None otherwise
        """
        data = await self._storage.get(f"webhook:{webhook_id}")
        if data:
            return WebhookSubscription(**data)
        return None

    async def list_all(self) -> list[WebhookSubscription]:
        """List all webhook subscriptions.

        Returns:
            List of all webhook subscriptions
        """
        keys = await self._storage.keys("webhook:*")
        # Filter out index keys
        webhook_keys = [k for k in keys if not k.startswith("webhook_index:")]

        subscriptions = []
        for key in webhook_keys:
            data = await self._storage.get(key)
            if data:
                subscriptions.append(WebhookSubscription(**data))

        return subscriptions

    async def get_subscribers(self, event_type: str) -> list[WebhookSubscription]:
        """Get all active webhooks subscribed to an event type.

        Args:
            event_type: Event type to find subscribers for (e.g., "deal.created")

        Returns:
            List of active webhook subscriptions for this event type
        """
        subscriber_ids = set()

        # Exact match
        exact_ids = (await self._storage.get(f"webhook_index:event:{event_type}")) or []
        subscriber_ids.update(exact_ids)

        # Wildcard match (e.g., "deal.*" matches "deal.created")
        if "." in event_type:
            pattern = event_type.split(".")[0] + ".*"
            pattern_ids = (await self._storage.get(f"webhook_index:event:{pattern}")) or []
            subscriber_ids.update(pattern_ids)

        # Global wildcard ("*")
        wildcard_ids = (await self._storage.get("webhook_index:event:*")) or []
        subscriber_ids.update(wildcard_ids)

        # Fetch subscriptions and filter by status
        subscriptions = []
        for webhook_id in subscriber_ids:
            subscription = await self.get(webhook_id)
            if subscription and subscription.status == WebhookStatus.ACTIVE:
                subscriptions.append(subscription)

        return subscriptions

    async def update(
        self, webhook_id: str, updates: WebhookSubscriptionUpdate
    ) -> Optional[WebhookSubscription]:
        """Update a webhook subscription.

        Args:
            webhook_id: Webhook ID to update
            updates: Fields to update

        Returns:
            Updated webhook subscription if found, None otherwise
        """
        subscription = await self.get(webhook_id)
        if not subscription:
            return None

        # Remove old event indexes if events are being updated
        if updates.events is not None:
            # Remove from old event indexes
            for event_type in subscription.events:
                index_key = f"webhook_index:event:{event_type}"
                existing = (await self._storage.get(index_key)) or []
                if webhook_id in existing:
                    existing.remove(webhook_id)
                    await self._storage.set(index_key, existing)

        # Update fields
        update_data = updates.model_dump(exclude_none=True)
        for field, value in update_data.items():
            setattr(subscription, field, value)

        subscription.updated_at = datetime.utcnow()

        # Save updated subscription
        await self._storage.set(
            f"webhook:{webhook_id}",
            subscription.model_dump(mode="json"),
        )

        # Re-index if events were updated
        if updates.events is not None:
            for event_type in subscription.events:
                index_key = f"webhook_index:event:{event_type}"
                existing = (await self._storage.get(index_key)) or []
                if webhook_id not in existing:
                    existing.append(webhook_id)
                    await self._storage.set(index_key, existing)

        logger.info("Webhook updated: %s", webhook_id)
        return subscription

    async def delete(self, webhook_id: str) -> bool:
        """Delete a webhook subscription.

        Args:
            webhook_id: Webhook ID to delete

        Returns:
            True if deleted, False if not found
        """
        subscription = await self.get(webhook_id)
        if not subscription:
            return False

        # Remove from event indexes
        for event_type in subscription.events:
            index_key = f"webhook_index:event:{event_type}"
            existing = (await self._storage.get(index_key)) or []
            if webhook_id in existing:
                existing.remove(webhook_id)
                await self._storage.set(index_key, existing)

        # Delete subscription
        await self._storage.delete(f"webhook:{webhook_id}")

        # Delete associated deliveries
        delivery_keys = await self._storage.keys(f"webhook_delivery:{webhook_id}:*")
        for key in delivery_keys:
            await self._storage.delete(key)

        logger.info("Webhook deleted: %s", webhook_id)
        return True

    async def mark_delivery(
        self, webhook_id: str, delivery: WebhookDelivery
    ) -> None:
        """Record a delivery attempt.

        Args:
            webhook_id: Webhook ID
            delivery: Delivery record
        """
        # Store delivery record
        await self._storage.set(
            f"webhook_delivery:{webhook_id}:{delivery.delivery_id}",
            delivery.model_dump(mode="json"),
        )

        # Update webhook last_delivery timestamp
        subscription = await self.get(webhook_id)
        if subscription:
            subscription.last_delivery = delivery.timestamp
            await self._storage.set(
                f"webhook:{webhook_id}",
                subscription.model_dump(mode="json"),
            )

    async def mark_failed(self, webhook_id: str) -> None:
        """Increment failure count and auto-disable after threshold.

        Args:
            webhook_id: Webhook ID that failed
        """
        subscription = await self.get(webhook_id)
        if not subscription:
            return

        subscription.failure_count += 1
        subscription.updated_at = datetime.utcnow()

        # Auto-disable after 10 consecutive failures
        if subscription.failure_count >= 10:
            subscription.status = WebhookStatus.FAILED
            logger.warning(
                "Webhook %s auto-disabled after %d failures",
                webhook_id,
                subscription.failure_count,
            )

        await self._storage.set(
            f"webhook:{webhook_id}",
            subscription.model_dump(mode="json"),
        )

    async def reset_failure_count(self, webhook_id: str) -> None:
        """Reset failure count after successful delivery.

        Args:
            webhook_id: Webhook ID that succeeded
        """
        subscription = await self.get(webhook_id)
        if not subscription:
            return

        if subscription.failure_count > 0:
            subscription.failure_count = 0
            subscription.updated_at = datetime.utcnow()
            await self._storage.set(
                f"webhook:{webhook_id}",
                subscription.model_dump(mode="json"),
            )

    async def list_deliveries(
        self, webhook_id: str, limit: int = 50
    ) -> list[WebhookDelivery]:
        """List delivery history for a webhook.

        Args:
            webhook_id: Webhook ID
            limit: Maximum number of deliveries to return

        Returns:
            List of webhook deliveries
        """
        keys = await self._storage.keys(f"webhook_delivery:{webhook_id}:*")

        deliveries = []
        for key in keys[-limit:]:
            data = await self._storage.get(key)
            if data:
                deliveries.append(WebhookDelivery(**data))

        # Sort by timestamp descending
        deliveries.sort(key=lambda d: d.timestamp, reverse=True)
        return deliveries

# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Webhook dispatcher for delivering events to subscriber endpoints.

Handles HTTP POST delivery with retry logic, HMAC signature generation,
and automatic failure tracking.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx

from ..events.models import Event
from ..models.webhooks import (
    DeliveryStatus,
    WebhookAuthType,
    WebhookDelivery,
    WebhookPayload,
    WebhookSubscription,
)
from .registry import WebhookRegistry

logger = logging.getLogger(__name__)


class WebhookDispatcher:
    """Dispatches webhooks with retry logic and failure tracking."""

    def __init__(
        self,
        registry: WebhookRegistry,
        timeout: int = 10,
        max_retries: int = 3,
    ) -> None:
        """Initialize webhook dispatcher.

        Args:
            registry: WebhookRegistry instance
            timeout: HTTP request timeout in seconds
            max_retries: Maximum retry attempts
        """
        self._registry = registry
        self._timeout = timeout
        self._max_retries = max_retries

    def _generate_signature(self, payload: dict[str, Any], secret: str) -> str:
        """Generate HMAC-SHA256 signature for webhook verification.

        Args:
            payload: Webhook payload dictionary
            secret: Secret key for HMAC

        Returns:
            Signature in format "sha256={hex}"
        """
        message = json.dumps(payload, sort_keys=True).encode()
        signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
        return f"sha256={signature}"

    def _build_payload(self, event: Event, webhook_id: str) -> dict[str, Any]:
        """Build webhook payload from event.

        Args:
            event: Event to send
            webhook_id: Webhook ID for tracking

        Returns:
            Webhook payload dictionary
        """
        payload_obj = WebhookPayload(
            webhook_id=webhook_id,
            event_type=event.event_type.value,
            event_id=event.event_id,
            timestamp=event.timestamp.isoformat(),
            deal_id=event.deal_id,
            proposal_id=event.proposal_id,
            session_id=event.session_id,
            payload=event.payload,
        )
        return payload_obj.model_dump(exclude_none=True)

    def _matches_filters(self, event: Event, filters: dict[str, Any]) -> bool:
        """Check if event matches subscription filters.

        Args:
            event: Event to check
            filters: Subscription filters

        Returns:
            True if event matches all filters, False otherwise
        """
        if not filters:
            return True

        # Filter by buyer_id in metadata
        if "buyer_id" in filters:
            if event.metadata.get("buyer_id") != filters["buyer_id"]:
                return False

        # Filter by deal_type in payload
        if "deal_type" in filters:
            deal_type = event.payload.get("deal_type")
            allowed_types = filters["deal_type"]
            if isinstance(allowed_types, list):
                if deal_type not in allowed_types:
                    return False
            elif deal_type != allowed_types:
                return False

        # Filter by min_deal_value
        if "min_deal_value" in filters:
            deal_value = event.payload.get("total_cost", 0)
            if deal_value < filters["min_deal_value"]:
                return False

        # Filter by inventory_types
        if "inventory_types" in filters:
            inventory_type = event.payload.get("inventory_type")
            if inventory_type not in filters["inventory_types"]:
                return False

        return True

    async def _send_webhook(
        self,
        subscription: WebhookSubscription,
        payload: dict[str, Any],
        retry_count: int = 0,
    ) -> WebhookDelivery:
        """Send webhook with retry logic.

        Args:
            subscription: Webhook subscription
            payload: Payload to send
            retry_count: Current retry attempt

        Returns:
            WebhookDelivery record
        """
        start_time = time.time()

        headers = {"Content-Type": "application/json"}

        # Add authentication
        if subscription.auth and subscription.auth.type == WebhookAuthType.BEARER:
            headers["Authorization"] = f"Bearer {subscription.auth.token}"

        # Add HMAC signature
        signature = self._generate_signature(payload, subscription.secret)
        headers["X-Webhook-Signature"] = signature

        delivery = WebhookDelivery(
            webhook_id=subscription.webhook_id,
            event_type=payload["event_type"],
            event_id=payload["event_id"],
            status=DeliveryStatus.FAILED,
            retry_count=retry_count,
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    str(subscription.url),
                    json=payload,
                    headers=headers,
                )

                duration_ms = int((time.time() - start_time) * 1000)
                delivery.duration_ms = duration_ms
                delivery.response_code = response.status_code

                if response.status_code in (200, 201, 202, 204):
                    delivery.status = DeliveryStatus.SUCCESS
                    logger.info(
                        "Webhook delivered: %s -> %s (%dms)",
                        subscription.webhook_id,
                        payload["event_type"],
                        duration_ms,
                    )
                else:
                    delivery.status = DeliveryStatus.FAILED
                    delivery.error = f"HTTP {response.status_code}: {response.text[:200]}"
                    logger.warning(
                        "Webhook delivery failed: %s -> %s (HTTP %d)",
                        subscription.webhook_id,
                        payload["event_type"],
                        response.status_code,
                    )

        except httpx.TimeoutException as e:
            duration_ms = int((time.time() - start_time) * 1000)
            delivery.duration_ms = duration_ms
            delivery.status = DeliveryStatus.FAILED
            delivery.error = f"Timeout after {self._timeout}s: {str(e)}"
            logger.warning(
                "Webhook timeout: %s -> %s",
                subscription.webhook_id,
                payload["event_type"],
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            delivery.duration_ms = duration_ms
            delivery.status = DeliveryStatus.FAILED
            delivery.error = str(e)
            logger.error(
                "Webhook error: %s -> %s: %s",
                subscription.webhook_id,
                payload["event_type"],
                e,
            )

        # Record delivery
        await self._registry.mark_delivery(subscription.webhook_id, delivery)

        return delivery

    async def _send_with_retry(
        self,
        subscription: WebhookSubscription,
        payload: dict[str, Any],
    ) -> WebhookDelivery:
        """Send webhook with exponential backoff retry.

        Retry logic: 1s, 2s, 4s delays between attempts.

        Args:
            subscription: Webhook subscription
            payload: Payload to send

        Returns:
            Final delivery record
        """
        delivery = None

        for attempt in range(self._max_retries):
            delivery = await self._send_webhook(subscription, payload, retry_count=attempt)

            if delivery.status == DeliveryStatus.SUCCESS:
                # Reset failure count on success
                await self._registry.reset_failure_count(subscription.webhook_id)
                return delivery

            # Exponential backoff: 1s, 2s, 4s
            if attempt < self._max_retries - 1:
                delay = 2**attempt
                logger.info(
                    "Retrying webhook %s in %ds (attempt %d/%d)",
                    subscription.webhook_id,
                    delay,
                    attempt + 1,
                    self._max_retries,
                )
                await asyncio.sleep(delay)

        # All retries failed
        await self._registry.mark_failed(subscription.webhook_id)
        return delivery

    async def dispatch_event(self, event: Event) -> None:
        """Dispatch event to all matching webhook subscribers.

        Args:
            event: Event to dispatch
        """
        # Get all subscribers for this event type
        subscribers = await self._registry.get_subscribers(event.event_type.value)

        if not subscribers:
            return

        # Filter by subscription filters
        filtered_subscribers = [
            sub for sub in subscribers if self._matches_filters(event, sub.filters)
        ]

        if not filtered_subscribers:
            return

        logger.info(
            "Dispatching event %s to %d webhooks",
            event.event_type.value,
            len(filtered_subscribers),
        )

        # Dispatch to all subscribers in parallel
        tasks = []
        for subscription in filtered_subscribers:
            payload = self._build_payload(event, subscription.webhook_id)
            task = self._send_with_retry(subscription, payload)
            tasks.append(task)

        # Wait for all deliveries to complete
        await asyncio.gather(*tasks, return_exceptions=True)

    async def retry_delivery(self, delivery: WebhookDelivery) -> WebhookDelivery:
        """Manually retry a failed delivery.

        Args:
            delivery: Delivery to retry

        Returns:
            New delivery record
        """
        subscription = await self._registry.get(delivery.webhook_id)
        if not subscription:
            raise ValueError(f"Webhook {delivery.webhook_id} not found")

        # Reconstruct payload from delivery
        payload = {
            "webhook_id": delivery.webhook_id,
            "event_type": delivery.event_type,
            "event_id": delivery.event_id,
        }

        return await self._send_webhook(subscription, payload, retry_count=0)

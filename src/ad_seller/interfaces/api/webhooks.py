# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Webhook API endpoints.

Provides REST API for webhook subscription management and delivery history.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ...storage.factory import get_storage
from ...webhooks.dispatcher import WebhookDispatcher
from ...models.webhooks import (
    DeliveryStatus,
    WebhookDeliveryListOutput,
    WebhookDeliveryOutput,
    WebhookListOutput,
    WebhookRetryInput,
    WebhookRetryOutput,
    WebhookRetryResult,
    WebhookStatus,
    WebhookSubscription,
    WebhookSubscriptionInput,
    WebhookSubscriptionOutput,
    WebhookSubscriptionUpdate,
)
from ...webhooks.registry import WebhookRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["Webhooks"])


async def get_webhook_registry() -> WebhookRegistry:
    """Get webhook registry instance."""
    storage = await get_storage()
    return WebhookRegistry(storage)


async def get_webhook_dispatcher() -> WebhookDispatcher:
    """Get webhook dispatcher instance."""
    registry = await get_webhook_registry()
    return WebhookDispatcher(registry)


@router.post("/subscribe", response_model=WebhookSubscriptionOutput)
async def subscribe_webhook(subscription_input: WebhookSubscriptionInput):
    """Register a new webhook subscription.

    Example:
    ```json
    {
      "url": "https://buyer.example.com/webhooks/events",
      "events": ["deal.created", "deal.registered", "proposal.evaluated"],
      "filters": {
        "buyer_id": "buyer-789",
        "deal_type": ["PG", "PD"]
      },
      "auth": {
        "type": "bearer",
        "token": "webhook_secret_token_xyz"
      }
    }
    ```
    """
    registry = await get_webhook_registry()

    # Create subscription
    subscription = WebhookSubscription(
        url=subscription_input.url,
        events=subscription_input.events,
        filters=subscription_input.filters,
        auth=subscription_input.auth,
    )

    # Register
    webhook_id = await registry.register(subscription)

    # Return output
    return WebhookSubscriptionOutput(
        webhook_id=webhook_id,
        url=str(subscription.url),
        events=subscription.events,
        filters=subscription.filters,
        status=subscription.status,
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
        failure_count=subscription.failure_count,
        last_delivery=subscription.last_delivery,
    )


@router.get("", response_model=WebhookListOutput)
async def list_webhooks():
    """List all webhook subscriptions."""
    registry = await get_webhook_registry()
    subscriptions = await registry.list_all()

    webhook_outputs = [
        WebhookSubscriptionOutput(
            webhook_id=sub.webhook_id,
            url=str(sub.url),
            events=sub.events,
            filters=sub.filters,
            status=sub.status,
            created_at=sub.created_at,
            updated_at=sub.updated_at,
            failure_count=sub.failure_count,
            last_delivery=sub.last_delivery,
        )
        for sub in subscriptions
    ]

    return WebhookListOutput(webhooks=webhook_outputs, total=len(webhook_outputs))


@router.get("/{webhook_id}", response_model=WebhookSubscriptionOutput)
async def get_webhook(webhook_id: str):
    """Get details of a specific webhook subscription."""
    registry = await get_webhook_registry()
    subscription = await registry.get(webhook_id)

    if not subscription:
        raise HTTPException(status_code=404, detail=f"Webhook {webhook_id} not found")

    return WebhookSubscriptionOutput(
        webhook_id=subscription.webhook_id,
        url=str(subscription.url),
        events=subscription.events,
        filters=subscription.filters,
        status=subscription.status,
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
        failure_count=subscription.failure_count,
        last_delivery=subscription.last_delivery,
    )


@router.put("/{webhook_id}", response_model=WebhookSubscriptionOutput)
async def update_webhook(webhook_id: str, updates: WebhookSubscriptionUpdate):
    """Update webhook configuration.

    Example:
    ```json
    {
      "events": ["deal.created", "deal.synced"],
      "status": "paused",
      "filters": {
        "buyer_id": "buyer-789",
        "min_deal_value": 10000
      }
    }
    ```
    """
    registry = await get_webhook_registry()
    subscription = await registry.update(webhook_id, updates)

    if not subscription:
        raise HTTPException(status_code=404, detail=f"Webhook {webhook_id} not found")

    return WebhookSubscriptionOutput(
        webhook_id=subscription.webhook_id,
        url=str(subscription.url),
        events=subscription.events,
        filters=subscription.filters,
        status=subscription.status,
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
        failure_count=subscription.failure_count,
        last_delivery=subscription.last_delivery,
    )


@router.delete("/{webhook_id}")
async def delete_webhook(webhook_id: str):
    """Delete a webhook subscription."""
    registry = await get_webhook_registry()
    deleted = await registry.delete(webhook_id)

    if not deleted:
        raise HTTPException(status_code=404, detail=f"Webhook {webhook_id} not found")

    return {"status": "deleted", "webhook_id": webhook_id}


@router.get("/{webhook_id}/deliveries", response_model=WebhookDeliveryListOutput)
async def get_webhook_deliveries(
    webhook_id: str,
    limit: int = Query(50, ge=1, le=500, description="Maximum deliveries to return"),
    status: Optional[DeliveryStatus] = Query(None, description="Filter by delivery status"),
):
    """Get delivery history for a webhook.

    Returns recent delivery attempts with status, response codes, and error messages.
    """
    registry = await get_webhook_registry()

    # Verify webhook exists
    subscription = await registry.get(webhook_id)
    if not subscription:
        raise HTTPException(status_code=404, detail=f"Webhook {webhook_id} not found")

    # Get deliveries
    deliveries = await registry.list_deliveries(webhook_id, limit=limit)

    # Filter by status if specified
    if status:
        deliveries = [d for d in deliveries if d.status == status]

    delivery_outputs = [
        WebhookDeliveryOutput(
            delivery_id=d.delivery_id,
            event_type=d.event_type,
            event_id=d.event_id,
            timestamp=d.timestamp,
            status=d.status,
            response_code=d.response_code,
            duration_ms=d.duration_ms,
            retry_count=d.retry_count,
            error=d.error,
        )
        for d in deliveries
    ]

    return WebhookDeliveryListOutput(
        webhook_id=webhook_id,
        deliveries=delivery_outputs,
        total=len(delivery_outputs),
    )


@router.post("/{webhook_id}/retry", response_model=WebhookRetryOutput)
async def retry_webhook_deliveries(webhook_id: str, retry_input: WebhookRetryInput):
    """Manually retry failed deliveries.

    Example:
    ```json
    {
      "delivery_ids": ["del-790", "del-791"]
    }
    ```
    """
    registry = await get_webhook_registry()
    dispatcher = await get_webhook_dispatcher()

    # Verify webhook exists
    subscription = await registry.get(webhook_id)
    if not subscription:
        raise HTTPException(status_code=404, detail=f"Webhook {webhook_id} not found")

    # Get all deliveries
    all_deliveries = await registry.list_deliveries(webhook_id, limit=500)
    delivery_map = {d.delivery_id: d for d in all_deliveries}

    results = []
    for delivery_id in retry_input.delivery_ids:
        if delivery_id not in delivery_map:
            results.append(
                WebhookRetryResult(
                    delivery_id=delivery_id,
                    status=DeliveryStatus.FAILED,
                    error=f"Delivery {delivery_id} not found",
                )
            )
            continue

        original_delivery = delivery_map[delivery_id]

        try:
            # Retry the delivery
            new_delivery = await dispatcher.retry_delivery(original_delivery)
            results.append(
                WebhookRetryResult(
                    delivery_id=delivery_id,
                    status=new_delivery.status,
                    response_code=new_delivery.response_code,
                    error=new_delivery.error,
                )
            )
        except Exception as e:
            results.append(
                WebhookRetryResult(
                    delivery_id=delivery_id,
                    status=DeliveryStatus.FAILED,
                    error=str(e),
                )
            )

    return WebhookRetryOutput(retried=len(retry_input.delivery_ids), results=results)

# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Webhook data models."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, HttpUrl


class WebhookStatus(str, Enum):
    """Status of a webhook subscription."""

    ACTIVE = "active"
    PAUSED = "paused"
    FAILED = "failed"


class DeliveryStatus(str, Enum):
    """Status of a webhook delivery attempt."""

    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"


class WebhookAuthType(str, Enum):
    """Webhook authentication types."""

    NONE = "none"
    BEARER = "bearer"


class WebhookAuth(BaseModel):
    """Webhook authentication configuration."""

    type: WebhookAuthType = WebhookAuthType.NONE
    token: Optional[str] = None


class WebhookSubscription(BaseModel):
    """A webhook subscription configuration."""

    webhook_id: str = Field(default_factory=lambda: f"wh-{uuid.uuid4().hex[:12]}")
    url: HttpUrl
    events: list[str] = Field(
        default_factory=list,
        description="Event types to subscribe to (e.g., ['deal.created', 'proposal.evaluated'])",
    )
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional filters (e.g., {'buyer_id': 'buyer-123', 'deal_type': ['PG', 'PD']})",
    )
    auth: Optional[WebhookAuth] = None
    status: WebhookStatus = WebhookStatus.ACTIVE
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    failure_count: int = 0
    last_delivery: Optional[datetime] = None
    secret: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Secret key for HMAC signature generation",
    )


class WebhookDelivery(BaseModel):
    """Record of a webhook delivery attempt."""

    delivery_id: str = Field(default_factory=lambda: f"del-{uuid.uuid4().hex[:12]}")
    webhook_id: str
    event_type: str
    event_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    status: DeliveryStatus
    response_code: Optional[int] = None
    duration_ms: Optional[int] = None
    retry_count: int = 0
    error: Optional[str] = None


class WebhookPayload(BaseModel):
    """Payload sent to webhook endpoints."""

    webhook_id: str
    event_type: str
    event_id: str
    timestamp: str
    deal_id: str = ""
    proposal_id: str = ""
    session_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class WebhookSubscriptionInput(BaseModel):
    """Input for creating a webhook subscription."""

    url: HttpUrl
    events: list[str]
    filters: dict[str, Any] = Field(default_factory=dict)
    auth: Optional[WebhookAuth] = None


class WebhookSubscriptionUpdate(BaseModel):
    """Input for updating a webhook subscription."""

    events: Optional[list[str]] = None
    filters: Optional[dict[str, Any]] = None
    status: Optional[WebhookStatus] = None


class WebhookSubscriptionOutput(BaseModel):
    """Output for webhook subscription details."""

    webhook_id: str
    url: str
    events: list[str]
    filters: dict[str, Any]
    status: WebhookStatus
    created_at: datetime
    updated_at: datetime
    failure_count: int
    last_delivery: Optional[datetime]


class WebhookListOutput(BaseModel):
    """Output for listing webhooks."""

    webhooks: list[WebhookSubscriptionOutput]
    total: int


class WebhookDeliveryOutput(BaseModel):
    """Output for webhook delivery details."""

    delivery_id: str
    event_type: str
    event_id: str
    timestamp: datetime
    status: DeliveryStatus
    response_code: Optional[int]
    duration_ms: Optional[int]
    retry_count: int
    error: Optional[str] = None


class WebhookDeliveryListOutput(BaseModel):
    """Output for listing webhook deliveries."""

    webhook_id: str
    deliveries: list[WebhookDeliveryOutput]
    total: int


class WebhookRetryInput(BaseModel):
    """Input for retrying failed deliveries."""

    delivery_ids: list[str]


class WebhookRetryResult(BaseModel):
    """Result of a delivery retry."""

    delivery_id: str
    status: DeliveryStatus
    response_code: Optional[int] = None
    error: Optional[str] = None


class WebhookRetryOutput(BaseModel):
    """Output for retry operation."""

    retried: int
    results: list[WebhookRetryResult]

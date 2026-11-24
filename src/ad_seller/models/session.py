# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Session models for multi-turn conversation persistence.

A Session tracks a buyer's ongoing conversation with the seller,
including message history, negotiation state, and linked flows.
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .buyer_identity import BuyerContext, BuyerIdentity


class SessionStatus(str, Enum):
    """Lifecycle status of a session."""

    ACTIVE = "active"
    EXPIRED = "expired"
    CLOSED = "closed"


class SessionMessage(BaseModel):
    """A single message in a session conversation."""

    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
    flow_id: Optional[str] = None
    message_type: Optional[str] = None  # "pricing", "deal", "availability", "general"


class NegotiationState(BaseModel):
    """Tracks the current negotiation context within a session.

    Captures where the buyer is in the negotiation funnel
    so the conversation can resume intelligently.
    """

    stage: str = "discovery"  # discovery, pricing, proposal, negotiation, deal, closed
    product_ids_discussed: list[str] = Field(default_factory=list)
    last_pricing_shown: dict[str, float] = Field(default_factory=dict)
    pending_proposal_id: Optional[str] = None
    active_deal_ids: list[str] = Field(default_factory=list)
    counter_round: int = 0
    last_intent: Optional[str] = None
    negotiation_id: Optional[str] = None  # Active NegotiationHistory ID
    last_counter_result: Optional[dict[str, Any]] = None  # Last NegotiationRound as dict


class Session(BaseModel):
    """A persistent multi-turn buyer conversation session.

    Links buyer identity, conversation history, negotiation state,
    and all flows triggered during the conversation.
    """

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: SessionStatus = SessionStatus.ACTIVE

    # Buyer identity
    buyer_identity: BuyerIdentity = Field(default_factory=BuyerIdentity)
    buyer_context: Optional[BuyerContext] = None
    # key_id of the API key that opened the session (None: opened without
    # one, or before this field was recorded).
    owner_key_id: Optional[str] = None

    # Conversation
    messages: list[SessionMessage] = Field(default_factory=list)

    # Negotiation tracking
    negotiation: NegotiationState = Field(default_factory=NegotiationState)

    # Flow threading
    linked_flow_ids: list[str] = Field(default_factory=list)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    # Extensibility
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_expired(self) -> bool:
        """Check if the session has expired."""
        if self.status == SessionStatus.EXPIRED:
            return True
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return True
        return False

    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = datetime.utcnow()

    def add_message(
        self,
        role: str,
        content: str,
        message_type: Optional[str] = None,
        flow_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SessionMessage:
        """Add a message to the conversation history."""
        msg = SessionMessage(
            role=role,
            content=content,
            message_type=message_type,
            flow_id=flow_id,
            metadata=metadata or {},
        )
        self.messages.append(msg)
        self.touch()
        return msg

    def link_flow(self, flow_id: str) -> None:
        """Link a flow to this session."""
        if flow_id not in self.linked_flow_ids:
            self.linked_flow_ids.append(flow_id)
            self.touch()

    def get_buyer_pricing_key(self) -> str:
        """Get the buyer pricing key for indexing."""
        if self.buyer_context:
            return self.buyer_context.get_pricing_key()
        if self.buyer_identity.advertiser_id:
            return f"advertiser:{self.buyer_identity.advertiser_id}"
        if self.buyer_identity.agency_id:
            return f"agency:{self.buyer_identity.agency_id}"
        if self.buyer_identity.seat_id:
            return f"seat:{self.buyer_identity.seat_id}"
        return "public"

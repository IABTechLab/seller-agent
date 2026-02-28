# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Conversational chat interface for buyer interactions.

Enables natural language conversations with buyers for:
- Discovery and inquiry
- Deal negotiation
- Non-agentic DSP workflows
"""

from datetime import datetime, timedelta
from typing import Any, Optional

from ...flows import DiscoveryInquiryFlow, NonAgenticDSPFlow, ProductSetupFlow
from ...models.buyer_identity import BuyerContext, BuyerIdentity, AccessTier
from ...models.session import Session, SessionStatus


class ChatInterface:
    """Chat interface for conversational buyer interactions.

    Supports:
    - Natural language inventory queries
    - Pricing inquiries with tiered responses
    - Deal creation for non-agentic DSPs
    - Negotiation workflows
    - Persistent multi-turn sessions (when storage is provided)

    Example (stateless):
        chat = ChatInterface()
        response = chat.process_message(
            "What CTV inventory do you have available?",
            buyer_context=context,
        )

    Example (session-persistent):
        chat = ChatInterface(storage=storage)
        await chat.initialize()
        session = await chat.start_session(buyer_context=context)
        response = await chat.process_message_async(
            "What CTV inventory do you have?",
            session_id=session.session_id,
        )
    """

    def __init__(self, storage: Any = None) -> None:
        """Initialize the chat interface.

        Args:
            storage: Optional storage backend for session persistence.
                     When None, operates in stateless mode (backward compatible).
        """
        self._products: dict[str, Any] = {}
        self._conversation_history: list[dict[str, str]] = []
        self._buyer_context: Optional[BuyerContext] = None
        self._storage = storage
        self._current_session: Optional[Session] = None

    async def initialize(self) -> None:
        """Initialize products and resources."""
        flow = ProductSetupFlow()
        await flow.kickoff()
        self._products = flow.state.products

    def set_buyer_context(self, context: BuyerContext) -> None:
        """Set the buyer context for the conversation.

        Args:
            context: Buyer identity and authentication context
        """
        self._buyer_context = context

    # =========================================================================
    # Session management
    # =========================================================================

    async def start_session(
        self, buyer_context: Optional[BuyerContext] = None
    ) -> Session:
        """Create a new persistent session.

        Args:
            buyer_context: Buyer context for the session.

        Returns:
            The created Session.

        Raises:
            RuntimeError: If no storage backend is configured.
        """
        if not self._storage:
            raise RuntimeError("Storage backend required for session persistence")

        from ...config import get_settings
        settings = get_settings()

        session = Session(
            buyer_identity=buyer_context.identity if buyer_context else BuyerIdentity(),
            buyer_context=buyer_context,
            expires_at=datetime.utcnow() + timedelta(seconds=settings.session_ttl_seconds),
        )
        self._current_session = session
        self._buyer_context = buyer_context

        await self._save_session(session)

        # Index by buyer
        await self._storage.add_session_to_buyer_index(
            session.session_id, session.get_buyer_pricing_key()
        )

        # Emit event
        from ...events.helpers import emit_event
        from ...events.models import EventType
        await emit_event(
            event_type=EventType.SESSION_CREATED,
            session_id=session.session_id,
            payload={
                "buyer_pricing_key": session.get_buyer_pricing_key(),
                "expires_at": session.expires_at.isoformat() if session.expires_at else None,
            },
        )

        return session

    async def resume_session(self, session_id: str) -> Session:
        """Resume an existing session.

        Args:
            session_id: The session to resume.

        Returns:
            The loaded Session.

        Raises:
            RuntimeError: If no storage backend is configured.
            ValueError: If session is not found, expired, or closed.
        """
        if not self._storage:
            raise RuntimeError("Storage backend required for session persistence")

        data = await self._storage.get_session(session_id)
        if not data:
            raise ValueError(f"Session not found: {session_id}")

        session = Session(**data)

        if session.is_expired():
            session.status = SessionStatus.EXPIRED
            await self._save_session(session)
            raise ValueError(f"Session expired: {session_id}")

        if session.status == SessionStatus.CLOSED:
            raise ValueError(f"Session closed: {session_id}")

        # Restore in-memory state
        self._current_session = session
        self._buyer_context = session.buyer_context
        self._conversation_history = [
            {"role": m.role, "content": m.content}
            for m in session.messages
        ]

        # Emit event
        from ...events.helpers import emit_event
        from ...events.models import EventType
        await emit_event(
            event_type=EventType.SESSION_RESUMED,
            session_id=session.session_id,
            payload={"message_count": len(session.messages)},
        )

        return session

    async def close_session(self, session_id: Optional[str] = None) -> None:
        """Close a session.

        Args:
            session_id: Session to close. Uses current session if None.
        """
        sid = session_id or (
            self._current_session.session_id if self._current_session else None
        )
        if not sid or not self._storage:
            return

        data = await self._storage.get_session(sid)
        if data:
            session = Session(**data)
            session.status = SessionStatus.CLOSED
            session.closed_at = datetime.utcnow()
            await self._save_session(session)

            from ...events.helpers import emit_event
            from ...events.models import EventType
            await emit_event(
                event_type=EventType.SESSION_CLOSED,
                session_id=sid,
                payload={"total_messages": len(session.messages)},
            )

        if self._current_session and self._current_session.session_id == sid:
            self._current_session = None

    async def _save_session(self, session: Session) -> None:
        """Persist a session to storage with TTL."""
        if not self._storage:
            return

        from ...config import get_settings
        settings = get_settings()

        await self._storage.set_session(
            session.session_id,
            session.model_dump(mode="json"),
            ttl=settings.session_ttl_seconds,
        )

    # =========================================================================
    # Message processing
    # =========================================================================

    def process_message(
        self,
        message: str,
        buyer_context: Optional[BuyerContext] = None,
    ) -> dict[str, Any]:
        """Process a chat message from a buyer.

        Args:
            message: The buyer's message
            buyer_context: Optional buyer context (uses session context if not provided)

        Returns:
            Response dict with text and any structured data
        """
        context = buyer_context or self._buyer_context or self._default_context()

        # Add to conversation history
        self._conversation_history.append({
            "role": "user",
            "content": message,
        })

        # Determine message intent
        message_lower = message.lower()
        response: dict[str, Any]

        if self._is_deal_request(message_lower):
            response = self._handle_deal_request(message, context)
        elif self._is_pricing_inquiry(message_lower):
            response = self._handle_pricing_inquiry(message, context)
        elif self._is_availability_inquiry(message_lower):
            response = self._handle_availability_inquiry(message, context)
        else:
            response = self._handle_general_inquiry(message, context)

        # Add response to history
        self._conversation_history.append({
            "role": "assistant",
            "content": response.get("text", ""),
        })

        return response

    async def process_message_async(
        self,
        message: str,
        buyer_context: Optional[BuyerContext] = None,
        session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Process a chat message, optionally within a persistent session.

        Args:
            message: The buyer's message.
            buyer_context: Optional buyer context.
            session_id: Optional session ID for persistence.

        Returns:
            Response dict with text and structured data.
        """
        # Load session if specified
        if session_id and self._storage:
            if not self._current_session or self._current_session.session_id != session_id:
                await self.resume_session(session_id)

        # Delegate to existing synchronous logic
        response = self.process_message(message, buyer_context=buyer_context)

        # Persist to session if active
        if self._current_session and self._storage:
            self._current_session.add_message(
                role="user",
                content=message,
                message_type=response.get("type"),
            )
            self._current_session.add_message(
                role="assistant",
                content=response.get("text", ""),
                message_type=response.get("type"),
            )

            # Update negotiation state based on response type
            resp_type = response.get("type")
            if resp_type == "pricing":
                self._current_session.negotiation.stage = "pricing"
                self._current_session.negotiation.last_intent = "pricing_inquiry"
            elif resp_type == "availability":
                self._current_session.negotiation.last_intent = "availability_inquiry"
            elif resp_type == "deal":
                self._current_session.negotiation.stage = "deal"
                self._current_session.negotiation.last_intent = "deal_request"
                deal_id = response.get("deal", {}).get("deal_id") if response.get("deal") else None
                if deal_id:
                    self._current_session.negotiation.active_deal_ids.append(deal_id)

            await self._save_session(self._current_session)

        return response

    # =========================================================================
    # Intent detection and handlers (unchanged)
    # =========================================================================

    def _default_context(self) -> BuyerContext:
        """Create default anonymous buyer context."""
        return BuyerContext(
            identity=BuyerIdentity(),
            is_authenticated=False,
        )

    def _is_deal_request(self, message: str) -> bool:
        """Check if message is a deal creation request."""
        deal_keywords = ["create deal", "book", "buy inventory", "want to buy", "make a deal"]
        return any(keyword in message for keyword in deal_keywords)

    def _is_pricing_inquiry(self, message: str) -> bool:
        """Check if message is a pricing inquiry."""
        pricing_keywords = ["price", "cost", "cpm", "rate", "how much"]
        return any(keyword in message for keyword in pricing_keywords)

    def _is_availability_inquiry(self, message: str) -> bool:
        """Check if message is an availability inquiry."""
        avail_keywords = ["available", "inventory", "impressions", "capacity"]
        return any(keyword in message for keyword in avail_keywords)

    def _handle_deal_request(
        self,
        message: str,
        context: BuyerContext,
    ) -> dict[str, Any]:
        """Handle a deal creation request."""
        flow = NonAgenticDSPFlow()
        result = flow.process_request(
            request_text=message,
            buyer_context=context,
        )

        return {
            "text": result["response"],
            "type": "deal",
            "deal": result.get("deal"),
            "status": result["status"],
        }

    def _handle_pricing_inquiry(
        self,
        message: str,
        context: BuyerContext,
    ) -> dict[str, Any]:
        """Handle a pricing inquiry."""
        tier = context.effective_tier

        # Build pricing response based on tier
        if tier == AccessTier.PUBLIC:
            text = """
Here are our typical pricing ranges:

| Inventory Type | Price Range |
|----------------|-------------|
| Display        | $10-15 CPM  |
| Video          | $20-30 CPM  |
| CTV            | $28-42 CPM  |
| Mobile App     | $15-22 CPM  |
| Native         | $8-12 CPM   |

For exact pricing, please authenticate with your agency credentials.
"""
        else:
            discount = 10 if tier == AccessTier.AGENCY else 15
            text = f"""
As a {tier.value} tier buyer, you receive a {discount}% discount from our standard rates:

| Inventory Type | Your Rate |
|----------------|-----------|
| Display        | ${12 * (1 - discount/100):.2f} CPM |
| Video          | ${25 * (1 - discount/100):.2f} CPM |
| CTV            | ${35 * (1 - discount/100):.2f} CPM |
| Mobile App     | ${18 * (1 - discount/100):.2f} CPM |
| Native         | ${10 * (1 - discount/100):.2f} CPM |

Volume discounts are available for orders over 5M impressions.
Ready to create a deal? Just let me know!
"""

        return {
            "text": text.strip(),
            "type": "pricing",
            "tier": tier.value,
        }

    def _handle_availability_inquiry(
        self,
        message: str,
        context: BuyerContext,
    ) -> dict[str, Any]:
        """Handle an availability inquiry."""
        tier = context.effective_tier

        if tier == AccessTier.PUBLIC:
            text = """
We have inventory available across all channels:

- **Display**: High availability
- **Video**: Moderate availability
- **CTV**: Premium availability
- **Mobile App**: High availability
- **Native**: Moderate availability

For specific impression counts and dates, please authenticate.
"""
        else:
            text = """
Current inventory availability (next 30 days):

| Inventory Type | Available Impressions | Fill Rate |
|----------------|----------------------|-----------|
| Display        | 15M+                 | 72%       |
| Video          | 8M+                  | 85%       |
| CTV            | 5M+                  | 78%       |
| Mobile App     | 12M+                 | 68%       |
| Native         | 10M+                 | 75%       |

What inventory type and volume are you interested in?
"""

        return {
            "text": text.strip(),
            "type": "availability",
            "tier": tier.value,
        }

    def _handle_general_inquiry(
        self,
        message: str,
        context: BuyerContext,
    ) -> dict[str, Any]:
        """Handle a general inquiry."""
        text = """
I can help you with:

1. **Inventory Discovery** - Ask about available inventory types
2. **Pricing** - Get pricing for specific products or ranges
3. **Availability** - Check impression availability
4. **Deal Creation** - Create deals for DSP activation

What would you like to know?

Example questions:
- "What CTV inventory do you have?"
- "How much does video inventory cost?"
- "I want to create a deal for 5M display impressions"
"""

        return {
            "text": text.strip(),
            "type": "general",
        }

    def get_conversation_history(self) -> list[dict[str, str]]:
        """Get the conversation history."""
        return self._conversation_history.copy()

    def clear_history(self) -> None:
        """Clear the conversation history."""
        self._conversation_history = []

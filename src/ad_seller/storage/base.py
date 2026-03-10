# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Base storage backend interface."""

from abc import ABC, abstractmethod
from typing import Any, Optional


class StorageBackend(ABC):
    """Abstract base class for storage backends."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to storage backend."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to storage backend."""
        pass

    @abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        """Retrieve a value by key."""
        pass

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Store a value with optional TTL (seconds)."""
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete a key. Returns True if key existed."""
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        pass

    @abstractmethod
    async def keys(self, pattern: str = "*") -> list[str]:
        """List keys matching pattern."""
        pass

    # Higher-level operations for common use cases

    async def get_product(self, product_id: str) -> Optional[dict]:
        """Get a product by ID."""
        return await self.get(f"product:{product_id}")

    async def set_product(self, product_id: str, product_data: dict) -> None:
        """Store a product."""
        await self.set(f"product:{product_id}", product_data)

    async def get_proposal(self, proposal_id: str) -> Optional[dict]:
        """Get a proposal by ID."""
        return await self.get(f"proposal:{proposal_id}")

    async def set_proposal(self, proposal_id: str, proposal_data: dict) -> None:
        """Store a proposal."""
        await self.set(f"proposal:{proposal_id}", proposal_data)

    async def get_deal(self, deal_id: str) -> Optional[dict]:
        """Get a deal by ID."""
        return await self.get(f"deal:{deal_id}")

    async def set_deal(self, deal_id: str, deal_data: dict) -> None:
        """Store a deal."""
        await self.set(f"deal:{deal_id}", deal_data)

    async def list_products(self) -> list[dict]:
        """List all products."""
        keys = await self.keys("product:*")
        products = []
        for key in keys:
            product = await self.get(key)
            if product:
                products.append(product)
        return products

    async def list_proposals(self) -> list[dict]:
        """List all proposals."""
        keys = await self.keys("proposal:*")
        proposals = []
        for key in keys:
            proposal = await self.get(key)
            if proposal:
                proposals.append(proposal)
        return proposals

    async def list_deals(self) -> list[dict]:
        """List all deals."""
        keys = await self.keys("deal:*")
        deals = []
        for key in keys:
            deal = await self.get(key)
            if deal:
                deals.append(deal)
        return deals

    # Session operations

    async def get_session(self, session_id: str) -> Optional[dict]:
        """Get a session by ID."""
        return await self.get(f"session:{session_id}")

    async def set_session(
        self, session_id: str, session_data: dict, ttl: Optional[int] = None
    ) -> None:
        """Store a session with optional TTL."""
        await self.set(f"session:{session_id}", session_data, ttl=ttl)

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        return await self.delete(f"session:{session_id}")

    async def list_sessions(self) -> list[dict]:
        """List all sessions."""
        keys = await self.keys("session:*")
        sessions = []
        for key in keys:
            if key.startswith("session_index:"):
                continue
            session = await self.get(key)
            if session:
                sessions.append(session)
        return sessions

    async def get_buyer_sessions(self, buyer_pricing_key: str) -> list[dict]:
        """Get all sessions for a buyer identity."""
        index_key = f"session_index:buyer:{buyer_pricing_key}"
        session_ids = await self.get(index_key) or []
        sessions = []
        for sid in session_ids:
            session = await self.get(f"session:{sid}")
            if session:
                sessions.append(session)
        return sessions

    async def add_session_to_buyer_index(
        self, session_id: str, buyer_pricing_key: str
    ) -> None:
        """Add a session to the buyer index."""
        index_key = f"session_index:buyer:{buyer_pricing_key}"
        existing = await self.get(index_key) or []
        if session_id not in existing:
            existing.append(session_id)
            await self.set(index_key, existing)

    async def remove_session_from_buyer_index(
        self, session_id: str, buyer_pricing_key: str
    ) -> None:
        """Remove a session from the buyer index."""
        index_key = f"session_index:buyer:{buyer_pricing_key}"
        existing = await self.get(index_key) or []
        if session_id in existing:
            existing.remove(session_id)
            await self.set(index_key, existing)

    # Package operations

    async def get_package(self, package_id: str) -> Optional[dict]:
        """Get a package by ID."""
        return await self.get(f"package:{package_id}")

    async def set_package(self, package_id: str, package_data: dict) -> None:
        """Store a package."""
        await self.set(f"package:{package_id}", package_data)

    async def delete_package(self, package_id: str) -> bool:
        """Delete a package."""
        return await self.delete(f"package:{package_id}")

    async def list_packages(self) -> list[dict]:
        """List all packages."""
        keys = await self.keys("package:*")
        packages = []
        for key in keys:
            package = await self.get(key)
            if package:
                packages.append(package)
        return packages

    async def get_media_kit(self, seller_org_id: str) -> Optional[dict]:
        """Get media kit metadata for a seller organization."""
        return await self.get(f"media_kit:{seller_org_id}")

    async def set_media_kit(self, seller_org_id: str, data: dict) -> None:
        """Store media kit metadata for a seller organization."""
        await self.set(f"media_kit:{seller_org_id}", data)

    # Quote operations

    async def get_quote(self, quote_id: str) -> Optional[dict]:
        """Get a quote by ID."""
        return await self.get(f"quote:{quote_id}")

    async def set_quote(
        self, quote_id: str, quote_data: dict, ttl: int = 86400
    ) -> None:
        """Store a quote with TTL (default 24 hours)."""
        await self.set(f"quote:{quote_id}", quote_data, ttl=ttl)

    async def list_quotes(self, filters: Optional[dict] = None) -> list[dict]:
        """List quotes, optionally filtered by status, buyer, or product."""
        keys = await self.keys("quote:*")
        quotes = []
        for key in keys:
            quote = await self.get(key)
            if quote is None:
                continue
            if filters:
                if "status" in filters and quote.get("status") != filters["status"]:
                    continue
                if "product_id" in filters and quote.get("product", {}).get(
                    "product_id"
                ) != filters["product_id"]:
                    continue
            quotes.append(quote)
        return quotes

    # Order operations

    async def get_order(self, order_id: str) -> Optional[dict]:
        """Get an order state machine by ID."""
        return await self.get(f"order:{order_id}")

    async def set_order(self, order_id: str, order_data: dict) -> None:
        """Store an order state machine."""
        await self.set(f"order:{order_id}", order_data)

    async def delete_order(self, order_id: str) -> bool:
        """Delete an order."""
        return await self.delete(f"order:{order_id}")

    async def list_orders(self, filters: Optional[dict] = None) -> list[dict]:
        """List orders, optionally filtered by status."""
        keys = await self.keys("order:*")
        orders = []
        for key in keys:
            order = await self.get(key)
            if order is None:
                continue
            if filters:
                if "status" in filters and order.get("status") != filters["status"]:
                    continue
            orders.append(order)
        return orders

    # Change request operations

    async def get_change_request(self, cr_id: str) -> Optional[dict]:
        """Get a change request by ID."""
        return await self.get(f"change_request:{cr_id}")

    async def set_change_request(self, cr_id: str, cr_data: dict) -> None:
        """Store a change request."""
        await self.set(f"change_request:{cr_id}", cr_data)

    async def list_change_requests(self, filters: Optional[dict] = None) -> list[dict]:
        """List change requests, optionally filtered by order_id or status."""
        keys = await self.keys("change_request:*")
        results = []
        for key in keys:
            cr = await self.get(key)
            if cr is None:
                continue
            if filters:
                if "order_id" in filters and cr.get("order_id") != filters["order_id"]:
                    continue
                if "status" in filters and cr.get("status") != filters["status"]:
                    continue
            results.append(cr)
        return results

    # Negotiation operations

    async def get_negotiation(self, proposal_id: str) -> Optional[dict]:
        """Get negotiation history by proposal ID."""
        return await self.get(f"negotiation:{proposal_id}")

    async def set_negotiation(self, proposal_id: str, data: dict) -> None:
        """Store negotiation history."""
        await self.set(f"negotiation:{proposal_id}", data)

    # Agent registry operations

    async def get_agent(self, agent_id: str) -> Optional[dict]:
        """Get a registered agent by ID."""
        return await self.get(f"agent:{agent_id}")

    async def set_agent(self, agent_id: str, agent_data: dict) -> None:
        """Store a registered agent."""
        await self.set(f"agent:{agent_id}", agent_data)

    async def delete_agent(self, agent_id: str) -> bool:
        """Delete a registered agent."""
        return await self.delete(f"agent:{agent_id}")

    async def list_agents(self) -> list[dict]:
        """List all registered agents."""
        keys = await self.keys("agent:*")
        agents = []
        for key in keys:
            if key.startswith("agent_url_index:"):
                continue
            agent = await self.get(key)
            if agent:
                agents.append(agent)
        return agents

# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Abstract deal-sync client interface.

Deal-sync connectors push negotiated deals to an external deal
synchronization service (e.g. the IAB deals-api-mcp server), which
propagates them to buyer-side providers. This is a peer of the other
connector families:
  - AdServerClient:  inventory sync, deal setup in the publisher's ad server
  - SSPClient:       deal distribution through SSP exchanges to DSPs
  - DealSyncClient:  deal sync through an external deal-sync service

Reuses the normalized deal models from ssp_base (SSPDeal,
SSPDealCreateRequest, SSPDealStatus); giving this family its own
models is out of scope for the connector split.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar, Optional

from .ssp_base import SSPDeal, SSPDealCreateRequest, SSPDealStatus


class DealSyncProvider(str, Enum):
    """Known deal-sync providers (extensible via config)."""

    DEALS_API_MCP = "deals_api_mcp"


class DealSyncClient(ABC):
    """Abstract base class for deal-sync integrations.

    Each provider implementation must provide these methods.
    The deal-sync registry manages configured providers.
    """

    channel: ClassVar[str] = "deal_sync"
    provider: DealSyncProvider
    provider_name: str = "Unknown Deal Sync Provider"

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the deal-sync service."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the deal-sync service."""
        ...

    async def __aenter__(self) -> "DealSyncClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.disconnect()

    @abstractmethod
    async def create_deal(self, request: SSPDealCreateRequest) -> SSPDeal:
        """Create a new deal on the deal-sync service."""
        ...

    @abstractmethod
    async def get_deal(self, deal_id: str) -> SSPDeal:
        """Get deal details (and sync status) by provider-internal ID."""
        ...

    @abstractmethod
    async def list_deals(
        self,
        *,
        status: Optional[SSPDealStatus] = None,
        limit: int = 100,
    ) -> list[SSPDeal]:
        """List deals on this provider."""
        ...

    @abstractmethod
    async def update_deal(self, deal_id: str, updates: dict[str, Any]) -> SSPDeal:
        """Update mutable deal attributes."""
        ...

    async def health_check(self) -> bool:
        """Check if the connection is healthy. Override for custom logic."""
        return True


class DealSyncRegistry:
    """Registry for configured deal-sync clients, keyed by provider name."""

    def __init__(self) -> None:
        self._clients: dict[str, DealSyncClient] = {}
        self._default: Optional[str] = None

    def register(self, name: str, client: DealSyncClient) -> None:
        """Register a deal-sync client by provider name."""
        self._clients[name] = client
        if self._default is None:
            self._default = name

    def get_client(self, name: str) -> DealSyncClient:
        """Get a deal-sync client by provider name."""
        if name not in self._clients:
            raise KeyError(
                f"Deal-sync provider '{name}' not registered. "
                f"Available: {list(self._clients.keys())}"
            )
        return self._clients[name]

    def get_default(self) -> DealSyncClient:
        """Get the default (first-registered) deal-sync client."""
        if not self._default:
            raise RuntimeError("No deal-sync clients registered")
        return self._clients[self._default]

    def list_providers(self) -> list[str]:
        """List registered provider names."""
        return list(self._clients.keys())

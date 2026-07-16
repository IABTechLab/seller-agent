# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Agent Registry Client — query AAMP and other registries.

Provides a base interface for agent registry interactions and concrete
implementations. Additional registry providers can be added by
subclassing BaseRegistryClient.

fetch_agent_card() is functional (fetches real .well-known/agent.json).

Two AAMP implementations exist (selected by config, not code — see
:func:`build_registry_clients`):

- :class:`AampApiRegistryClient` — the REAL IAB agent registry API
  (``/api/agents``, ``{"success": true, "data": ...}`` envelope, Bearer
  JWT), spoken via the shared contract library's RegistryClient. Used
  when ``AAMP_REGISTRY_URL`` is configured.
- :class:`AAMPRegistryClient` — the legacy stub retained as the default
  for tests/local dev when no real registry is configured.
"""

import hashlib
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx
from iab_agentic_primitives.registry_client import (
    ENV_BACKEND,
    RegistryError,
)
from iab_agentic_primitives.registry_client import (
    RegistryClient as LibRegistryClient,
)
from pydantic import ValidationError

from ..models.agent_registry import AgentCard

logger = logging.getLogger(__name__)


# =============================================================================
# Base Registry Client (extensible for future registries)
# =============================================================================


class BaseRegistryClient(ABC):
    """Abstract base for agent registry clients.

    Subclass this to integrate with vendor-specific registries,
    private enterprise registries, or future IAB standards.
    """

    def __init__(self, registry_id: str, registry_name: str, registry_url: str):
        self.registry_id = registry_id
        self.registry_name = registry_name
        self.registry_url = registry_url.rstrip("/")

    @abstractmethod
    async def verify_registration(self, agent_url: str) -> tuple[bool, Optional[str]]:
        """Check if an agent URL is registered in this registry.

        Returns:
            (is_registered, external_agent_id) — external_agent_id is the
            ID assigned by this registry, or None if not registered.
        """

    @abstractmethod
    async def lookup_agent(self, agent_id: str) -> Optional[dict]:
        """Look up an agent by its registry-assigned ID.

        Returns raw registry data or None if not found.
        """

    @abstractmethod
    async def search_agents(
        self,
        agent_type: Optional[str] = None,
        inventory_types: Optional[list[str]] = None,
    ) -> list[dict]:
        """Search registry for agents matching criteria."""


# =============================================================================
# Agent Card Fetcher (shared utility, works with any agent)
# =============================================================================


async def fetch_agent_card(agent_url: str, timeout: float = 15.0) -> Optional[AgentCard]:
    """Fetch an agent's card from its .well-known endpoint.

    This is registry-independent — any A2A-compliant agent can serve
    an agent card at {url}/.well-known/agent.json.

    Args:
        agent_url: Base URL of the agent (e.g. https://seller.example.com)
        timeout: HTTP timeout in seconds

    Returns:
        AgentCard if successfully fetched and parsed, None otherwise.
    """
    url = f"{agent_url.rstrip('/')}/.well-known/agent.json"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            if response.status_code == 200:
                return AgentCard(**response.json())
    except (httpx.HTTPError, ValidationError, ValueError) as e:
        logger.debug("Failed to fetch agent card from %s: %s", url, e)
    return None


# =============================================================================
# IAB Tech Lab AAMP Registry Client
# =============================================================================

# Known AAMP test agent URLs for stub verification
_AAMP_TEST_AGENTS = {
    "https://agentic-direct-server-hwgrypmndq-uk.a.run.app",
}


class AAMPRegistryClient(BaseRegistryClient):
    """Client for the IAB Tech Lab AAMP Agent Registry.

    The AAMP registry is the primary trust layer for agentic advertising.
    Launched March 2026 as part of IAB Tech Lab's Tools Portal.

    verify_registration() and lookup_agent() are stubbed pending the
    public AAMP API specification. They return realistic mock data.
    fetch_agent_card() is functional (shared utility above).

    TODO: Replace stubs when AAMP publishes registry API spec
    TODO: Add webhook support for registry update notifications
    """

    def __init__(
        self,
        registry_url: str = "https://tools.iabtechlab.com/agent-registry",
    ):
        super().__init__(
            registry_id="iab_aamp",
            registry_name="IAB Tech Lab AAMP",
            registry_url=registry_url,
        )

    async def verify_registration(self, agent_url: str) -> tuple[bool, Optional[str]]:
        """Check if an agent URL is registered in AAMP.

        STUB: Returns True for known IAB Tech Lab test URLs.
        Real implementation will query the AAMP registry API.
        """
        normalized = agent_url.rstrip("/")

        # Stub: known test agents are "registered"
        if normalized in _AAMP_TEST_AGENTS:
            ext_id = f"aamp-{hashlib.sha256(normalized.encode()).hexdigest()[:12]}"
            return True, ext_id

        # Stub: unknown agents are not registered
        logger.debug("[STUB] AAMP verify_registration(%s) → not registered", agent_url)
        return False, None

    async def lookup_agent(self, agent_id: str) -> Optional[dict]:
        """Look up an agent by AAMP registry ID.

        STUB: Returns mock data for any ID starting with 'aamp-'.
        """
        if agent_id.startswith("aamp-"):
            return {
                "agent_id": agent_id,
                "registry": "iab_aamp",
                "status": "active",
                "verified": True,
                "note": "[STUB] Pending AAMP API integration",
            }
        return None

    async def search_agents(
        self,
        agent_type: Optional[str] = None,
        inventory_types: Optional[list[str]] = None,
    ) -> list[dict]:
        """Search AAMP registry for agents.

        STUB: Returns empty list. Real implementation will query
        the AAMP search API with filters.
        """
        logger.debug(
            "[STUB] AAMP search_agents(type=%s, inventory=%s) → []",
            agent_type,
            inventory_types,
        )
        return []


# =============================================================================
# Real IAB Agent Registry client (EP-5.1)
# =============================================================================


class _TolerantLibClient(LibRegistryClient):
    """Library client tolerant of the hosted registry's null list fields.

    KNOWN LIB GAP (EP-5.1, verified against registry-uat.iabtechlab.com):
    the real registry serializes unset list-typed columns as JSON ``null``
    (``endorsements``, ``iab_capabilities``, ``iab_subcategories``, ...),
    but the library's ``RegistryAgent`` declares them as ``list[...]`` with
    a default_factory, so ``model_validate`` rejects ``null``. Until the
    lib coerces ``None`` to the field default, this shim drops null-valued
    keys from envelope payloads before validation (every RegistryAgent
    field except ``agent_name``/``primary_domain`` is optional, so
    dropping is lossless).
    """

    @staticmethod
    def _scrub(record: Any) -> Any:
        if isinstance(record, dict):
            return {k: v for k, v in record.items() if v is not None}
        return record

    @staticmethod
    def _data(response: httpx.Response) -> Any:
        data = LibRegistryClient._data(response)
        if isinstance(data, dict):
            if isinstance(data.get("agents"), list):
                return {
                    **data,
                    "agents": [_TolerantLibClient._scrub(a) for a in data["agents"]],
                }
            return _TolerantLibClient._scrub(data)
        return data


def _normalize_endpoint(url: str) -> str:
    return url.strip().rstrip("/").lower()


class AampApiRegistryClient(BaseRegistryClient):
    """Client for the REAL IAB agent registry API (``/api/agents``).

    Speaks the actual registry protocol — ``{"success": true, "data": ...}``
    envelope, JWT ``Authorization: Bearer`` auth — through the shared
    contract library's RegistryClient, and adapts it onto the seller's
    :class:`BaseRegistryClient` surface so :class:`AgentRegistryService`
    is untouched.

    A fresh library client is created per operation so long-lived
    instances survive event-loop churn.

    Args:
        base_url: Registry base URL. Defaults to ``AAMP_REGISTRY_URL``.
        auth_token: Bearer JWT. Defaults to ``AAMP_REGISTRY_AUTH_TOKEN`` /
            ``AAMP_REGISTRY_TOKEN``. Never logged.
        timeout: HTTP request timeout in seconds. Defaults to 15.
        transport: Optional httpx transport (tests pass an ASGITransport
            wrapping the library's in-process registry double).
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        *,
        timeout: float = 15.0,
        transport: Any = None,
    ):
        resolved = base_url or os.environ.get("AAMP_REGISTRY_URL", "")
        super().__init__(
            registry_id="iab_aamp_api",
            registry_name="IAB Tech Lab Agent Registry",
            registry_url=resolved,
        )
        self._base_url = base_url
        self._auth_token = auth_token
        self._timeout = timeout
        self._transport = transport

    def _make_client(self) -> LibRegistryClient:
        """Fresh library client per operation (event-loop-churn safe)."""
        backend = os.environ.get(ENV_BACKEND) or ("IAB_SANDBOX" if self._base_url else None)
        return _TolerantLibClient(
            backend=backend,
            base_url=self._base_url,
            auth_token=self._auth_token,
            transport=self._transport,
            timeout=self._timeout,
        )

    async def verify_registration(self, agent_url: str) -> tuple[bool, Optional[str]]:
        """Check whether an agent endpoint URL is registered.

        The real API has no URL-lookup endpoint, so this lists agents and
        matches ``endpoint_url``. Errors degrade to (False, None).
        """
        wanted = _normalize_endpoint(agent_url)
        try:
            async with self._make_client() as client:
                agents = await client.list_agents()
        except (RegistryError, httpx.HTTPError, ValueError) as e:
            logger.warning("AAMP registry verify_registration failed: %s", e)
            return False, None
        for agent in agents:
            if agent.endpoint_url and _normalize_endpoint(agent.endpoint_url) == wanted:
                ext_id = str(agent.id) if agent.id is not None else None
                return True, ext_id
        return False, None

    async def lookup_agent(self, agent_id: str) -> Optional[dict]:
        """Fetch one agent card by registry id (``GET /api/agents/:id``)."""
        try:
            async with self._make_client() as client:
                agent = await client.get_agent(agent_id)
        except (RegistryError, httpx.HTTPError, ValueError) as e:
            logger.debug("AAMP registry lookup_agent(%s) failed: %s", agent_id, e)
            return None
        return agent.model_dump(mode="json")

    async def search_agents(
        self,
        agent_type: Optional[str] = None,
        inventory_types: Optional[list[str]] = None,
    ) -> list[dict]:
        """Search the registry for agents.

        The real API has no agent-type or inventory filter, so filters are
        applied client-side: ``agent_type`` against ``industry_roles`` and
        ``inventory_types`` against ``capabilities`` + ``iab_capabilities``.
        Errors degrade to an empty list.
        """
        try:
            async with self._make_client() as client:
                agents = await client.list_agents()
        except (RegistryError, httpx.HTTPError, ValueError) as e:
            logger.warning("AAMP registry search_agents failed: %s", e)
            return []
        if agent_type:
            wanted_role = agent_type.lower()
            agents = [
                a for a in agents if wanted_role in {r.lower() for r in a.industry_roles}
            ]
        if inventory_types:
            wanted = {t.lower() for t in inventory_types}
            agents = [
                a
                for a in agents
                if wanted & {c.lower() for c in [*a.capabilities, *a.iab_capabilities]}
            ]
        return [a.model_dump(mode="json") for a in agents]

    async def register_self(self, agent: dict) -> Optional[dict]:
        """Publish this seller's card to the registry (``POST /api/agents``).

        The registry requires ``agent_name`` + ``primary_domain`` (which
        must match the JWT's company domain) and an ``endpoint_url`` for
        remote agents. Returns the stored record, or None on failure.
        """
        try:
            async with self._make_client() as client:
                stored = await client.register_agent(agent)
        except (RegistryError, httpx.HTTPError, ValueError) as e:
            logger.warning("AAMP registry self-registration failed: %s", e)
            return None
        logger.info("Registered seller card in AAMP registry (id=%s)", stored.id)
        return stored.model_dump(mode="json")


# =============================================================================
# Registry client factory (config-swap seam)
# =============================================================================


def build_registry_clients(settings) -> list[BaseRegistryClient]:
    """Build the registry client list from settings — config, not code.

    When ``AAMP_REGISTRY_URL`` is configured, the real IAB agent registry
    client is used. Otherwise the legacy stub clients (primary +
    ``agent_registry_extra_urls``) are kept — the default for tests and
    local dev.
    """
    if getattr(settings, "aamp_registry_url", ""):
        return [
            AampApiRegistryClient(
                base_url=settings.aamp_registry_url,
                auth_token=settings.aamp_registry_auth_token or None,
            )
        ]
    clients: list[BaseRegistryClient] = [
        AAMPRegistryClient(registry_url=settings.agent_registry_url)
    ]
    if settings.agent_registry_extra_urls:
        for url in settings.agent_registry_extra_urls.split(","):
            url = url.strip()
            if url:
                # Extra registries use the stub client for now (same protocol).
                clients.append(AAMPRegistryClient(registry_url=url))
    return clients

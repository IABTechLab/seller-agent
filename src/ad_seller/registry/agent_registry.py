# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Local Agent Registry Service.

Manages a local registry of known agents backed by the existing
StorageBackend. Integrates with one or more external registries
(IAB AAMP primary, others extensible) for trust verification.

Key method: resolve_agent_access() — given an agent URL, determines
the agent's trust status and maximum access tier by checking:
1. Local registry (fast path)
2. External registries (AAMP, etc.) for verification
3. Agent's .well-known/agent.json for card data

The resulting AccessTier ceiling flows into BuyerContext.max_access_tier,
which caps the agent's effective tier through the existing pricing/
media-kit/negotiation infrastructure.
"""

import hashlib
import logging
import uuid
from datetime import datetime
from typing import Optional

from iab_agentic_primitives.registry_client import VerifiedTrust

from ..clients.agent_registry_client import (
    AAMPRegistryClient,
    BaseRegistryClient,
    fetch_agent_card,
)
from ..models.agent_registry import (
    TRUST_TO_TIER_MAP,
    AgentCard,
    AgentType,
    RegisteredAgent,
    RegistrySource,
    TrustStatus,
)
from ..models.buyer_identity import AccessTier
from ..storage.base import StorageBackend

logger = logging.getLogger(__name__)


def _url_hash(url: str) -> str:
    """Deterministic short hash of a URL for index keys."""
    return hashlib.sha256(url.rstrip("/").encode()).hexdigest()[:16]


# Ordered tiers for min() comparison
_TIER_ORDER = [AccessTier.PUBLIC, AccessTier.SEAT, AccessTier.AGENCY, AccessTier.ADVERTISER]


class AgentRegistryService:
    """Local agent registry with multi-registry external verification.

    Usage:
        storage = await get_storage()
        aamp = AAMPRegistryClient()
        service = AgentRegistryService(storage, registry_clients=[aamp])

        agent, tier = await service.resolve_agent_access("https://buyer.example.com")
        if tier is None:
            # Agent is blocked — reject request
        else:
            # tier is the max AccessTier this agent can claim
    """

    def __init__(
        self,
        storage: StorageBackend,
        registry_clients: Optional[list[BaseRegistryClient]] = None,
    ):
        self._storage = storage
        self._registry_clients: list[BaseRegistryClient] = registry_clients or [
            AAMPRegistryClient()
        ]

    # =========================================================================
    # CRUD Operations
    # =========================================================================

    async def register_agent(
        self,
        agent_card: AgentCard,
        agent_type: AgentType = AgentType.BUYER,
        trust_status: TrustStatus = TrustStatus.UNKNOWN,
        registry_sources: Optional[list[RegistrySource]] = None,
    ) -> RegisteredAgent:
        """Register or update an agent in the local registry."""
        # Check if already registered by URL
        existing = await self.get_agent_by_url(agent_card.url)
        if existing:
            # Update existing entry
            existing.agent_card = agent_card
            existing.trust_status = trust_status
            existing.last_seen = datetime.utcnow()
            if registry_sources:
                # Merge sources — don't duplicate by registry_id
                existing_ids = {s.registry_id for s in existing.registry_sources}
                for src in registry_sources:
                    if src.registry_id not in existing_ids:
                        existing.registry_sources.append(src)
            await self._save_agent(existing)
            return existing

        agent = RegisteredAgent(
            agent_id=f"agent-{uuid.uuid4().hex[:8]}",
            agent_card=agent_card,
            agent_type=agent_type,
            trust_status=trust_status,
            registry_sources=registry_sources or [],
            registered_at=datetime.utcnow(),
        )
        await self._save_agent(agent)
        return agent

    async def get_agent(self, agent_id: str) -> Optional[RegisteredAgent]:
        """Look up an agent by local ID."""
        data = await self._storage.get_agent(agent_id)
        if data:
            return RegisteredAgent(**data)
        return None

    async def get_agent_by_url(self, url: str) -> Optional[RegisteredAgent]:
        """Look up an agent by its service URL."""
        index_key = f"agent_url_index:{_url_hash(url)}"
        agent_id = await self._storage.get(index_key)
        if agent_id:
            return await self.get_agent(agent_id)
        return None

    async def list_agents(
        self,
        agent_type: Optional[AgentType] = None,
        trust_status: Optional[TrustStatus] = None,
    ) -> list[RegisteredAgent]:
        """List agents with optional filters."""
        all_data = await self._storage.list_agents()
        agents = [RegisteredAgent(**d) for d in all_data]
        if agent_type:
            agents = [a for a in agents if a.agent_type == agent_type]
        if trust_status:
            agents = [a for a in agents if a.trust_status == trust_status]
        return agents

    async def update_trust_status(
        self,
        agent_id: str,
        new_status: TrustStatus,
        notes: Optional[str] = None,
    ) -> Optional[RegisteredAgent]:
        """Update an agent's trust status."""
        agent = await self.get_agent(agent_id)
        if not agent:
            return None
        agent.trust_status = new_status
        if notes:
            agent.notes = notes
        await self._save_agent(agent)
        logger.info(
            "Agent %s (%s) trust updated to %s",
            agent_id,
            agent.agent_card.name,
            new_status.value,
        )
        return agent

    async def remove_agent(self, agent_id: str) -> bool:
        """Remove an agent from the local registry."""
        agent = await self.get_agent(agent_id)
        if not agent:
            return False
        # Clean up URL index
        index_key = f"agent_url_index:{_url_hash(agent.agent_card.url)}"
        await self._storage.delete(index_key)
        return await self._storage.delete_agent(agent_id)

    async def record_interaction(self, agent_id: str) -> None:
        """Record that we interacted with this agent."""
        agent = await self.get_agent(agent_id)
        if agent:
            agent.last_seen = datetime.utcnow()
            agent.interaction_count += 1
            await self._save_agent(agent)

    # =========================================================================
    # Access Resolution (the key method)
    # =========================================================================

    async def resolve_agent_access(
        self, agent_url: str
    ) -> tuple[Optional[RegisteredAgent], Optional[AccessTier]]:
        """Resolve an agent's trust status and maximum access tier.

        This is the primary integration point. Given an agent URL:
        1. Check local registry (fast path)
        2. If not found, fetch agent card from .well-known
        3. Check all configured external registries for verification
        4. Register locally with appropriate trust status
        5. Return (agent, max_access_tier)

        Returns:
            (RegisteredAgent, AccessTier) — the agent and its tier ceiling.
            AccessTier is None if the agent is blocked (caller must reject).
            RegisteredAgent is None if the agent card couldn't be fetched.
        """
        # 1. Check local registry
        agent = await self.get_agent_by_url(agent_url)
        if agent:
            if agent.is_blocked:
                return agent, None
            await self.record_interaction(agent.agent_id)
            return agent, agent.effective_access_ceiling

        # 2. Fetch agent card
        card = await fetch_agent_card(agent_url)
        if not card:
            logger.warning("Could not fetch agent card from %s", agent_url)
            return None, AccessTier.PUBLIC

        # 3. Check external registries
        registry_sources: list[RegistrySource] = []
        is_registered_anywhere = False

        for client in self._registry_clients:
            is_registered, ext_id = await client.verify_registration(agent_url)
            if is_registered:
                is_registered_anywhere = True
                registry_sources.append(
                    RegistrySource(
                        registry_id=client.registry_id,
                        registry_name=client.registry_name,
                        registry_url=client.registry_url,
                        external_agent_id=ext_id,
                        verified_at=datetime.utcnow(),
                    )
                )

        # 4. Determine trust status
        if is_registered_anywhere:
            trust = TrustStatus.REGISTERED
        else:
            trust = TrustStatus.UNKNOWN

        # 5. Register locally
        agent = await self.register_agent(
            agent_card=card,
            agent_type=AgentType.BUYER,  # Default; operator can change
            trust_status=trust,
            registry_sources=registry_sources,
        )

        tier = TRUST_TO_TIER_MAP.get(trust)
        return agent, tier

    async def verify_buyer_trust(
        self, agent_url: str
    ) -> tuple[Optional[RegisteredAgent], Optional[AccessTier], VerifiedTrust]:
        """Resolve an agent AND build the auditable trust verdict (EP-5.2).

        Wraps :meth:`resolve_agent_access` and maps the outcome onto the
        shared contract library's ``VerifiedTrust`` primitive so callers can
        persist the verification result in an auditable store.

        Mapping notes:
        - ``verified`` is True when the agent's trust status grants more
          than the floor (REGISTERED / APPROVED / PREFERRED). UNKNOWN and
          BLOCKED agents are not verified.
        - ``verification_status`` carries the seller's TrustStatus value.
        - ``domain_verified`` / ``iab_member`` / ``badge_available`` default
          False: the seller's BaseRegistryClient surface only exposes
          (is_registered, external_id), not the real registry's full
          verification triplet. Enriching these requires widening the
          EP-5.1 client surface (documented follow-up).

        Returns:
            (agent, ceiling, verdict) — ceiling is None when blocked
            (caller must reject with 403).
        """
        agent, ceiling = await self.resolve_agent_access(agent_url)

        if agent is None:
            # No card, no registry record — unverifiable, floor tier.
            verdict = VerifiedTrust(
                agent_id=agent_url,
                verified=False,
                verification_status=TrustStatus.UNKNOWN.value,
            )
            return None, ceiling, verdict

        primary_source = agent.registry_sources[0] if agent.registry_sources else None
        external_id = primary_source.external_agent_id if primary_source else None
        registry_id = primary_source.registry_id if primary_source else "local"
        verdict = VerifiedTrust(
            agent_id=external_id or agent_url,
            verified=agent.trust_status
            in (TrustStatus.REGISTERED, TrustStatus.APPROVED, TrustStatus.PREFERRED),
            verification_status=agent.trust_status.value,
            registry_id=registry_id,
        )
        return agent, ceiling, verdict

    # =========================================================================
    # Tier Computation (static utility)
    # =========================================================================

    @staticmethod
    def compute_effective_tier(
        trust_status: TrustStatus,
        claimed_tier: AccessTier,
    ) -> Optional[AccessTier]:
        """Compute effective tier = min(trust_ceiling, claimed_tier).

        Returns None if the agent is blocked.
        """
        ceiling = TRUST_TO_TIER_MAP.get(trust_status)
        if ceiling is None:
            return None  # Blocked
        claimed_idx = _TIER_ORDER.index(claimed_tier)
        ceiling_idx = _TIER_ORDER.index(ceiling)
        return _TIER_ORDER[min(claimed_idx, ceiling_idx)]

    # =========================================================================
    # Internal
    # =========================================================================

    async def _save_agent(self, agent: RegisteredAgent) -> None:
        """Persist agent and maintain URL index."""
        await self._storage.set_agent(agent.agent_id, agent.model_dump(mode="json"))
        # Maintain URL → agent_id index
        index_key = f"agent_url_index:{_url_hash(agent.agent_card.url)}"
        await self._storage.set(index_key, agent.agent_id)

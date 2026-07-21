# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""EP-5.2: server-side trust-tier verification + VerifiedTrust persistence.

The seller must verify the buyer's CLAIMED tier against the agent registry
and cap the effective tier at the registry-verified ceiling on every
price-moving path. Before this epic the counter endpoint applied no ceiling
at all and a buyer could self-assert ADVERTISER pricing simply by
populating ``advertiser_id`` in the request body.

Fail-closed semantics under test:
- Self-asserted identity claims never raise the effective tier above what
  the registry verifies for the presenting agent.
- Unverifiable buyers (no registry-resolvable ``agent_url`` and no
  seller-issued API key) get the floor tier (PUBLIC).
- Unknown agents (card fetched but not found in any registry) floor.
- Blocked agents are rejected with 403 before any pricing data leaks.
- Every registry verification outcome is persisted as the shared contract
  library's ``VerifiedTrust`` primitive in an auditable store, with the
  EP-0.2 durable-fallback behavior on storage failure.

Seller-issued API keys are the EP-4.5 verified-principal path: the key's
identity is seller-approved, so it is NOT floored (but a registry ceiling,
when resolvable, still caps it).
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

# Stub broken flow modules (pre-existing @listen() bugs with CrewAI version
# mismatch) before any import of ad_seller.flows triggers __init__.py.
_broken_flows = [
    "ad_seller.flows.discovery_inquiry_flow",
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

from datetime import datetime  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport  # noqa: E402
from iab_agentic_primitives.registry_client import VerifiedTrust  # noqa: E402
from iab_agentic_primitives.sandbox_registry.real_double import (  # noqa: E402
    create_registry_double,
)

from ad_seller.clients.agent_registry_client import (  # noqa: E402
    AampApiRegistryClient,
    BaseRegistryClient,
    build_registry_clients,
)
from ad_seller.interfaces.api.main import _get_optional_api_key_record, app  # noqa: E402
from ad_seller.models.agent_registry import (  # noqa: E402
    AgentCard,
    AgentProvider,
    RegisteredAgent,
    TrustStatus,
)
from ad_seller.models.api_key import (  # noqa: E402
    API_KEY_STORAGE_PREFIX,
    ApiKeyRecord,
    generate_api_key,
    hash_api_key,
)
from ad_seller.models.buyer_identity import AccessTier, BuyerIdentity  # noqa: E402
from ad_seller.models.core import DealType, PricingModel  # noqa: E402
from ad_seller.models.flow_state import ProductDefinition  # noqa: E402
from ad_seller.registry.agent_registry import AgentRegistryService  # noqa: E402
from ad_seller.storage.trust_verifications import TrustVerificationStore  # noqa: E402

BUYER_URL = "https://buyer.example.com"


# =============================================================================
# Helpers / fixtures
# =============================================================================


class FakeRegistryClient(BaseRegistryClient):
    """In-process registry client double (no network)."""

    def __init__(self, registered: bool = True, ext_id: str = "ext-1"):
        super().__init__(
            registry_id="fake_registry",
            registry_name="Fake Registry",
            registry_url="http://registry.test",
        )
        self._registered = registered
        self._ext_id = ext_id

    async def verify_registration(self, agent_url):
        if self._registered:
            return True, self._ext_id
        return False, None

    async def lookup_agent(self, agent_id):
        return None

    async def search_agents(self, agent_type=None, inventory_types=None):
        return []


def _agent_card(url: str = BUYER_URL) -> AgentCard:
    return AgentCard(
        name="Test Buyer Agent",
        description="A buyer agent",
        url=url,
        provider=AgentProvider(name="Test DSP"),
    )


def _make_product(**overrides):
    defaults = dict(
        product_id="ctv-premium-sports",
        name="Premium CTV - Sports",
        description="Premium CTV sports inventory",
        inventory_type="ctv",
        supported_deal_types=[DealType.PREFERRED_DEAL, DealType.PROGRAMMATIC_GUARANTEED],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=35.0,
        floor_cpm=28.0,
        minimum_impressions=100000,
    )
    defaults.update(overrides)
    return ProductDefinition(**defaults)


def _mock_catalog():
    products = {"ctv-premium-sports": _make_product()}
    return {"products": products, "inventory_types": ["ctv"]}


@pytest.fixture
def mock_storage():
    """In-memory dict-backed mock storage (matches house pattern)."""
    store = {}
    storage = AsyncMock()
    storage.get = AsyncMock(side_effect=lambda k: store.get(k))
    storage.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    storage.get_agent = AsyncMock(side_effect=lambda aid: store.get(f"agent:{aid}"))
    storage.set_agent = AsyncMock(
        side_effect=lambda aid, data: store.__setitem__(f"agent:{aid}", data)
    )
    storage.get_quote = AsyncMock(side_effect=lambda qid: store.get(f"quote:{qid}"))
    storage.set_quote = AsyncMock(
        side_effect=lambda qid, data, ttl=86400: store.__setitem__(f"quote:{qid}", data)
    )
    storage.get_proposal = AsyncMock(side_effect=lambda pid: store.get(f"proposal:{pid}"))
    storage.get_product = AsyncMock(side_effect=lambda pid: store.get(f"product:{pid}"))
    storage.get_negotiation = AsyncMock(
        side_effect=lambda pid: store.get(f"negotiation:{pid}")
    )
    storage.set_negotiation = AsyncMock(
        side_effect=lambda pid, data: store.__setitem__(f"negotiation:{pid}", data)
    )
    storage._store = store
    return storage


@pytest.fixture
def client():
    app.dependency_overrides[_get_optional_api_key_record] = lambda: None
    transport = ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


def _service(mock_storage, client_double) -> AgentRegistryService:
    return AgentRegistryService(mock_storage, registry_clients=[client_double])


def _patch_registry(service):
    """Route the wire edge's registry resolution to the given service."""
    return patch(
        "ad_seller.interfaces.api.deps._get_registry_service",
        new=AsyncMock(return_value=service),
    )


def _patch_card(card):
    return patch(
        "ad_seller.registry.agent_registry.fetch_agent_card",
        new=AsyncMock(return_value=card),
    )


def _trust_records(mock_storage) -> list[dict]:
    return [
        v for k, v in mock_storage._store.items() if k.startswith("verified_trust:")
    ]


def _seed_api_key(store, identity: BuyerIdentity) -> str:
    """Seed a seller-issued API key record; return the raw key.

    Same pattern as test_auth_header_binding: the key record is validated
    for real by ApiKeyService (EP-4.5 verified-principal plumbing).
    """
    raw_key = generate_api_key()
    record = ApiKeyRecord(
        key_id="key-ep52",
        key_hash=hash_api_key(raw_key),
        key_prefix_hint=raw_key[:12] + "...",
        identity=identity,
        label="EP-5.2 test key",
    )
    store[f"{API_KEY_STORAGE_PREFIX}{record.key_hash}"] = record.model_dump(mode="json")
    return raw_key


# =============================================================================
# AgentRegistryService.verify_buyer_trust — verdict construction
# =============================================================================


class TestVerifyBuyerTrust:
    async def test_registered_agent_gets_seat_ceiling_and_verified_primitive(
        self, mock_storage
    ):
        service = _service(mock_storage, FakeRegistryClient(registered=True))
        with _patch_card(_agent_card()):
            agent, ceiling, verdict = await service.verify_buyer_trust(BUYER_URL)

        assert agent is not None
        assert agent.trust_status == TrustStatus.REGISTERED
        assert ceiling == AccessTier.SEAT
        assert isinstance(verdict, VerifiedTrust)
        assert verdict.verified is True
        assert verdict.verification_status == "registered"
        assert verdict.agent_id == "ext-1"
        assert verdict.registry_id == "fake_registry"

    async def test_unknown_agent_with_card_floors_to_public(self, mock_storage):
        service = _service(mock_storage, FakeRegistryClient(registered=False))
        with _patch_card(_agent_card()):
            agent, ceiling, verdict = await service.verify_buyer_trust(BUYER_URL)

        assert agent is not None
        assert ceiling == AccessTier.PUBLIC
        assert verdict.verified is False
        assert verdict.verification_status == "unknown"

    async def test_unfetchable_card_floors_to_public_unverified(self, mock_storage):
        service = _service(mock_storage, FakeRegistryClient(registered=True))
        with _patch_card(None):
            agent, ceiling, verdict = await service.verify_buyer_trust(BUYER_URL)

        assert agent is None
        assert ceiling == AccessTier.PUBLIC
        assert verdict.verified is False
        assert verdict.agent_id == BUYER_URL

    async def test_blocked_agent_returns_none_ceiling(self, mock_storage):
        blocked = RegisteredAgent(
            agent_id="agent-blocked1",
            agent_card=_agent_card(),
            trust_status=TrustStatus.BLOCKED,
            registered_at=datetime(2026, 3, 1),
        )
        mock_storage._store["agent:agent-blocked1"] = blocked.model_dump(mode="json")
        # URL index lookup goes through storage.get
        original_get = mock_storage.get.side_effect
        mock_storage.get.side_effect = lambda k: (
            "agent-blocked1" if k.startswith("agent_url_index:") else original_get(k)
        )

        service = _service(mock_storage, FakeRegistryClient(registered=True))
        agent, ceiling, verdict = await service.verify_buyer_trust(BUYER_URL)

        assert agent is not None and agent.is_blocked
        assert ceiling is None
        assert verdict.verified is False
        assert verdict.verification_status == "blocked"

    async def test_real_registry_double_end_to_end(self, mock_storage):
        """The EP-5.1 real-API client against the lib's in-process double."""
        double = create_registry_double()
        double.state.store.agents[1] = {
            "id": 1,
            "agent_name": "Acme Buyer",
            "primary_domain": "acme-buyer.example.com",
            "type": "remote",
            "endpoint_url": "https://buyer.acme.example.com",
            "verification_status": "active",
            "domain_verified": True,
        }
        double.state.store.next_id = 2
        api_client = AampApiRegistryClient(
            base_url="http://registry.test",
            auth_token="user-token",
            transport=httpx.ASGITransport(app=double),
        )
        service = _service(mock_storage, api_client)
        with _patch_card(_agent_card("https://buyer.acme.example.com")):
            agent, ceiling, verdict = await service.verify_buyer_trust(
                "https://buyer.acme.example.com"
            )

        assert agent is not None
        assert ceiling == AccessTier.SEAT
        assert verdict.verified is True
        assert verdict.agent_id == "1"
        assert verdict.registry_id == "iab_aamp_api"

    async def test_default_stub_path_is_deterministic_without_registry_url(
        self, mock_storage, monkeypatch
    ):
        """No AAMP_REGISTRY_URL -> legacy stub clients; unknown agents floor."""
        monkeypatch.delenv("AAMP_REGISTRY_URL", raising=False)
        from ad_seller.config import get_settings

        clients = build_registry_clients(get_settings())
        service = AgentRegistryService(mock_storage, registry_clients=clients)
        with _patch_card(_agent_card()):
            agent, ceiling, verdict = await service.verify_buyer_trust(BUYER_URL)

        assert ceiling == AccessTier.PUBLIC
        assert verdict.verified is False


# =============================================================================
# TrustVerificationStore — auditable persistence
# =============================================================================


class TestTrustVerificationStore:
    async def test_record_round_trips_verified_trust_primitive(self, mock_storage):
        store = TrustVerificationStore(mock_storage)
        verdict = VerifiedTrust(
            agent_id="ext-1",
            verified=True,
            verification_status="registered",
            registry_id="fake_registry",
        )
        record = await store.record_verification(
            verdict,
            agent_url=BUYER_URL,
            claimed_tier="advertiser",
            effective_ceiling="seat",
            endpoint="POST /api/v1/quotes",
        )

        assert record["verification_id"]
        stored = mock_storage._store[f"verified_trust:{record['verification_id']}"]
        # The persisted payload IS the shared primitive (re-validates).
        assert VerifiedTrust.model_validate(stored["verified_trust"]).verified is True
        assert stored["claimed_tier"] == "advertiser"
        assert stored["effective_ceiling"] == "seat"
        assert stored["endpoint"] == "POST /api/v1/quotes"
        assert stored["agent_url"] == BUYER_URL

    async def test_list_for_agent_returns_history(self, mock_storage):
        store = TrustVerificationStore(mock_storage)
        verdict = VerifiedTrust(agent_id="ext-1", verified=True)
        await store.record_verification(
            verdict,
            agent_url=BUYER_URL,
            claimed_tier="advertiser",
            effective_ceiling="seat",
            endpoint="POST /api/v1/quotes",
        )
        await store.record_verification(
            verdict,
            agent_url=BUYER_URL,
            claimed_tier="agency",
            effective_ceiling="seat",
            endpoint="POST /proposals/{proposal_id}/counter",
        )

        history = await store.list_for_agent(BUYER_URL)
        assert len(history) == 2
        assert {h["claimed_tier"] for h in history} == {"advertiser", "agency"}

    async def test_storage_failure_falls_back_to_audit_jsonl(self, mock_storage):
        """EP-0.2 pattern: bus/storage failure -> durable JSONL fallback."""
        mock_storage.set = AsyncMock(side_effect=RuntimeError("db down"))
        store = TrustVerificationStore(mock_storage)
        verdict = VerifiedTrust(agent_id="ext-1", verified=True)

        with patch(
            "ad_seller.storage.trust_verifications.write_audit_fallback"
        ) as fallback:
            await store.record_verification(
                verdict,
                agent_url=BUYER_URL,
                claimed_tier="advertiser",
                effective_ceiling="seat",
                endpoint="POST /api/v1/quotes",
            )
        fallback.assert_called_once()
        assert fallback.call_args.args[0]["agent_url"] == BUYER_URL

    async def test_storage_and_fallback_failure_propagates(self, mock_storage):
        """Fail-closed: losing the audit record is an error, not a warning."""
        mock_storage.set = AsyncMock(side_effect=RuntimeError("db down"))
        store = TrustVerificationStore(mock_storage)
        verdict = VerifiedTrust(agent_id="ext-1", verified=True)

        with patch(
            "ad_seller.storage.trust_verifications.write_audit_fallback",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(OSError):
                await store.record_verification(
                    verdict,
                    agent_url=BUYER_URL,
                    claimed_tier="advertiser",
                    effective_ceiling="seat",
                    endpoint="POST /api/v1/quotes",
                )


# =============================================================================
# Price-moving path: POST /proposals/{proposal_id}/counter (the gap under test)
# =============================================================================


class TestCounterEndpointCeiling:
    async def test_self_asserted_advertiser_is_floored_to_public(self, client):
        """Anonymous buyer self-asserting advertiser_id must get the floor.

        Pre-EP-5.2 the counter endpoint applied NO ceiling: this context
        reached the negotiation engine at ADVERTISER tier.
        """
        counter = AsyncMock(return_value={"ok": True})
        with patch(
            "ad_seller.services.negotiation_service.counter_proposal", new=counter
        ):
            async with client as c:
                resp = await c.post(
                    "/proposals/prop-1/counter",
                    json={
                        "buyer_price": 15.0,
                        "buyer_tier": "advertiser",
                        "agency_id": "agency-groupm-001",
                        "advertiser_id": "adv-nike-001",
                    },
                )

        assert resp.status_code == 200
        ctx = counter.await_args.kwargs["buyer_context"]
        assert ctx.effective_tier == AccessTier.PUBLIC

    async def test_registered_agent_claiming_advertiser_capped_at_seat(
        self, client, mock_storage
    ):
        """agent_url present -> registry ceiling (SEAT) caps the claim."""
        counter = AsyncMock(return_value={"ok": True})
        service = _service(mock_storage, FakeRegistryClient(registered=True))
        with (
            patch(
                "ad_seller.services.negotiation_service.counter_proposal", new=counter
            ),
            _patch_registry(service),
            _patch_card(_agent_card()),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            async with client as c:
                resp = await c.post(
                    "/proposals/prop-1/counter",
                    json={
                        "buyer_price": 15.0,
                        "buyer_tier": "advertiser",
                        "agency_id": "agency-groupm-001",
                        "advertiser_id": "adv-nike-001",
                        "agent_url": BUYER_URL,
                    },
                )

        assert resp.status_code == 200
        ctx = counter.await_args.kwargs["buyer_context"]
        assert ctx.effective_tier == AccessTier.SEAT
        # Verification outcome persisted as the auditable primitive.
        records = _trust_records(mock_storage)
        assert len(records) == 1
        assert VerifiedTrust.model_validate(records[0]["verified_trust"]).verified

    async def test_api_key_identity_is_not_floored(self, client, mock_storage):
        """EP-4.5 verified principal: seller-issued key identity survives."""
        raw_key = _seed_api_key(
            mock_storage._store, BuyerIdentity(agency_id="agency-001")
        )
        counter = AsyncMock(return_value={"ok": True})
        with (
            patch(
                "ad_seller.services.negotiation_service.counter_proposal", new=counter
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            async with client as c:
                resp = await c.post(
                    "/proposals/prop-1/counter",
                    json={"buyer_price": 15.0, "buyer_tier": "public"},
                    headers={"X-Api-Key": raw_key},
                )

        assert resp.status_code == 200
        ctx = counter.await_args.kwargs["buyer_context"]
        assert ctx.effective_tier == AccessTier.AGENCY

    async def test_blocked_agent_rejected_403(self, client, mock_storage):
        blocked = RegisteredAgent(
            agent_id="agent-blocked1",
            agent_card=_agent_card(),
            trust_status=TrustStatus.BLOCKED,
            registered_at=datetime(2026, 3, 1),
        )
        mock_storage._store["agent:agent-blocked1"] = blocked.model_dump(mode="json")
        original_get = mock_storage.get.side_effect
        mock_storage.get.side_effect = lambda k: (
            "agent-blocked1" if k.startswith("agent_url_index:") else original_get(k)
        )
        service = _service(mock_storage, FakeRegistryClient())
        with (
            _patch_registry(service),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            async with client as c:
                resp = await c.post(
                    "/proposals/prop-1/counter",
                    json={"buyer_price": 15.0, "agent_url": BUYER_URL},
                )

        assert resp.status_code == 403
        # The rejection itself is audited.
        records = _trust_records(mock_storage)
        assert len(records) == 1
        assert records[0]["effective_ceiling"] is None


# =============================================================================
# Price-moving path: POST /api/v1/negotiations/messages (canonical surface)
# =============================================================================


class TestNegotiationMessagesCeiling:
    async def test_self_asserted_advertiser_identity_is_floored(self, client):
        counter = AsyncMock(
            return_value={
                "negotiation_id": "neg-1",
                "round_number": 1,
                "action": "counter",
                "buyer_price": 25.0,
                "seller_price": 30.0,
                "concession_pct": 0.1,
                "cumulative_concession_pct": 0.1,
                "rationale": "Counter",
                "status": "active",
                "rounds_remaining": 4,
            }
        )
        with patch(
            "ad_seller.services.negotiation_service.counter_proposal", new=counter
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/negotiations/messages",
                    json={
                        "idempotency_key": "idem-1",
                        "action": "counter",
                        "proposal_id": "prop-1",
                        "buyer_price": {"amount_micros": 25_000_000, "currency": "USD"},
                        "buyer_identity": {
                            "agency_id": "agency-groupm-001",
                            "advertiser_id": "adv-nike-001",
                        },
                    },
                )

        assert resp.status_code == 200, resp.text
        ctx = counter.await_args.kwargs["buyer_context"]
        assert ctx.effective_tier == AccessTier.PUBLIC


# =============================================================================
# Price-moving path: POST /api/v1/quotes
# =============================================================================


class TestQuoteEndpointCeiling:
    def _patches(self, mock_storage):
        return (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        )

    async def test_self_asserted_advertiser_identity_floors_to_public(
        self, client, mock_storage
    ):
        catalog_patch, storage_patch = self._patches(mock_storage)
        with catalog_patch, storage_patch:
            async with client as c:
                resp = await c.post(
                    "/api/v1/quotes",
                    json={
                        "idempotency_key": "idem-q1",
                        "product_id": "ctv-premium-sports",
                        "deal_type": "PD",
                        "impressions": 1000000,
                        "buyer_identity": {
                            "agency_id": "agency-groupm-001",
                            "advertiser_id": "adv-nike-001",
                        },
                    },
                )

        assert resp.status_code == 200, resp.text
        quote = resp.json()["quote"]
        assert quote["buyer_tier"] == "public"
        assert quote["pricing"]["tier_discount_pct"] == 0.0

    async def test_registered_agent_claiming_advertiser_capped_at_seat(
        self, client, mock_storage
    ):
        catalog_patch, storage_patch = self._patches(mock_storage)
        service = _service(mock_storage, FakeRegistryClient(registered=True))
        with (
            catalog_patch,
            storage_patch,
            _patch_registry(service),
            _patch_card(_agent_card()),
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/quotes",
                    json={
                        "idempotency_key": "idem-q2",
                        "product_id": "ctv-premium-sports",
                        "deal_type": "PD",
                        "impressions": 1000000,
                        "agent_url": BUYER_URL,
                        "buyer_identity": {
                            "agency_id": "agency-groupm-001",
                            "advertiser_id": "adv-nike-001",
                        },
                    },
                )

        assert resp.status_code == 200, resp.text
        quote = resp.json()["quote"]
        assert quote["buyer_tier"] == "seat"
        assert quote["pricing"]["tier_discount_pct"] == 5.0

    async def test_unknown_agent_floors_to_public(self, client, mock_storage):
        catalog_patch, storage_patch = self._patches(mock_storage)
        service = _service(mock_storage, FakeRegistryClient(registered=False))
        with (
            catalog_patch,
            storage_patch,
            _patch_registry(service),
            _patch_card(_agent_card()),
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/quotes",
                    json={
                        "idempotency_key": "idem-q3",
                        "product_id": "ctv-premium-sports",
                        "deal_type": "PD",
                        "impressions": 1000000,
                        "agent_url": BUYER_URL,
                        "buyer_identity": {
                            "agency_id": "agency-groupm-001",
                            "advertiser_id": "adv-nike-001",
                        },
                    },
                )

        assert resp.status_code == 200, resp.text
        assert resp.json()["quote"]["buyer_tier"] == "public"

    async def test_blocked_agent_rejected_403(self, client, mock_storage):
        blocked = RegisteredAgent(
            agent_id="agent-blocked1",
            agent_card=_agent_card(),
            trust_status=TrustStatus.BLOCKED,
            registered_at=datetime(2026, 3, 1),
        )
        mock_storage._store["agent:agent-blocked1"] = blocked.model_dump(mode="json")
        original_get = mock_storage.get.side_effect
        mock_storage.get.side_effect = lambda k: (
            "agent-blocked1" if k.startswith("agent_url_index:") else original_get(k)
        )
        catalog_patch, storage_patch = self._patches(mock_storage)
        service = _service(mock_storage, FakeRegistryClient())
        with catalog_patch, storage_patch, _patch_registry(service):
            async with client as c:
                resp = await c.post(
                    "/api/v1/quotes",
                    json={
                        "idempotency_key": "idem-q4",
                        "product_id": "ctv-premium-sports",
                        "deal_type": "PD",
                        "impressions": 1000000,
                        "agent_url": BUYER_URL,
                    },
                )

        assert resp.status_code == 403

    async def test_verification_outcome_persisted_as_primitive(
        self, client, mock_storage
    ):
        catalog_patch, storage_patch = self._patches(mock_storage)
        service = _service(mock_storage, FakeRegistryClient(registered=True))
        with (
            catalog_patch,
            storage_patch,
            _patch_registry(service),
            _patch_card(_agent_card()),
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/quotes",
                    json={
                        "idempotency_key": "idem-q5",
                        "product_id": "ctv-premium-sports",
                        "deal_type": "PD",
                        "impressions": 1000000,
                        "agent_url": BUYER_URL,
                        "buyer_identity": {
                            "agency_id": "agency-groupm-001",
                            "advertiser_id": "adv-nike-001",
                        },
                    },
                )

        assert resp.status_code == 200, resp.text
        records = _trust_records(mock_storage)
        assert len(records) == 1
        record = records[0]
        verdict = VerifiedTrust.model_validate(record["verified_trust"])
        assert verdict.verified is True
        assert verdict.agent_id == "ext-1"
        assert record["agent_url"] == BUYER_URL
        assert record["claimed_tier"] == "advertiser"
        assert record["effective_ceiling"] == "seat"
        assert record["endpoint"] == "POST /api/v1/quotes"


# =============================================================================
# Price-moving path: POST /proposals
# =============================================================================


class TestProposalEndpointCeiling:
    async def test_self_asserted_agency_is_floored_to_public(self, client):
        submit = AsyncMock(
            return_value={
                "proposal_id": "prop-1",
                "recommendation": "accept",
                "status": "accepted",
                "counter_terms": None,
                "approval_id": None,
                "pricing_verified": False,
                "pricing_verification_reason": "",
                "errors": [],
            }
        )
        with (
            patch("ad_seller.services.negotiation_service.submit_proposal", new=submit),
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(),
            ),
        ):
            async with client as c:
                resp = await c.post(
                    "/proposals",
                    json={
                        "product_id": "ctv-premium-sports",
                        "deal_type": "PD",
                        "price": 30.0,
                        "impressions": 1000000,
                        "start_date": "2026-08-01",
                        "end_date": "2026-08-31",
                        "agency_id": "agency-groupm-001",
                    },
                )

        assert resp.status_code == 200, resp.text
        ctx = submit.await_args.args[1]
        assert ctx.effective_tier == AccessTier.PUBLIC


# =============================================================================
# Price-moving path: POST /api/v1/deals/from-template
# =============================================================================


class TestDealFromTemplateCeiling:
    async def test_registry_ceiling_caps_api_key_identity(self, client, mock_storage):
        """Defense in depth: key identity (AGENCY) capped by registry (SEAT)."""
        raw_key = _seed_api_key(
            mock_storage._store, BuyerIdentity(agency_id="agency-001")
        )
        service = _service(mock_storage, FakeRegistryClient(registered=True))
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
            _patch_registry(service),
            _patch_card(_agent_card()),
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/deals/from-template",
                    json={
                        "deal_type": "PD",
                        "product_id": "ctv-premium-sports",
                        "impressions": 1000000,
                        "agent_url": BUYER_URL,
                    },
                    headers={"X-Api-Key": raw_key},
                )

        assert resp.status_code == 201, resp.text
        assert resp.json()["buyer_tier"] == "seat"
        assert len(_trust_records(mock_storage)) == 1

    async def test_api_key_identity_kept_without_agent_url(self, client, mock_storage):
        raw_key = _seed_api_key(
            mock_storage._store, BuyerIdentity(agency_id="agency-001")
        )
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                return_value=_mock_catalog(),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=mock_storage),
        ):
            async with client as c:
                resp = await c.post(
                    "/api/v1/deals/from-template",
                    json={
                        "deal_type": "PD",
                        "product_id": "ctv-premium-sports",
                        "impressions": 1000000,
                    },
                    headers={"X-Api-Key": raw_key},
                )

        assert resp.status_code == 201, resp.text
        assert resp.json()["buyer_tier"] == "agency"


# =============================================================================
# Price-moving path: POST /pricing
# =============================================================================


class TestPricingEndpointCeiling:
    async def test_self_asserted_advertiser_is_floored(self, client):
        with patch(
            "ad_seller.interfaces.api.main._get_static_product_catalog",
            return_value=_mock_catalog(),
        ):
            async with client as c:
                resp = await c.post(
                    "/pricing",
                    json={
                        "product_id": "ctv-premium-sports",
                        "buyer_tier": "advertiser",
                        "agency_id": "agency-groupm-001",
                        "advertiser_id": "adv-nike-001",
                        "volume": 1000000,
                    },
                )

        assert resp.status_code == 200, resp.text
        # PUBLIC tier -> no tier discount (advertiser would be 0.15).
        assert resp.json()["tier_discount"] == 0.0

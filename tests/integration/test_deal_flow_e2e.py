"""Task 10: End-to-End Deal Flow with Dummy Data.

Tests the key flow components with mocked external dependencies:
- Pricing engine produces correct tiered pricing
- Deal request flow creates deals end-to-end
- Order state transitions (via storage)
- Approval gate integration
"""

import importlib
import json
import sys
from unittest.mock import MagicMock, patch

from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity
from ad_seller.models.core import DealType, PricingModel
from ad_seller.models.flow_state import (
    ProductDefinition,
)

from .conftest import InMemoryStorage, make_settings


def _get_deal_request_flow_class():
    """Import DealRequestFlow directly from its module, bypassing flows/__init__.py
    which triggers discovery_inquiry_flow (broken with current crewai version)."""
    mod_name = "ad_seller.flows.deal_request_flow"
    if mod_name in sys.modules:
        return sys.modules[mod_name].DealRequestFlow

    # Ensure the parent package 'ad_seller.flows' exists in sys.modules
    # as a stub so that find_spec / relative imports work, without
    # executing the __init__.py that pulls in the broken module.
    parent_name = "ad_seller.flows"
    if parent_name not in sys.modules:
        import types

        import ad_seller  # noqa: F401

        stub = types.ModuleType(parent_name)
        stub.__path__ = [
            str(importlib.resources.files("ad_seller").joinpath("flows"))
        ]
        stub.__package__ = parent_name
        sys.modules[parent_name] = stub

    spec = importlib.util.find_spec(mod_name)
    if spec is None:
        raise ImportError(f"Cannot find {mod_name}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod.DealRequestFlow


# ============================================================================
# Pricing Engine
# ============================================================================


class TestPricingEngine:
    """Verify pricing engine produces correct tiered pricing."""

    def _build_engine(self):
        from ad_seller.engines.pricing_rules_engine import PricingRulesEngine
        from ad_seller.models.pricing_tiers import TieredPricingConfig

        config = TieredPricingConfig(seller_organization_id="test-seller")
        return PricingRulesEngine(config)

    def test_public_tier_no_discount(self):
        engine = self._build_engine()
        ctx = BuyerContext(identity=BuyerIdentity(), is_authenticated=False)
        decision = engine.calculate_price(
            product_id="ctv-001",
            base_price=35.0,
            buyer_context=ctx,
            deal_type=DealType.PREFERRED_DEAL,
            volume=0,
            inventory_type="ctv",
        )
        # Public tier should get base price (no discount)
        assert decision.final_price >= 35.0
        assert decision.tier_discount == 0.0

    def test_agency_tier_gets_discount(self):
        engine = self._build_engine()
        identity = BuyerIdentity(agency_id="ag-001", agency_name="Test Agency")
        ctx = BuyerContext(identity=identity, is_authenticated=True)
        decision = engine.calculate_price(
            product_id="ctv-001",
            base_price=35.0,
            buyer_context=ctx,
            deal_type=DealType.PREFERRED_DEAL,
            volume=0,
            inventory_type="ctv",
        )
        assert decision.tier_discount > 0
        assert decision.final_price < 35.0

    def test_advertiser_tier_best_discount(self):
        engine = self._build_engine()
        identity = BuyerIdentity(
            agency_id="ag-001",
            agency_name="Test Agency",
            advertiser_id="adv-001",
            advertiser_name="Test Advertiser",
        )
        ctx = BuyerContext(identity=identity, is_authenticated=True)
        decision = engine.calculate_price(
            product_id="ctv-001",
            base_price=35.0,
            buyer_context=ctx,
            deal_type=DealType.PREFERRED_DEAL,
            volume=0,
            inventory_type="ctv",
        )
        assert decision.tier_discount > 0
        assert decision.final_price < 35.0


# ============================================================================
# Deal Request Flow (DealRequestFlow)
# ============================================================================


class TestDealRequestFlowE2E:
    """End-to-end tests of the deal request flow logic.

    We import the flow state and step methods directly, bypassing CrewAI's
    Flow.__init__ (which cannot instantiate SellerFlowState without defaults
    in the current crewai version). This tests the actual business logic.
    """

    def _make_state(self, request_text, buyer_context=None, seller_org="INTEG"):
        """Create a DealRequestState manually and return (module, state)."""
        mod = sys.modules["ad_seller.flows.deal_request_flow"]
        state = mod.DealRequestState(
            flow_id="test-flow-001",
            flow_type="deal_request",
            seller_organization_id=seller_org,
            seller_name="Test Publisher",
            request_text=request_text,
            buyer_context=buyer_context,
        )
        return mod, state

    async def _run_steps(self, request_text, buyer_context=None, seller_org="INTEG"):
        """Run the flow steps manually in sequence on a DealRequestState."""
        _get_deal_request_flow_class()  # ensure module is loaded
        mod, state = self._make_state(request_text, buyer_context, seller_org)

        settings = make_settings(seller_organization_id=seller_org)

        # We create a minimal object that has .state and ._settings
        # and call each step method as a plain coroutine (they only use self.state).
        class FakeFlow:
            pass

        flow = FakeFlow()
        flow.state = state
        flow._settings = settings

        # Execute steps in order
        await mod.DealRequestFlow.receive_request(flow)
        await mod.DealRequestFlow.validate_buyer(flow)
        await mod.DealRequestFlow.parse_deal_requirements(flow)
        await mod.DealRequestFlow.apply_tiered_pricing(flow)
        await mod.DealRequestFlow.create_deal_for_dsp(flow)
        await mod.DealRequestFlow.generate_response(flow)

        deal_dict = flow.state.deal_output.model_dump() if flow.state.deal_output else None
        return {
            "request_type": flow.state.request_type,
            "response": flow.state.response_text,
            "deal": deal_dict,
            "status": flow.state.status.value,
            "errors": flow.state.errors,
        }

    async def test_agency_buyer_creates_ctv_deal(self):
        identity = BuyerIdentity(agency_id="ag-001", agency_name="Test Agency")
        ctx = BuyerContext(identity=identity, is_authenticated=True)

        result = await self._run_steps("I want to create a CTV deal", buyer_context=ctx)

        assert result["status"] in ("completed", "deal_created")
        assert result["deal"] is not None
        deal = result["deal"]
        assert deal["product_id"] == "ctv"
        assert deal["price"] > 0
        assert deal["deal_id"].startswith("INTE")

    async def test_public_buyer_rejected_for_deal_creation(self):
        ctx = BuyerContext(identity=BuyerIdentity(), is_authenticated=False)

        result = await self._run_steps("Create a display deal", buyer_context=ctx)

        assert result["status"] == "failed"
        assert "authenticate" in result["response"].lower()

    async def test_inquiry_returns_pricing_info(self):
        identity = BuyerIdentity(agency_id="ag-001", agency_name="Test Agency")
        ctx = BuyerContext(identity=identity, is_authenticated=True)

        result = await self._run_steps(
            "What is the price for video inventory?", buyer_context=ctx
        )

        assert result["request_type"] == "inquiry"
        assert result["status"] == "completed"

    async def test_pg_deal_type_detection(self):
        identity = BuyerIdentity(agency_id="ag-001", agency_name="Test Agency")
        ctx = BuyerContext(identity=identity, is_authenticated=True)

        result = await self._run_steps(
            "Create a programmatic guaranteed CTV deal",
            buyer_context=ctx,
        )

        assert result["deal"] is not None
        assert result["deal"]["deal_type"] == DealType.PROGRAMMATIC_GUARANTEED.value


# ============================================================================
# Order state transitions (using in-memory storage)
# ============================================================================


class TestOrderStateTransitions:
    """Test order lifecycle via storage."""

    async def test_order_draft_to_approved(self, in_memory_storage: InMemoryStorage):
        storage = in_memory_storage
        await storage.connect()

        order = {
            "order_id": "ord-001",
            "deal_id": "DEAL-001",
            "status": "draft",
            "created_at": "2026-03-26T10:00:00Z",
        }
        await storage.set_order("ord-001", order)

        # Transition to approved
        loaded = await storage.get_order("ord-001")
        assert loaded is not None
        loaded["status"] = "approved"
        await storage.set_order("ord-001", loaded)

        updated = await storage.get_order("ord-001")
        assert updated["status"] == "approved"

    async def test_order_approved_to_delivering(self, in_memory_storage: InMemoryStorage):
        storage = in_memory_storage
        await storage.connect()

        order = {"order_id": "ord-002", "status": "approved"}
        await storage.set_order("ord-002", order)

        loaded = await storage.get_order("ord-002")
        loaded["status"] = "delivering"
        await storage.set_order("ord-002", loaded)

        updated = await storage.get_order("ord-002")
        assert updated["status"] == "delivering"

    async def test_list_orders(self, in_memory_storage: InMemoryStorage):
        storage = in_memory_storage
        await storage.connect()

        await storage.set_order("ord-a", {"order_id": "ord-a", "status": "draft"})
        await storage.set_order("ord-b", {"order_id": "ord-b", "status": "approved"})

        orders = await storage.list_orders()
        assert len(orders) == 2

        draft_orders = await storage.list_orders(filters={"status": "draft"})
        assert len(draft_orders) == 1
        assert draft_orders[0]["order_id"] == "ord-a"


# ============================================================================
# Deal template creation path
# ============================================================================


class TestDealTemplateCreation:
    """Test the create_deal_from_template MCP tool path.

    The MCP tool calls httpx to the seller API. We mock httpx to simulate
    the API response and verify the tool processes it correctly.
    """

    def test_template_tool_builds_correct_request(self):
        """Verify CreateDealFromTemplateTool builds the correct HTTP request body."""
        from ad_seller.tools.deal_library.create_from_template import (
            CreateDealFromTemplateTool,
        )

        tool = CreateDealFromTemplateTool()
        assert tool.name == "create_deal_from_template"

        # Verify input schema accepts required fields
        from ad_seller.tools.deal_library.create_from_template import (
            CreateDealFromTemplateInput,
        )

        inp = CreateDealFromTemplateInput(
            deal_type="PG",
            product_id="fw-ctv-premium-001",
            impressions=100000,
            max_cpm=40.0,
            flight_start="2026-04-01",
            flight_end="2026-04-30",
        )
        assert inp.deal_type == "PG"
        assert inp.product_id == "fw-ctv-premium-001"
        assert inp.impressions == 100000

    def test_template_tool_handles_success_response(self):
        """Verify tool correctly processes a 201 success response."""
        from ad_seller.tools.deal_library.create_from_template import (
            CreateDealFromTemplateTool,
        )

        tool = CreateDealFromTemplateTool()

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "deal_id": "TEST-ABC123",
            "deal_type": "PG",
            "product_id": "fw-ctv-premium-001",
            "price": 38.0,
        }

        with patch("httpx.post", return_value=mock_response):
            result = tool._run(
                deal_type="PG",
                product_id="fw-ctv-premium-001",
                impressions=100000,
                max_cpm=40.0,
            )

        parsed = json.loads(result)
        assert parsed["deal_id"] == "TEST-ABC123"

    def test_template_tool_handles_rejection(self):
        """Verify tool handles a 422 rejection response."""
        from ad_seller.tools.deal_library.create_from_template import (
            CreateDealFromTemplateTool,
        )

        tool = CreateDealFromTemplateTool()

        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.json.return_value = {
            "detail": {
                "reason": "max_cpm below floor",
                "floor_cpm": 30.0,
                "requested_cpm": 10.0,
            }
        }

        with patch("httpx.post", return_value=mock_response):
            result = tool._run(
                deal_type="PG",
                product_id="fw-ctv-premium-001",
                max_cpm=10.0,
            )

        parsed = json.loads(result)
        assert parsed["rejected"] is True
        assert parsed["floor_cpm"] == 30.0


# ============================================================================
# Approval gate integration
# ============================================================================


class TestApprovalGateIntegration:
    """Test approval gate flow using in-memory storage."""

    async def test_request_and_approve(self, in_memory_storage: InMemoryStorage):
        storage = in_memory_storage
        await storage.connect()

        # Simulate creating an approval request
        approval = {
            "approval_id": "appr-001",
            "flow_id": "flow-001",
            "deal_id": "DEAL-001",
            "status": "pending",
            "requested_at": "2026-03-26T10:00:00Z",
            "details": {"price": 35.0, "deal_type": "PG"},
        }
        await storage.set("approval:appr-001", approval)

        # List pending approvals
        pending = await storage.get("approval:appr-001")
        assert pending is not None
        assert pending["status"] == "pending"

        # Approve
        pending["status"] = "approved"
        pending["decided_at"] = "2026-03-26T10:05:00Z"
        pending["decided_by"] = "publisher-admin"
        await storage.set("approval:appr-001", pending)

        approved = await storage.get("approval:appr-001")
        assert approved["status"] == "approved"

    async def test_request_and_reject(self, in_memory_storage: InMemoryStorage):
        storage = in_memory_storage
        await storage.connect()

        approval = {
            "approval_id": "appr-002",
            "status": "pending",
            "details": {"price": 5.0, "deal_type": "PA"},
        }
        await storage.set("approval:appr-002", approval)

        loaded = await storage.get("approval:appr-002")
        loaded["status"] = "rejected"
        loaded["rejection_reason"] = "Price too low"
        await storage.set("approval:appr-002", loaded)

        rejected = await storage.get("approval:appr-002")
        assert rejected["status"] == "rejected"
        assert rejected["rejection_reason"] == "Price too low"

    async def test_list_multiple_pending(self, in_memory_storage: InMemoryStorage):
        storage = in_memory_storage
        await storage.connect()

        for i in range(3):
            await storage.set(
                f"approval:appr-{i:03d}",
                {"approval_id": f"appr-{i:03d}", "status": "pending"},
            )

        keys = await storage.keys("approval:*")
        assert len(keys) == 3

        # Approve one
        item = await storage.get("approval:appr-001")
        item["status"] = "approved"
        await storage.set("approval:appr-001", item)

        # Verify we can filter pending ones
        all_approvals = []
        for k in await storage.keys("approval:*"):
            a = await storage.get(k)
            if a and a["status"] == "pending":
                all_approvals.append(a)
        assert len(all_approvals) == 2


# ============================================================================
# Fixture data validation
# ============================================================================


class TestFixtureData:
    """Verify fixture JSON files load correctly and have expected structure."""

    def test_freewheel_inventory_loads(self, freewheel_products):
        assert len(freewheel_products) == 5
        ctv_products = [p for p in freewheel_products if p["inventory_type"] == "ctv"]
        assert len(ctv_products) == 2
        display_products = [p for p in freewheel_products if p["inventory_type"] == "display"]
        assert len(display_products) == 1

    def test_pubmatic_response_structure(self, pubmatic_deal_response):
        assert pubmatic_deal_response["ssp_name"] == "pubmatic"
        assert pubmatic_deal_response["status"] == "active"
        assert pubmatic_deal_response["cpm"] == 15.0

    def test_ix_response_structure(self, ix_deal_response):
        assert ix_deal_response["ssp_name"] == "index_exchange"
        assert ix_deal_response["status"] == "active"
        assert ix_deal_response["cpm"] == 22.5

    def test_products_can_become_product_definitions(self, freewheel_products):
        """Verify fixture data can be loaded into ProductDefinition models."""
        for p in freewheel_products:
            pd = ProductDefinition(
                product_id=p["product_id"],
                name=p["name"],
                inventory_type=p["inventory_type"],
                base_cpm=p["base_cpm"],
                floor_cpm=p["floor_cpm"],
                supported_deal_types=[DealType(dt) for dt in p["supported_deal_types"]],
                supported_pricing_models=[
                    PricingModel(pm) for pm in p["supported_pricing_models"]
                ],
                minimum_impressions=p.get("minimum_impressions", 10000),
            )
            assert pd.product_id == p["product_id"]
            assert pd.base_cpm > 0

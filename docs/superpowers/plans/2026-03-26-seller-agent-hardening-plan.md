# Seller Agent Hardening & Testing Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the seller agent through comprehensive testing, documentation fixes, and UX improvements across three personas (engineer, product manager, ad ops).

**Architecture:** The seller agent is a FastAPI + MCP + CLI system with 33 MCP tools, ~58 REST endpoints, 3-level CrewAI agent hierarchy, pluggable ad server/SSP backends, and multi-backend storage. This plan audits, tests, and documents the full system.

**Tech Stack:** Python 3.12, FastAPI, FastMCP (mcp 1.16.0), CrewAI, Pydantic v2, pytest + pytest-asyncio, SQLite/PostgreSQL/Redis, Docker, Terraform/CloudFormation

**Spec:** `docs/superpowers/specs/2026-03-26-mcp-prompts-slash-commands-design.md`

---

## Phase 0: Rename "Deal Library" to "Deal Library" & Lint

### Task 0A: Rename Deal Library → Deal Library

> **Context:** In the buyer agent, "Deal Library" is the v2 storage/schema layer (the deal data store), while "Deal Library" remains the agent name (Level 2 agent, portfolio manager role). On the seller side, "Deal Library" was used for the Phase 4 API layer. We are renaming the seller's "Deal Library" to "Deal Library" to align with the buyer's terminology — both sides use "Deal Library" for the deal data/API layer.
>
> **Do NOT rename** the buyer agent's "DealJockey" agent name — that stays as-is. This task only affects the seller agent codebase.

**Files:**
- All files containing "deal library", "DealLibrary", "Deal Library", "deal-library"
- `.beads/PROGRESS.md`
- `.beads/generate_progress.py` (PHASE_MAP entry for Phase 4)

- [ ] **Step 1: Find all occurrences**

Run: `grep -ri "deal.jockey" --include="*.py" --include="*.md" --include="*.yaml" --include="*.json" -l`
Document every file that contains the term.

- [ ] **Step 2: Replace in Python source files**

Replace all variants:
- `Deal Library` → `DealLibrary`
- `Deal Library` → `Deal Library`
- `deal_library` → `deal_library`
- `deal-library` → `deal-library`

In all `.py` files under `src/` and `tests/`.

- [ ] **Step 3: Rename deal_library directory if it exists**

If `src/ad_seller/tools/deal_library/` exists, rename to `src/ad_seller/tools/deal_library/`.
Update all imports that reference `tools.deal_library`.

- [ ] **Step 4: Replace in documentation**

Replace all variants in all `.md` files under `docs/`, `README.md`, `CHANGELOG.md`, and root-level `.md` files.

- [ ] **Step 5: Update PHASE_MAP in generate_progress.py**

Change: `"4": ("Phase 4", "Deal Library — Seller API")`
To: `"4": ("Phase 4", "Deal Library — Seller API")`

- [ ] **Step 6: Update beads issue titles**

For any beads issues referencing "Deal Library" in the title, update them:
```bash
bd update <issue-id> --title "new title"
```

- [ ] **Step 7: Regenerate PROGRESS.md**

Run: `python .beads/generate_progress.py`

- [ ] **Step 8: Verify no remaining references**

Run: `grep -ri "deal.jockey" --include="*.py" --include="*.md" -l`
Expected: No matches

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor: rename Deal Library to Deal Library across codebase and docs"
```

---

### Task 0B: Lint Entire Codebase

**Files:**
- All `.py` files under `src/` and `tests/`

- [ ] **Step 1: Run ruff check**

Run: `ruff check src/ tests/`
Document all errors.

- [ ] **Step 2: Auto-fix what ruff can**

Run: `ruff check src/ tests/ --fix`

- [ ] **Step 3: Run ruff format**

Run: `ruff format src/ tests/`

- [ ] **Step 4: Manually fix remaining issues**

Review any errors ruff couldn't auto-fix and resolve them.

- [ ] **Step 5: Verify clean lint**

Run: `ruff check src/ tests/`
Expected: No errors

- [ ] **Step 5b: Run type check (optional but recommended)**

Run: `mypy src/ad_seller/ --ignore-missing-imports --no-error-summary 2>&1 | head -50`
Note: This may have many pre-existing errors. Fix critical ones (wrong types, missing awaits). Don't block on cosmetic type issues.

- [ ] **Step 6: Run tests to verify nothing broke**

Run: `pytest -v --tb=short`
Expected: All existing tests still pass

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "style: lint and format entire codebase with ruff"
```

---

### Task 0C: Buyer Agent Compatibility Check

> **Context:** The buyer agent at `/Users/bjt/Documents/crewAITechLabAgent/ad_buyer_system` was built by Aidan in parallel. The seller and buyer agents must be compatible: shared models, API contracts, deal formats, MCP tool expectations, and authentication must align.

**Files:**
- Reference: `/Users/bjt/Documents/crewAITechLabAgent/ad_buyer_system/src/`
- Reference: `/Users/bjt/Documents/crewAITechLabAgent/ad_buyer_system/docs/`
- Create: `docs/validation/buyer-compatibility-report.md`

- [ ] **Step 1: Compare deal models**

Read the buyer agent's deal models (`ad_buyer_system/src/ad_buyer/models/`) and the seller agent's deal models (`src/ad_seller/models/core.py`, `models/quotes.py`). Verify:
- Deal ID format matches (both sides generate/consume the same format)
- Deal type enums align (PG, PD, PA naming)
- Deal status values are compatible
- CPM/price field names and units match

- [ ] **Step 2: Compare API contracts**

Check the buyer agent's seller-facing API calls against our REST endpoints:
- Does the buyer's `DealRequestFlow` call our `/api/v1/deals/from-template` endpoint correctly?
- Does the buyer's deal push consumption match our IAB Deals API v1.0 push format?
- Does the buyer's supply chain query match our `/api/v1/supply-chain` response format?
- Do export formats (`/api/v1/deals/export`) match what the buyer expects for DSP connectors?

- [ ] **Step 3: Compare MCP tool expectations**

If the buyer agent connects to the seller via MCP:
- Does it expect tools that exist? (e.g., `request_quote`, `list_packages`, `get_pricing`)
- Do the tool parameter names and return formats match?
- Does the buyer's auth (API key, bearer token) work with our auth middleware?

- [ ] **Step 4: Compare authentication flow**

Verify the buyer agent's API key / bearer token format matches what our `auth/dependencies.py` expects:
- Key prefix format (`ask_live_` on seller side)
- Header format (`Authorization: Bearer` vs `X-Api-Key`)
- Access tier mapping (buyer sends seat_id → seller resolves to tier)

- [ ] **Step 5: Compare deal lifecycle events**

Check if the buyer agent emits or expects events that the seller's event bus should handle:
- `proposal.received` / `proposal.accepted` / `proposal.rejected` event payloads
- Negotiation round event formats
- Deal booking confirmation format

- [ ] **Step 6: Check for naming inconsistencies**

After Task 0A renamed "Deal Library" → "Deal Library" on the seller side, verify no cross-references break:
- Buyer docs referencing seller's "Deal Library" API
- Buyer code importing or calling seller endpoints by old names
- Shared documentation (root-level `.md` files in the parent directory)

- [ ] **Step 7: Document findings**

Write `docs/validation/buyer-compatibility-report.md` with:
- Aligned items (confirmed compatible)
- Mismatches (need fixes, with specific file paths and line numbers)
- Missing integration points (things the buyer expects but seller doesn't have, or vice versa)

- [ ] **Step 8: Fix any critical mismatches found**

If deal models, API contracts, or auth flows are incompatible, fix them. Prioritize anything that would prevent a buyer agent from completing a deal request against the seller.

- [ ] **Step 9: Commit**

```bash
git add docs/validation/buyer-compatibility-report.md
git add -A  # if any fixes were made
git commit -m "audit: buyer agent compatibility check and fixes"
```

---

## Phase A: Git Hygiene & Codebase Cleanup

### Task 1: Clean Git State

**Files:**
- Modify: `.beads/generate_progress.py` (unstaged change)
- Check: all branches, remotes, unpushed commits

- [ ] **Step 1: Check current git state**

Run: `git status && git log --oneline -5 && git branch -a`
Expected: main branch, 1 commit ahead of origin, 1 unstaged file (.beads/generate_progress.py)

- [ ] **Step 2: Stage and commit the generate_progress.py change**

```bash
git add .beads/generate_progress.py
git commit -m "chore: add Phase 5 to progress generator PHASE_MAP"
```

- [ ] **Step 3: Push to origin**

Run: `git push origin main`
Expected: Up to date with origin/main

- [ ] **Step 4: Verify clean state**

Run: `git status`
Expected: "nothing to commit, working tree clean"

---

### Task 2: Fix Documentation Number Discrepancies

**Files:**
- Modify: `README.md`
- Modify: `docs/api/mcp.md`
- Modify: `docs/api/overview.md`
- Modify: `docs/guides/media-kit.md`

- [ ] **Step 1: Count actual MCP tools**

Run: `grep -c '@mcp.tool()' src/ad_seller/interfaces/mcp_server.py`
Expected: 33 (the actual count)

- [ ] **Step 2: Count actual REST endpoints**

Run: `grep -c '@app\.\(get\|post\|put\|patch\|delete\)' src/ad_seller/interfaces/api/main.py`
Note: Use this to get the real count, then update docs

- [ ] **Step 3: Fix MCP tool count in docs/api/mcp.md**

Replace "45+ tools" or "45+ MCP tools" with the actual count from Step 1.

- [ ] **Step 4: Fix endpoint count in README.md**

Replace "70+ endpoints" with the actual count from Step 2.

- [ ] **Step 5: Fix port typo in docs/guides/media-kit.md**

Replace any `localhost:8001` references with `localhost:8000`.

- [ ] **Step 6: Fix endpoint count in docs/api/overview.md**

Align with actual count.

- [ ] **Step 7: Commit**

```bash
git add README.md docs/api/mcp.md docs/api/overview.md docs/guides/media-kit.md
git commit -m "docs: fix tool/endpoint counts and port typo"
```

---

## Phase B: Unit Tests (seller-f1x)

### Task 3: Test Pricing Engine

**Files:**
- Test: `tests/unit/test_pricing_engine.py`
- Reference: `src/ad_seller/engines/pricing_rules_engine.py`
- Check first: `tests/unit/test_engines.py` (may have existing pricing tests — avoid duplication)

- [ ] **Step 1: Read the pricing engine and existing tests**

Read `src/ad_seller/engines/pricing_rules_engine.py` to understand the actual API. The main method is `calculate_price(product_id, base_price, buyer_context, deal_type, volume, inventory_type)` which returns a `PricingDecision` object. Tier is passed via `BuyerContext`, not a string. Floor is at `engine.config.global_floor_cpm`.

Also read `tests/unit/test_engines.py` to see what's already covered.

- [ ] **Step 2: Write failing tests for tier pricing**

```python
"""Tests for PricingRulesEngine tier calculations."""
import pytest
from ad_seller.engines.pricing_rules_engine import PricingRulesEngine
from ad_seller.models.buyer_identity import BuyerContext


@pytest.fixture
def engine():
    return PricingRulesEngine()


class TestTierPricing:
    """Each access tier gets different pricing."""

    def test_agency_tier_gets_discount_over_seat(self, engine):
        """Agency tier should get better pricing than seat tier."""
        seat_ctx = BuyerContext(tier="seat", seat_id="dsp-1")
        agency_ctx = BuyerContext(tier="agency", agency_id="agency-1")
        seat_result = engine.calculate_price("product-1", 20.0, seat_ctx, "PD", 0, "display")
        agency_result = engine.calculate_price("product-1", 20.0, agency_ctx, "PD", 0, "display")
        assert agency_result.final_cpm <= seat_result.final_cpm

    def test_advertiser_tier_gets_best_discount(self, engine):
        """Advertiser tier should get deepest discount."""
        agency_ctx = BuyerContext(tier="agency", agency_id="agency-1")
        adv_ctx = BuyerContext(tier="advertiser", advertiser_id="adv-1")
        agency_result = engine.calculate_price("product-1", 20.0, agency_ctx, "PD", 0, "display")
        adv_result = engine.calculate_price("product-1", 20.0, adv_ctx, "PD", 0, "display")
        assert adv_result.final_cpm <= agency_result.final_cpm

    def test_floor_price_enforced(self, engine):
        """CPM should never go below configured floor."""
        adv_ctx = BuyerContext(tier="advertiser", advertiser_id="adv-1")
        result = engine.calculate_price("product-1", 20.0, adv_ctx, "PD", 999999, "display")
        assert result.final_cpm >= engine.config.global_floor_cpm
```

Note: Adjust field names (`final_cpm`, `BuyerContext` constructor) to match actual API after reading the source.

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/test_pricing_engine.py -v`
Expected: FAIL (adjust test method signatures to match actual engine API)

- [ ] **Step 4: Fix tests to match actual engine API**

Read the actual method signatures and adjust the test to call them correctly. The tests should now pass against the real engine.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_pricing_engine.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_pricing_engine.py
git commit -m "test: add pricing engine tier and floor tests"
```

---

### Task 4: Test Negotiation Engine

**Files:**
- Test: `tests/unit/test_negotiation_engine.py`
- Reference: `src/ad_seller/engines/negotiation_engine.py`
- Check first: `tests/unit/test_engines.py` (may have existing negotiation tests)

- [ ] **Step 1: Read the negotiation engine**

Read `src/ad_seller/engines/negotiation_engine.py` to understand: `NegotiationEngine` class, multi-round negotiation, concession tracking, strategy-per-tier, walk-away conditions, counter-offer generation.

- [ ] **Step 2: Write failing tests**

Test cases:
- Counter-offer respects concession limits (max per round, total cap)
- Walk-away triggers when gap exceeds threshold
- Strategy varies by buyer tier (aggressive for unknown, collaborative for strategic)
- Multi-round concession tracking (each round concedes less)
- Package-aware counter (suggest alternative package, not just lower price)

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/test_negotiation_engine.py -v`

- [ ] **Step 4: Adjust tests to match actual API, verify pass**

Run: `pytest tests/unit/test_negotiation_engine.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_negotiation_engine.py
git commit -m "test: add negotiation engine multi-round and strategy tests"
```

---

### Task 5: Test Media Kit Service

**Files:**
- Test: `tests/unit/test_media_kit_service.py`
- Reference: `src/ad_seller/engines/media_kit_service.py`
- Check first: `tests/unit/test_engines.py` (may have existing media kit tests)

- [ ] **Step 1: Read the media kit service**

Read `src/ad_seller/engines/media_kit_service.py` — the class is `MediaKitService`, not an engine.

- [ ] **Step 2: Write tests for tier-gated access**

Test cases:
- Public access returns packages without exact pricing
- Authenticated access returns exact pricing
- Featured packages appear in public listing
- Package search by keyword works
- Empty media kit returns graceful response

- [ ] **Step 3: Run, fix, verify pass**

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_media_kit_service.py
git commit -m "test: add media kit tier-gated access tests"
```

---

### Task 6: Test MCP Server Prompts and Tools

**Files:**
- Test: `tests/unit/test_mcp_server.py`
- Reference: `src/ad_seller/interfaces/mcp_server.py`

- [ ] **Step 1: Read the MCP server to understand all tool signatures**

- [ ] **Step 2: Write tests for setup status detection**

```python
"""Tests for MCP server tools."""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_get_setup_status_incomplete():
    """Unconfigured system should report setup incomplete."""
    from ad_seller.interfaces.mcp_server import get_setup_status
    import json

    with patch("ad_seller.interfaces.mcp_server._get_settings") as mock_settings:
        mock_settings.return_value.seller_organization_name = "Default Publisher"
        mock_settings.return_value.gam_network_code = ""
        mock_settings.return_value.freewheel_sh_mcp_url = ""
        mock_settings.return_value.ssp_connectors = ""
        mock_settings.return_value.default_price_floor_cpm = 5.0
        mock_settings.return_value.default_currency = "USD"
        mock_settings.return_value.approval_gate_enabled = False

        with patch("ad_seller.interfaces.mcp_server._get_storage", new_callable=AsyncMock):
            result = json.loads(await get_setup_status())
            assert result["setup_complete"] is False
            assert "incomplete" in result["message"].lower()
```

- [ ] **Step 3: Write tests for health_check, get_config**

- [ ] **Step 4: Run tests, fix, verify pass**

Run: `pytest tests/unit/test_mcp_server.py -v`

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_mcp_server.py
git commit -m "test: add MCP server setup status and config tests"
```

---

### Task 7: Test SSP Connectors

**Files:**
- Test: `tests/unit/test_ssp_connectors.py`
- Reference: `src/ad_seller/clients/ssp_base.py`, `ssp_index_exchange.py`, `ssp_mcp_client.py`

- [ ] **Step 1: Read SSP base class and both implementations**

- [ ] **Step 2: Write tests with mocked HTTP/MCP responses**

Test cases:
- SSPRegistry routes CTV deals to correct SSP
- SSPRegistry routes display deals to correct SSP
- PubMatic MCP client formats deal_management call correctly
- Index Exchange REST client sends correct HTTP payload
- SSP error (timeout, 500) returns graceful error, doesn't crash flow
- Deal distribution to multiple SSPs collects per-SSP results

- [ ] **Step 3: Run, fix, verify pass**

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_ssp_connectors.py
git commit -m "test: add SSP connector routing and error handling tests"
```

---

### Task 8: Test Event Bus and Approval Gates

**Files:**
- Test: `tests/unit/test_event_bus.py`
- Reference: `src/ad_seller/events/bus.py`, `src/ad_seller/events/approval.py`

- [ ] **Step 1: Read event bus and approval gate implementations**

- [ ] **Step 2: Write tests**

Test cases:
- Event published is received by subscriber
- Subscriber filtering by event_type works
- Wildcard subscriber ("*") receives all events
- ApprovalGate.request_approval creates pending request
- ApprovalGate.list_pending returns only pending items
- Approval timeout detection works
- Approve/reject updates status correctly

- [ ] **Step 3: Run, fix, verify pass**

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_event_bus.py
git commit -m "test: add event bus pub/sub and approval gate tests"
```

---

### Task 9: Test Order State Machine (expand existing)

**Files:**
- Modify: `tests/unit/test_order_state_machine.py`
- Reference: `src/ad_seller/models/order_state_machine.py`

- [ ] **Step 1: Read existing tests and the state machine**

- [ ] **Step 2: Add edge case tests**

Test cases:
- Invalid transition raises InvalidTransitionError with clear message
- Guard condition failure blocks transition
- Audit log records every transition with actor and timestamp
- Concurrent transitions on same order (race condition safety)
- All 12 states reachable from draft via valid paths
- Transition from terminal state (completed, failed) is blocked

- [ ] **Step 3: Run full test suite**

Run: `pytest tests/unit/test_order_state_machine.py -v`
Expected: All PASS (existing + new)

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_order_state_machine.py
git commit -m "test: expand order state machine edge case coverage"
```

---

## Phase C: Integration Tests with Dummy Data (seller-6he)

### Task 10: End-to-End Deal Flow with Dummy Data

**Files:**
- Create: `tests/integration/test_deal_flow_e2e.py`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/fixtures/` (dummy responses)

- [ ] **Step 1: Create test fixtures directory**

```bash
mkdir -p tests/integration/fixtures
```

- [ ] **Step 2: Create dummy FreeWheel inventory response fixture**

Create `tests/integration/fixtures/freewheel_inventory.json` with realistic but synthetic inventory data (3-5 products, CTV + display).

- [ ] **Step 3: Create dummy SSP response fixtures**

Create `tests/integration/fixtures/pubmatic_deal_response.json` and `tests/integration/fixtures/index_exchange_deal_response.json`.

- [ ] **Step 4: Create conftest.py with shared fixtures**

```python
"""Integration test fixtures."""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_storage():
    """In-memory storage for integration tests."""
    from ad_seller.storage.sqlite_backend import SQLiteBackend
    return SQLiteBackend(":memory:")


@pytest.fixture
def mock_ad_server():
    """Mock ad server that returns dummy inventory."""
    # Return fixture data
    ...


@pytest.fixture
def mock_ssp_registry():
    """Mock SSP registry with dummy PubMatic + IX responses."""
    ...
```

- [ ] **Step 5: Write end-to-end deal flow test**

Test the full path: deal request → pricing → negotiation → approval → booking → SSP distribution.

```python
@pytest.mark.asyncio
async def test_full_deal_flow(mock_storage, mock_ad_server, mock_ssp_registry):
    """Deal request flows from receipt to SSP distribution."""
    # 1. Buyer discovers inventory
    # 2. Buyer requests deal (CTV, PD, 1M impressions)
    # 3. Pricing engine calculates CPM
    # 4. Negotiation engine generates counter-offer
    # 5. Buyer accepts counter
    # 6. Approval gate triggered (if above threshold)
    # 7. Deal booked in ad server
    # 8. Deal distributed to SSP
    # 9. Verify deal appears in orders list
    ...
```

- [ ] **Step 6: Run integration tests**

Run: `pytest tests/integration/test_deal_flow_e2e.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add tests/integration/
git commit -m "test: add end-to-end deal flow integration tests with dummy data"
```

---

### Task 11: MCP Server Integration Test with Dummy Data

**Files:**
- Create: `tests/integration/test_mcp_integration.py`

- [ ] **Step 1: Write MCP tool integration test**

Test the MCP tools as a business user would invoke them, with mocked backends:
- `get_setup_status` → detect incomplete config
- `list_products` → return dummy products
- `create_deal_from_template` → create deal with dummy params
- `list_orders` → show the created deal
- `distribute_deal_via_ssp` → push to mocked SSP

- [ ] **Step 2: Run, fix, verify pass**

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_mcp_integration.py
git commit -m "test: add MCP server integration tests with dummy data"
```

---

### Task 12: Setup Wizard Flow Test

**Files:**
- Create: `tests/integration/test_setup_wizard_flow.py`

- [ ] **Step 1: Write setup wizard test**

Simulate the business user's day-1 experience:
1. Call `get_setup_status` → incomplete
2. Call `set_publisher_identity` with name, domain, org_id
3. Call `create_package` to add media kit entries
4. Call `update_rate_card` to set pricing
5. Call `set_approval_gates` to configure thresholds
6. Call `get_setup_status` → complete

- [ ] **Step 2: Run, fix, verify pass**

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_setup_wizard_flow.py
git commit -m "test: add setup wizard end-to-end flow test"
```

---

## Phase D: MCP Prompts (Slash Commands) Implementation

### Task 13: Add 9 MCP Prompts

> **Dependency:** Task 6 creates `tests/unit/test_mcp_server.py`. This task appends to that file — do not create a new file.

**Files:**
- Modify: `src/ad_seller/interfaces/mcp_server.py`
- Modify: `tests/unit/test_mcp_server.py` (created in Task 6)

- [ ] **Step 1: Read the MCP server to find the right insertion point**

The prompts go after the existing tools section, before the `.env helper` section.

- [ ] **Step 2: Write failing test for prompt registration**

```python
"""Tests for MCP prompt registration."""
import pytest


def test_all_prompts_registered():
    """All 9 prompts should be registered on the MCP server."""
    from ad_seller.interfaces.mcp_server import mcp

    prompt_names = {p.name for p in mcp._prompt_manager.list_prompts()}
    expected = {"setup", "status", "inventory", "deals", "queue",
                "new-deal", "configure", "buyers", "help"}
    assert expected.issubset(prompt_names), f"Missing: {expected - prompt_names}"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/unit/test_mcp_server.py::test_all_prompts_registered -v`
Expected: FAIL (no prompts registered yet)

- [ ] **Step 4: Implement all 9 prompts**

Add to `mcp_server.py` after the last `@mcp.tool()` section:

```python
# =============================================================================
# Prompts (Slash Commands)
# =============================================================================

from mcp.server.fastmcp.prompts.base import Message


@mcp.prompt(name="setup", description="First-time guided setup wizard")
async def setup_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Check setup status and walk me through configuring everything "
                "that's incomplete. Go step by step: publisher identity, ad server, "
                "SSPs, media kit, pricing, approval gates, and buyer agent access. "
                "Ask me one question at a time.",
    )]


@mcp.prompt(name="status", description="Configuration and health overview")
async def status_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me a complete status overview: configuration state, system "
                "health, ad server connection, SSP connectors, and any issues "
                "that need attention.",
    )]


@mcp.prompt(name="inventory", description="What inventory do I have to sell?")
async def inventory_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me my current inventory: products, media kit packages, "
                "and sync status. Highlight anything that needs attention.",
    )]


@mcp.prompt(name="deals", description="Full status report on all deal activity")
async def deals_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Give me a full status report on all deal activity: active deals, "
                "deals in negotiation, recently completed deals, and any deals "
                "with issues. Include SSP distribution status.",
    )]


@mcp.prompt(name="queue", description="Inbound items needing publisher action")
async def queue_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me everything in the inbound queue that needs my action: "
                "pending deal requests, approvals waiting for my decision, and "
                "proposals I need to review. Most urgent first.",
    )]


@mcp.prompt(name="new-deal", description="Create a new deal with guided steps")
async def new_deal_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Help me create a new deal. Walk me through it step by step: "
                "which inventory, deal type (PG/PD/PA), pricing, targeting, "
                "and which buyers or SSPs to distribute to.",
    )]


@mcp.prompt(name="configure", description="Add or edit event bus flows, approval gates, and guard conditions")
async def configure_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me all configurable automation rules: event bus flows, "
                "approval gates, and guard conditions. Tell me what each one "
                "does and let me add, modify, or remove them.",
    )]


@mcp.prompt(name="buyers", description="Buyer agents accessing your media kit and inbound requests")
async def buyers_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="Show me which buyer agents have been accessing my media kit "
                "and inventory recently. For each buyer, show what they looked "
                "at, whether they initiated any deals, and their current trust "
                "level. I want to know who to follow up with.",
    )]


@mcp.prompt(name="help", description="What can this seller agent do?")
async def help_prompt() -> list[Message]:
    return [Message(
        role="user",
        content="List all the things I can do with this seller agent, organized "
                "by category. Include the slash commands available and a brief "
                "description of each.",
    )]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_mcp_server.py::test_all_prompts_registered -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/ad_seller/interfaces/mcp_server.py tests/unit/test_mcp_server.py
git commit -m "feat: add 9 MCP prompts (slash commands) for Claude Desktop/web"
```

---

### Task 14: Implement `get_inbound_queue` Tool

**Files:**
- Modify: `src/ad_seller/interfaces/mcp_server.py`
- Test: `tests/unit/test_mcp_server.py`

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_get_inbound_queue_returns_pending_items():
    """Queue tool should aggregate pending approvals and proposal events."""
    # Mock ApprovalGate.list_pending() to return 2 pending approvals
    # Mock event bus to return 1 proposal.received event
    # Call get_inbound_queue()
    # Verify 3 items returned, sorted by timestamp, tagged with type
    ...
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement get_inbound_queue**

```python
from datetime import datetime, timedelta

@mcp.tool()
async def get_inbound_queue(limit: int = 50) -> str:
    """Get all inbound items needing publisher action: pending approvals
    and unresolved proposals. Most urgent first."""
    items = []
    warnings = []

    # 1. Pending approvals
    try:
        storage = await _get_storage()
        from ..events.approval import ApprovalGate
        gate = ApprovalGate(storage)
        pending = await gate.list_pending()
        for approval in pending:
            items.append({
                "type": "approval",
                "id": approval.approval_id,
                "summary": f"{approval.gate_name}: {approval.context.get('summary', 'Pending approval')}",
                "timestamp": approval.created_at.isoformat(),
                "from": approval.context.get("buyer", "unknown"),
                "urgency": "high" if (approval.expires_at and approval.expires_at < datetime.utcnow() + timedelta(hours=2)) else "normal",
            })
    except Exception as e:
        warnings.append(f"Could not load approvals: {e}")

    # 2. Unresolved proposal events
    try:
        storage = await _get_storage()
        from ..events.bus import StorageEventBus
        from ..events.models import EventType
        bus = StorageEventBus(storage)

        received = await bus.list_events(event_type=EventType.PROPOSAL_RECEIVED.value, limit=limit)
        resolved_ids = set()
        for et in [EventType.PROPOSAL_ACCEPTED, EventType.PROPOSAL_REJECTED, EventType.PROPOSAL_COUNTERED]:
            resolved = await bus.list_events(event_type=et.value, limit=500)
            resolved_ids.update(e.proposal_id for e in resolved)

        for event in received:
            if event.proposal_id not in resolved_ids:
                items.append({
                    "type": "proposal",
                    "id": event.proposal_id or event.event_id,
                    "summary": event.payload.get("summary", "New proposal"),
                    "timestamp": event.timestamp.isoformat(),
                    "from": event.metadata.get("buyer_agent", "unknown"),
                    "urgency": "normal",
                })
    except Exception as e:
        warnings.append(f"Could not load proposals: {e}")

    items.sort(key=lambda x: x["timestamp"], reverse=True)
    result = {"items": items[:limit], "total": len(items)}
    if warnings:
        result["warnings"] = warnings
    return json.dumps(result, indent=2)
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add src/ad_seller/interfaces/mcp_server.py tests/unit/test_mcp_server.py
git commit -m "feat: add get_inbound_queue composite tool for /queue prompt"
```

---

### Task 15: Implement `get_buyer_activity` Tool

**Files:**
- Modify: `src/ad_seller/interfaces/mcp_server.py`
- Test: `tests/unit/test_mcp_server.py`

- [ ] **Step 1: Write failing test**

Test cases:
- Returns buyer agents grouped by identity
- Includes activity summary (event types seen)
- Respects `days` parameter filter
- Returns empty list gracefully when no activity

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement get_buyer_activity**

Query event bus for events with non-empty session_id in the last N days. Join with session records to get buyer identity. Group by buyer agent, summarize activity.

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add src/ad_seller/interfaces/mcp_server.py tests/unit/test_mcp_server.py
git commit -m "feat: add get_buyer_activity composite tool for /buyers prompt"
```

---

### Task 16: Implement `list_configurable_flows` Tool

**Files:**
- Modify: `src/ad_seller/interfaces/mcp_server.py`
- Test: `tests/unit/test_mcp_server.py`

- [ ] **Step 1: Write failing test**

Test cases:
- Returns approval gate config from settings
- Returns guard conditions from OrderStateMachine._DEFAULT_TRANSITIONS
- Returns event bus subscriber count (runtime)
- Each section has `configurable` hint

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement list_configurable_flows**

Read settings for approval gates. Import `_build_default_rules()` from `order_state_machine` module — this converts the raw `_DEFAULT_TRANSITIONS` tuples into `TransitionRule` objects with `from_status`, `to_status`, `guard`, `description` fields. Query event bus `_subscribers` dict for active listener count. Note: event bus subscribers are runtime-only and will be empty on a fresh start.

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add src/ad_seller/interfaces/mcp_server.py tests/unit/test_mcp_server.py
git commit -m "feat: add list_configurable_flows composite tool for /configure prompt"
```

---

## Phase E: Deployment & Infra Validation

### Task 17: Docker Build & Run Test

**Files:**
- Reference: `Dockerfile`, `docker-compose.yml`

- [ ] **Step 1: Build Docker image**

Run: `docker build -f infra/docker/Dockerfile -t seller-agent:test .`
Expected: Build succeeds, image created

- [ ] **Step 2: Run with docker-compose**

Run: `docker-compose -f infra/docker/docker-compose.yml up -d`
Expected: All 3 services healthy (app, postgres, redis)

- [ ] **Step 3: Hit health endpoint**

Run: `curl -s http://localhost:8000/health | jq .`
Expected: `{"status": "healthy", ...}`

- [ ] **Step 4: Hit MCP SSE endpoint**

Run: `curl -s http://localhost:8000/mcp-sse/sse -H "Accept: text/event-stream" --max-time 5` (legacy SSE) or `curl -s http://localhost:8000/mcp -X POST -H "Accept: application/json, text/event-stream" --max-time 5` (Streamable HTTP)
Expected: SSE connection established (may timeout after 5s, that's fine)

- [ ] **Step 5: Tear down**

Run: `docker-compose -f infra/docker/docker-compose.yml down -v`

- [ ] **Step 6: Document any issues found**

If build fails or services don't start, document the issue and fix it before committing.

---

### Task 18: Verify .env.example Completeness

**Files:**
- Modify: `.env.example`
- Reference: `src/ad_seller/config/settings.py`

- [ ] **Step 1: Compare settings.py fields to .env.example**

Every field in the Settings class should have a corresponding entry in `.env.example` with a comment explaining what it does.

- [ ] **Step 2: Add missing FreeWheel config vars**

Add `FREEWHEEL_SH_MCP_URL`, `FREEWHEEL_BC_MCP_URL`, `FREEWHEEL_INVENTORY_MODE`, `FREEWHEEL_NETWORK_ID` with descriptive comments.

- [ ] **Step 3: Add missing SSP config vars**

Ensure `SSP_CONNECTORS`, `SSP_ROUTING_RULES`, `PUBMATIC_MCP_URL`, `INDEX_EXCHANGE_API_URL` are all present.

- [ ] **Step 4: Verify no secrets in .env.example**

Confirm all values are placeholders, not real keys.

- [ ] **Step 5: Commit**

```bash
git add .env.example
git commit -m "docs: complete .env.example with all configuration variables"
```

---

## Phase F: Documentation Updates

### Task 19: Add Troubleshooting Guide

**Files:**
- Create: `docs/guides/troubleshooting.md`

- [ ] **Step 1: Write troubleshooting guide**

Sections:
- **Health check failures**: what each check means, how to fix
- **Ad server connection errors**: GAM auth issues, FreeWheel MCP connection failures
- **SSP sync failures**: PubMatic MCP timeout, Index Exchange API errors
- **Storage issues**: SQLite lock errors, PostgreSQL connection pool exhaustion, Redis connection refused
- **MCP connection problems**: SSE timeout, Claude Desktop can't connect, ChatGPT connector errors
- **Common error codes**: map HTTP status codes to likely causes
- **Logs**: where to find them, what to look for

- [ ] **Step 2: Commit**

```bash
git add docs/guides/troubleshooting.md
git commit -m "docs: add troubleshooting guide for common issues"
```

---

### Task 20: Update Claude Desktop Setup Guide

**Files:**
- Modify: `docs/guides/claude-desktop-setup.md`

- [ ] **Step 1: Read current guide**

- [ ] **Step 2: Add slash command reference section**

After the setup wizard section, add:

```markdown
## Available Slash Commands

Once connected, you can use these commands in any chat:

| Command | What it does |
|---------|-------------|
| `/setup` | Run the guided setup wizard (first-time or reconfigure) |
| `/status` | Check configuration and system health |
| `/inventory` | See your products and media kit packages |
| `/deals` | Full report on all deal activity |
| `/queue` | Inbound items waiting for your action |
| `/new-deal` | Create a new deal step by step |
| `/configure` | Manage event bus flows, approval gates, guard conditions |
| `/buyers` | See which buyer agents are accessing your inventory |
| `/help` | List all available capabilities |
```

- [ ] **Step 3: Update the "First-Run" section to reference /setup**

Replace the vague "the seller agent detects that business setup isn't complete" with a clear instruction: "Type `/setup` to begin the guided configuration wizard."

- [ ] **Step 4: Commit**

```bash
git add docs/guides/claude-desktop-setup.md
git commit -m "docs: add slash command reference to Claude setup guide"
```

---

### Task 21: Update ChatGPT Setup Guide

**Files:**
- Modify: `docs/guides/chatgpt-setup.md`

- [ ] **Step 1: Read current guide**

- [ ] **Step 2: Add natural language equivalents section**

Since ChatGPT doesn't surface MCP prompts as `/` commands, add a section explaining what to say instead:

```markdown
## Getting Started After Setup

ChatGPT doesn't show slash commands, but you can use natural language:

| Instead of `/setup` | Say: "Help me set up my seller agent" |
| Instead of `/queue` | Say: "Show me what's in my inbound queue" |
| Instead of `/buyers` | Say: "Who's been looking at my inventory?" |
...
```

- [ ] **Step 3: Commit**

```bash
git add docs/guides/chatgpt-setup.md
git commit -m "docs: add natural language equivalents to ChatGPT setup guide"
```

---

### Task 22: Add FreeWheel Integration Status Banner

**Files:**
- Modify: `FREEWHEEL_MCP_TOOL_PROPOSAL.md` (root level)

- [ ] **Step 1: Add status banner at top**

```markdown
> **Implementation Status (2026-03-26):**
> - Phase 1 (Read-Only): ✅ Complete
> - Phase 2 (PD/PA Deal Booking): ✅ Complete (with auth stubs)
> - Phase 3 (PG Cross-MCP): ✅ Auth implemented — SH (OAuth 2.0 ROPCG) + BC (Beeswax session cookie)
> - See PROGRESS.md for current status
```

- [ ] **Step 2: Commit**

```bash
git add FREEWHEEL_MCP_TOOL_PROPOSAL.md
git commit -m "docs: add implementation status banner to FreeWheel proposal"
```

---

### Task 23: Add CHANGELOG.md

**Files:**
- Create: `CHANGELOG.md`

- [ ] **Step 1: Generate changelog from git history**

Read the last 20 commit messages and group by feature area.

- [ ] **Step 2: Write CHANGELOG.md**

```markdown
# Changelog

## [Unreleased]

### Added
- 9 MCP prompts (slash commands) for Claude Desktop/web
- 3 composite tools: get_inbound_queue, get_buyer_activity, list_configurable_flows
- Comprehensive unit and integration tests (Phase 5)
- Troubleshooting guide

### Fixed
- Documentation tool/endpoint count discrepancies
- Port typo in media-kit guide (8001 → 8000)

## [2.0.0] — 2026-03-23

### Added
- MCP server with 33+ tools for Claude Desktop, ChatGPT, Cursor
- Interactive setup wizard (developer + business phases)
- Deal migration, deprecation, and lineage tracking
- Curator support with Agent Range as day-one curator
- IAB Deals API v1.0 integration
- SSP connector abstraction (PubMatic MCP, Index Exchange REST)
- SSP deal distribution in ExecutionActivationFlow
- FreeWheel Streaming Hub integration (Phases 1-2)
- IaC deployment (CloudFormation + Terraform)
- Docker + docker-compose with PostgreSQL + Redis
...
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: add CHANGELOG.md"
```

---

## Phase G: User Experience Validation

### Task 24: Engineer Persona Walkthrough

**Files:**
- Create: `docs/validation/engineer-walkthrough-results.md` (findings log)

- [ ] **Step 1: Clone fresh and follow developer-setup.md**

Validate that a new engineer can:
1. Clone the repo
2. Install dependencies (`pip install -e ".[dev]"`)
3. Copy `.env.example` to `.env`
4. Run `ad-seller init` (CLI)
5. Start the server (`uvicorn ad_seller.interfaces.api.main:app`)
6. Hit `/health` endpoint
7. See MCP tools listed

Document any steps that fail or are confusing.

- [ ] **Step 2: Run all tests**

Run: `pytest -v`
Expected: All tests pass on a clean install

- [ ] **Step 3: Document issues found and fix them**

---

### Task 25: Product Manager Persona Walkthrough

**Files:**
- Create: `docs/validation/pm-walkthrough-results.md` (findings log)

- [ ] **Step 1: Simulate Claude Desktop connection**

Follow `docs/guides/claude-desktop-setup.md`:
1. Add the MCP URL to Claude Desktop settings
2. Verify the integration appears
3. Type `/` and verify all 9 prompts appear
4. Run `/setup` and walk through the wizard
5. Run `/status` to verify configuration

- [ ] **Step 2: Simulate daily operations**

1. `/inventory` — see what's available
2. `/new-deal` — create a test deal
3. `/deals` — see the deal in the status report
4. `/queue` — check for inbound items

- [ ] **Step 3: Document any confusing UX or missing guidance**

---

### Task 26: Ad Ops Persona Walkthrough

**Files:**
- Create: `docs/validation/adops-walkthrough-results.md` (findings log)

- [ ] **Step 1: Check inbound queue**

Run `/queue` — verify it shows pending approvals and proposals clearly.

- [ ] **Step 2: Check buyer activity**

Run `/buyers` — verify it shows which buyer agents have been active.

- [ ] **Step 3: Configure approval gates**

Run `/configure` — verify the user can:
1. See current approval gate settings
2. Add a new gate (e.g., "require approval for deals > $50 CPM")
3. Modify an existing guard condition

- [ ] **Step 4: Document any workflow gaps**

---

## Phase H: Final Cleanup & Push

### Task 27: Run Full Test Suite

**Files:**
- All test files

- [ ] **Step 1: Run all tests**

Run: `pytest -v --tb=short`
Expected: All PASS

- [ ] **Step 2: Run linter**

Run: `ruff check src/ tests/`
Expected: No errors

- [ ] **Step 3: Fix any failures**

- [ ] **Step 4: Commit fixes if any**

---

### Task 28: Update PROGRESS.md

**Files:**
- Modify: `.beads/PROGRESS.md`

- [ ] **Step 1: Regenerate progress**

Run: `python .beads/generate_progress.py`

- [ ] **Step 2: Close completed beads issues**

Close seller-f1x (unit tests) and seller-6he (dummy data tests) if all their tests pass.

- [ ] **Step 3: Sync beads**

Run: `bd sync`

- [ ] **Step 4: Push everything**

Run: `git push origin main`
Expected: Clean push, all changes on origin

---

## Task Summary

| Phase | Tasks | Focus |
|-------|-------|-------|
| 0: Rename, Lint & Compat | 0A-0C | Rename Deal Library → Deal Library, lint codebase, buyer agent compatibility check |
| A: Git Hygiene | 1-2 | Clean state, fix doc numbers |
| B: Unit Tests | 3-9 | Pricing, negotiation, media kit, MCP, SSP, events, orders |
| C: Integration Tests | 10-12 | E2E deal flow, MCP integration, setup wizard |
| D: MCP Prompts | 13-16 | 9 prompts + 3 composite tools |
| E: Infra Validation | 17-18 | Docker build, .env completeness |
| F: Documentation | 19-23 | Troubleshooting, Claude/ChatGPT guides, changelog |
| G: UX Validation | 24-26 | Engineer, PM, ad ops walkthroughs |
| H: Final Cleanup | 27-28 | Full test suite, progress update, push |

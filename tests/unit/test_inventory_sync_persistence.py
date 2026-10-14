# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Regression tests for AI-8, AI-9, AI-10 (field feedback).

AI-9 / AI-10 — inventory_sync_scheduler._run_sync() called the ad server
client directly, counted the items, and discarded them: no product or
package was ever persisted, despite a "success" response. Fix under
test: _run_sync() now delegates to ProductSetupFlow.sync_from_ad_server
(the same path POST /packages/sync already uses), so a sync actually
creates products and upserts SYNCED-layer packages into storage.

AI-8 — synced products/packages fell back to a hardcoded base_cpm/floor
of 10.0 when the ad server item carried no floor_price_cpm, ignoring the
operator's configured default_price_floor_cpm. Fix under test: both
fallback sites (catalog_service.product_from_inventory_item and
ProductSetupFlow._estimate_base_cpm) now use the configured floor.
"""

from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch

import pytest

from ad_seller.storage.base import StorageBackend


class InMemoryStorage(StorageBackend):
    """Fully in-memory storage backend for tests (mirrors test_issue34_catalog_fixes.py)."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        self._data.clear()

    async def get(self, key: str) -> Optional[Any]:
        return self._data.get(key)

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        self._data[key] = value

    async def delete(self, key: str) -> bool:
        if key in self._data:
            del self._data[key]
            return True
        return False

    async def exists(self, key: str) -> bool:
        return key in self._data

    async def keys(self, pattern: str = "*") -> list[str]:
        import fnmatch

        return [k for k in self._data if fnmatch.fnmatch(k, pattern)]


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


class _FakeInventoryItem:
    def __init__(self, id: str, name: str, raw: Optional[dict] = None):
        self.id = id
        self.name = name
        self.raw = raw or {}


class _FakeAdServerClient:
    """Minimal stand-in for a real ad server client (GAM/FreeWheel/CSV)."""

    ad_server_type = SimpleNamespace(value="gam")

    def __init__(self, items):
        self._items = items

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def list_inventory(self, filter_str=None):
        return self._items


def _flow_settings(**overrides) -> SimpleNamespace:
    defaults = {
        "gam_network_code": "test-network-code",
        "freewheel_sh_mcp_url": None,
        "ad_server_type": "google_ad_manager",
        "seller_organization_id": "test-seller-org",
        "seller_organization_name": "Test Seller",
        "default_price_floor_cpm": 5.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestRunSyncPersists:
    """inventory_sync_scheduler._run_sync() (AI-9, AI-10)."""

    async def test_sync_persists_packages_not_just_a_count(self, storage):
        """A sync must leave real packages in storage, not merely count items."""
        from ad_seller.services import inventory_sync_scheduler

        items = [
            _FakeInventoryItem("gam-001", "Homepage Display Banner"),
            _FakeInventoryItem("gam-002", "CTV Premium App"),
        ]
        settings = _flow_settings()

        with (
            patch("ad_seller.flows.product_setup_flow.get_settings", return_value=settings),
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch(
                "ad_seller.clients.ad_server_base.get_ad_server_client",
                lambda: _FakeAdServerClient(items),
            ),
        ):
            result = await inventory_sync_scheduler._run_sync()

        assert result["status"] == "success"
        persisted = await storage.list_packages()
        assert len(persisted) > 0, "sync reported success but persisted nothing (AI-10)"
        assert all(p["layer"] == "synced" for p in persisted)

    async def test_sync_result_reflects_persisted_counts(self, storage):
        """The returned counts must describe what was actually persisted (AI-9), not a
        raw item count from a client call whose result was otherwise discarded."""
        from ad_seller.services import inventory_sync_scheduler

        items = [
            _FakeInventoryItem("gam-001", "Homepage Display Banner"),
            _FakeInventoryItem("gam-002", "CTV Premium App"),
        ]
        settings = _flow_settings()

        with (
            patch("ad_seller.flows.product_setup_flow.get_settings", return_value=settings),
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch(
                "ad_seller.clients.ad_server_base.get_ad_server_client",
                lambda: _FakeAdServerClient(items),
            ),
        ):
            result = await inventory_sync_scheduler._run_sync()

        assert result["items_synced"] == len(items)
        persisted = await storage.list_packages()
        assert result["packages_synced"] == len(persisted)

    async def test_repeated_sync_is_idempotent(self, storage):
        """Calling _run_sync() twice must converge, not duplicate the synced layer
        (the underlying flow already guarantees this; this pins it at this call site
        too, now that _run_sync actually reaches storage)."""
        from ad_seller.services import inventory_sync_scheduler

        items = [_FakeInventoryItem("gam-001", "Homepage Display Banner")]
        settings = _flow_settings()

        with (
            patch("ad_seller.flows.product_setup_flow.get_settings", return_value=settings),
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch(
                "ad_seller.clients.ad_server_base.get_ad_server_client",
                lambda: _FakeAdServerClient(items),
            ),
        ):
            await inventory_sync_scheduler._run_sync()
            after_first = await storage.list_packages()
            await inventory_sync_scheduler._run_sync()
            after_second = await storage.list_packages()

        assert len(after_first) == len(after_second)


class TestDefaultFloorFallback:
    """Synced products/packages fall back to the configured floor, not a
    hardcoded 10.0 (AI-8)."""

    def test_product_from_inventory_item_uses_configured_floor(self):
        from ad_seller.services.catalog_service import product_from_inventory_item

        settings = _flow_settings(default_price_floor_cpm=22.5)
        item = _FakeInventoryItem("gam-001", "Homepage Display Banner")  # no floor_price_cpm

        with patch("ad_seller.config.get_settings", return_value=settings):
            product = product_from_inventory_item(item)

        assert product.base_cpm == 22.5
        assert product.base_cpm != 10.0

    def test_product_from_inventory_item_honors_explicit_item_floor(self):
        """An item that DOES carry floor_price_cpm is unaffected by the default."""
        from ad_seller.services.catalog_service import product_from_inventory_item

        settings = _flow_settings(default_price_floor_cpm=22.5)
        item = _FakeInventoryItem(
            "gam-001", "Homepage Display Banner", raw={"floor_price_cpm": 40.0}
        )

        with patch("ad_seller.config.get_settings", return_value=settings):
            product = product_from_inventory_item(item)

        assert product.base_cpm == 40.0

    async def test_estimate_base_cpm_fallback_uses_configured_floor(self, storage):
        """An inventory type not in the estimate table falls back to the configured
        floor, not the hardcoded 10.0 the fallback used to return."""
        from ad_seller.flows.product_setup_flow import ProductSetupFlow

        settings = _flow_settings(default_price_floor_cpm=17.0)
        with patch("ad_seller.flows.product_setup_flow.get_settings", return_value=settings):
            flow = ProductSetupFlow()

        assert flow._estimate_base_cpm("some_unrecognized_type") == 17.0
        assert flow._estimate_base_cpm("some_unrecognized_type") != 10.0
        # Recognized types are untouched by this change.
        assert flow._estimate_base_cpm("display") == 12.0

    def test_settings_default_matches_the_old_hardcoded_fallback(self):
        """The Settings class default (used whenever nothing overrides it,
        e.g. a fresh deployment with no explicit DEFAULT_PRICE_FLOOR_CPM env
        var) must equal the 10.0 this replaced -- otherwise GAM/FreeWheel
        inventory (which never carries floor_price_cpm in raw, unlike CSV's
        35 sample rows) gets a silently halved real floor/base price and
        check_avails claims roughly double the available impressions for a
        fixed budget."""
        from ad_seller.config.settings import Settings

        assert Settings.model_fields["default_price_floor_cpm"].default == 10.0


class _RecordingAdServerClient:
    """Like _FakeAdServerClient, but records the filter_str it was called with."""

    ad_server_type = SimpleNamespace(value="gam")

    def __init__(self, items):
        self._items = items
        self.received_filter_str: Any = "UNSET"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def list_inventory(self, filter_str=None):
        self.received_filter_str = filter_str
        return self._items


class _RecordingCsvAdServerClient(_RecordingAdServerClient):
    ad_server_type = SimpleNamespace(value="csv")


class _FailingAdServerClient:
    """Raises from list_inventory(), simulating a transient ad-server outage."""

    ad_server_type = SimpleNamespace(value="gam")

    def __init__(self, error: Exception):
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def list_inventory(self, filter_str=None):
        raise self._error


class TestStatusActiveFilterRestored:
    """sync_from_ad_server() must exclude archived inventory by default for
    real ad servers, but never pass a filter CSV's naive substring match
    would zero out (a maintainer review caught both the drop and, on the
    CSV side, that restoring it unconditionally would regress CSV back to
    matching zero rows)."""

    async def test_gam_receives_the_status_active_filter(self, storage):
        from ad_seller.flows.product_setup_flow import ProductSetupFlow

        client = _RecordingAdServerClient(
            [_FakeInventoryItem("gam-001", "Homepage Display Banner")]
        )
        settings = _flow_settings()

        with (
            patch("ad_seller.flows.product_setup_flow.get_settings", return_value=settings),
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch("ad_seller.clients.ad_server_base.get_ad_server_client", lambda: client),
        ):
            flow = ProductSetupFlow()
            await flow.sync_from_ad_server()

        assert client.received_filter_str == "status:ACTIVE"

    async def test_csv_receives_no_filter(self, storage):
        """CSV's filter_str is a literal substring match against item names
        -- passing "status:ACTIVE" there matches zero rows."""
        from ad_seller.flows.product_setup_flow import ProductSetupFlow

        client = _RecordingCsvAdServerClient(
            [_FakeInventoryItem("csv-001", "Homepage Display Banner")]
        )
        settings = _flow_settings(ad_server_type="csv")

        with (
            patch("ad_seller.flows.product_setup_flow.get_settings", return_value=settings),
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch("ad_seller.clients.ad_server_base.get_ad_server_client", lambda: client),
        ):
            flow = ProductSetupFlow()
            await flow.sync_from_ad_server()

        assert client.received_filter_str is None


class TestAdServerFailureNeverDestroysRealPackages:
    """A transient ad-server failure must never silently prune the real
    synced layer from the last successful sync, nor report success (a
    maintainer review found sync_from_ad_server's _finish_sync() ran even
    on the mock-fallback-after-failure path, and _run_sync always
    returned "success" whenever kickoff_async() didn't raise -- which it
    never did, since the failure was caught and swallowed internally)."""

    async def test_failure_leaves_previously_synced_packages_untouched(self, storage):
        from ad_seller.flows.product_setup_flow import ProductSetupFlow

        # Simulate a prior successful sync: a real SYNCED package already
        # in storage, not re-seeded by this (failing) run.
        await storage.set_package(
            "pkg-synced-gam-display",
            {
                "package_id": "pkg-synced-gam-display",
                "name": "Display - Synced",
                "layer": "synced",
                "status": "active",
            },
        )
        client = _FailingAdServerClient(ConnectionError("GAM API unreachable"))
        settings = _flow_settings()

        with (
            patch("ad_seller.flows.product_setup_flow.get_settings", return_value=settings),
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch("ad_seller.clients.ad_server_base.get_ad_server_client", lambda: client),
        ):
            flow = ProductSetupFlow()
            await flow.sync_from_ad_server()

        assert flow.state.ad_server_sync_failed is True
        remaining = {p["package_id"] for p in await storage.list_packages()}
        assert "pkg-synced-gam-display" in remaining, (
            "a transient failure must never delete the real synced layer"
        )
        # No mock fallback either -- mixing fake demo packages into a real
        # seller's live catalog on an outage is exactly as wrong as deleting it.
        assert not any("mock" in pid for pid in remaining)

    async def test_run_sync_reports_error_not_success(self, storage):
        from ad_seller.services import inventory_sync_scheduler

        client = _FailingAdServerClient(ConnectionError("GAM API unreachable"))
        settings = _flow_settings()

        with (
            patch("ad_seller.flows.product_setup_flow.get_settings", return_value=settings),
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch("ad_seller.clients.ad_server_base.get_ad_server_client", lambda: client),
        ):
            result = await inventory_sync_scheduler._run_sync()

        assert result["status"] == "error"

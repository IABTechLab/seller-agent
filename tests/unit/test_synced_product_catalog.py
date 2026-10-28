# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""AI-6: real, storage-backed catalog for synced ad servers.

Defect: ``GET /products`` (and everything reading the catalog) served the
static 13-product default catalog even after a real ad-server sync
(GAM/FreeWheel/S3) had run. ``ProductSetupFlow.state.products`` is
per-flow-instance and never outlives the request, and the catalog
service's in-memory cache is per-worker-process (``infra/docker/Dockerfile``
runs uvicorn with ``--workers 2``), so at most one of two worker processes
ever saw synced data.

Fix under test: synced products are persisted to a dedicated
``synced_product:{id}`` storage key -- deliberately NOT the generic
``product:{id}`` key ``negotiation_service``/``media_kit_service`` already
write/read for an unrelated reason (a proposal-time snapshot, narrower
shape) -- and served fresh from storage on every catalog read when a real
ad server is configured, matching how packages already work (issue #34).
"""

from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch

import pytest

from ad_seller.models.core import DealType, PricingModel
from ad_seller.models.flow_state import ProductDefinition
from ad_seller.services import catalog_service
from ad_seller.storage.base import StorageBackend


class InMemoryStorage(StorageBackend):
    """Fully in-memory storage backend for tests."""

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
        return self._data.pop(key, None) is not None

    async def exists(self, key: str) -> bool:
        return key in self._data

    async def keys(self, pattern: str = "*") -> list[str]:
        import fnmatch

        return [k for k in self._data if fnmatch.fnmatch(k, pattern)]


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    """Reset the static-catalog cache between tests (issue #34 convention)."""
    catalog_service.reset_catalog_cache()
    yield
    catalog_service.reset_catalog_cache()


def _product(
    product_id: str = "prod-a",
    inventory_type: str = "display",
    base_cpm: float = 10.0,
) -> ProductDefinition:
    return ProductDefinition(
        product_id=product_id,
        name=f"Product {product_id}",
        inventory_type=inventory_type,
        supported_deal_types=[DealType.PREFERRED_DEAL],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=base_cpm,
        floor_cpm=round(base_cpm * 0.85, 2),
    )


# =============================================================================
# persist_synced_product / prune_stale_synced_products
# =============================================================================


class TestPersistSyncedProduct:
    async def test_writes_to_dedicated_synced_product_key(self, storage):
        product = _product("prod-a")
        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            await catalog_service.persist_synced_product(product)

        assert await storage.exists("synced_product:prod-a")
        # Never the generic key negotiation_service/media_kit_service use.
        assert not await storage.exists("product:prod-a")

    async def test_does_not_treat_negotiation_snapshot_as_a_sync(self, storage):
        """Storage-key-namespace isolation: an unrelated ``product:{id}``
        write (negotiation_service's proposal-time snapshot, via
        ``serialize_product``'s narrower 7-field shape) must never be read
        as evidence a real ad-server sync happened."""
        await storage.set(
            "product:prod-b",
            {"product_id": "prod-b", "name": "Snapshot only", "inventory_type": "display"},
        )

        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            catalog = await catalog_service.build_synced_product_catalog()

        assert catalog is None, "a generic product:{id} snapshot must not count as a sync"


class TestPruneStaleSyncedProducts:
    async def test_removes_products_not_in_seen_set(self, storage):
        await storage.set("synced_product:keep", _product("keep").model_dump(mode="json"))
        await storage.set("synced_product:drop", _product("drop").model_dump(mode="json"))

        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            await catalog_service.prune_stale_synced_products({"keep"})

        assert await storage.exists("synced_product:keep")
        assert not await storage.exists("synced_product:drop")

    async def test_noop_when_all_seen(self, storage):
        await storage.set("synced_product:a", _product("a").model_dump(mode="json"))

        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            await catalog_service.prune_stale_synced_products({"a"})

        assert await storage.exists("synced_product:a")


# =============================================================================
# build_synced_product_catalog
# =============================================================================


class TestBuildSyncedProductCatalog:
    async def test_returns_none_when_nothing_synced(self, storage):
        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            assert await catalog_service.build_synced_product_catalog() is None

    async def test_reconstructs_products_from_storage(self, storage):
        product = _product("prod-x", inventory_type="ctv", base_cpm=30.0)
        await storage.set("synced_product:prod-x", product.model_dump(mode="json"))

        with patch("ad_seller.storage.factory.get_storage", return_value=storage):
            catalog = await catalog_service.build_synced_product_catalog()

        assert catalog is not None
        assert set(catalog["products"]) == {"prod-x"}
        assert catalog["products"]["prod-x"].base_cpm == 30.0
        assert catalog["inventory_types"] == ["ctv"]


# =============================================================================
# _real_ad_server_configured
# =============================================================================


class TestRealAdServerConfigured:
    @pytest.mark.parametrize(
        "kwargs,expected",
        [
            (
                {
                    "gam_network_code": "12345",
                    "freewheel_sh_mcp_url": None,
                    "ad_server_type": "none",
                },
                True,
            ),
            (
                {
                    "gam_network_code": None,
                    "freewheel_sh_mcp_url": "https://fw.example",
                    "ad_server_type": "none",
                },
                True,
            ),
            (
                {"gam_network_code": None, "freewheel_sh_mcp_url": None, "ad_server_type": "s3"},
                True,
            ),
            (
                {"gam_network_code": None, "freewheel_sh_mcp_url": None, "ad_server_type": "csv"},
                False,
            ),
            (
                {"gam_network_code": None, "freewheel_sh_mcp_url": None, "ad_server_type": "none"},
                False,
            ),
        ],
    )
    def test_matrix(self, kwargs, expected):
        settings = SimpleNamespace(**kwargs)
        assert catalog_service._real_ad_server_configured(settings) is expected


# =============================================================================
# get_static_product_catalog — the live-read invariant
# =============================================================================


class TestGetStaticProductCatalogLiveRead:
    async def test_serves_synced_catalog_live_every_call_not_cached(self, storage):
        """The core AI-6 invariant: a product persisted AFTER the first
        catalog read must appear on the very next read -- no restart, no
        explicit cache-reset call -- proving reads are live, not cached."""
        settings = SimpleNamespace(
            gam_network_code="12345", freewheel_sh_mcp_url=None, ad_server_type="none"
        )

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch("ad_seller.config.get_settings", return_value=settings),
        ):
            first = await catalog_service.get_static_product_catalog()

            await catalog_service.persist_synced_product(
                _product("prod-live", inventory_type="native", base_cpm=42.0)
            )

            second = await catalog_service.get_static_product_catalog()

        # Before any sync: honest fallback to the static default catalog.
        assert len(first["products"]) == len(catalog_service.DEFAULT_PRODUCT_CONFIGS)
        # Immediately after persisting, with NO cache reset in between.
        assert set(second["products"]) == {"prod-live"}
        assert second["products"]["prod-live"].base_cpm == 42.0

    async def test_non_real_ad_server_ignores_synced_products(self, storage):
        """A synced_product key with no real ad server configured (e.g. a
        stale key from a prior config) must not leak into the served
        catalog -- only CSV/static-default apply."""
        settings = SimpleNamespace(
            gam_network_code=None, freewheel_sh_mcp_url=None, ad_server_type="none"
        )
        await storage.set("synced_product:orphan", _product("orphan").model_dump(mode="json"))

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch("ad_seller.config.get_settings", return_value=settings),
        ):
            catalog = await catalog_service.get_static_product_catalog()

        assert "orphan" not in catalog["products"]
        assert len(catalog["products"]) == len(catalog_service.DEFAULT_PRODUCT_CONFIGS)


# =============================================================================
# End-to-end: ProductSetupFlow.sync_from_ad_server persists + prunes
# =============================================================================


class _FakeInventoryItem:
    def __init__(self, id: str, name: str, raw: Optional[dict] = None):
        self.id = id
        self.name = name
        self.raw = raw or {}


class _FakeAdServerClient:
    ad_server_type = SimpleNamespace(value="gam")

    def __init__(self, items):
        self._items = items
        self.limit_requested = "not called"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    # Same default as the real adapters, so a caller that omits limit is caught.
    async def list_inventory(self, filter_str=None, limit=100):
        self.limit_requested = limit
        return self._items


def _flow_settings(**overrides) -> SimpleNamespace:
    defaults = {
        "gam_network_code": "12345",
        "freewheel_sh_mcp_url": None,
        "ad_server_type": "none",
        "seller_organization_id": "test-seller-org",
        "seller_organization_name": "Test Seller",
        # AI-8 added this to real Settings after this fixture was written;
        # _estimate_base_cpm() reads it unconditionally.
        "default_price_floor_cpm": 10.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def _run_sync(storage, settings, client) -> None:
    with (
        patch("ad_seller.flows.product_setup_flow.get_settings", return_value=settings),
        patch("ad_seller.storage.factory.get_storage", return_value=storage),
        patch("ad_seller.clients.ad_server_base.get_ad_server_client", lambda: client),
    ):
        from ad_seller.flows.product_setup_flow import ProductSetupFlow

        flow = ProductSetupFlow()
        await flow.sync_from_ad_server()


class TestSyncPersistsAndPrunesProducts:
    async def test_sync_requests_the_whole_inventory(self, storage):
        """The adapters' default limit is 100; a sync that inherited it
        would see only the first 100 items and prune the rest."""
        client = _FakeAdServerClient([_FakeInventoryItem("gam-001", "Homepage Display Banner")])
        await _run_sync(storage, _flow_settings(), client)

        assert client.limit_requested is None

    async def test_sync_persists_products_to_storage(self, storage):
        client = _FakeAdServerClient(
            [
                _FakeInventoryItem("gam-001", "Homepage Display Banner", {"floor_price_cpm": 8.0}),
                _FakeInventoryItem("gam-002", "CTV Premium App", {"floor_price_cpm": 20.0}),
            ]
        )
        await _run_sync(storage, _flow_settings(), client)

        assert await storage.exists("synced_product:gam-001")
        assert await storage.exists("synced_product:gam-002")

    async def test_resync_with_fewer_items_prunes_the_dropped_product(self, storage):
        settings = _flow_settings()
        client_a = _FakeAdServerClient(
            [
                _FakeInventoryItem("gam-001", "Homepage Display Banner"),
                _FakeInventoryItem("gam-002", "CTV Premium App"),
            ]
        )
        await _run_sync(storage, settings, client_a)

        client_b = _FakeAdServerClient([_FakeInventoryItem("gam-001", "Homepage Display Banner")])
        await _run_sync(storage, settings, client_b)

        assert await storage.exists("synced_product:gam-001")
        assert not await storage.exists("synced_product:gam-002"), (
            "product dropped from the ad server must be pruned, not left as phantom inventory"
        )

    async def test_catalog_reflects_synced_products_live_after_flow_sync(self, storage):
        """End-to-end: GET /products' data source reflects a sync immediately."""
        settings = _flow_settings()
        client = _FakeAdServerClient([_FakeInventoryItem("gam-999", "Pre-Roll Video Unit")])
        await _run_sync(storage, settings, client)

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch("ad_seller.config.get_settings", return_value=settings),
        ):
            catalog = await catalog_service.get_static_product_catalog()

        assert set(catalog["products"]) == {"gam-999"}

    async def test_resync_with_zero_items_serves_an_honestly_empty_catalog(self, storage):
        """A real sync finding zero inventory must not resurrect demo products.

        Regression test: a zero-item sync is a genuine, successful result
        (the ad server really has nothing matching right now), not the
        absence of a sync. Both leave zero ``synced_product:*`` keys, so
        without a separate "a real sync has completed" marker, the catalog
        builder couldn't tell the two apart and silently fell back to the
        13-product static demo catalog -- fake inventory reappearing in
        place of an honest empty one.
        """
        settings = _flow_settings()
        client_a = _FakeAdServerClient([_FakeInventoryItem("gam-999", "Pre-Roll Video Unit")])
        await _run_sync(storage, settings, client_a)

        client_b = _FakeAdServerClient([])
        await _run_sync(storage, settings, client_b)

        assert not await storage.exists("synced_product:gam-999")

        with (
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch("ad_seller.config.get_settings", return_value=settings),
        ):
            catalog = await catalog_service.get_static_product_catalog()

        assert catalog["products"] == {}, (
            "a real sync that found zero inventory must serve an empty catalog, "
            "not fall back to the static demo products"
        )


class TestMidSyncCrashRollsBackPartialWrites:
    """A sync that crashes partway through persisting items must leave
    storage exactly as the last successful sync left it (AI-6 follow-up).

    Defect: ``persist_synced_product``/``_upsert_synced_package`` write
    straight to storage one item at a time inside the loop. An exception
    partway through left every item already written in THIS run
    permanently in storage, even though the sync is reported as failed
    and the code's own docstring claims a failure "never silently
    changes" the synced layer. Found live: synced A+B cleanly, then a
    sync that persists C and crashes before D: A and B survived as
    expected, but C leaked in anyway.
    """

    async def test_item_persisted_before_a_later_crash_is_rolled_back(self, storage):
        settings = _flow_settings()
        client_a = _FakeAdServerClient(
            [_FakeInventoryItem("A", "Product A"), _FakeInventoryItem("B", "Product B")]
        )
        await _run_sync(storage, settings, client_a)

        client_b = _FakeAdServerClient(
            [
                _FakeInventoryItem("C", "Product C"),
                _FakeInventoryItem("D", "Product D"),
                _FakeInventoryItem("E", "Product E"),
            ]
        )
        real_persist = catalog_service.persist_synced_product

        async def flaky_persist(product):
            if product.product_id == "D":
                raise RuntimeError("simulated crash persisting D")
            await real_persist(product)

        with patch("ad_seller.services.catalog_service.persist_synced_product", flaky_persist):
            await _run_sync(storage, settings, client_b)

        assert await storage.exists("synced_product:A")
        assert await storage.exists("synced_product:B")
        assert not await storage.exists("synced_product:C"), (
            "C was persisted by the crashed run before D failed -- it must be "
            "rolled back, not left behind despite the sync being reported as failed"
        )
        assert not await storage.exists("synced_product:D")
        assert not await storage.exists("synced_product:E")

    async def test_resynced_existing_item_restores_its_prior_value_on_rollback(self, storage):
        """Rollback must restore, not blindly delete, an id that already
        existed -- a re-synced item can share an id with one a previous
        successful sync already persisted."""
        settings = _flow_settings()
        client_a = _FakeAdServerClient(
            [_FakeInventoryItem("A", "Product A", {"floor_price_cpm": 8.0})]
        )
        await _run_sync(storage, settings, client_a)
        original = await storage.get("synced_product:A")
        assert original["floor_cpm"] == pytest.approx(8.0 * 0.85)

        client_b = _FakeAdServerClient(
            [
                _FakeInventoryItem("A", "Product A", {"floor_price_cpm": 99.0}),
                _FakeInventoryItem("D", "Product D"),
            ]
        )
        real_persist = catalog_service.persist_synced_product

        async def flaky_persist(product):
            if product.product_id == "D":
                raise RuntimeError("simulated crash persisting D")
            await real_persist(product)

        with patch("ad_seller.services.catalog_service.persist_synced_product", flaky_persist):
            await _run_sync(storage, settings, client_b)

        restored = await storage.get("synced_product:A")
        assert restored == original, (
            "A existed before this crashed run and must be restored to its "
            "prior value, not deleted or left with the crashed run's new value"
        )
        assert not await storage.exists("synced_product:D")

    async def test_packages_loop_crash_rolls_back_this_runs_products(self, storage):
        """The products loop commits before the packages loop runs, so a
        crash in the packages loop must roll back this run's products too,
        not only its packages."""
        settings = _flow_settings()
        client_a = _FakeAdServerClient(
            [_FakeInventoryItem("A", "Product A", {"floor_price_cpm": 8.0})]
        )
        await _run_sync(storage, settings, client_a)
        original = await storage.get("synced_product:A")

        client_b = _FakeAdServerClient(
            [
                _FakeInventoryItem("A", "Product A", {"floor_price_cpm": 99.0}),
                _FakeInventoryItem("C", "Product C"),
            ]
        )

        async def crashing_upsert(self, storage, package):
            raise RuntimeError("simulated crash upserting a package")

        with patch(
            "ad_seller.flows.product_setup_flow.ProductSetupFlow._upsert_synced_package",
            crashing_upsert,
        ):
            await _run_sync(storage, settings, client_b)

        assert await storage.get("synced_product:A") == original, (
            "A must be restored to the last successful sync's value"
        )
        assert not await storage.exists("synced_product:C"), (
            "C was persisted by the products loop of the failed run -- it must be "
            "rolled back when the packages loop fails"
        )


class TestSyncWithRealCsvAdapter:
    """The flow tests above use _FakeAdServerClient, which accepts any
    ``limit``. This runs the sync through the real CSVAdServerClient so a
    flow/adapter contract mismatch (the flow passing ``limit=None``) fails
    here instead of only on a live server."""

    async def test_csv_sync_completes_and_persists_every_row(self, storage, tmp_path):
        import shutil
        from pathlib import Path

        from ad_seller.clients.csv_adapter import CSVAdServerClient

        sample = Path(__file__).parents[2] / "data" / "csv" / "samples" / "ctv_streaming"
        shutil.copy(sample / "inventory.csv", tmp_path / "inventory.csv")
        client = CSVAdServerClient(data_dir=str(tmp_path))
        async with client:
            rows = await client.list_inventory(limit=10_000)

        settings = _flow_settings(gam_network_code=None, ad_server_type="csv")
        with (
            patch("ad_seller.flows.product_setup_flow.get_settings", return_value=settings),
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch("ad_seller.clients.ad_server_base.get_ad_server_client", lambda: client),
        ):
            from ad_seller.flows.product_setup_flow import ProductSetupFlow

            flow = ProductSetupFlow()
            await flow.sync_from_ad_server()

        assert not flow.state.ad_server_sync_failed, flow.state.warnings
        assert len(await storage.keys("synced_product:*")) == len(rows)

    async def test_aws_workshop_packages_never_fall_back_to_desktop_banner(self, storage):
        from pathlib import Path

        from ad_seller.clients.csv_adapter import CSVAdServerClient

        sample = Path(__file__).parents[2] / "data" / "csv" / "samples" / "aws_workshop"
        client = CSVAdServerClient(data_dir=str(sample))

        settings = _flow_settings(gam_network_code=None, ad_server_type="csv")
        with (
            patch("ad_seller.flows.product_setup_flow.get_settings", return_value=settings),
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch("ad_seller.clients.ad_server_base.get_ad_server_client", lambda: client),
        ):
            from ad_seller.flows.product_setup_flow import ProductSetupFlow

            flow = ProductSetupFlow()
            await flow.sync_from_ad_server()

        assert not flow.state.ad_server_sync_failed, flow.state.warnings
        packages = {
            p["name"].removesuffix(" - Synced"): p
            for p in await storage.list_packages()
            if p.get("layer") == "synced"
        }
        expected = {
            "Linear": (["video"], [3, 7], 40.0),
            "Digital Video": (["video"], [2, 4, 5], 25.0),
            "Audio": (["audio"], [2, 4, 5, 6], 15.0),
            "Ctv": (["video"], [3, 7], 35.0),
        }
        for name, (ad_formats, device_types, base_price) in expected.items():
            assert name in packages, f"no synced {name!r} package: {sorted(packages)}"
            pkg = packages[name]
            assert (pkg["ad_formats"], pkg["device_types"], pkg["base_price"]) == (
                ad_formats,
                device_types,
                base_price,
            ), f"{name} package fell back to defaults: {pkg['ad_formats']}, {pkg['device_types']}"

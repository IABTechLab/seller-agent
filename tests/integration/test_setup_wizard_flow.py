"""Task 12: Setup Wizard Flow Test.

Simulates the business user's day-1 setup experience:
1. get_setup_status -> incomplete
2. set_publisher_identity -> set name, domain, org_id
3. create_package -> add media kit entries
4. update_rate_card -> set pricing
5. set_approval_gates -> configure thresholds
6. get_setup_status -> more complete

All .env writes are mocked via _update_env to avoid side effects.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from .conftest import InMemoryStorage, make_settings


class TestSetupWizardFlow:
    """Simulate the complete day-1 setup wizard experience."""

    async def test_step1_initial_status_incomplete(self):
        """Step 1: get_setup_status reports incomplete."""
        from ad_seller.interfaces.mcp_server import get_setup_status

        settings = make_settings(
            seller_organization_name="Default Publisher",
            gam_network_code=None,
            freewheel_sh_mcp_url=None,
        )
        storage = AsyncMock()

        with patch(
            "ad_seller.interfaces.mcp_server._get_settings", return_value=settings
        ), patch(
            "ad_seller.interfaces.mcp_server._get_storage",
            new_callable=AsyncMock,
            return_value=storage,
        ):
            result = json.loads(await get_setup_status())

        assert result["setup_complete"] is False
        assert result["publisher_identity"]["configured"] is False
        assert result["ad_server"]["configured"] is False
        assert result["media_kit"]["configured"] is False
        assert "incomplete" in result["message"].lower()

    async def test_step2_set_publisher_identity(self):
        """Step 2: set_publisher_identity sets name, domain, org_id."""
        from ad_seller.interfaces.mcp_server import set_publisher_identity

        env_updates = {}

        def mock_update_env(key, value):
            env_updates[key] = value

        with patch("ad_seller.interfaces.mcp_server._update_env", side_effect=mock_update_env):
            result = json.loads(
                await set_publisher_identity(
                    name="Acme Media Corp",
                    domain="acmemedia.com",
                    org_id="org-acme-001",
                )
            )

        assert result["status"] == "updated"
        assert result["name"] == "Acme Media Corp"
        assert result["domain"] == "acmemedia.com"
        assert result["org_id"] == "org-acme-001"

        # Verify env updates were called
        assert env_updates["SELLER_ORGANIZATION_NAME"] == "Acme Media Corp"
        assert env_updates["SELLER_DOMAIN"] == "acmemedia.com"
        assert env_updates["SELLER_ORGANIZATION_ID"] == "org-acme-001"

    async def test_step3_create_package(self, in_memory_storage: InMemoryStorage):
        """Step 3: create_package adds media kit entries."""
        from ad_seller.interfaces.mcp_server import create_package

        storage = in_memory_storage
        await storage.connect()

        with patch(
            "ad_seller.interfaces.mcp_server._get_storage",
            new_callable=AsyncMock,
            return_value=storage,
        ):
            # Create CTV package
            result1 = json.loads(
                await create_package(
                    name="Premium CTV Sports",
                    inventory_type="ctv",
                    base_price=38.0,
                    floor_price=30.0,
                    description="Live sports streaming on CTV",
                    is_featured=True,
                )
            )
            assert result1["status"] == "created"
            assert result1["name"] == "Premium CTV Sports"
            result1["package_id"]

            # Create display package
            result2 = json.loads(
                await create_package(
                    name="Display News ROS",
                    inventory_type="display",
                    base_price=14.0,
                    floor_price=9.0,
                    description="Run-of-site display on news properties",
                )
            )
            assert result2["status"] == "created"
            result2["package_id"]

        # Verify packages are in storage
        packages = await storage.list_packages()
        assert len(packages) == 2
        names = {p["name"] for p in packages}
        assert "Premium CTV Sports" in names
        assert "Display News ROS" in names

    async def test_step4_update_rate_card(self, in_memory_storage: InMemoryStorage):
        """Step 4: update_rate_card sets pricing."""
        from ad_seller.interfaces.mcp_server import update_rate_card

        storage = in_memory_storage
        await storage.connect()

        entries = json.dumps(
            [
                {"inventory_type": "ctv", "base_cpm": 40.0},
                {"inventory_type": "display", "base_cpm": 14.0},
                {"inventory_type": "video", "base_cpm": 28.0},
            ]
        )

        with patch(
            "ad_seller.interfaces.mcp_server._get_storage",
            new_callable=AsyncMock,
            return_value=storage,
        ):
            result = json.loads(await update_rate_card(entries=entries))

        assert result["status"] == "updated"
        assert result["entries"] == 3

        # Verify rate card is in storage
        rate_card = await storage.get("rate_card:current")
        assert rate_card is not None
        assert len(rate_card["entries"]) == 3
        ctv_entry = next(e for e in rate_card["entries"] if e["inventory_type"] == "ctv")
        assert ctv_entry["base_cpm"] == 40.0

    async def test_step5_set_approval_gates(self):
        """Step 5: set_approval_gates configures thresholds."""
        from ad_seller.interfaces.mcp_server import set_approval_gates

        env_updates = {}

        def mock_update_env(key, value):
            env_updates[key] = value

        with patch("ad_seller.interfaces.mcp_server._update_env", side_effect=mock_update_env):
            result = json.loads(
                await set_approval_gates(
                    enabled=True,
                    required_flows="proposal_decision,deal_registration",
                    timeout_hours=48,
                )
            )

        assert result["status"] == "updated"
        assert result["enabled"] is True
        assert result["timeout_hours"] == 48

        assert env_updates["APPROVAL_GATE_ENABLED"] == "true"
        assert env_updates["APPROVAL_REQUIRED_FLOWS"] == "proposal_decision,deal_registration"
        assert env_updates["APPROVAL_TIMEOUT_HOURS"] == "48"

    async def test_step6_status_more_complete_after_setup(self, in_memory_storage: InMemoryStorage):
        """Step 6: get_setup_status reports more complete after setup steps.

        After setting identity and creating packages, setup_complete depends
        on having identity + ad_server + packages. We configure identity
        and packages but NOT ad_server, so setup_complete is still False
        but identity and media_kit are now configured.
        """
        from ad_seller.interfaces.mcp_server import create_package, get_setup_status

        storage = in_memory_storage
        await storage.connect()

        # Create a package first
        with patch(
            "ad_seller.interfaces.mcp_server._get_storage",
            new_callable=AsyncMock,
            return_value=storage,
        ):
            await create_package(
                name="Test Package",
                inventory_type="display",
                base_price=12.0,
                floor_price=8.0,
            )

        # Now check status with updated identity
        settings = make_settings(
            seller_organization_name="Acme Media Corp",
            gam_network_code=None,
            freewheel_sh_mcp_url=None,
        )

        # Mock MediaKitService to read from our storage
        mock_service = AsyncMock()
        mock_service.list_packages_public.return_value = [MagicMock()]
        fake_module = MagicMock()
        fake_module.MediaKitService.return_value = mock_service

        with patch(
            "ad_seller.interfaces.mcp_server._get_settings", return_value=settings
        ), patch(
            "ad_seller.interfaces.mcp_server._get_storage",
            new_callable=AsyncMock,
            return_value=storage,
        ), patch.dict(
            "sys.modules",
            {"ad_seller.engines.media_kit_service": fake_module},
        ):
            result = json.loads(await get_setup_status())

        # Identity is now configured
        assert result["publisher_identity"]["configured"] is True
        assert result["publisher_identity"]["name"] == "Acme Media Corp"

        # Media kit has packages
        assert result["media_kit"]["configured"] is True

        # Ad server still not configured, so setup_complete is False
        assert result["ad_server"]["configured"] is False
        assert result["setup_complete"] is False

    async def test_full_setup_complete(self, in_memory_storage: InMemoryStorage):
        """When identity + ad_server + packages are all set, setup is complete."""
        from ad_seller.interfaces.mcp_server import get_setup_status

        storage = in_memory_storage
        await storage.connect()

        settings = make_settings(
            seller_organization_name="Acme Media Corp",
            gam_network_code="12345",
        )

        mock_service = AsyncMock()
        mock_service.list_packages_public.return_value = [MagicMock()]
        fake_module = MagicMock()
        fake_module.MediaKitService.return_value = mock_service

        with patch(
            "ad_seller.interfaces.mcp_server._get_settings", return_value=settings
        ), patch(
            "ad_seller.interfaces.mcp_server._get_storage",
            new_callable=AsyncMock,
            return_value=storage,
        ), patch.dict(
            "sys.modules",
            {"ad_seller.engines.media_kit_service": fake_module},
        ):
            result = json.loads(await get_setup_status())

        assert result["publisher_identity"]["configured"] is True
        assert result["ad_server"]["configured"] is True
        assert result["media_kit"]["configured"] is True
        assert result["setup_complete"] is True
        assert "fully configured" in result["message"].lower()


class TestSetPublisherIdentityEdgeCases:
    """Edge cases for set_publisher_identity."""

    async def test_name_only(self):
        from ad_seller.interfaces.mcp_server import set_publisher_identity

        env_updates = {}

        def mock_update_env(key, value):
            env_updates[key] = value

        with patch("ad_seller.interfaces.mcp_server._update_env", side_effect=mock_update_env):
            result = json.loads(
                await set_publisher_identity(name="Just A Name")
            )

        assert result["status"] == "updated"
        assert result["name"] == "Just A Name"
        assert result["domain"] == "(unchanged)"
        assert result["org_id"] == "(unchanged)"

        # Only name should be written to env
        assert "SELLER_ORGANIZATION_NAME" in env_updates
        assert "SELLER_DOMAIN" not in env_updates
        assert "SELLER_ORGANIZATION_ID" not in env_updates


class TestGetRateCardIntegration:
    """Test get_rate_card returns defaults or stored card."""

    async def test_returns_defaults_when_no_card_stored(self, in_memory_storage: InMemoryStorage):
        from ad_seller.interfaces.mcp_server import get_rate_card

        storage = in_memory_storage
        await storage.connect()

        with patch(
            "ad_seller.interfaces.mcp_server._get_storage",
            new_callable=AsyncMock,
            return_value=storage,
        ):
            result = json.loads(await get_rate_card())

        assert result["source"] == "defaults"
        assert len(result["entries"]) >= 5  # display, video, ctv, mobile_app, native, audio

    async def test_returns_stored_card(self, in_memory_storage: InMemoryStorage):
        from ad_seller.interfaces.mcp_server import get_rate_card, update_rate_card

        storage = in_memory_storage
        await storage.connect()

        entries = json.dumps([{"inventory_type": "ctv", "base_cpm": 42.0}])
        with patch(
            "ad_seller.interfaces.mcp_server._get_storage",
            new_callable=AsyncMock,
            return_value=storage,
        ):
            await update_rate_card(entries=entries)
            result = json.loads(await get_rate_card())

        assert result["entries"][0]["base_cpm"] == 42.0

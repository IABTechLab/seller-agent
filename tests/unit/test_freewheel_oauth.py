from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from ad_seller.clients import freewheel_oauth
from ad_seller.clients.freewheel_oauth import (
    FreeWheelOAuthManager,
    FreeWheelOAuthState,
    build_authorize_url,
    build_oauth_mcp_url,
    generate_pkce_pair,
)


class TestOAuthHelpers:
    def test_build_oauth_mcp_url(self):
        assert build_oauth_mcp_url("https://shmcp.freewheel.com") == (
            "https://shmcp.freewheel.com/mcp/oauth"
        )

    def test_generate_pkce_pair(self):
        verifier, challenge = generate_pkce_pair()
        assert len(verifier) >= 43
        assert challenge

    def test_build_authorize_url(self):
        url = build_authorize_url(
            "https://shmcp.freewheel.com/oauth/authorize",
            client_id="client-123",
            redirect_uri="http://127.0.0.1:8765/callback",
            code_challenge="challenge",
            state="state-123",
            scope="api",
        )
        parsed = urlparse(url)
        query = parse_qs(parsed.query)

        assert parsed.path == "/oauth/authorize"
        assert query["client_id"] == ["client-123"]
        assert query["code_challenge_method"] == ["S256"]
        assert query["state"] == ["state-123"]


class TestOAuthManager:
    def _settings(self, tmp_path):
        return SimpleNamespace(
            freewheel_sh_mcp_url="https://shmcp.freewheel.com",
            freewheel_sh_oauth_token_path=str(tmp_path / "freewheel-oauth.json"),
            freewheel_sh_oauth_client_id=None,
            freewheel_sh_oauth_client_name="Ad Seller Agent",
            freewheel_sh_oauth_redirect_uri="http://127.0.0.1:8765/callback",
            freewheel_sh_oauth_scope="api",
            freewheel_bc_mcp_url="https://bcmcp.freewheel.com",
            freewheel_bc_oauth_token_path=str(tmp_path / "freewheel-bc-oauth.json"),
            freewheel_bc_oauth_client_id=None,
            freewheel_bc_oauth_client_name="Ad Seller Agent",
            freewheel_bc_oauth_redirect_uri="http://127.0.0.1:8766/callback",
            freewheel_bc_oauth_scope="api",
        )

    def test_for_provider_bc_uses_bc_config(self, tmp_path):
        settings = self._settings(tmp_path)
        manager = FreeWheelOAuthManager.for_provider(settings, "bc")

        assert manager.config.provider_name == "Buyer Cloud"
        assert manager.config.mcp_url == "https://bcmcp.freewheel.com"
        assert manager.config.redirect_uri == "http://127.0.0.1:8766/callback"

    @pytest.mark.asyncio
    async def test_get_access_token_uses_cached_token(self, tmp_path):
        manager = FreeWheelOAuthManager(self._settings(tmp_path))
        state = FreeWheelOAuthState(
            client_id="client-123",
            access_token="cached-token",
            refresh_token="refresh-token",
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )
        manager.save_state(state)

        assert await manager.get_access_token() == "cached-token"

    @pytest.mark.asyncio
    async def test_get_access_token_refreshes_expired_token(self, tmp_path, monkeypatch):
        manager = FreeWheelOAuthManager(self._settings(tmp_path))
        state = FreeWheelOAuthState(
            client_id="client-123",
            access_token="expired-token",
            refresh_token="refresh-token",
            expires_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        )
        manager.save_state(state)

        async def fake_discover(url):
            return {"token_endpoint": f"{url}/oauth/token"}

        async def fake_refresh(metadata, *, client_id, refresh_token):
            assert client_id == "client-123"
            assert refresh_token == "refresh-token"
            return {
                "access_token": "fresh-token",
                "refresh_token": "rotated-refresh-token",
                "expires_in": 3600,
                "scope": "api",
                "token_type": "Bearer",
            }

        monkeypatch.setattr(freewheel_oauth, "discover_oauth_metadata", fake_discover)
        monkeypatch.setattr(freewheel_oauth, "refresh_oauth_token", fake_refresh)

        assert await manager.get_access_token() == "fresh-token"

        saved = manager.load_state()
        assert saved is not None
        assert saved.refresh_token == "rotated-refresh-token"

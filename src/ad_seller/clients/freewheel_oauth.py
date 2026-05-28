"""OAuth helpers for FreeWheel Streaming Hub MCP access."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import threading
import webbrowser
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


def _origin_from_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid FreeWheel URL: {url}")
    return f"{parsed.scheme}://{parsed.netloc}"


def build_oauth_mcp_url(url: str) -> str:
    """Return the OAuth-protected MCP endpoint for a FreeWheel SH URL."""
    base = _origin_from_url(url)
    return f"{base}/mcp/oauth"


def build_oauth_discovery_url(url: str) -> str:
    """Return the OAuth discovery URL for a FreeWheel SH URL."""
    base = _origin_from_url(url)
    return f"{base}/.well-known/oauth-authorization-server"


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code verifier/challenge pair."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return verifier, challenge


def build_authorize_url(
    authorization_endpoint: str,
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    scope: str,
) -> str:
    """Build the OAuth authorization URL for PKCE login."""
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "scope": scope,
        }
    )
    return f"{authorization_endpoint}?{query}"


@dataclass
class FreeWheelOAuthState:
    """Persisted OAuth state for FreeWheel SH."""

    client_id: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_at: Optional[str] = None
    scope: str = "api"
    token_type: str = "Bearer"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FreeWheelOAuthState":
        return cls(
            client_id=data.get("client_id"),
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            expires_at=data.get("expires_at"),
            scope=data.get("scope", "api"),
            token_type=data.get("token_type", "Bearer"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_access_token_valid(self, *, skew_seconds: int = 60) -> bool:
        if not self.access_token or not self.expires_at:
            return False
        try:
            expires_at = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at - timedelta(seconds=skew_seconds) > datetime.now(timezone.utc)


@dataclass
class FreeWheelOAuthConfig:
    """Provider-specific OAuth settings for FreeWheel MCP connections."""

    provider_name: str
    mcp_url: str
    client_id: Optional[str]
    client_name: str
    redirect_uri: str
    scope: str
    token_path: str
    login_command_hint: str


async def discover_oauth_metadata(url: str) -> dict[str, Any]:
    """Discover OAuth metadata from the FreeWheel SH server."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(build_oauth_discovery_url(url))
        response.raise_for_status()
        return response.json()


async def register_oauth_client(
    metadata: dict[str, Any],
    *,
    redirect_uri: str,
    client_name: str,
) -> str:
    """Dynamically register a public PKCE client and return its client_id."""
    registration_endpoint = metadata.get("registration_endpoint")
    if not registration_endpoint:
        raise RuntimeError("OAuth metadata missing registration_endpoint")

    payload = {"redirect_uris": [redirect_uri], "client_name": client_name}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(registration_endpoint, json=payload)
        response.raise_for_status()
        body = response.json()

    client_id = body.get("client_id")
    if not client_id:
        raise RuntimeError("OAuth registration response missing client_id")
    return client_id


async def exchange_oauth_code(
    metadata: dict[str, Any],
    *,
    client_id: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, Any]:
    """Exchange an authorization code for OAuth tokens."""
    token_endpoint = metadata.get("token_endpoint")
    if not token_endpoint:
        raise RuntimeError("OAuth metadata missing token_endpoint")

    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(token_endpoint, data=payload)
        response.raise_for_status()
        return response.json()


async def refresh_oauth_token(
    metadata: dict[str, Any],
    *,
    client_id: str,
    refresh_token: str,
) -> dict[str, Any]:
    """Refresh an OAuth access token using a stored refresh token."""
    token_endpoint = metadata.get("token_endpoint")
    if not token_endpoint:
        raise RuntimeError("OAuth metadata missing token_endpoint")

    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(token_endpoint, data=payload)
        response.raise_for_status()
        return response.json()


class _OAuthCallbackHTTPServer(HTTPServer):
    """Single-use local HTTP server for OAuth callback capture."""

    allow_reuse_address = True

    def __init__(self, redirect_uri: str):
        parsed = urlparse(redirect_uri)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        self.callback_path = parsed.path or "/"
        self.result: Optional[dict[str, str]] = None
        self.event = threading.Event()
        super().__init__((host, port), _OAuthCallbackHandler)

    def wait_for_result(self, timeout_seconds: int) -> dict[str, str]:
        if not self.event.wait(timeout_seconds):
            raise TimeoutError("Timed out waiting for OAuth callback")
        if self.result is None:
            raise RuntimeError("OAuth callback completed without a result")
        return self.result


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Capture the OAuth redirect query once and stop."""

    server: _OAuthCallbackHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != self.server.callback_path:
            self.send_response(404)
            self.end_headers()
            return

        params = {key: values[0] for key, values in parse_qs(parsed.query).items() if values}
        self.server.result = params
        self.server.event.set()

        has_error = "error" in params
        self.send_response(400 if has_error else 200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if has_error:
            body = "<html><body><h1>FreeWheel login failed</h1><p>You can close this window.</p></body></html>"
        else:
            body = "<html><body><h1>FreeWheel login complete</h1><p>You can close this window.</p></body></html>"
        self.wfile.write(body.encode("utf-8"))


class FreeWheelOAuthManager:
    """Manage FreeWheel OAuth bootstrap and token refresh."""

    def __init__(self, settings_or_config: Any):
        if isinstance(settings_or_config, FreeWheelOAuthConfig):
            self.config = settings_or_config
        else:
            settings = settings_or_config
            self.config = FreeWheelOAuthConfig(
                provider_name="Streaming Hub",
                mcp_url=settings.freewheel_sh_mcp_url,
                client_id=settings.freewheel_sh_oauth_client_id,
                client_name=settings.freewheel_sh_oauth_client_name,
                redirect_uri=settings.freewheel_sh_oauth_redirect_uri,
                scope=settings.freewheel_sh_oauth_scope,
                token_path=settings.freewheel_sh_oauth_token_path,
                login_command_hint="ad-seller freewheel-login --provider sh",
            )
        self.token_path = Path(self.config.token_path).expanduser()

    @classmethod
    def for_provider(cls, settings: Any, provider: str) -> "FreeWheelOAuthManager":
        """Create a provider-specific OAuth manager for SH or BC."""
        normalized = provider.strip().lower()
        if normalized == "sh":
            return cls(
                FreeWheelOAuthConfig(
                    provider_name="Streaming Hub",
                    mcp_url=settings.freewheel_sh_mcp_url,
                    client_id=settings.freewheel_sh_oauth_client_id,
                    client_name=settings.freewheel_sh_oauth_client_name,
                    redirect_uri=settings.freewheel_sh_oauth_redirect_uri,
                    scope=settings.freewheel_sh_oauth_scope,
                    token_path=settings.freewheel_sh_oauth_token_path,
                    login_command_hint="ad-seller freewheel-login --provider sh",
                )
            )
        if normalized == "bc":
            return cls(
                FreeWheelOAuthConfig(
                    provider_name="Buyer Cloud",
                    mcp_url=settings.freewheel_bc_mcp_url,
                    client_id=settings.freewheel_bc_oauth_client_id,
                    client_name=settings.freewheel_bc_oauth_client_name,
                    redirect_uri=settings.freewheel_bc_oauth_redirect_uri,
                    scope=settings.freewheel_bc_oauth_scope,
                    token_path=settings.freewheel_bc_oauth_token_path,
                    login_command_hint="ad-seller freewheel-login --provider bc",
                )
            )
        raise ValueError(f"Unsupported FreeWheel provider: {provider}")

    def load_state(self) -> Optional[FreeWheelOAuthState]:
        if not self.token_path.exists():
            return None
        data = json.loads(self.token_path.read_text(encoding="utf-8"))
        return FreeWheelOAuthState.from_dict(data)

    def save_state(self, state: FreeWheelOAuthState) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        self.token_path.chmod(0o600)

    async def bootstrap(
        self,
        *,
        open_browser: bool = True,
        timeout_seconds: int = 300,
    ) -> FreeWheelOAuthState:
        mcp_url = self.config.mcp_url
        if not mcp_url:
            raise ConnectionError(f"{self.config.provider_name} MCP URL not configured.")

        metadata = await discover_oauth_metadata(mcp_url)
        state = self.load_state() or FreeWheelOAuthState(scope=self.config.scope)
        client_id = self.config.client_id or state.client_id
        if not client_id:
            client_id = await register_oauth_client(
                metadata,
                redirect_uri=self.config.redirect_uri,
                client_name=self.config.client_name,
            )

        code_verifier, code_challenge = generate_pkce_pair()
        csrf_state = secrets.token_urlsafe(24)
        authorize_url = build_authorize_url(
            metadata["authorization_endpoint"],
            client_id=client_id,
            redirect_uri=self.config.redirect_uri,
            code_challenge=code_challenge,
            state=csrf_state,
            scope=self.config.scope,
        )

        callback_server = _OAuthCallbackHTTPServer(self.config.redirect_uri)
        server_thread = threading.Thread(target=callback_server.handle_request, daemon=True)
        server_thread.start()

        if open_browser:
            webbrowser.open(authorize_url)

        try:
            callback_params = await asyncio.to_thread(callback_server.wait_for_result, timeout_seconds)
        finally:
            callback_server.server_close()

        if callback_params.get("state") != csrf_state:
            raise RuntimeError("OAuth callback state mismatch")
        if "error" in callback_params:
            description = callback_params.get("error_description", callback_params["error"])
            raise RuntimeError(f"OAuth authorization failed: {description}")

        code = callback_params.get("code")
        if not code:
            raise RuntimeError("OAuth callback missing authorization code")

        token_response = await exchange_oauth_code(
            metadata,
            client_id=client_id,
            code=code,
            redirect_uri=self.config.redirect_uri,
            code_verifier=code_verifier,
        )
        new_state = self._state_from_token_response(
            token_response,
            client_id=client_id,
            existing_state=state,
        )
        self.save_state(new_state)
        return new_state

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        state = self.load_state()
        if not state:
            raise ConnectionError(
                f"{self.config.provider_name} OAuth session not initialized. "
                f"Run '{self.config.login_command_hint}'."
            )

        if not force_refresh and state.is_access_token_valid():
            return state.access_token or ""

        if not state.refresh_token:
            raise ConnectionError(
                f"{self.config.provider_name} OAuth refresh token missing. "
                f"Run '{self.config.login_command_hint}'."
            )

        mcp_url = self.config.mcp_url
        if not mcp_url:
            raise ConnectionError(f"{self.config.provider_name} MCP URL not configured.")

        metadata = await discover_oauth_metadata(mcp_url)
        client_id = self.config.client_id or state.client_id
        if not client_id:
            raise ConnectionError(
                f"{self.config.provider_name} OAuth client_id missing. "
                f"Run '{self.config.login_command_hint}'."
            )

        token_response = await refresh_oauth_token(
            metadata,
            client_id=client_id,
            refresh_token=state.refresh_token,
        )
        new_state = self._state_from_token_response(
            token_response,
            client_id=client_id,
            existing_state=state,
        )
        self.save_state(new_state)
        return new_state.access_token or ""

    def _state_from_token_response(
        self,
        token_response: dict[str, Any],
        *,
        client_id: str,
        existing_state: Optional[FreeWheelOAuthState],
    ) -> FreeWheelOAuthState:
        expires_in = int(token_response.get("expires_in", 3600))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        return FreeWheelOAuthState(
            client_id=client_id,
            access_token=token_response.get("access_token"),
            refresh_token=token_response.get("refresh_token")
            or (existing_state.refresh_token if existing_state else None),
            expires_at=expires_at.isoformat(),
            scope=token_response.get("scope", self.config.scope),
            token_type=token_response.get("token_type", "Bearer"),
        )
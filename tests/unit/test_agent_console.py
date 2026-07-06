"""Tests for the internal Agent Console."""

import httpx
import pytest
from httpx import ASGITransport

from ad_seller.interfaces.api.main import app


@pytest.mark.asyncio
async def test_console_shell_and_generated_client_render():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        shell = await client.get("/console")
        script = await client.get("/console/openapi-client.js")

    assert shell.status_code == 200
    assert "Ad Seller Agent Console" in shell.text
    assert "/docs" in shell.text
    assert script.status_code == 200
    assert "window.agentConsoleOpenApi" in script.text
    assert '"/health"' in script.text


@pytest.mark.asyncio
async def test_console_proxy_forwards_to_health_and_rejects_external_urls():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.post(
            "/console/api/proxy",
            json={"method": "GET", "path": "/health"},
        )
        external = await client.post(
            "/console/api/proxy",
            json={"method": "GET", "path": "https://example.com/health"},
        )

    assert health.status_code == 200
    assert health.json() == {"status": "healthy"}
    assert external.status_code == 400
    assert "app-relative" in external.json()["detail"]

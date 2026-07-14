# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Quickstart smoke test — proves the *documented* startup command works.

The seller quickstart (README.md and docs/getting-started/quickstart.md)
tells a fresh-clone developer to run:

    uvicorn ad_seller.interfaces.api.main:app --port 8000

and then hit ``GET /health``, ``GET /products``, etc. This test is the
executable contract behind that promise. It:

1. imports the app at the documented module path
   (``ad_seller.interfaces.api.main:app``) — if that path is wrong or the
   app fails to import, this fails at collection;
2. boots it through the real ASGI lifespan via ``TestClient`` as a context
   manager (inventory-sync scheduler start + MCP mount), exactly as uvicorn
   would — no real network, no LLM calls;
3. exercises the health/root endpoints and one representative real endpoint
   (``GET /products``, the static catalog) that the quickstart documents.

If someone renames the module, moves ``app``, or breaks startup, the docs'
entrypoint is now a lie and this test goes red.

``ANTHROPIC_API_KEY`` is a *required* setting for this service (see
``ad_seller/config/settings.py``), so the quickstart's ``.env`` step is not
optional. We inject a dummy value here — the smoke path never calls the LLM,
so no real key is needed, but the app will not import/boot without *some*
value present. This mirrors what a developer's ``.env`` provides.
"""

import os

# Must be set before the app (and its settings) import — settings.anthropic_api_key
# has no default. A real key is NOT needed: the endpoints exercised below do no
# LLM/network work. This is the same value CI uses (ANTHROPIC_API_KEY=test).
os.environ.setdefault("ANTHROPIC_API_KEY", "test-not-a-real-key")

from fastapi.testclient import TestClient  # noqa: E402

from ad_seller.interfaces.api.main import app  # noqa: E402


def test_documented_entrypoint_boots_and_serves():
    """Boot the app through its real lifespan and hit the documented endpoints."""
    # Context-manager form runs startup + shutdown (lifespan), i.e. the same
    # path `uvicorn ad_seller.interfaces.api.main:app` takes.
    with TestClient(app) as client:
        # 1) Health — the quickstart's "Verify It Works" step.
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}

        # 2) Root — advertised API entrypoint.
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Ad Seller System API"
        assert body["docs"] == "/docs"

        # 3) Representative real endpoint: the product catalog. This is the
        #    quickstart's first "real" call (`curl .../products`). It reads the
        #    static catalog — no ad-server/LLM dependency — so it must return a
        #    populated catalog on a fresh clone.
        resp = client.get("/products")
        assert resp.status_code == 200
        payload = resp.json()
        assert "products" in payload
        assert isinstance(payload["products"], list)
        assert len(payload["products"]) > 0

# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Drift guard for the committed OpenAPI document.

Builds the OpenAPI schema in memory the same way ``scripts/generate_openapi.py``
does (``app.openapi()``) and asserts it equals the committed
``docs/api/openapi.json``. Any code change that adds, removes, or reshapes a
REST endpoint or schema breaks this test until the document is regenerated with:

    python scripts/generate_openapi.py

The comparison is on parsed JSON, so formatting alone cannot cause a false drift.
"""

import json
from pathlib import Path

from ad_seller.interfaces.api.main import app

_COMMITTED = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.json"


def test_openapi_json_matches_app():
    """Committed docs/api/openapi.json must equal the schema the app generates."""
    assert _COMMITTED.exists(), f"{_COMMITTED} is missing — run: python scripts/generate_openapi.py"
    assert json.loads(_COMMITTED.read_text()) == app.openapi(), (
        "docs/api/openapi.json is out of date with the code.\n"
        "Regenerate it with: python scripts/generate_openapi.py"
    )

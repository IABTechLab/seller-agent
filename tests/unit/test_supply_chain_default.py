# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""GET /api/v1/supply-chain must not 500 when SELLER_ORGANIZATION_ID is unset.

``seller_organization_id`` is ``Optional[str] = None``, so the old
``getattr(..., "default")`` never fired and pydantic rejected ``sid=None``
on the default single-node chain (issue #74).
"""

import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

_broken_flows = [
    "ad_seller.flows.execution_activation_flow",
]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

from ad_seller.config.settings import seller_id_or_default  # noqa: E402
from ad_seller.interfaces.api.routers.admin import get_supply_chain  # noqa: E402


def test_seller_id_or_default_uses_fallback_when_attribute_is_none():
    assert seller_id_or_default(SimpleNamespace(seller_organization_id=None)) == "default"
    assert seller_id_or_default(SimpleNamespace(seller_organization_id="")) == "default"
    assert seller_id_or_default(SimpleNamespace()) == "default"
    assert seller_id_or_default(SimpleNamespace(seller_organization_id="pub-9")) == "pub-9"


@pytest.mark.asyncio
async def test_http_default_chain_when_organization_id_unset():
    settings = SimpleNamespace(seller_organization_id=None, sellers_json_path=None)
    with patch("ad_seller.config.get_settings", return_value=settings):
        body = await get_supply_chain()

    assert body.seller_id == "default"
    assert body.seller_name == "Demo Publisher"
    assert body.domain == "demo-publisher.example.com"
    assert len(body.schain) == 1
    assert body.schain[0].sid == "default"
    assert body.schain[0].asi == "demo-publisher.example.com"


@pytest.mark.asyncio
async def test_http_default_chain_keeps_configured_organization_id():
    settings = SimpleNamespace(seller_organization_id="org-live", sellers_json_path=None)
    with patch("ad_seller.config.get_settings", return_value=settings):
        body = await get_supply_chain()

    assert body.seller_id == "org-live"
    assert body.schain[0].sid == "org-live"


@pytest.mark.asyncio
async def test_mcp_default_chain_when_organization_id_unset():
    from ad_seller.interfaces.mcp_server import get_supply_chain as mcp_get_supply_chain

    settings = SimpleNamespace(seller_organization_id=None, sellers_json_path=None)
    with patch("ad_seller.interfaces.mcp_server._get_settings", return_value=settings):
        payload = json.loads(await mcp_get_supply_chain())

    assert payload["seller_id"] == "default"
    assert payload["schain"][0]["sid"] == "default"

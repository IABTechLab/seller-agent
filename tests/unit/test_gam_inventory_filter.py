"""Issue #127: the inventory sync's GAM filter must be one the Ad Manager API accepts.

The sync runs against the real ``GAMAdServerClient`` and ``GAMRestClient``;
only the discovery service object is replaced, by a stand-in that applies the
documented filter rules (https://developers.google.com/ad-manager/api/beta/filters):
``status`` is an enum, so it takes ``=`` with the case-sensitive value, and the
``:`` (HAS) operator on it is a 400.
"""

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.unit.test_inventory_sync_persistence import InMemoryStorage, _flow_settings

_AD_UNIT_STATUSES = {"ACTIVE", "INACTIVE", "ARCHIVED"}
_HAS_ERROR = "HAS operator only supports repeated fields or singular string fields."


class _AdManagerHttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"<HttpError {status}: {message}>")
        self.status = status


class _FakeAdManagerService:
    """``service.networks().adUnits().list(...).execute()`` over a fixed ad unit list."""

    def __init__(self, units: list[tuple[str, str]]):
        self._units = [
            {
                "name": f"networks/12345/adUnits/{unit_id}",
                "displayName": f"Display Unit {unit_id}",
                "status": status,
                "adUnitSizes": [{"size": {"width": 300, "height": 250}}],
            }
            for unit_id, status in units
        ]
        self.requests: list[dict] = []
        self._filters_by_token: dict[str, str] = {}

    def networks(self):
        return self

    def adUnits(self):
        return self

    def list(self, **kwargs):
        self.requests.append(kwargs)
        request = MagicMock()
        request.execute.side_effect = lambda: self._execute(**kwargs)
        return request

    def _matches(self, unit: dict, filter_str: str | None) -> bool:
        if not filter_str:
            return True
        if re.fullmatch(r"\s*status\s*:.*", filter_str):
            raise _AdManagerHttpError(400, _HAS_ERROR)
        match = re.fullmatch(r"\s*status\s*=\s*(\S+)\s*", filter_str)
        if not match or match.group(1) not in _AD_UNIT_STATUSES:
            raise _AdManagerHttpError(400, "Value is not parsable.")
        return unit["status"] == match.group(1)

    def _execute(self, parent, pageSize, pageToken=None, filter=None):
        if pageToken is not None and self._filters_by_token.get(pageToken) != filter:
            raise _AdManagerHttpError(400, "The page token does not match the request.")
        matching = [u for u in self._units if self._matches(u, filter)]
        start = int(pageToken or 0)
        end = start + pageSize
        response = {"adUnits": matching[start:end]}
        if end < len(matching):
            response["nextPageToken"] = str(end)
            self._filters_by_token[str(end)] = filter
        return response


def _gam_client(service: _FakeAdManagerService):
    from ad_seller.clients.gam_adapter import GAMAdServerClient

    with patch("ad_seller.clients.gam_adapter.GAMSoapClient"):
        client = GAMAdServerClient()
    client._rest.network_code = "12345"
    client._rest.connect = AsyncMock()
    client._rest.disconnect = AsyncMock()
    client._rest._service = service
    return client


async def _run_scheduled_sync(client, storage):
    from ad_seller.services import inventory_sync_scheduler

    with (
        patch("ad_seller.flows.product_setup_flow.get_settings", return_value=_flow_settings()),
        patch("ad_seller.storage.factory.get_storage", return_value=storage),
        patch("ad_seller.clients.ad_server_base.get_ad_server_client", lambda: client),
    ):
        return await inventory_sync_scheduler._run_sync()


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


async def _synced_product_ids(storage) -> set[str]:
    return {key.split(":", 1)[1] for key in await storage.keys("synced_product:*")}


async def test_the_reported_filter_is_rejected_by_the_stand_in():
    service = _FakeAdManagerService([("1", "ACTIVE")])

    with pytest.raises(_AdManagerHttpError, match="HAS operator"):
        service.networks().adUnits().list(
            parent="networks/12345", pageSize=100, filter="status:ACTIVE"
        ).execute()


async def test_scheduled_gam_sync_succeeds_and_keeps_only_active_units(storage):
    service = _FakeAdManagerService(
        [("101", "ACTIVE"), ("102", "INACTIVE"), ("103", "ARCHIVED"), ("104", "ACTIVE")]
    )

    result = await _run_scheduled_sync(_gam_client(service), storage)

    assert result["status"] == "success", result
    assert result["items_synced"] == 2
    assert await _synced_product_ids(storage) == {"101", "104"}
    assert [r["filter"] for r in service.requests] == ["status = ACTIVE"]
    assert all(r["parent"] == "networks/12345" for r in service.requests)


async def test_filter_is_repeated_on_every_page(storage):
    units = [(str(i), "ACTIVE") for i in range(250)] + [("archived-1", "ARCHIVED")]
    service = _FakeAdManagerService(units)

    result = await _run_scheduled_sync(_gam_client(service), storage)

    assert result["status"] == "success", result
    assert len(await _synced_product_ids(storage)) == 250
    assert [(r["pageToken"], r["filter"]) for r in service.requests] == [
        (None, "status = ACTIVE"),
        ("100", "status = ACTIVE"),
        ("200", "status = ACTIVE"),
    ]


async def test_network_with_no_active_units_syncs_an_empty_catalog(storage):
    service = _FakeAdManagerService([("102", "INACTIVE"), ("103", "ARCHIVED")])

    result = await _run_scheduled_sync(_gam_client(service), storage)

    assert result["status"] == "success", result
    assert await _synced_product_ids(storage) == set()


@pytest.mark.parametrize(
    "rejected_filter, error",
    [
        ("status:ACTIVE", "HAS operator"),
        ("status = active", "not parsable"),
        ("status = ENABLED", "not parsable"),
    ],
)
async def test_a_rejected_filter_fails_the_sync_and_keeps_the_last_good_catalog(
    storage, rejected_filter, error
):
    service = _FakeAdManagerService([("101", "ACTIVE"), ("104", "ACTIVE")])
    first = await _run_scheduled_sync(_gam_client(service), storage)
    assert first["status"] == "success", first

    from ad_seller.flows import product_setup_flow

    with patch.dict(
        product_setup_flow._ACTIVE_INVENTORY_FILTERS,
        {"google_ad_manager": rejected_filter},
    ):
        result = await _run_scheduled_sync(
            _gam_client(_FakeAdManagerService([("999", "ACTIVE")])), storage
        )

    assert result["status"] == "error"
    assert error in result["error"]
    assert await _synced_product_ids(storage) == {"101", "104"}

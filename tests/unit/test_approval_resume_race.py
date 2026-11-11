"""Exercise simultaneous resumes through real, independent SQLite connections."""

import asyncio
import os
from contextvars import ContextVar
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from ad_seller.events.models import ApprovalRequest, ApprovalResponse, ApprovalStatus
from ad_seller.services.approval_service import (
    _resume_ssp_distribution,
    _transition_approval,
    resume_flow,
)
from ad_seller.storage.sqlite_backend import SQLiteBackend
from tests.unit.test_dsp_resolution_gate import TestResumeSSPDistribution as ResumeFixtures


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure", "unknown", "reconcile"])
@pytest.mark.parametrize("backend_kind", ["sqlite", "redis"])
async def test_simultaneous_resume_has_one_owner(tmp_path, outcome, backend_kind):
    fixture = ResumeFixtures()
    request = fixture._approval_request(
        status=ApprovalStatus.OUTCOME_UNKNOWN if outcome == "reconcile" else ApprovalStatus.APPROVED
    )
    response = ApprovalResponse(
        approval_id=request.approval_id, decision="approve", modifications={"selected_dsp_id": 52}
    )
    if backend_kind == "redis":
        if not os.environ.get("TEST_REDIS_URL"):
            pytest.skip("TEST_REDIS_URL is not set")
        from uuid import uuid4

        from ad_seller.storage.redis_backend import RedisBackend

        prefix = f"approval-race-{uuid4()}:"
        backends = [RedisBackend(os.environ["TEST_REDIS_URL"], key_prefix=prefix) for _ in range(2)]
    else:
        backends = [SQLiteBackend(str(tmp_path / "approval.db")) for _ in range(2)]
    for backend in backends:
        await backend.connect()
    key = f"approval:{request.approval_id}"
    await backends[0].set(key, request.model_dump(mode="json"))
    await backends[0].set(
        f"approval_response:{request.approval_id}", response.model_dump(mode="json")
    )
    selected = ContextVar("selected_backend", default=backends[0])
    registry, ssp = fixture._mock_registry(fixture._created_deal())
    ssp.find_deal_by_external_id.return_value = fixture._created_deal()
    if outcome == "failure":
        ssp.create_deal.side_effect = RuntimeError("definite failure")
    if outcome == "unknown":
        from ad_seller.clients.ssp_base import SSPDealOutcomeUnknownError

        ssp.create_deal.side_effect = SSPDealOutcomeUnknownError("unknown outcome")

    # Both resume_flow calls read the same decision before either can claim it.
    arrived = 0
    ready = asyncio.Event()
    originals = [backend.get for backend in backends]

    def synchronized_get(original):
        first = True

        async def get(name):
            nonlocal first, arrived
            value = await original(name)
            if name == key and first:
                first = False
                arrived += 1
                if arrived == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), 5)
            return value

        return get

    for backend, original in zip(backends, originals):
        backend.get = synchronized_get(original)

    async def run(backend):
        selected.set(backend)
        return await resume_flow(request.approval_id)

    try:
        with (
            patch("ad_seller.storage.factory.get_storage", AsyncMock(side_effect=selected.get)),
            patch("ad_seller.clients.ssp_factory.build_ssp_registry", return_value=registry),
        ):
            results = await asyncio.wait_for(
                asyncio.gather(*(run(b) for b in backends), return_exceptions=True), 10
            )
            conflicts = [
                r for r in results if isinstance(r, HTTPException) and r.status_code == 409
            ]
            assert len(conflicts) == 1
            assert ssp.create_deal.await_count == (0 if outcome == "reconcile" else 1)
            assert ssp.find_deal_by_external_id.await_count == (1 if outcome == "reconcile" else 0)
            stored = await backends[0].get(key)
            expected = {"failure": "approved", "unknown": "outcome_unknown"}.get(
                outcome, "consumed"
            )
            assert stored["status"] == expected
            assert stored["resume_token"]
            if outcome == "failure":
                # A stale approved snapshot cannot claim a new attempt after restoration.
                with pytest.raises(HTTPException) as error:
                    await _resume_ssp_distribution(request, response)
                assert error.value.status_code == 409
                ssp.create_deal.side_effect = None
                await resume_flow(request.approval_id)
                assert ssp.create_deal.await_count == 2
                assert (await backends[0].get(key))["status"] == "consumed"
    finally:
        await backends[0].delete(key)
        await backends[0].delete(f"approval_response:{request.approval_id}")
        for backend in backends:
            await backend.disconnect()


@pytest.mark.asyncio
async def test_old_attempt_cannot_restore_newer_consumption(tmp_path):
    backend = SQLiteBackend(str(tmp_path / "approval.db"))
    await backend.connect()
    try:
        request = ResumeFixtures()._approval_request(status=ApprovalStatus.CONSUMED)
        request.resume_token = "old-attempt"
        newer = request.model_copy(update={"resume_token": "new-attempt"})
        key = f"approval:{request.approval_id}"
        await backend.set(key, newer.model_dump(mode="json"))
        with patch("ad_seller.storage.factory.get_storage", return_value=backend):
            with pytest.raises(HTTPException) as error:
                await _transition_approval(request, ApprovalStatus.APPROVED)
        assert error.value.status_code == 409
        assert ApprovalRequest.model_validate(await backend.get(key)) == newer
    finally:
        await backend.disconnect()


@pytest.mark.asyncio
async def test_cancelled_create_requires_reconciliation(tmp_path):
    backend = SQLiteBackend(str(tmp_path / "approval.db"))
    await backend.connect()
    try:
        fixture = ResumeFixtures()
        request = fixture._approval_request(status=ApprovalStatus.APPROVED)
        response = ApprovalResponse(
            approval_id=request.approval_id,
            decision="approve",
            modifications={"selected_dsp_id": 52},
        )
        key = f"approval:{request.approval_id}"
        # Legacy records without a token are claimable too.
        stored = request.model_dump(mode="json")
        stored.pop("resume_token")
        await backend.set(key, stored)
        entered = asyncio.Event()

        async def create(_request):
            entered.set()
            await asyncio.Future()

        registry, ssp = fixture._mock_registry(create_side_effect=create)
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=backend),
            patch("ad_seller.clients.ssp_factory.build_ssp_registry", return_value=registry),
        ):
            task = asyncio.create_task(_resume_ssp_distribution(request, response))
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert (await backend.get(key))["status"] == "outcome_unknown"
            ssp.find_deal_by_external_id.return_value = fixture._created_deal()
            await _resume_ssp_distribution(
                ApprovalRequest.model_validate(await backend.get(key)), response
            )
            assert ssp.create_deal.await_count == 1
            assert ssp.find_deal_by_external_id.await_count == 1
            assert (await backend.get(key))["status"] == "consumed"
    finally:
        await backend.disconnect()


@pytest.mark.asyncio
async def test_failed_reconciliation_never_retries_create(tmp_path):
    backend = SQLiteBackend(str(tmp_path / "approval.db"))
    await backend.connect()
    try:
        fixture = ResumeFixtures()
        request = fixture._approval_request(status=ApprovalStatus.OUTCOME_UNKNOWN)
        response = ApprovalResponse(
            approval_id=request.approval_id,
            decision="approve",
            modifications={"selected_dsp_id": 52},
        )
        key = f"approval:{request.approval_id}"
        await backend.set(key, request.model_dump(mode="json"))
        await backend.set(
            f"approval_response:{request.approval_id}", response.model_dump(mode="json")
        )
        registry, ssp = fixture._mock_registry(fixture._created_deal())
        ssp.find_deal_by_external_id.side_effect = RuntimeError("lookup failed")
        with (
            patch("ad_seller.storage.factory.get_storage", return_value=backend),
            patch("ad_seller.clients.ssp_factory.build_ssp_registry", return_value=registry),
        ):
            for _ in range(2):
                with pytest.raises(RuntimeError, match="lookup failed"):
                    await resume_flow(request.approval_id)
                assert (await backend.get(key))["status"] == "outcome_unknown"
                ssp.create_deal.assert_not_awaited()
        assert ssp.find_deal_by_external_id.await_count == 2
    finally:
        await backend.disconnect()

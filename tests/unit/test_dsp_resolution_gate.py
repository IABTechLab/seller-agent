# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Unit tests for DSP-resolution approval gating in interfaces/api/main.py.

Covers:
- _resolve_dsp_id_or_pending_approval: dsp_id passthrough, no-seat-ids
  short-circuit, single-match resolution, zero-match error, and
  multi-match approval gating
- _resume_ssp_distribution: reject short-circuits, missing selected_dsp_id
  raises, approve applies the human's selection and creates the deal
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from ad_seller.clients.ssp_base import SSPDealCreateRequest, SSPDealType, SSPType
from ad_seller.clients.ssp_index_exchange import DspSeatMatch
from ad_seller.events.models import ApprovalRequest, ApprovalResponse
from ad_seller.interfaces.api.main import (
    _resolve_dsp_id_or_pending_approval,
    _resume_ssp_distribution,
)


def _create_request(**overrides) -> SSPDealCreateRequest:
    defaults = dict(
        deal_type=SSPDealType.PMP,
        name="Test Deal",
        cpm=15.0,
        buyer_seat_ids=["acc-100522"],
        external_deal_id="EXT-DEAL-001",
        account_id=12345,
    )
    defaults.update(overrides)
    return SSPDealCreateRequest(**defaults)


class TestResolveDspIdOrPendingApproval:
    @pytest.mark.asyncio
    async def test_returns_none_when_dsp_id_already_set(self):
        ssp = MagicMock()
        ssp.resolve_dsp_ids_for_seat_ids = AsyncMock()
        request = _create_request(dsp_id=17)

        result = await _resolve_dsp_id_or_pending_approval(ssp, request, "deal-1")

        assert result is None
        ssp.resolve_dsp_ids_for_seat_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_buyer_seat_ids(self):
        ssp = MagicMock()
        ssp.resolve_dsp_ids_for_seat_ids = AsyncMock()
        request = _create_request(buyer_seat_ids=[])

        result = await _resolve_dsp_id_or_pending_approval(ssp, request, "deal-1")

        assert result is None
        ssp.resolve_dsp_ids_for_seat_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_when_ssp_lacks_resolver(self):
        ssp = MagicMock(spec=["ssp_type", "create_deal"])
        request = _create_request()

        result = await _resolve_dsp_id_or_pending_approval(ssp, request, "deal-1")

        assert result is None
        assert request.dsp_id is None

    @pytest.mark.asyncio
    async def test_single_match_sets_dsp_id(self):
        ssp = MagicMock()
        ssp.resolve_dsp_ids_for_seat_ids = AsyncMock(
            return_value=[DspSeatMatch(dsp_id=17, extended_seat_id="acc-100522", status="A")]
        )
        request = _create_request()

        result = await _resolve_dsp_id_or_pending_approval(ssp, request, "deal-1")

        assert result is None
        assert request.dsp_id == 17

    @pytest.mark.asyncio
    async def test_no_match_raises_400(self):
        ssp = MagicMock()
        ssp.resolve_dsp_ids_for_seat_ids = AsyncMock(return_value=[])
        request = _create_request()

        with pytest.raises(HTTPException) as exc_info:
            await _resolve_dsp_id_or_pending_approval(ssp, request, "deal-1")

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"] == "dsp_resolution_failed"

    @pytest.mark.asyncio
    async def test_multiple_matches_requests_approval_and_returns_pending(self):
        ssp = MagicMock()
        ssp.ssp_type = SSPType.INDEX_EXCHANGE
        ssp.resolve_dsp_ids_for_seat_ids = AsyncMock(
            return_value=[
                DspSeatMatch(dsp_id=17, extended_seat_id="acc-100522", status="A"),
                DspSeatMatch(dsp_id=52, extended_seat_id="acc-100522", status="A"),
            ]
        )
        request = _create_request()

        mock_approval_request = MagicMock(approval_id="apr-999")
        mock_gate_instance = MagicMock()
        mock_gate_instance.request_approval = AsyncMock(return_value=mock_approval_request)

        with (
            patch(
                "ad_seller.storage.factory.get_storage",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch(
                "ad_seller.events.approval.ApprovalGate",
                return_value=mock_gate_instance,
            ),
        ):
            result = await _resolve_dsp_id_or_pending_approval(ssp, request, "deal-1")

        assert result == {
            "status": "pending_approval",
            "approval_id": "apr-999",
            "message": (
                'Seat ID(s) [\'acc-100522\'] matched multiple DSPs [17, 52]; a human must '
                "select the correct one via POST /approvals/apr-999/decide "
                '(modifications={"selected_dsp_id": <id>}) before this deal can be created.'
            ),
        }
        assert request.dsp_id is None  # left unresolved — resume path sets it

        call_kwargs = mock_gate_instance.request_approval.call_args.kwargs
        assert call_kwargs["flow_type"] == "ssp_distribution"
        assert call_kwargs["gate_name"] == "dsp_resolution"
        assert call_kwargs["deal_id"] == "deal-1"
        assert call_kwargs["context"]["seat_ids"] == ["acc-100522"]
        assert {c["dsp_id"] for c in call_kwargs["context"]["candidates"]} == {17, 52}
        assert call_kwargs["flow_state_snapshot"]["ssp_name"] == "index_exchange"
        assert call_kwargs["flow_state_snapshot"]["create_request"]["external_deal_id"] == (
            "EXT-DEAL-001"
        )


class TestResumeSSPDistribution:
    def _approval_request(self, **overrides):
        defaults = dict(
            approval_id="apr-999",
            event_id="evt-1",
            flow_id="ssp-distribute-deal-1",
            flow_type="ssp_distribution",
            gate_name="dsp_resolution",
            deal_id="deal-1",
            flow_state_snapshot={
                "ssp_name": "index_exchange",
                "create_request": _create_request().model_dump(mode="json"),
            },
        )
        defaults.update(overrides)
        return ApprovalRequest(**defaults)

    @pytest.mark.asyncio
    async def test_reject_returns_without_creating_deal(self):
        request = self._approval_request()
        response = ApprovalResponse(approval_id="apr-999", decision="reject")

        with patch("ad_seller.clients.ssp_factory.build_ssp_registry") as mock_build:
            result = await _resume_ssp_distribution(request, response)

        mock_build.assert_not_called()
        assert result == {
            "deal_id": "deal-1",
            "status": "reject",
            "resumed_from_approval": "apr-999",
        }

    @pytest.mark.asyncio
    async def test_approve_missing_selected_dsp_id_raises_400(self):
        request = self._approval_request()
        response = ApprovalResponse(approval_id="apr-999", decision="approve", modifications={})

        with pytest.raises(HTTPException) as exc_info:
            await _resume_ssp_distribution(request, response)

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_approve_with_selected_dsp_id_creates_deal(self):
        request = self._approval_request()
        response = ApprovalResponse(
            approval_id="apr-999",
            decision="approve",
            modifications={"selected_dsp_id": 52},
        )

        created_deal = MagicMock()
        created_deal.deal_id = "EXT-DEAL-001"
        created_deal.ssp_name = "Index Exchange"
        created_deal.ssp_type = SSPType.INDEX_EXCHANGE
        created_deal.status = MagicMock(value="active")
        created_deal.model_dump.return_value = {"deal_id": "EXT-DEAL-001"}

        mock_ssp = AsyncMock()
        mock_ssp.create_deal = AsyncMock(return_value=created_deal)
        mock_ssp.__aenter__ = AsyncMock(return_value=mock_ssp)
        mock_ssp.__aexit__ = AsyncMock(return_value=False)

        mock_registry = MagicMock()
        mock_registry.get_client.return_value = mock_ssp

        with patch(
            "ad_seller.clients.ssp_factory.build_ssp_registry",
            return_value=mock_registry,
        ):
            result = await _resume_ssp_distribution(request, response)

        mock_registry.get_client.assert_called_once_with("index_exchange")
        create_request_arg = mock_ssp.create_deal.call_args.args[0]
        assert create_request_arg.dsp_id == 52
        assert create_request_arg.external_deal_id == "EXT-DEAL-001"
        assert result["resumed_from_approval"] == "apr-999"
        assert result["deal_id"] == "EXT-DEAL-001"

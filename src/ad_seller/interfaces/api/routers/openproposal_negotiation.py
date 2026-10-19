# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""OpenProposal 3.0 negotiation over the existing seller-agent engine.

Price is negotiated as a ``requestable`` counter (spec section 3). Every move
is recorded as an append-only ``negotiation_history`` entry (spec 4.1), seller
counters create a new ``version``, and commit books through the existing deal
flow before the line item is ``agreed`` (book, then agree). The seller's
floor, strategy and round budget are never returned.
"""

import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path
from iab_agentic_primitives.primitives import BuyerIdentity, DealType, Money
from iab_agentic_primitives.protocol import (
    DealBookingRequest,
    NegotiationMessage,
    QuoteRequest,
)
from iab_agentic_primitives.protocol.negotiation import NegotiationAction

from ....models.openproposal import (
    Commitment,
    ExternalReference,
    HoldState,
    HoldStatus,
    LineItem,
    NegotiationActor,
    NegotiationHistoryAction,
    NegotiationHistoryEntry,
    NegotiationThreadState,
    OPAssent,
    OPNegotiationCommitRequest,
    OPNegotiationMessageAction,
    OPNegotiationMessageRequest,
    OPNegotiationOpenRequest,
    OPNegotiationThread,
    PricingEntry,
    Property,
    Selectable,
)
from .. import deps
from . import deals as deals_routes
from . import negotiation as negotiation_routes
from . import quotes as quotes_routes

router = APIRouter()

TAG = "OpenProposal (negotiation)"

THREAD_KEY = "op_negotiation:{}"
OPEN_IDEMPOTENCY_KEY = "idempotency:op_negotiation_open:{}:{}"

MECHANISM_TO_DEAL_TYPE = {
    "PROGRAMMATIC_GUARANTEED": DealType.PROGRAMMATIC_GUARANTEED,
    "PREFERRED_DEAL": DealType.PREFERRED_DEAL,
    "PRIVATE_AUCTION": DealType.PRIVATE_AUCTION,
}

SELLER_ACTION_TO_HISTORY = {
    "counter": NegotiationHistoryAction.COUNTERED,
    "final_offer": NegotiationHistoryAction.COUNTERED,
    "accept": NegotiationHistoryAction.ACCEPTED,
    "reject": NegotiationHistoryAction.DECLINED,
}

PRICE_FIELD = "pricing[].agreed_rate"

INVENTORY_TYPE_TO_CHANNEL = {
    "display": "display",
    "video": "video",
    "ctv": "ctv",
    "native": "native",
    "mobile_app": "display",
    "linear_tv": "video",
}

OPEN_EXAMPLES = {
    "ask_lower_price": {
        "summary": "Agency buyer asks $36 CPM on a $40.50 line",
        "value": {
            "idempotency_key": "op-neg-001",
            "proposal_id": "examplepub-standard",
            "version": 1,
            "line_item_id": "inv-ctv-apex-series",
            "transaction_mechanism": "PREFERRED_DEAL",
            "commitment": {
                "basis": "units",
                "amount": 500000,
                "flight_start": "2026-11-01",
                "flight_end": "2026-11-30",
            },
            "requested_rate": 36.0,
            "buyer_identity": {"seat_id": "seat-demo", "agency_id": "agency-demo"},
        },
    }
}

MESSAGE_EXAMPLES = {
    "counter": {
        "summary": "Counter at $37 CPM",
        "value": {
            "idempotency_key": "op-neg-002",
            "version": 2,
            "action": "counter",
            "requested_rate": 37.0,
        },
    },
    "accept": {
        "summary": "Accept the seller's last price",
        "value": {"idempotency_key": "op-neg-003", "version": 2, "action": "accept"},
    },
    "decline": {
        "summary": "Decline and end the negotiation",
        "value": {"idempotency_key": "op-neg-004", "version": 2, "action": "decline"},
    },
}

COMMIT_EXAMPLES = {
    "commit": {
        "summary": "Commit at the agreed price",
        "value": {"idempotency_key": "op-neg-commit-001", "version": 2},
    }
}

NEGOTIATION_ID_EXAMPLES = {
    "thread": {"summary": "negotiation_id returned when the thread opened", "value": "opn-1a2b3c4d"}
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _money(value: Any) -> Optional[float]:
    if isinstance(value, dict) and value.get("amount_micros") is not None:
        return value["amount_micros"] / 1_000_000
    return None


def _require_key(api_key_record: Any) -> str:
    if api_key_record is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "authentication_required", "message": "API key required."},
        )
    return str(getattr(api_key_record, "key_id", ""))


async def _storage():
    from ....storage.factory import get_storage

    return await get_storage()


async def _load(negotiation_id: str, owner: str) -> dict[str, Any]:
    thread = await (await _storage()).get(THREAD_KEY.format(negotiation_id))
    if not thread or thread.get("owner") != owner:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "negotiation_not_found",
                "message": f"Negotiation '{negotiation_id}' not found.",
            },
        )
    return thread


async def _save(thread: dict[str, Any]) -> None:
    await (await _storage()).set(THREAD_KEY.format(thread["negotiation_id"]), thread)


def _history(
    thread: dict[str, Any],
    actor: NegotiationActor,
    action: NegotiationHistoryAction,
    rate: Optional[float] = None,
    fields: Optional[list[str]] = None,
    **ext: Any,
) -> None:
    data = {k: v for k, v in {"rate": rate, **ext}.items() if v is not None}
    thread["history"].append(
        NegotiationHistoryEntry(
            version=thread["version"],
            actor=actor,
            action=action,
            fields_changed=fields if fields is not None else ([PRICE_FIELD] if rate else []),
            at=_now(),
            ext={"seller_agent": data} if data else None,
        ).model_dump(mode="json", exclude_none=True)
    )


def _apply_seller_round(thread: dict[str, Any], round_data: dict[str, Any]) -> None:
    action = round_data.get("action")
    price = _money(round_data.get("seller_price"))
    history_action = SELLER_ACTION_TO_HISTORY.get(action, NegotiationHistoryAction.COUNTERED)
    if history_action == NegotiationHistoryAction.COUNTERED:
        thread["version"] += 1
    if price is not None and action != "reject":
        thread["seller_rate"] = price
    _history(
        thread,
        NegotiationActor.SELLER,
        history_action,
        rate=price if action != "reject" else None,
        fields=[PRICE_FIELD] if action in ("counter", "final_offer") else [],
        final_offer=True if action == "final_offer" else None,
        rationale=round_data.get("rationale") or None,
    )
    if action == "accept":
        thread["state"] = NegotiationThreadState.ACCEPTED.value
        thread["agreed_rate"] = price
    elif action == "reject":
        thread["state"] = NegotiationThreadState.DECLINED.value


def _check_version(thread: dict[str, Any], version: int) -> None:
    if version != thread["version"]:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "stale_version",
                "message": f"Version {version} is stale; the current version is {thread['version']}.",
                "current_version": thread["version"],
            },
        )


def _check_open(thread: dict[str, Any], *allowed: str) -> None:
    if thread["state"] not in allowed:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "negotiation_closed",
                "message": f"Negotiation is {thread['state']}.",
            },
        )


def _line_item_status(state: str) -> str:
    return {
        "open": "under_review",
        "accepted": "under_review",
        "committed": "agreed",
    }.get(state, "published")


def _view(thread: dict[str, Any]) -> OPNegotiationThread:
    quote = thread["quote"]
    product = quote.get("product") or {}
    pricing = quote.get("pricing") or {}
    terms = quote.get("terms") or {}
    mech = thread["transaction_mechanism"]
    current = thread.get("agreed_rate") or thread.get("seller_rate")
    refs = [ExternalReference(system="seller-agent", id=quote["quote_id"], role="quote")]
    if thread.get("deal_id"):
        refs.append(ExternalReference(system="seller-agent", id=thread["deal_id"], role="deal"))
    line_item = LineItem(
        line_item_id=thread["line_item_id"],
        status=_line_item_status(thread["state"]),
        channel=INVENTORY_TYPE_TO_CHANNEL.get(
            product.get("inventory_type") or "display", "display"
        ),
        properties=Selectable(
            available=[Property(id=product.get("product_id", ""), name=product.get("name"))],
            included=[product.get("product_id", "")],
        ),
        content_detail=product.get("name"),
        pricing=[
            PricingEntry(
                cost_method=str(pricing.get("pricing_model") or "cpm").upper(),
                gross_rate=_money(pricing.get("final_cpm")),
                agreed_rate=current,
                price_valid_until=quote.get("expires_at"),
                currency=(pricing.get("final_cpm") or {}).get("currency", "USD"),
            )
        ],
        commitment=Commitment(
            basis="units",
            amount=terms.get("impressions") or thread["commitment"]["amount"],
            flight_start=terms.get("flight_start"),
            flight_end=terms.get("flight_end"),
            idempotency_key=thread.get("commit_idempotency_key"),
        ),
        hold_status=HoldStatus(
            hold_available=False,
            state=HoldState.CONVERTED if thread["state"] == "committed" else HoldState.NONE,
        ),
        transaction_mechanism=Selectable(available=[mech], included=[mech]),
        deal_id_issuance="at_commitment",
        external_references=refs,
        correlation_id=thread["negotiation_id"],
    )
    return OPNegotiationThread(
        negotiation_id=thread["negotiation_id"],
        proposal_id=thread["proposal_id"],
        version=thread["version"],
        state=thread["state"],
        line_item=line_item,
        negotiation_history=thread["history"],
        assent=OPAssent(**thread["assent"]) if thread.get("assent") else None,
    )


async def _engine(
    thread: dict[str, Any],
    action: NegotiationAction,
    price: Optional[float],
    key: str,
    api_key_record: Any,
) -> dict[str, Any]:
    message = NegotiationMessage(
        idempotency_key=f"op:{thread['negotiation_id']}:{key}",
        action=action,
        quote_id=thread["quote"]["quote_id"],
        buyer_price=Money(amount_micros=round(price * 1_000_000), currency="USD")
        if price is not None
        else None,
        buyer_identity=BuyerIdentity(**thread["buyer_identity"])
        if thread.get("buyer_identity")
        else None,
    )
    response = await negotiation_routes.post_negotiation_message(
        message=message, api_key_record=api_key_record
    )
    return response.model_dump(mode="json")["round"]


def _replay(
    thread: dict[str, Any], key: str, payload: dict[str, Any]
) -> Optional[OPNegotiationThread]:
    prior = thread.setdefault("idempotency", {}).get(key)
    if prior is None:
        return None
    if prior != payload:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "idempotency_conflict",
                "message": f"idempotency_key '{key}' was already used for a different message.",
            },
        )
    return _view(thread)


@router.post(
    "/api/v1/op-negotiations",
    tags=[TAG],
    response_model=OPNegotiationThread,
    response_model_exclude_none=True,
    status_code=201,
)
async def open_op_negotiation(
    request: OPNegotiationOpenRequest = Body(openapi_examples=OPEN_EXAMPLES),
    api_key_record=Depends(deps._get_optional_api_key_record),
) -> OPNegotiationThread:
    """Ask for a price on a line item. Opens a negotiation thread.

    The seller agent quotes the line item (tier pricing applies), then runs
    its negotiation engine on ``requested_rate``. The reply holds the
    buyer's ``proposed`` entry and the seller's ``countered``, ``accepted``
    or ``declined`` entry. Requires an API key.
    """
    owner = _require_key(api_key_record)
    storage = await _storage()
    idem_key = OPEN_IDEMPOTENCY_KEY.format(owner, request.idempotency_key)
    payload = request.model_dump(mode="json")
    prior = await storage.get(idem_key)
    if prior:
        if prior.get("payload") != payload:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "idempotency_conflict",
                    "message": f"idempotency_key '{request.idempotency_key}' was already used.",
                },
            )
        return _view(await _load(prior["negotiation_id"], owner))

    deal_type = MECHANISM_TO_DEAL_TYPE.get(request.transaction_mechanism.value)
    if deal_type is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_transaction_mechanism",
                "message": f"{request.transaction_mechanism.value} cannot be negotiated here; use "
                "PROGRAMMATIC_GUARANTEED, PREFERRED_DEAL or PRIVATE_AUCTION.",
            },
        )
    try:
        quote_request = QuoteRequest(
            idempotency_key=f"op-negotiation:{request.idempotency_key}",
            product_id=request.line_item_id,
            deal_type=deal_type,
            impressions=request.commitment.amount,
            flight_start=date.fromisoformat(request.commitment.flight_start)
            if request.commitment.flight_start
            else None,
            flight_end=date.fromisoformat(request.commitment.flight_end)
            if request.commitment.flight_end
            else None,
            buyer_identity=BuyerIdentity(**request.buyer_identity)
            if request.buyer_identity
            else None,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error": "invalid_request", "message": str(exc)}
        ) from exc
    quote = await quotes_routes.create_quote(request=quote_request, api_key_record=api_key_record)

    thread: dict[str, Any] = {
        "negotiation_id": f"opn-{uuid.uuid4().hex[:8]}",
        "owner": owner,
        "proposal_id": request.proposal_id,
        "line_item_id": request.line_item_id,
        "version": request.version,
        "state": NegotiationThreadState.OPEN.value,
        "transaction_mechanism": request.transaction_mechanism.value,
        "commitment": request.commitment.model_dump(mode="json"),
        "buyer_identity": request.buyer_identity,
        "quote": quote.quote.model_dump(mode="json"),
        "seller_rate": _money(quote.quote.model_dump(mode="json")["pricing"].get("final_cpm")),
        "history": [],
        "idempotency": {},
    }
    _history(
        thread,
        NegotiationActor.BUYER,
        NegotiationHistoryAction.PROPOSED,
        rate=request.requested_rate,
        fields=[PRICE_FIELD, "commitment", "transaction_mechanism"],
    )
    round_data = await _engine(
        thread, NegotiationAction.COUNTER, request.requested_rate, "open", api_key_record
    )
    _apply_seller_round(thread, round_data)
    await _save(thread)
    await storage.set(idem_key, {"negotiation_id": thread["negotiation_id"], "payload": payload})
    return _view(thread)


@router.post(
    "/api/v1/op-negotiations/{negotiation_id}/messages",
    tags=[TAG],
    response_model=OPNegotiationThread,
    response_model_exclude_none=True,
)
async def post_op_negotiation_message(
    negotiation_id: str = Path(openapi_examples=NEGOTIATION_ID_EXAMPLES),
    request: OPNegotiationMessageRequest = Body(openapi_examples=MESSAGE_EXAMPLES),
    api_key_record=Depends(deps._get_optional_api_key_record),
) -> OPNegotiationThread:
    """Counter, accept, decline or withdraw.

    ``version`` must be the thread's current version (409 ``stale_version``
    otherwise). ``accept`` takes the seller's last price; the line item stays
    ``under_review`` until commit.
    """
    owner = _require_key(api_key_record)
    thread = await _load(negotiation_id, owner)
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    replay = _replay(thread, request.idempotency_key, payload)
    if replay:
        return replay
    _check_open(thread, NegotiationThreadState.OPEN.value)
    _check_version(thread, request.version)

    action = request.action
    if action == OPNegotiationMessageAction.COUNTER:
        if request.requested_rate is None:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_request", "message": "counter needs requested_rate."},
            )
        _history(
            thread,
            NegotiationActor.BUYER,
            NegotiationHistoryAction.COUNTERED,
            rate=request.requested_rate,
        )
        round_data = await _engine(
            thread,
            NegotiationAction.COUNTER,
            request.requested_rate,
            request.idempotency_key,
            api_key_record,
        )
        _apply_seller_round(thread, round_data)
    elif action == OPNegotiationMessageAction.ACCEPT:
        price = thread["seller_rate"]
        await _engine(
            thread, NegotiationAction.ACCEPT, price, request.idempotency_key, api_key_record
        )
        _history(
            thread, NegotiationActor.BUYER, NegotiationHistoryAction.ACCEPTED, rate=price, fields=[]
        )
        thread["state"] = NegotiationThreadState.ACCEPTED.value
        thread["agreed_rate"] = price
    else:
        await _engine(
            thread, NegotiationAction.REJECT, None, request.idempotency_key, api_key_record
        )
        history_action = (
            NegotiationHistoryAction.DECLINED
            if action == OPNegotiationMessageAction.DECLINE
            else NegotiationHistoryAction.WITHDRAWN
        )
        _history(thread, NegotiationActor.BUYER, history_action, fields=[])
        thread["state"] = (
            NegotiationThreadState.DECLINED.value
            if action == OPNegotiationMessageAction.DECLINE
            else NegotiationThreadState.WITHDRAWN.value
        )

    thread["idempotency"][request.idempotency_key] = payload
    await _save(thread)
    return _view(thread)


@router.post(
    "/api/v1/op-negotiations/{negotiation_id}/commit",
    tags=[TAG],
    response_model=OPNegotiationThread,
    response_model_exclude_none=True,
)
async def commit_op_negotiation(
    negotiation_id: str = Path(openapi_examples=NEGOTIATION_ID_EXAMPLES),
    request: OPNegotiationCommitRequest = Body(openapi_examples=COMMIT_EXAMPLES),
    api_key_record=Depends(deps._get_optional_api_key_record),
) -> OPNegotiationThread:
    """Commit at the agreed price: book first, then agree.

    Books the negotiated quote through the existing deal flow (which honours
    the accepted price). Only on success does the line item become
    ``agreed``, with ``assent`` recorded and the deal id in
    ``external_references``. A failed booking leaves nothing agreed.
    """
    owner = _require_key(api_key_record)
    thread = await _load(negotiation_id, owner)
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    if thread["state"] == NegotiationThreadState.COMMITTED.value:
        if thread.get("commit_idempotency_key") == request.idempotency_key:
            return _view(thread)
        raise HTTPException(
            status_code=409,
            detail={"error": "negotiation_closed", "message": "Negotiation is already committed."},
        )
    _check_open(thread, NegotiationThreadState.ACCEPTED.value)
    _check_version(thread, request.version)

    booking = DealBookingRequest(
        idempotency_key=f"op-commit:{request.idempotency_key}",
        quote_id=thread["quote"]["quote_id"],
        buyer_identity=BuyerIdentity(**thread["buyer_identity"])
        if thread.get("buyer_identity")
        else None,
    )
    try:
        response = await deals_routes.book_deal(request=booking, api_key_record=api_key_record)
    except HTTPException as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "error": "booking_failed",
                "message": "Nothing was agreed.",
                "cause": exc.detail,
            },
        ) from exc
    deal = response.deal.model_dump(mode="json")
    now = _now()
    thread["deal_id"] = deal["deal_id"]
    thread["agreed_rate"] = _money((deal.get("pricing") or {}).get("final_cpm")) or thread.get(
        "agreed_rate"
    )
    thread["state"] = NegotiationThreadState.COMMITTED.value
    thread["commit_idempotency_key"] = request.idempotency_key
    thread["assent"] = {"buyer_at": now, "seller_at": now, "version": thread["version"]}
    thread["idempotency"][request.idempotency_key] = payload
    await _save(thread)
    return _view(thread)


@router.get(
    "/api/v1/op-negotiations/{negotiation_id}",
    tags=[TAG],
    response_model=OPNegotiationThread,
    response_model_exclude_none=True,
)
async def get_op_negotiation(
    negotiation_id: str = Path(openapi_examples=NEGOTIATION_ID_EXAMPLES),
    api_key_record=Depends(deps._get_optional_api_key_record),
) -> OPNegotiationThread:
    """The thread as OpenProposal: line item, version, history and assent."""
    owner = _require_key(api_key_record)
    return _view(await _load(negotiation_id, owner))
